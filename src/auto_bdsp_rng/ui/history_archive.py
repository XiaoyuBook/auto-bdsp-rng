"""Temporary disk storage for complete history, independent of log saving.

Only data produced by this process is unpickled. The temporary SQLite stores
are removed on close; no history or images are collected across runs.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterator
import pickle
import queue
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any

from auto_bdsp_rng.rng_core.cancellation import SearchCancelled

MAX_PENDING_CANDIDATE_BATCHES = 3


@dataclass(frozen=True)
class PlainSnapshot:
    """An append-only read boundary; clearing invalidates older readers."""

    epoch: int
    through_seq: int


class CandidateReservation:
    """Own a candidate snapshot from publication until disk commit."""

    def __init__(self, archive: HistoryArchive, epoch: int) -> None:
        self._archive = archive
        self.epoch = epoch
        self._publication_generation = archive._capacity_generation
        self.claimed = False
        self.released = False
        self.prepared_rows: Any | None = None

    def release(self) -> None:
        self._archive._release_reservation(self)

    def release_unclaimed(self) -> None:
        if not self.claimed:
            self.release()

    def valid_for(self, archive: HistoryArchive) -> bool:
        with archive._capacity:
            return self._archive is archive and not self.released and not self.claimed and self.epoch == archive._epoch


@dataclass(frozen=True)
class ReservedHistoryEvent:
    values: tuple[object, ...]
    reservation: CandidateReservation

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self):
        return iter(self.values)

    def __getitem__(self, index):
        return self.values[index]

    def __del__(self) -> None:
        # Qt may discard a queued event when its receiver is destroyed. Its
        # Python payload still releases the slot without calling a Qt object.
        self.reservation.release_unclaimed()


def publish_history_event(
    archive: HistoryArchive, emit: Callable[[str, object], None], event: str,
    values: tuple[object, ...], *, cancelled: Callable[[], bool] | None = None,
) -> None:
    """Publish history without making automation wait for archival capacity.

    A cleared/closed archive discards its own pending records. Only the run's
    cancellation callback may cancel automation, including reverse lookup.
    """
    reservation = None

    def interrupted() -> bool:
        if cancelled is not None and cancelled():
            return True
        if reservation is not None:
            with archive._capacity:
                return (archive._closed or not archive._accepting or reservation.released
                        or reservation.epoch != archive._epoch
                        or reservation._publication_generation != archive._capacity_generation)
        return False

    try:
        if event in {"candidates_found", "candidates_refiltered", "reverse_lookup_results"} and values and len(values[0]):
            reservation = archive.reserve_candidate_batch(wait=False, allow_overflow=True, cancelled=cancelled)
            if reservation is None:
                # An explicit clear owns the records being discarded. Do not
                # pause or fail the game workflow while it clears the store.
                return
        if interrupted():
            raise SearchCancelled("History event producer stopped")
        if reservation is not None and archive._prepare_candidates is not None:
            values, prepared = archive._prepare_candidates(event, values, cancelled=interrupted)
            with archive._capacity:
                if interrupted():
                    raise SearchCancelled("History event producer stopped")
                reservation.prepared_rows = prepared
        emit(event, values if reservation is None else ReservedHistoryEvent(values, reservation))
    except SearchCancelled:
        if reservation is not None:
            reservation.release_unclaimed()
        if cancelled is not None and cancelled():
            raise
    except BaseException:
        if reservation is not None:
            reservation.release_unclaimed()
        raise


class HistoryArchive:
    """Keep payloads on disk and a bounded cache of recently viewed rows."""

    def __init__(self, notice: Callable[[str], None] | None = None, *, prepare_candidates: Callable[..., Any] | None = None) -> None:
        # A plain Python factory prepares immutable candidate input before Qt
        # emission; it must not capture or access its panel's widgets.
        self._prepare_candidates = prepare_candidates
        self._lock = threading.RLock()
        self._db_lock = threading.RLock()
        self._candidate_db_lock = threading.RLock()
        self._candidate_read_lock = threading.RLock()
        self._capacity = threading.Condition()
        self._reservations: set[CandidateReservation] = set()
        self._accepting = True
        self._capacity_generation = 0
        self._clearing = False
        self._notice = notice
        self._cache: OrderedDict[tuple[int, int], Any] = OrderedDict()
        self._closed = False
        self._epoch = 0
        self._plain_seq = 0
        self.in_memory_fallback = False
        self._memory_databases: set[str] = set()
        schema = (
            # A memory rollback journal protects committed history from a
            # SQLITE_FULL failure without needing additional journal disk space.
            "PRAGMA cache_size=-2048; PRAGMA synchronous=OFF; PRAGMA journal_mode=MEMORY;"
            "CREATE TABLE plain (seq INTEGER PRIMARY KEY, uid INTEGER, data BLOB);"
            "CREATE INDEX plain_uid ON plain(uid, seq);"
            "CREATE TABLE feed (seq INTEGER PRIMARY KEY, uid INTEGER, data BLOB);"
            "CREATE INDEX feed_uid ON feed(uid, seq);"
            "CREATE TABLE candidate (batch INTEGER, ordinal INTEGER, data BLOB, "
            "PRIMARY KEY(batch, ordinal));"
            "CREATE TABLE round_record (uid INTEGER PRIMARY KEY, data BLOB);"
        )
        self._db = self._open_database("_db", schema)
        # WAL gives completed, immutable batches an independent reader even
        # while a new batch has a slow write transaction open. Metadata still
        # uses a separate small database and never waits on that writer.
        self._candidate_temp: tempfile.TemporaryDirectory | None = None
        self._candidate_memory_uri = f"file:bdsp-history-{id(self)}?mode=memory&cache=shared"
        self._open_candidate_databases(schema)
        self._next_batch = 0
        self._pending: dict[int, tuple[Callable[[], Iterator[Any]], int, CandidateReservation]] = {}
        self._fallback_candidates: dict[int, tuple[Callable[[], Iterator[Any]], int]] = {}
        # Keep complete snapshots when the disk falls behind. Automation must
        # not wait for a display/archive queue before acting on its target.
        # Sources are released as soon as the background writer commits them.
        self._jobs: queue.Queue[tuple[int, Callable[[], Iterator[Any]], CandidateReservation]] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None

    def _open_database(self, name: str, schema: str) -> sqlite3.Connection:
        connection = None
        try:
            connection = sqlite3.connect("", check_same_thread=False)
            connection.executescript(schema)
            connection.commit()
        except sqlite3.Error:
            if connection is not None:
                connection.close()
            connection = sqlite3.connect(":memory:", check_same_thread=False)
            connection.executescript(schema)
            connection.commit()
            self._memory_databases.add(name)
            self.in_memory_fallback = True
        return connection

    @staticmethod
    def _candidate_reader(location: str, *, memory: bool) -> sqlite3.Connection:
        connection = sqlite3.connect(location, uri=memory, check_same_thread=False)
        try:
            connection.execute("PRAGMA query_only=ON")
            if memory:
                # Only completed batches use this reader. New candidate rows
                # are append-only and pending batches read their source.
                connection.execute("PRAGMA read_uncommitted=ON")
        except BaseException:
            connection.close()
            raise
        return connection

    def _open_candidate_databases(self, schema: str) -> None:
        writer = reader = None
        try:
            self._candidate_temp = tempfile.TemporaryDirectory(prefix="bdsp-history-")
            location = str(Path(self._candidate_temp.name) / "candidates.sqlite")
            writer = sqlite3.connect(location, check_same_thread=False)
            writer.executescript(schema.replace("journal_mode=MEMORY", "journal_mode=WAL"))
            writer.commit()
            reader = self._candidate_reader(location, memory=False)
        except (OSError, sqlite3.Error):
            if reader is not None:
                reader.close()
            if writer is not None:
                writer.close()
            writer = sqlite3.connect(self._candidate_memory_uri, uri=True, check_same_thread=False)
            writer.executescript(schema)
            writer.commit()
            reader = self._candidate_reader(self._candidate_memory_uri, memory=True)
            self._memory_databases.add("_candidate_db")
            self.in_memory_fallback = True
        self._candidate_db, self._candidate_read_db = writer, reader

    def _read_candidates(self, action: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._candidate_read_lock:
            if self._closed:
                raise RuntimeError("history archive is closed")
            return action(self._candidate_read_db)

    def _perform(self, action: Callable[[sqlite3.Connection], Any], *, write: bool = False, candidates: bool = False) -> Any:
        name = "_candidate_db" if candidates else "_db"
        with self._candidate_db_lock if candidates else self._db_lock:
            if self._closed:
                raise RuntimeError("history archive is closed")
            db = getattr(self, name)
            try:
                if write:
                    db.execute("BEGIN")
                result = action(db)
                if write:
                    db.commit()
                return result
            except sqlite3.Error:
                # A failed write can implicitly roll back its transaction. In
                # either case restore the last committed boundary *before*
                # copying the database, or SQLITE_FULL may corrupt recovery.
                db.rollback()
                if name in self._memory_databases:
                    raise
                recovered = (sqlite3.connect(self._candidate_memory_uri, uri=True, check_same_thread=False)
                             if candidates else sqlite3.connect(":memory:", check_same_thread=False))
                try:
                    db.backup(recovered)
                    recovered.execute("PRAGMA journal_mode=MEMORY")
                    recovered.execute("PRAGMA max_page_count=2147483646")
                    if candidates:
                        reader = self._candidate_reader(self._candidate_memory_uri, memory=True)
                        with self._candidate_read_lock:
                            self._candidate_read_db.close()
                            self._candidate_read_db = reader
                except BaseException:
                    recovered.close()
                    if self._notice is not None:
                        self._notice("历史临时存储恢复失败；请保留当前会话并重试导出。")
                    raise
                setattr(self, name, recovered)
                self._memory_databases.add(name)
                self.in_memory_fallback = True
                db.close()
                if self._notice is not None:
                    self._notice("历史临时存储不可用，完整记录已转为内存保存；请在退出前导出。")
                if write:
                    recovered.execute("BEGIN")
                try:
                    result = action(recovered)
                    if write:
                        recovered.commit()
                    return result
                except BaseException:
                    recovered.rollback()
                    raise
            except BaseException:
                if write:
                    db.rollback()
                raise

    def append(self, table: str, uid: int, value: Any) -> None:
        if table not in {"plain", "feed"}:
            raise ValueError("invalid history table")
        encoded = pickle.dumps(value, protocol=5)
        with self._lock:
            result = self._perform(lambda db: db.execute(f"INSERT INTO {table}(uid, data) VALUES (?, ?)", (uid, encoded)), write=True)
            if table == "plain":
                self._plain_seq = result.lastrowid

    def store_record(self, uid: int, value: Any) -> None:
        encoded = pickle.dumps(value, protocol=5)
        self._perform(lambda db: db.execute("INSERT OR REPLACE INTO round_record VALUES (?, ?)", (uid, encoded)), write=True)

    def record(self, uid: int) -> Any:
        result = self._perform(lambda db: db.execute("SELECT data FROM round_record WHERE uid=?", (uid,)).fetchone())
        if result is None:
            raise KeyError(uid)
        return pickle.loads(result[0])

    def plain_snapshot(self) -> PlainSnapshot:
        with self._lock:
            return PlainSnapshot(self._epoch, self._plain_seq)

    def values(self, table: str, uid: int | None = None, *, offset: int = 0, limit: int = -1, snapshot: PlainSnapshot | None = None, after_seq: int = 0) -> Iterator[Any]:
        if table not in {"plain", "feed"}:
            raise ValueError("invalid history table")
        condition = "" if uid is None else " AND uid=?"
        last_seq = after_seq
        remaining = limit
        while remaining != 0:
            chunk_limit = 128 if remaining < 0 else min(128, remaining)
            boundary = "" if snapshot is None else " AND seq<=?"
            args = (last_seq,) + (() if uid is None else (uid,)) + (() if snapshot is None else (snapshot.through_seq,)) + (chunk_limit, offset)
            def read_rows(db):
                if snapshot is not None and snapshot.epoch != self._epoch:
                    raise RuntimeError("history snapshot was cleared")
                return db.execute(f"SELECT seq, data FROM {table} WHERE seq>?{condition}{boundary} ORDER BY seq LIMIT ? OFFSET ?", args).fetchall()
            rows = self._perform(read_rows)
            if not rows:
                break
            offset = 0
            last_seq = rows[-1][0]
            if remaining > 0:
                remaining -= len(rows)
            for _, encoded in rows:
                yield pickle.loads(encoded)

    def reserve_candidate_batch(self, *, wait: bool = True, allow_overflow: bool = False,
                                cancelled: Callable[[], bool] | None = None) -> CandidateReservation | None:
        """Automation bypasses capacity waits; direct GUI admission stays bounded."""
        with self._capacity:
            generation = self._capacity_generation
            while True:
                if self._closed or not self._accepting or generation != self._capacity_generation or (cancelled is not None and cancelled()):
                    raise SearchCancelled("History candidate admission cancelled")
                if not self._clearing and (allow_overflow or len(self._reservations) < MAX_PENDING_CANDIDATE_BATCHES):
                    reservation = CandidateReservation(self, self._epoch)
                    self._reservations.add(reservation)
                    return reservation
                if not wait:
                    return None
                self._capacity.wait(0.05)

    def _release_reservation(self, reservation: CandidateReservation) -> None:
        with self._capacity:
            reservation.released = True
            reservation.prepared_rows = None
            self._reservations.discard(reservation)
            self._capacity.notify_all()

    def stop_accepting(self) -> None:
        with self._capacity:
            self._accepting = False
            self._capacity_generation += 1
            self._capacity.notify_all()

    def resume_accepting(self) -> None:
        with self._capacity:
            if not self._closed:
                self._accepting = True
                self._capacity.notify_all()

    def create_candidate_batch(self, rows: Callable[[], Iterator[Any]], count: int, *, reservation: CandidateReservation | None = None) -> tuple[int, int]:
        reservation = reservation or self.reserve_candidate_batch(wait=False)
        if reservation is None:
            raise RuntimeError("history candidate capacity is full; retry after archiving")
        try:
            with self._capacity:
                if reservation._archive is not self or reservation.released or reservation.claimed or reservation.epoch != self._epoch:
                    raise SearchCancelled("History candidate reservation expired")
                reservation.claimed = True
            with self._lock:
                self._next_batch += 1
                batch = self._next_batch
                # Only the visible page is evaluated on the GUI. Every batch,
                # including one admitted during a burst, is written by worker.
                for ordinal, value in enumerate(islice(rows(), 100)):
                    self._cache[(batch, ordinal)] = value
                    if len(self._cache) > 128:
                        self._cache.popitem(last=False)
                self._pending[batch] = (rows, count, reservation)
                self._jobs.put_nowait((batch, rows, reservation))
        except BaseException:
            reservation.release()
            raise
        return batch, count

    def start_worker(self) -> None:
        """Start after the current Qt event finishes creating the visible page."""
        if (self._worker is None or not self._worker.is_alive()) and self._pending and not self._stop_event.is_set():
            self._worker = threading.Thread(target=self._run_jobs, name="History archive", daemon=True)
            self._worker.start()

    def _store_candidate_batch(self, batch: int, factory: Callable[[], Iterator[Any]], *, background: bool = False) -> bool:
        chunk = []
        for ordinal, row in enumerate(factory()):
            if self._stop_event.is_set():
                return False
            chunk.append((batch, ordinal, pickle.dumps(row, protocol=5)))
            if len(chunk) >= 128:
                self._perform(lambda db: db.executemany("INSERT OR REPLACE INTO candidate VALUES (?, ?, ?)", chunk), write=True, candidates=True)
                chunk = []
                if background:
                    # Windows Event.wait can round a 2 ms delay to an entire
                    # scheduler tick. Yield without imposing disk throughput
                    # delays here; automation must remain independent of I/O.
                    time.sleep(0)
        if chunk:
            self._perform(lambda db: db.executemany("INSERT OR REPLACE INTO candidate VALUES (?, ?, ?)", chunk), write=True, candidates=True)
        return True

    def _run_jobs(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    job = self._jobs.get(timeout=0.05)
                except queue.Empty:
                    continue
                batch, factory, reservation = job
                complete = False
                try:
                    complete = self._store_candidate_batch(batch, factory, background=True)
                except Exception as exc:
                    # An exceptional storage failure has a visible, complete
                    # memory fallback, rather than an ever-growing work queue.
                    if not self._stop_event.is_set():
                        with self._lock:
                            self._fallback_candidates[batch] = (factory, self._pending[batch][1])
                        self.in_memory_fallback = True
                        if self._notice is not None:
                            self._notice(f"历史候选归档失败，完整快照仍保存在内存；请在退出前导出: {exc}")
                finally:
                    with self._lock:
                        if not complete and not self._closed and batch not in self._fallback_candidates:
                            # A timed-out clear has not deleted its history.
                            # Preserve the unwritten suffix for a later export
                            # until clear is actually accepted.
                            self._fallback_candidates[batch] = (factory, self._pending[batch][1])
                        self._pending.pop(batch, None)
                    self._jobs.task_done()
                    del job, factory
                    reservation.release()
        finally:
            if self._closed:
                self._close_databases()

    def candidate(self, batch: int, ordinal: int) -> Any:
        key = (batch, ordinal)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            pending = self._pending.get(batch) or self._fallback_candidates.get(batch)
            if pending is not None:
                if not 0 <= ordinal < pending[1]:
                    raise IndexError(ordinal)
                start = ordinal // 128 * 128
                source = pending[0]
                page_reader = getattr(source, "read_page", None)
                page = (page_reader(start, start + 128) if page_reader is not None
                        else islice(source(), start, start + 128))
                for index, candidate in enumerate(page, start):
                    self._cache[(batch, index)] = candidate
                    if len(self._cache) > 128:
                        self._cache.popitem(last=False)
                value = self._cache[key]
            else:
                result = self._read_candidates(lambda db: db.execute("SELECT data FROM candidate WHERE batch=? AND ordinal=?", key).fetchone())
                if result is None:
                    raise IndexError(ordinal)
                value = pickle.loads(result[0])
            self._cache[key] = value
            if len(self._cache) > 128:
                self._cache.popitem(last=False)
            return value

    def candidate_rows(self, batch: int) -> Iterator[Any]:
        with self._lock:
            pending = self._pending.get(batch) or self._fallback_candidates.get(batch)
        if pending is not None:
            yield from pending[0]()
            return
        ordinal = -1
        while True:
            rows = self._read_candidates(lambda db: db.execute("SELECT ordinal, data FROM candidate WHERE batch=? AND ordinal>? ORDER BY ordinal LIMIT 128", (batch, ordinal)).fetchall())
            if not rows:
                break
            ordinal = rows[-1][0]
            for _, encoded in rows:
                yield pickle.loads(encoded)

    def close(self, *, wait: bool = True) -> None:
        self.stop_accepting()
        self._stop_event.set()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._epoch += 1
        worker = self._worker
        if wait and worker is not None and worker is not threading.current_thread():
            worker.join(0.1)
        with self._lock:
            self._cache.clear()
            self._pending.clear()
            self._fallback_candidates.clear()
            while not self._jobs.empty():
                try:
                    self._jobs.get_nowait()
                    self._jobs.task_done()
                except queue.Empty:
                    break
        with self._capacity:
            for reservation in tuple(self._reservations):
                reservation.release()
        if worker is None or not worker.is_alive():
            self._close_databases()

    def _close_databases(self) -> None:
        with self._db_lock:
            self._db.close()
        with self._candidate_db_lock:
            with self._candidate_read_lock:
                self._candidate_read_db.close()
                self._candidate_db.close()
            if self._candidate_temp is not None:
                self._candidate_temp.cleanup()
                self._candidate_temp = None

    def clear(self) -> None:
        # Finish a running chunk before clearing its database. Clearing is an
        # explicit user action, so a bounded shutdown wait is appropriate.
        with self._capacity:
            self._clearing = True
        self._stop_event.set()
        if self._worker is not None:
            self._worker.join(0.5)
            if self._worker.is_alive():
                # Clear was not accepted. Continue the existing writer and
                # leave every old record/source available for retry/export.
                self._stop_event.clear()
                with self._capacity:
                    self._clearing = False
                    self._capacity.notify_all()
                raise TimeoutError("历史归档尚未停止，请稍后重试清空")
        def clear_rows(db):
            for table in ("plain", "feed", "candidate", "round_record"):
                db.execute(f"DELETE FROM {table}")
        try:
            with self._lock:
                self._perform(clear_rows, write=True)
                self._perform(clear_rows, write=True, candidates=True)
                self._epoch += 1
                self._plain_seq = 0
        except BaseException:
            self._stop_event.clear()
            with self._capacity:
                self._clearing = False
                self._capacity.notify_all()
            raise
        self._cache.clear()
        self._pending.clear()
        self._fallback_candidates.clear()
        with self._capacity:
            for reservation in tuple(self._reservations):
                reservation.release()
        while not self._jobs.empty():
            try:
                self._jobs.get_nowait()
                self._jobs.task_done()
            except queue.Empty:
                break
        self._worker = None
        self._stop_event.clear()
        with self._capacity:
            self._clearing = False
            self._capacity.notify_all()
