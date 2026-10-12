"""历史记录面板 —— 第 6 个 Tab。

记录自动定点乱数每轮循环的候选、锁定、错过、结果、反查信息。
"""

from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QListWidget,
    QListWidgetItem,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.ui.workspace_theme import ui_styles
from auto_bdsp_rng.ui.history_archive import CandidateReservation, HistoryArchive, PlainSnapshot
from auto_bdsp_rng.ui.background_search import BackgroundSearch
from auto_bdsp_rng.ui.result_transfer import PreparedExport, ResultTransfer
from auto_bdsp_rng.rng_core.cancellation import SearchCancellation, SearchCancelled
from auto_bdsp_rng.gen8_static.models import State8
from auto_bdsp_rng.ui.run_log_panel import MAX_LOG_ENTRIES
from auto_bdsp_rng.ui.combo_box import ChevronComboBox as QComboBox
from auto_bdsp_rng.ui.workspace_controls import (
    EmptyIllustration,
    MenuToolButton,
    configure_disclosure_button,
)

# ── 配色 ──────────────────────────────────────────────────
CLR_SEP     = "#F0F2F5"   # 分隔线
CLR_TS      = "#687480"   # 时间戳
CLR_BODY    = "#202A33"   # 正文
CLR_LOCK    = "#087C58"   # 锁定 / 成功
CLR_SHINY   = "#B45309"   # 出闪
CLR_ERROR   = "#AC4B42"   # 错过 / 失败
CLR_LOCK_BG = "#EAF7F1"   # 锁定行背景
CLR_LOCK_FG = "#202A33"   # 锁定行普通数据
CLR_SYNC_BG = "#CFFAFE"   # 同步状态背景
CLR_SYNC_FG = "#155E75"   # 同步状态文字
CLR_SHINY_BG = "#FEF3C7"   # 异色单元格背景
CLR_SHINY_FG = "#92400E"   # 异色单元格文字

ROUND_PAGE_SIZE = 100
CANDIDATE_PAGE_SIZE = 100
FEED_PAGE_SIZE = 100


class _CandidateRowDelegate(QStyledItemDelegate):
    """Draw row separators without overriding semantic item backgrounds."""

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        painter.save()
        painter.setPen(QColor(CLR_SEP))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()


CANDIDATE_HEADERS = (
    "#", "状态", "Adv", "异色", "性格",
    "HP", "攻", "防", "特攻", "特防", "速",
    "特性", "性别", "EC", "PID", "身高", "体重",
)
CANDIDATE_WIDTHS = (42, 126, 92, 62, 66, 48, 48, 48, 54, 54, 48, 54, 54, 104, 104, 58, 58)
REVERSE_HEADERS = (
    "#", "状态", "Adv", "实际 delay", "异色", "性格", "个性",
    "HP", "攻", "防", "特攻", "特防", "速",
    "特性", "性别", "EC", "PID", "身高", "体重",
)
REVERSE_WIDTHS = (42, 94, 92, 88, 62, 66, 104, 48, 48, 48, 54, 54, 48, 54, 54, 104, 104, 58, 58)


class _CopyableTextEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setUndoRedoEnabled(False)
        self.setObjectName("LogView")
        self.document().setMaximumBlockCount(MAX_LOG_ENTRIES)
        self.full_text = None

    def toPlainText(self) -> str:
        return self.full_text() if self.full_text is not None else super().toPlainText()

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if menu is None or menu.isEmpty():
            from PySide6.QtWidgets import QMenu
            from PySide6.QtGui import QAction
            menu = QMenu(self)
            menu.addAction("复制", self.copy, QAction.Shortcut("Ctrl+C"))
            menu.addAction("全选", self.selectAll, QAction.Shortcut("Ctrl+A"))
        menu.exec(event.globalPos())

    def w(self, text: str = "", color: str = CLR_BODY) -> None:
        scroll_bar = self.verticalScrollBar()
        follow = scroll_bar.maximum() - scroll_bar.value() <= 4
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self.setTextCursor(cursor)
        if follow:
            self.ensureCursorVisible()


SEPARATOR_THICK = "═" * 54
SEPARATOR_THIN  = "─" * 54
SEPARATOR_END   = "─" * 4


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _ts(text: str = "") -> str:
    """带时间戳前缀的文本"""
    return f"[{_now()}] {text}"


def _state_iv_text(ivs: object) -> str:
    values = _state_iv_values(ivs)
    if values == ("?",) * 6:
        return "?"
    return " / ".join(f"{n}={v}" for n, v in zip(("HP", "攻击", "防御", "特攻", "特防", "速度"), values))


def _state_iv_values(ivs: object) -> tuple[str, str, str, str, str, str]:
    if ivs is not None:
        try:
            values = tuple(str(int(value)) for value in ivs)
        except (TypeError, ValueError):
            values = ()
        if len(values) == 6:
            return values  # type: ignore[return-value]
    return ("?", "?", "?", "?", "?", "?")


def _state_ec(state: object) -> str:
    v = getattr(state, "ec", None)
    return f"{int(v):08X}" if v is not None else "?"


def _state_pid(state: object) -> str:
    v = getattr(state, "pid", None)
    return f"{int(v):08X}" if v is not None else "?"


def _state_characteristic(state: object, recognized: str | None = None) -> str:
    if recognized:
        return recognized
    try:
        ivs = [int(value) for value in getattr(state, "ivs", ())]
        ec = int(getattr(state, "ec", 0))
    except (TypeError, ValueError):
        return "-"
    if len(ivs) != 6:
        return "-"
    from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import compute_characteristic

    return compute_characteristic(ec, ivs) or "-"


def _get_int(state: object, name: str) -> int:
    v = getattr(state, name, None)
    return int(v) if v is not None else 0


def _pid_ec_key(state: object) -> str:
    return f"{_state_pid(state)}:{_state_ec(state)}"


NATURE_ZH_MAP: dict[int, str] = {
    0: "勤奋", 1: "怕寂寞", 2: "勇敢", 3: "固执", 4: "顽皮", 5: "大胆",
    6: "坦率", 7: "悠闲", 8: "淘气", 9: "乐天", 10: "胆小",
    11: "急躁", 12: "认真", 13: "爽朗", 14: "天真", 15: "内敛",
    16: "慢吞吞", 17: "冷静", 18: "害羞", 19: "马虎", 20: "温和",
    21: "温顺", 22: "自大", 23: "慎重", 24: "浮躁",
}
GENDER_MAP = {0: "雄", 1: "雌", 2: "无"}
SHINY_MAP  = {0: "否", 1: "星闪", 2: "方闪"}


def _nature_text(state: object) -> str:
    n = _get_int(state, "nature")
    return NATURE_ZH_MAP.get(n, str(n))


def _gender_text(state: object) -> str:
    g = _get_int(state, "gender")
    return GENDER_MAP.get(g, str(g))


def _shiny_text(state: object) -> str:
    s = _get_int(state, "shiny")
    return SHINY_MAP.get(s, str(s))


@dataclass(frozen=True, slots=True)
class _CandidateSnapshot:
    advances: int
    ec: int | None
    pid: int | None
    ivs: tuple[int, ...]
    ability: int
    gender: int
    nature: int
    shiny: int
    height: int
    weight: int


def _snapshot_candidate(state: object) -> _CandidateSnapshot:
    try:
        ivs = tuple(int(value) for value in getattr(state, "ivs", ()))
    except (TypeError, ValueError):
        ivs = ()

    def optional_int(name: str) -> int | None:
        value = getattr(state, name, None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return _CandidateSnapshot(
        advances=_get_int(state, "advances"),
        ec=optional_int("ec"),
        pid=optional_int("pid"),
        ivs=ivs,
        ability=_get_int(state, "ability"),
        gender=_get_int(state, "gender"),
        nature=_get_int(state, "nature"),
        shiny=_get_int(state, "shiny"),
        height=_get_int(state, "height"),
        weight=_get_int(state, "weight"),
    )


class _CandidateRows:
    """Frozen source with direct page reads and pre-indexed duplicate tags."""

    def __init__(self, candidates, locked_index, flags, delays, *, reverse, characteristic, cancelled=None):
        # Mutable integrations are copied at admission. Production State8
        # values are frozen and need only a bounded conversion when viewed.
        self.flags, self.delays = tuple(flags or ()), tuple(delays or ())
        self.locked_index, self.reverse, self.characteristic = locked_index, reverse, characteristic
        seen = {}
        states, duplicates = [], []
        for index, source in enumerate(candidates):
            if cancelled is not None and index % 1024 == 0:
                if cancelled():
                    raise SearchCancelled("History candidate preparation stopped")
                # Preparation runs on the producer; allow Qt to run while
                # leaving GC enabled, including on a cold 100k batch.
                time.sleep(0.001)
            state = (source if type(source) is State8 and isinstance(source.ivs, tuple)
                     else _snapshot_candidate(source))
            states.append(state)
            pid, ec = state.pid, state.ec
            # A valid PID/EC pair fits in one integer. This avoids retaining
            # 100k GC-tracked key tuples during admission. Unknown integration
            # values retain the original tuple identity without truncation.
            key = ((pid << 32) | ec if pid is not None and ec is not None
                   and 0 <= pid <= 0xFFFFFFFF and 0 <= ec <= 0xFFFFFFFF else (pid, ec))
            duplicates.append(seen.get(key, 0))
            seen[key] = index + 1
        if cancelled is not None and cancelled():
            raise SearchCancelled("History candidate preparation stopped")
        self.states = tuple(states)
        self.duplicates = tuple(duplicates)

    def __call__(self):
        return self.read_page(0, len(self.states))

    def read_page(self, start: int, stop: int):
        for index in range(start, min(stop, len(self.states))):
            source = self.states[index]
            state = source if isinstance(source, _CandidateSnapshot) else _snapshot_candidate(source)
            duplicate = self.duplicates[index]
            if self.reverse:
                status = f"匹配 · 同候选{duplicate}" if duplicate else "匹配"
            else:
                tags = []
                if index == self.locked_index:
                    tags.append("锁定")
                if index < len(self.flags) and self.flags[index] == "sync":
                    tags.append("同步")
                if duplicate:
                    tags.append(f"同候选{duplicate}")
                status = " · ".join(tags) if tags else "普通"
            delay = self.delays[index] if index < len(self.delays) else state.advances
            characteristic = _state_characteristic(state, self.characteristic) if self.reverse else "-"
            yield state, status, delay, characteristic


def _prepare_history_candidates(event, values, *, cancelled):
    """Freeze a bounded producer payload without touching any Qt object."""
    if cancelled():
        raise SearchCancelled("History candidate preparation stopped")
    candidates = tuple(values[0])
    frozen = list(values)
    frozen[0] = candidates
    if event == "reverse_lookup_results":
        characteristic = str(values[1]) if len(values) > 1 and values[1] is not None else None
        delays = tuple(values[2]) if len(values) > 2 and values[2] is not None else None
        rows = _CandidateRows(candidates, -1, None, delays, reverse=True,
                              characteristic=characteristic, cancelled=cancelled)
        if len(values) > 1:
            frozen[1] = characteristic
        if len(values) > 2:
            frozen[2] = delays
    else:
        locked_index = int(values[1])
        flags = tuple(values[2]) if len(values) > 2 and values[2] is not None else None
        rows = _CandidateRows(candidates, locked_index, flags, None, reverse=False,
                              characteristic=None, cancelled=cancelled)
        frozen[1] = locked_index
        if len(values) > 2:
            frozen[2] = flags
    return tuple(frozen), rows


@dataclass(frozen=True)
class _FeedEntry:
    kind: str
    text: str = ""
    color: str = CLR_BODY
    bold: bool = False
    strong: bool = False
    rows: Sequence[tuple[str, ...]] = ()
    states: Sequence[_CandidateSnapshot] = ()
    statuses: Sequence[str] = ()
    reverse: bool = False
    archive_batch: int | None = None


class _CandidateColumn(Sequence):
    def __init__(self, archive: HistoryArchive, batch: int, count: int, column: str, reverse: bool) -> None:
        self.archive, self.batch, self.count = archive, batch, count
        self.column, self.reverse = column, reverse

    def __len__(self) -> int:
        return self.count

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(self.count)))
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        state, status, delay, characteristic = self.archive.candidate(self.batch, index)
        if self.column == "state":
            return state
        if self.column == "status":
            return status
        values = (HistoryPanel._reverse_row(index, state, status, delay, characteristic)
                  if self.reverse else HistoryPanel._candidate_row(index, state, status))
        return tuple(values)


def _candidate_entry(archive: HistoryArchive, batch: int, count: int, reverse: bool) -> _FeedEntry:
    return _FeedEntry(
        kind="table", reverse=reverse, archive_batch=batch,
        rows=_CandidateColumn(archive, batch, count, "row", reverse),
        states=_CandidateColumn(archive, batch, count, "state", reverse),
        statuses=_CandidateColumn(archive, batch, count, "status", reverse),
    )


def _candidate_plain_text(index: int, state, status: str, delay: int, characteristic: str, reverse: bool) -> str:
    if reverse:
        chara = f" 个性={characteristic}" if characteristic != "-" else ""
        return (
            f"反查候选{index + 1}: adv={state.advances} delay={delay} EC={_state_ec(state)} PID={_state_pid(state)} "
            f"{_state_iv_text(state.ivs)} 性格={_nature_text(state)}{chara} 特性={state.ability} "
            f"性别={_gender_text(state)} 异色={_shiny_text(state)} 身高={state.height} 体重={state.weight}"
        )
    return (
        f"  候选{index + 1} ({status}) adv={state.advances} EC={_state_ec(state)} PID={_state_pid(state)} "
        f"{_state_iv_text(state.ivs)} 性格={_nature_text(state)} 异色={_shiny_text(state)} "
        f"特性={state.ability} 性别={_gender_text(state)} 身高={state.height} 体重={state.weight}"
    )


def _plain_lines(
    archive: HistoryArchive, uid: int | None, *, snapshot: PlainSnapshot | None = None,
    cancellation: SearchCancellation | None = None, after_seq: int = 0,
) -> Iterator[str]:
    """Format frozen Python payloads; workers never read a widget or record."""
    for value in archive.values("plain", uid, snapshot=snapshot, after_seq=after_seq):
        if cancellation is not None:
            cancellation.check()
        if value[0] == "text":
            yield value[1]
        else:
            _, batch, reverse = value
            for index, (state, status, delay, characteristic) in enumerate(archive.candidate_rows(batch)):
                if cancellation is not None and index % 128 == 0:
                    cancellation.check()
                    # Formatting is Python work. Yield the GIL regularly so a
                    # large search/copy does not starve the GUI heartbeat.
                    time.sleep(0)
                yield _candidate_plain_text(index, state, status, delay, characteristic, reverse)


class _PlainLines:
    def __init__(self, archive: HistoryArchive, uid: int | None) -> None:
        self.archive, self.uid, self.count = archive, uid, 0

    def __len__(self) -> int:
        return self.count

    def append(self, text: str) -> None:
        self.archive.append("plain", self.uid, ("text", text))
        self.count += 1

    def append_candidates(self, batch: int, count: int, reverse: bool) -> None:
        self.archive.append("plain", self.uid, ("candidate", batch, reverse))
        self.count += count

    def __iter__(self) -> Iterator[str]:
        return _plain_lines(self.archive, self.uid)


class _FeedEntries(Sequence):
    def __init__(self, archive: HistoryArchive, uid: int) -> None:
        self.archive, self.uid, self.count = archive, uid, 0

    def __len__(self) -> int:
        return self.count

    def append(self, entry: _FeedEntry) -> None:
        value = (entry.kind, entry.text, entry.color, entry.bold, entry.strong,
                 entry.archive_batch, len(entry.rows), entry.reverse)
        self.archive.append("feed", self.uid, value)
        self.count += 1

    def _decode(self, value) -> _FeedEntry:
        kind, text, color, bold, strong, batch, count, reverse = value
        if batch is not None:
            return _candidate_entry(self.archive, batch, count, reverse)
        return _FeedEntry(kind=kind, text=text, color=color, bold=bold, strong=strong, reverse=reverse)

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(self.count)
            if step != 1:
                return tuple(self[i] for i in range(start, stop, step))
            return tuple(self._decode(value) for value in self.archive.values("feed", self.uid, offset=start, limit=stop-start))
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        return self._decode(next(self.archive.values("feed", self.uid, offset=index, limit=1)))


@dataclass
class _RoundRecord:
    uid: int
    run_id: object
    round_id: object
    run_index: int
    cycle_index: int | None
    target_label: str
    started_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    detail_title: str = "运行消息"
    outcome: str | None = None
    seed_text: str | None = None
    locked_advances: int | None = None
    candidate_count: int = 0
    candidate_snapshot: _FeedEntry | None = None
    warnings: list[str] = field(default_factory=list)
    entries: _FeedEntries | list[_FeedEntry] = field(default_factory=list)
    plain_lines: _PlainLines | list[str] = field(default_factory=list)
    implicit: bool = False


@dataclass(frozen=True)
class _RoundSearchSummary:
    uid: int
    revision: int
    status: str
    has_warnings: bool
    searchable: str

    def matches_result(self, result: str) -> bool:
        return (
            result == "all"
            or result == "warning" and self.has_warnings
            or result == "active" and self.status in {"进行中", "记录中"}
            or result == "shiny" and self.status == "疑似出闪"
            or result == "not-shiny" and self.status == "未出闪"
            or result == "no-candidate" and self.status == "无候选"
        )


class _RoundRecords:
    """Keep identity indexes in memory and only 64 loaded round summaries."""

    def __init__(self, archive: HistoryArchive) -> None:
        self.archive = archive
        self.keys: dict[tuple[object, object], int] = {}
        self.identities: dict[int, tuple[object, object]] = {}
        self.cache: OrderedDict[int, _RoundRecord] = OrderedDict()
        self.search_summaries: dict[int, _RoundSearchSummary] = {}

    def __len__(self) -> int:
        return len(self.keys)

    def __getitem__(self, key) -> _RoundRecord:
        return self.by_uid(self.keys[key])

    def get(self, key, default=None):
        uid = self.keys.get(key)
        return default if uid is None else self.by_uid(uid)

    def __setitem__(self, key, record: _RoundRecord) -> None:
        self.keys[key] = record.uid
        self.identities[record.uid] = (record.run_id, record.round_id)
        self.store(record)

    def store(self, record: _RoundRecord) -> None:
        snapshot = record.candidate_snapshot
        fields = {name: getattr(record, name) for name in (
            "uid", "run_index", "cycle_index", "target_label", "started_at", "updated_at",
            "detail_title", "outcome", "seed_text", "locked_advances", "candidate_count", "warnings", "implicit",
        )}
        fields["plain_count"] = len(record.plain_lines)
        fields["entry_count"] = len(record.entries)
        fields["snapshot"] = None if snapshot is None else (snapshot.archive_batch, len(snapshot.rows), snapshot.reverse)
        self.archive.store_record(record.uid, fields)
        self.remember_summary(record)
        self._remember(record)

    def remember_summary(self, record: _RoundRecord) -> _RoundSearchSummary:
        previous = self.search_summaries.get(record.uid)
        status = _record_status(record)
        summary = _RoundSearchSummary(
            record.uid, 1 if previous is None else previous.revision + 1,
            status, bool(record.warnings),
            " ".join((str(record.run_id), str(record.round_id), f"运行 {record.run_index}",
                      str(record.cycle_index or ""), record.target_label, record.seed_text or "",
                      str(record.locked_advances or ""), status, record.detail_title, *record.warnings)).casefold(),
        )
        self.search_summaries[record.uid] = summary
        return summary

    def _remember(self, record: _RoundRecord) -> None:
        self.cache[record.uid] = record
        self.cache.move_to_end(record.uid)
        while len(self.cache) > 64:
            self.cache.popitem(last=False)

    def by_uid(self, uid: int) -> _RoundRecord:
        if uid in self.cache:
            self.cache.move_to_end(uid)
            return self.cache[uid]
        fields = self.archive.record(uid)
        entries = _FeedEntries(self.archive, uid)
        entries.count = fields.pop("entry_count")
        lines = _PlainLines(self.archive, uid)
        lines.count = fields.pop("plain_count")
        snapshot = fields.pop("snapshot")
        run_id, round_id = self.identities[uid]
        record = _RoundRecord(**fields, run_id=run_id, round_id=round_id, entries=entries, plain_lines=lines)
        if snapshot is not None:
            record.candidate_snapshot = _candidate_entry(self.archive, *snapshot)
        self._remember(record)
        return record

    def clear(self) -> None:
        self.keys.clear()
        self.identities.clear()
        self.cache.clear()
        self.search_summaries.clear()


class _RecordsByUid(Mapping):
    def __init__(self, records: _RoundRecords) -> None:
        self.records = records

    def __getitem__(self, uid: int) -> _RoundRecord:
        return self.records.by_uid(uid)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self):
        return iter(self.records.identities)

    def clear(self) -> None:
        self.records.clear()


class _RoundListRow(QWidget):
    def __init__(self, record: _RoundRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(self)
        self.title_label.setStyleSheet(ui_styles("font-weight: 500; color: #202A33;"))
        self.status_label = QLabel(self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.title_label, 1)
        top.addWidget(self.status_label)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        self.target_label = QLabel(self)
        self.target_label.setStyleSheet("color: #687480; font-size: 12px;")
        self.note_label = QLabel(self)
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.note_label.setStyleSheet("color: #687480; font-size: 12px;")
        bottom.addWidget(self.target_label, 1)
        bottom.addWidget(self.note_label)

        layout.addLayout(top)
        layout.addLayout(bottom)
        self.update_record(record)

    def update_record(self, record: _RoundRecord) -> None:
        if record.cycle_index is None:
            title = f"运行 {record.run_index} · 会话"
        else:
            title = f"运行 {record.run_index} · 第 {record.cycle_index} 轮"
        status = _record_status(record)
        status_color = {
            "有警告": "#A35F08",
            "疑似出闪": CLR_SHINY,
            "未出闪": CLR_LOCK,
            "无候选": "#687480",
        }.get(status, "#087C58")
        self.title_label.setText(title)
        self.status_label.setText(status)
        self.status_label.setStyleSheet(ui_styles(f"color: {status_color}; font-size: 12px; font-weight: 500;"))
        self.target_label.setText(record.target_label)
        note = f"{record.candidate_count} 个候选" if record.candidate_count else record.updated_at.strftime("%H:%M:%S")
        self.note_label.setText(note)


def _record_status(record: _RoundRecord) -> str:
    if record.outcome:
        return record.outcome
    if record.warnings:
        return "有警告"
    return "记录中" if record.implicit else "进行中"


class HistoryPanel(QWidget):
    related_logs_requested = Signal(object, object)
    archiveWarning = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cycle_index = 0
        self._cycle_count = 0
        self._candidate_count = 0
        self._pid_ec_seen: dict[str, int] = {}
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self._follow_pending = False
        self._follow_origin = 0
        self._rendering_record = False
        self._round_uid_counter = 0
        self._run_index_counter = 0
        self._run_indexes: dict[object, int] = {}
        self._round_order: list[tuple[object, object]] = []
        self._active_uid: int | None = None
        self._selected_uid: int | None = None
        self._current_run_context: object = None
        self._current_target_label = "自动定点"
        self._select_next_record = False
        self._compact = None
        self._round_page = 0
        self._visible_uids: list[int] = []
        self._visible_uid_set: set[int] = set()
        self._round_items: dict[int, QListWidgetItem] = {}
        self._feed_start = 0
        self._feed_at_end = True
        self._keyword = ""
        self._query_generation = 0
        self._keyword_matches: set[int] = set()
        self._searched_through_seq: dict[int, int] = {}
        self._search_dirty: set[int] = set()
        self._search_inflight = False
        self._search_preferred_uid: int | None = None
        self._copy_busy = False
        self._export_busy = False
        self._closing_requested = False
        self._archive = HistoryArchive(self.archiveWarning.emit, prepare_candidates=_prepare_history_candidates)
        self._round_records = _RoundRecords(self._archive)
        self._record_by_uid = _RecordsByUid(self._round_records)
        archive = self._archive
        self._all_plain_lines = _PlainLines(archive, None)
        self._build_ui()
        self._history_search = BackgroundSearch(self)
        self._copy_work = BackgroundSearch(self)
        self._export_transfer = ResultTransfer(
            self, changed=self._export_changed, copied=lambda *_args: None,
            exported=self._export_finished, failed=self._export_failed,
        )
        # Keep the existing worker diagnostics available; export cancellation
        # and close must go through the transfer's accepted-commit protocol.
        self._export_work = self._export_transfer._search
        history_search, copy_work, export_transfer = self._history_search, self._copy_work, self._export_transfer
        def close_history() -> None:
            history_search.close()
            copy_work.close()
            export_transfer.close()
            archive.close(wait=False)
        self.destroyed.connect(close_history)
        self._filter_start_timer = QTimer(self)
        self._filter_start_timer.setSingleShot(True)
        self._filter_start_timer.setInterval(30)
        self._filter_start_timer.timeout.connect(self._start_keyword_search)
        self.archiveWarning.connect(self._show_archive_notice)
        if archive.in_memory_fallback:
            self._show_archive_notice("历史临时存储不可用，完整记录将保存在内存；请在退出前导出。")
        self._scroll_top_timer = QTimer(self)
        self._scroll_top_timer.setSingleShot(True)
        self._scroll_top_timer.timeout.connect(self._scroll_selected_record_to_top)
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.timeout.connect(self._finish_auto_follow)
        self._archive_start_timer = QTimer(self)
        self._archive_start_timer.setSingleShot(True)
        self._archive_start_timer.timeout.connect(self._archive.start_worker)

    def _build_ui(self) -> None:
        self.setObjectName("HistoryPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet("QWidget#HistoryPanel { background: #F2F4F7; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.summary_label = QLabel("0 轮 · 0 条候选")
        self.summary_label.setObjectName("HistorySummary")
        self.summary_label.setStyleSheet(ui_styles("color: #687480; font-weight: 500;"))
        self.copy_button = QPushButton("复制全部")
        self.copy_button.setFixedHeight(34)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_all)
        self.clear_button = QPushButton("清空")
        self.clear_button.setFixedHeight(34)
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self.clear)
        self.export_button = QPushButton("导出")
        self.export_button.setFixedHeight(34)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_to_file)
        toolbar.addWidget(self.summary_label)
        toolbar.addStretch()
        self.round_picker_button = QPushButton("选择轮次")
        self.round_picker_button.setCheckable(True)
        self.round_picker_button.toggled.connect(self._show_round_picker)
        toolbar.addWidget(self.round_picker_button)
        more = MenuToolButton()
        more.setText("更多")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        for text, button in (("复制全部", self.copy_button), ("清空", self.clear_button), ("导出", self.export_button)):
            menu.addAction(text, button.click)
            button.setParent(self)
            button.hide()
        more.setMenu(menu)
        toolbar.addWidget(more)
        layout.addLayout(toolbar)
        self.archive_notice = QLabel(self)
        self.archive_notice.setWordWrap(True)
        self.archive_notice.setStyleSheet("color: #74400A; background: #FFF7E8; padding: 6px;")
        self.archive_notice.hide()
        layout.addWidget(self.archive_notice)

        filters = QGridLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        result_label = QLabel("结果")
        result_label.setStyleSheet("color: #687480; font-size: 12px;")
        self.result_filter = QComboBox(self)
        self.result_filter.setObjectName("HistoryResultFilter")
        self.result_filter.setMinimumWidth(126)
        for text, value in (
            ("全部结果", "all"),
            ("进行中", "active"),
            ("疑似出闪", "shiny"),
            ("未出闪", "not-shiny"),
            ("无候选", "no-candidate"),
            ("有警告", "warning"),
        ):
            self.result_filter.addItem(text, value)
        self.result_filter.currentIndexChanged.connect(self._refresh_round_list)
        search_label = QLabel("搜索轮次")
        search_label.setStyleSheet("color: #687480; font-size: 12px;")
        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("HistoryRoundSearch")
        self.search_edit.setPlaceholderText("目标、Seed、Adv 或结果")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refresh_round_list)
        filters.addWidget(result_label, 0, 0)
        filters.addWidget(self.result_filter, 0, 1)
        filters.addWidget(search_label, 1, 0)
        filters.addWidget(self.search_edit, 1, 1)
        filters.setColumnStretch(1, 1)
        layout.addLayout(filters)

        self.empty_state = QFrame(self)
        self.empty_state.setObjectName("HistoryEmptyState")
        self.empty_state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.empty_state.setStyleSheet(
            "QFrame#HistoryEmptyState { background: #FFFFFF; border: 1px solid #E3E8ED; "
            "border-radius: 8px; }"
            "QFrame#HistoryEmptyState QLabel { background: transparent; border: 0; }"
        )
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(12)
        empty_layout.addStretch(1)
        empty_layout.addWidget(EmptyIllustration("journal"), 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_state_title = QLabel("暂无轮次记录", self.empty_state)
        self.empty_state_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_title.setStyleSheet(ui_styles("color: #687480; font-size: 16px; font-weight: 500;"))
        self.empty_state_detail = QLabel("当前会话尚未产生运行结果", self.empty_state)
        self.empty_state_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_detail.setStyleSheet("color: #87939F; font-size: 12px;")
        empty_layout.addWidget(self.empty_state_title)
        empty_layout.addWidget(self.empty_state_detail)
        self.empty_action_button = QPushButton("开始前检查")
        self.empty_action_button.setFixedHeight(34)
        self.empty_action_button.clicked.connect(self._empty_action)
        self.empty_navigation = None
        empty_layout.addWidget(self.empty_action_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty_state, 1)

        self.round_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.round_splitter.setObjectName("HistoryRoundSplitter")
        self.round_splitter.setChildrenCollapsible(False)
        self.round_splitter.setHandleWidth(1)

        list_panel = QWidget(self.round_splitter)
        list_panel.setObjectName("HistoryRoundListPanel")
        list_panel.setMinimumWidth(220)
        list_panel.setMaximumWidth(330)
        self.list_panel = list_panel
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        self.round_list_heading = QLabel("本次会话 · 0 轮", list_panel)
        self.round_list_heading.setObjectName("HistoryRoundListHeading")
        self.round_list_heading.setFixedHeight(36)
        self.round_list_heading.setStyleSheet(
            ui_styles("color: #687480; background: #F7F8FA; border: 1px solid #E0E5EB; "
            "border-bottom: 0; padding: 0 11px; font-size: 12px; font-weight: 500;")
        )
        self.round_list = QListWidget(list_panel)
        self.round_list.setObjectName("HistoryRoundList")
        self.round_list.setAccessibleName("轮次记录列表")
        self.round_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.round_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.round_list.setSpacing(0)
        self.round_list.setStyleSheet(
            "QListWidget#HistoryRoundList { background: #F7F8FA; color: #202A33; "
            "border: 1px solid #E0E5EB; outline: 0; }"
            "QListWidget#HistoryRoundList::item { min-height: 62px; border-bottom: 1px solid #F0F2F5; }"
            "QListWidget#HistoryRoundList::item:hover { background: #F0F3F6; }"
            "QListWidget#HistoryRoundList::item:selected { background: #EAF7F1; "
            "border-left: 3px solid #087C58; }"
        )
        self.round_list.currentItemChanged.connect(self._on_round_selected)
        list_layout.addWidget(self.round_list_heading)
        list_layout.addWidget(self.round_list, 1)
        round_pager = QHBoxLayout()
        self.round_previous_button = QPushButton("上一页", list_panel)
        self.round_next_button = QPushButton("下一页", list_panel)
        self.round_page_label = QLabel("1 / 1", list_panel)
        self.round_page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.round_previous_button.clicked.connect(lambda: self._change_round_page(-1))
        self.round_next_button.clicked.connect(lambda: self._change_round_page(1))
        round_pager.addWidget(self.round_previous_button)
        round_pager.addWidget(self.round_page_label, 1)
        round_pager.addWidget(self.round_next_button)
        list_layout.addLayout(round_pager)

        detail_panel = QWidget()
        detail_panel.setObjectName("HistoryRoundDetail")
        detail_panel.setMinimumWidth(0)
        detail_panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(14, 12, 12, 12)
        detail_layout.setSpacing(10)

        detail_header = QVBoxLayout()
        detail_header.setContentsMargins(0, 0, 0, 0)
        detail_heading = QVBoxLayout()
        detail_heading.setContentsMargins(0, 0, 0, 0)
        detail_heading.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self.detail_title_label = QLabel("请选择轮次", detail_panel)
        self.detail_title_label.setObjectName("HistoryDetailTitle")
        self.detail_title_label.setStyleSheet(ui_styles("color: #202A33; font-size: 16px; font-weight: 500;"))
        self.detail_status_label = QLabel("", detail_panel)
        self.detail_status_label.setObjectName("HistoryDetailStatus")
        title_row.addWidget(self.detail_title_label, 1)
        title_row.addWidget(self.detail_status_label)
        self.detail_time_label = QLabel("", detail_panel)
        self.detail_time_label.setObjectName("HistoryDetailTime")
        self.detail_time_label.setStyleSheet("color: #687480; font-size: 12px;")
        detail_heading.addLayout(title_row)
        detail_heading.addWidget(self.detail_time_label)
        detail_header.addLayout(detail_heading)

        self.copy_round_button = QPushButton("复制本轮", detail_panel)
        self.copy_round_button.setObjectName("HistoryCopyRound")
        self.copy_round_button.setFixedHeight(32)
        self.copy_round_button.setEnabled(False)
        self.copy_round_button.clicked.connect(self.copy_selected_round)
        self.related_logs_button = QPushButton("查看本轮日志", detail_panel)
        self.related_logs_button.setObjectName("HistoryRelatedLogs")
        self.related_logs_button.setFixedHeight(32)
        self.related_logs_button.setEnabled(False)
        self.related_logs_button.clicked.connect(self._request_related_logs)
        self.related_logs_button.setStyleSheet(
            "QPushButton { color: #087C58; background: #EAF7F1; border: 1px solid #D7EDE3; "
            "border-radius: 7px; padding: 0 10px; }"
            "QPushButton:hover { background: #DBF1E6; }"
            "QPushButton:disabled { color: #A0A9B2; background: #E0E5EB; border-color: #E0E5EB; }"
        )
        detail_actions = QHBoxLayout()
        detail_actions.addWidget(self.copy_round_button)
        detail_actions.addWidget(self.related_logs_button)
        detail_actions.addStretch(1)
        detail_header.addLayout(detail_actions)
        detail_layout.addLayout(detail_header)

        meta_frame = QFrame(detail_panel)
        meta_frame.setObjectName("HistoryRoundMeta")
        self.meta_frame = meta_frame
        meta_frame.setStyleSheet(
            "QFrame#HistoryRoundMeta { border-top: 1px solid #F0F2F5; "
            "border-bottom: 1px solid #F0F2F5; }"
        )
        meta_layout = QGridLayout(meta_frame)
        meta_layout.setContentsMargins(0, 8, 0, 8)
        meta_layout.setHorizontalSpacing(16)
        meta_layout.setVerticalSpacing(2)
        self.target_value_label = self._add_meta_column(meta_layout, 0, "目标")
        self.seed_value_label = self._add_meta_column(meta_layout, 1, "Seed")
        self.locked_adv_value_label = self._add_meta_column(meta_layout, 2, "锁定 Adv")
        self.candidate_value_label = self._add_meta_column(meta_layout, 3, "候选结果")
        for column in range(4):
            meta_layout.setColumnStretch(column, 1)
        meta_items = [meta_layout.itemAtPosition(row, column).widget() for column in range(4) for row in range(2)]
        for widget in meta_items:
            meta_layout.removeWidget(widget)
        for index, (row, col, span) in enumerate(((0, 0, 1), (4, 0, 2), (0, 1, 1), (2, 0, 2))):
            meta_layout.addWidget(meta_items[index * 2], row, col, 1, span)
            meta_layout.addWidget(meta_items[index * 2 + 1], row + 1, col, 1, span)
        for col in range(4):
            meta_layout.setColumnStretch(col, 1 if col < 2 else 0)
        detail_layout.addWidget(meta_frame)

        self.warning_frame = QFrame(detail_panel)
        self.warning_frame.setObjectName("HistoryWarning")
        self.warning_frame.setStyleSheet(
            "QFrame#HistoryWarning { color: #74400A; background: #FFF7E8; "
            "border: 0; border-left: 3px solid #D97706; border-radius: 3px; }"
        )
        warning_layout = QHBoxLayout(self.warning_frame)
        warning_layout.setContentsMargins(10, 8, 10, 8)
        self.warning_label = QLabel(self.warning_frame)
        self.warning_label.setObjectName("HistoryWarningText")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #74400A;")
        warning_layout.addWidget(self.warning_label)
        self.warning_frame.hide()
        detail_layout.addWidget(self.warning_frame)

        feed_heading = QHBoxLayout()
        feed_title = QLabel("处理详情", detail_panel)
        feed_title.setStyleSheet(ui_styles("color: #202A33; font-size: 13px; font-weight: 500;"))
        feed_note = QLabel("候选数据为识别当时的快照", detail_panel)
        feed_note.setStyleSheet("color: #687480; font-size: 12px;")
        feed_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        feed_heading.addWidget(feed_title)
        feed_heading.addStretch()
        feed_heading.addWidget(feed_note)
        detail_layout.addLayout(feed_heading)

        self.history_scroll = QScrollArea(detail_panel)
        self.history_scroll.setObjectName("HistoryScroll")
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_scroll.setStyleSheet(
            "QScrollArea#HistoryScroll { background: #FFFFFF; border: 1px solid #E0E5EB; border-radius: 6px; }"
            "QWidget#HistoryFeed { background: #FFFFFF; }"
        )
        self._feed_body = QWidget(self.history_scroll)
        self._feed_body.setObjectName("HistoryFeed")
        self._feed_body.setMinimumWidth(0)
        self._feed_body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._feed_layout = QVBoxLayout(self._feed_body)
        self._feed_layout.setContentsMargins(12, 12, 12, 12)
        self._feed_layout.setSpacing(7)
        self._feed_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._feed_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.history_scroll.setWidget(self._feed_body)
        scroll_bar = self.history_scroll.verticalScrollBar()
        scroll_bar.sliderPressed.connect(self._cancel_auto_follow)
        scroll_bar.actionTriggered.connect(self._cancel_auto_follow)
        detail_layout.addWidget(self.history_scroll, 1)

        self.round_splitter.addWidget(list_panel)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("HistoryDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_scroll.setMinimumSize(0, 0)
        self.detail_scroll.setWidget(detail_panel)
        self.detail_panel = self.detail_scroll
        self.round_splitter.addWidget(self.detail_scroll)
        self.round_splitter.setStretchFactor(0, 0)
        self.round_splitter.setStretchFactor(1, 1)
        self.round_splitter.setSizes([250, 760])
        layout.addWidget(self.round_splitter, 1)
        self.round_splitter.hide()

        # Selected-round summary and candidate data own the narrow surface;
        # full metadata and the event feed remain available on demand.
        self.meta_toggle = QToolButton()
        self.meta_toggle.setObjectName("HistoryDisclosure")
        self.meta_toggle.setText("Seed / 轮次详情")
        self.meta_toggle.setCheckable(True)
        configure_disclosure_button(self.meta_toggle)
        self.meta_toggle.toggled.connect(self.meta_frame.setVisible)
        self.meta_frame.hide()
        detail_layout.insertWidget(1, self.meta_toggle)
        self.round_summary = QLabel("目标 — · 候选 — · 锁定帧 —")
        self.round_summary.setWordWrap(True)
        detail_layout.insertWidget(2, self.round_summary)
        self.round_candidate_table = self._create_candidate_table(_FeedEntry(kind="table"))
        self.round_candidate_table.setAccessibleName("本轮候选快照")
        self.round_candidate_table.setMinimumHeight(100)
        self.round_candidate_table.setMaximumHeight(16777215)
        self.round_candidate_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        detail_layout.removeWidget(self.history_scroll)
        detail_layout.addWidget(self.round_candidate_table._container, 1)
        self.feed_toggle = QToolButton()
        self.feed_toggle.setObjectName("HistoryDisclosure")
        self.feed_toggle.setText("处理详情")
        self.feed_toggle.setCheckable(True)
        configure_disclosure_button(self.feed_toggle)
        self.feed_toggle.toggled.connect(self.history_scroll.setVisible)
        detail_layout.addWidget(self.feed_toggle)
        self.history_scroll.setMaximumHeight(180)
        self.history_scroll.hide()
        feed_pager = QHBoxLayout()
        self.feed_previous_button = QPushButton("较早详情", detail_panel)
        self.feed_next_button = QPushButton("较新详情", detail_panel)
        self.feed_page_label = QLabel(detail_panel)
        self.feed_previous_button.clicked.connect(lambda: self._change_feed_page(-1))
        self.feed_next_button.clicked.connect(lambda: self._change_feed_page(1))
        feed_pager.addWidget(self.feed_previous_button)
        feed_pager.addWidget(self.feed_page_label, 1)
        feed_pager.addWidget(self.feed_next_button)
        self.feed_pager = QWidget(detail_panel)
        self.feed_pager.setLayout(feed_pager)
        self.feed_pager.hide()
        self.feed_toggle.toggled.connect(lambda show: self.feed_pager.setVisible(show and self._selected_record() is not None and len(self._selected_record().entries) > FEED_PAGE_SIZE))
        detail_layout.addWidget(self.feed_pager)
        detail_layout.addWidget(self.history_scroll)
        for i in range(feed_heading.count()):
            if feed_heading.itemAt(i).widget():
                feed_heading.itemAt(i).widget().hide()
        self.detail_title_label.setWordWrap(True)
        self.detail_title_label.setMinimumHeight(22)
        self.detail_title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.detail_time_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        # Plain-text mirror kept for exports and callers that use text_view.toPlainText().
        self.text_view = _CopyableTextEdit(self)
        self.text_view.setFont(QFont("Consolas", 10))
        self.text_view.full_text = self._complete_plain_text
        self.text_view.hide()
        self.view = self.text_view

    def _show_archive_notice(self, message: str) -> None:
        self.archive_notice.setText(message)
        self.archive_notice.show()

    def _complete_plain_text(self) -> str:
        return "".join(line + "\n" for line in self._all_plain_lines)

    def _show_round_picker(self, show):
        if self._compact:
            self.list_panel.setMaximumWidth(16777215)
            self.list_panel.setVisible(show)
            self.detail_panel.setVisible(not show)
            self.round_picker_button.setText("返回本轮" if show else "选择轮次")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "list_panel"):
            return
        compact = self.width() < 760
        if compact != self._compact:
            self._compact = compact
            self.round_picker_button.setVisible(compact)
            self.list_panel.setMaximumWidth(16777215 if compact else 330)
            self.list_panel.setVisible(not compact or self.round_picker_button.isChecked())
            self.detail_panel.setVisible(not compact or not self.round_picker_button.isChecked())


    @staticmethod
    def _add_meta_column(layout: QGridLayout, column: int, title: str) -> QLabel:
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #687480; font-size: 12px;")
        value_label = QLabel("-")
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value_label.setWordWrap(True)
        value_label.setStyleSheet(ui_styles("color: #202A33; font-weight: 500;"))
        layout.addWidget(title_label, 0, column)
        layout.addWidget(value_label, 1, column)
        return value_label

    @property
    def current_run_id(self) -> object:
        record = self._active_record()
        return record.run_id if record is not None else self._current_run_context

    @property
    def current_round_id(self) -> object:
        record = self._active_record()
        return record.round_id if record is not None and not record.implicit else None

    @property
    def current_target_label(self) -> str:
        record = self._active_record()
        return record.target_label if record is not None else self._current_target_label

    @property
    def selected_run_id(self) -> object:
        record = self._selected_record()
        return record.run_id if record is not None else None

    @property
    def selected_round_id(self) -> object:
        record = self._selected_record()
        return record.round_id if record is not None and not record.implicit else None

    def begin_run(self, run_id: object, target_label: str = "自动定点") -> None:
        self._current_run_context = run_id
        self._run_index_for(run_id)
        self._current_target_label = str(target_label or "自动定点")
        self._active_uid = None
        self._select_next_record = True
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0

    @staticmethod
    def _group_component(value: object) -> object:
        try:
            hash(value)
        except TypeError:
            return ("object", id(value))
        return ("value", value)

    @classmethod
    def _group_key(cls, run_id: object, round_id: object) -> tuple[object, object]:
        return cls._group_component(run_id), cls._group_component(round_id)

    def _run_index_for(self, run_id: object) -> int:
        key = self._group_component(run_id)
        index = self._run_indexes.get(key)
        if index is None:
            self._run_index_counter += 1
            index = self._run_index_counter
            self._run_indexes[key] = index
        return index

    def _active_record(self) -> _RoundRecord | None:
        if self._active_uid is None:
            return None
        return self._record_by_uid.get(self._active_uid)

    def _selected_record(self) -> _RoundRecord | None:
        if self._selected_uid is None:
            return None
        return self._record_by_uid.get(self._selected_uid)

    def _get_or_create_record(
        self,
        run_id: object,
        round_id: object,
        *,
        cycle_index: int | None,
        target_label: str,
        implicit: bool,
    ) -> tuple[_RoundRecord, bool]:
        key = self._group_key(run_id, round_id)
        record = self._round_records.get(key)
        created = record is None
        was_following_active = self._selected_uid is None or self._selected_uid == self._active_uid
        if record is None:
            self._round_uid_counter += 1
            record = _RoundRecord(
                uid=self._round_uid_counter,
                run_id=run_id,
                round_id=round_id,
                run_index=self._run_index_for(run_id),
                cycle_index=cycle_index,
                target_label=target_label,
                detail_title="运行消息" if implicit else "轮次进行中",
                implicit=implicit,
                entries=_FeedEntries(self._archive, self._round_uid_counter),
                plain_lines=_PlainLines(self._archive, self._round_uid_counter),
            )
            self._round_records[key] = record
            self._round_order.append(key)
        else:
            record.target_label = target_label or record.target_label
            if cycle_index is not None:
                record.cycle_index = cycle_index
                record.implicit = False
        self._active_uid = record.uid
        preferred_uid = self._selected_uid
        if self._select_next_record or (created and was_following_active):
            preferred_uid = record.uid
        self._select_next_record = False
        self._sync_round_record(record, preferred_uid=preferred_uid)
        return record, created

    def _ensure_record(self, *, target_label: str | None = None, namespace: str = "session") -> _RoundRecord:
        record = self._active_record()
        if record is not None:
            return record
        label = str(self._current_target_label or target_label or "自动定点")
        round_id = f"{namespace}-session"
        record, _created = self._get_or_create_record(
            self._current_run_context,
            round_id,
            cycle_index=None,
            target_label=label,
            implicit=True,
        )
        return record

    def _ensure_auto_tid_record(self) -> _RoundRecord:
        record = self._active_record()
        if record is not None and "TID" in record.target_label.upper():
            return record
        if "TID" in self._current_target_label.upper():
            return self._ensure_record(target_label=self._current_target_label, namespace="auto-tid")
        record, _created = self._get_or_create_record(
            None,
            "auto-tid-session",
            cycle_index=None,
            target_label="自动 TID",
            implicit=True,
        )
        return record

    def _record_matches_filter(self, record: _RoundRecord) -> bool:
        summary = self._round_records.search_summaries[record.uid]
        return summary.matches_result(self.result_filter.currentData() or "all") and (
            not self._keyword or self._keyword in summary.searchable or record.uid in self._keyword_matches
        )

    def _refresh_round_list(self, *_args: object, preferred_uid: int | None = None) -> None:
        if not hasattr(self, "round_list"):
            return
        keyword = self.search_edit.text().strip().casefold()
        if keyword != self._keyword:
            self._history_search.cancel()
            self._filter_start_timer.stop()
            self._query_generation += 1
            self._keyword = keyword
            self._keyword_matches.clear()
            self._searched_through_seq.clear()
            self._search_dirty = set(self._round_records.search_summaries) if keyword else set()
            self._search_inflight = False
            if self._selected_uid is not None:
                self._search_preferred_uid = self._selected_uid
            if keyword and self._search_dirty:
                self._filter_start_timer.start()
        self._apply_round_filter(preferred_uid)

    def _apply_round_filter(self, preferred_uid: int | None = None) -> None:
        result = self.result_filter.currentData() or "all"
        self._visible_uids = [
            uid for uid, summary in reversed(self._round_records.search_summaries.items())
            if summary.matches_result(result) and (
                not self._keyword or self._keyword in summary.searchable or uid in self._keyword_matches
            )
        ]
        self._visible_uid_set = set(self._visible_uids)
        self._select_visible_round(preferred_uid if preferred_uid is not None else self._selected_uid or self._search_preferred_uid)

    def _start_keyword_search(self) -> None:
        if not self._keyword or not self._search_dirty:
            return
        summaries = tuple(self._round_records.search_summaries[uid] for uid in sorted(self._search_dirty)
                          if uid in self._round_records.search_summaries)
        self._search_dirty.clear()
        keyword, generation = self._keyword, self._query_generation
        archive, snapshot = self._archive, self._archive.plain_snapshot()
        search_inputs = tuple((summary, self._searched_through_seq.get(summary.uid, 0)) for summary in summaries
                              if summary.uid not in self._keyword_matches)
        self._search_inflight = True
        self._update_search_heading()

        def search(cancellation: SearchCancellation):
            scanned = {}
            for summary, after_seq in search_inputs:
                cancellation.check()
                if keyword in summary.searchable:
                    continue
                scanned[summary.uid] = any(
                    keyword in line.casefold() for line in _plain_lines(
                        archive, summary.uid, snapshot=snapshot, cancellation=cancellation, after_seq=after_seq,
                    )
                )
            return generation, snapshot.through_seq, scanned

        self._history_search.submit(search, self._keyword_search_finished, self._keyword_search_failed)

    def _keyword_search_finished(self, result) -> None:
        generation, through_seq, scanned = result
        if generation != self._query_generation:
            return
        self._search_inflight = False
        for uid, matched in scanned.items():
            if uid in self._round_records.search_summaries:
                self._searched_through_seq[uid] = through_seq
                if matched:
                    self._keyword_matches.add(uid)
        self._apply_round_filter()
        if self._search_dirty:
            self._filter_start_timer.start()
        self._update_search_heading()

    def _keyword_search_failed(self, error: Exception) -> None:
        self._search_inflight = False
        self._update_search_heading()
        self._show_archive_notice(f"历史搜索未完成，请重试：{error}")

    def _update_search_heading(self) -> None:
        pending = self._keyword and (self._search_inflight or self._filter_start_timer.isActive())
        self.round_list_heading.setText(f"本次会话 · {len(self._round_order)} 条记录" + (" · 搜索中…" if pending else ""))

    def _sync_round_record(self, record: _RoundRecord, *, preferred_uid: int | None = None) -> None:
        self._round_records.remember_summary(record)
        if self._keyword:
            self._search_dirty.add(record.uid)
            if not self._search_inflight:
                self._filter_start_timer.start()
            self._update_search_heading()
        visible = self._record_matches_filter(record)
        was_visible = record.uid in self._visible_uid_set
        if visible != was_visible:
            position = bisect_left(self._visible_uids, -record.uid, key=lambda uid: -uid)
            if visible:
                self._visible_uids.insert(position, record.uid)
                self._visible_uid_set.add(record.uid)
            else:
                self._visible_uids.pop(position)
                self._visible_uid_set.remove(record.uid)
            self._select_visible_round(preferred_uid)
            return
        item = self._round_items.get(record.uid)
        if item is not None:
            row = self.round_list.itemWidget(item)
            row.update_record(record)
            self._update_round_accessibility(item, record)
            row.setAccessibleName(item.toolTip())
        if preferred_uid is not None and preferred_uid != self._selected_uid:
            self._select_visible_round(preferred_uid)
        else:
            self._update_detail_header()

    def _select_visible_round(self, preferred_uid: int | None = None) -> None:
        previous_uid = self._selected_uid
        if preferred_uid is None:
            preferred_uid = previous_uid
        selected_uid = next((uid for uid in (preferred_uid, self._active_uid) if uid in self._visible_uid_set), None)
        if selected_uid is None and self._visible_uids:
            selected_uid = self._visible_uids[0]
        if selected_uid is not None:
            position = bisect_left(self._visible_uids, -selected_uid, key=lambda uid: -uid)
            self._round_page = position // ROUND_PAGE_SIZE
        else:
            self._round_page = 0
        self._selected_uid = selected_uid
        self._refresh_round_page()
        self._update_search_heading()
        self.clear_button.setEnabled(bool(self._round_records))
        self.export_button.setEnabled(bool(self._all_plain_lines) and not self._export_busy and not self._closing_requested)
        self._set_empty_state(not self._visible_uids, filtered=bool(self._round_order))
        if selected_uid != previous_uid:
            self._render_selected_record()
        else:
            self._update_detail_header()

    @staticmethod
    def _update_round_accessibility(item: QListWidgetItem, record: _RoundRecord) -> None:
        text = (f"运行 {record.run_index}，"
                f"{'会话记录' if record.cycle_index is None else f'第 {record.cycle_index} 轮'}，"
                f"{record.target_label}，{_record_status(record)}")
        item.setToolTip(text)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, text)

    def _refresh_round_page(self) -> None:
        page_count = max(1, (len(self._visible_uids) + ROUND_PAGE_SIZE - 1) // ROUND_PAGE_SIZE)
        self._round_page = min(self._round_page, page_count - 1)
        start = self._round_page * ROUND_PAGE_SIZE
        page_uids = self._visible_uids[start:start + ROUND_PAGE_SIZE]
        page_set = set(page_uids)
        blocker = QSignalBlocker(self.round_list)
        for uid in tuple(self._round_items):
            if uid not in page_set:
                item = self._round_items.pop(uid)
                widget = self.round_list.itemWidget(item)
                self.round_list.removeItemWidget(item)
                self.round_list.takeItem(self.round_list.row(item))
                if widget is not None:
                    widget.deleteLater()
        for index, uid in enumerate(page_uids):
            if uid not in self._round_items:
                record = self._record_by_uid[uid]
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, uid)
                row = _RoundListRow(record, self.round_list)
                self._update_round_accessibility(item, record)
                row.setAccessibleName(item.toolTip())
                item.setSizeHint(row.sizeHint())
                self.round_list.insertItem(index, item)
                self.round_list.setItemWidget(item, row)
                self._round_items[uid] = item
        if self._selected_uid in self._round_items:
            self.round_list.setCurrentItem(self._round_items[self._selected_uid])
        del blocker
        self.round_page_label.setText(f"{self._round_page + 1} / {page_count}")
        self.round_previous_button.setEnabled(self._round_page > 0)
        self.round_next_button.setEnabled(self._round_page + 1 < page_count)

    def _change_round_page(self, delta: int) -> None:
        page_count = max(1, (len(self._visible_uids) + ROUND_PAGE_SIZE - 1) // ROUND_PAGE_SIZE)
        page = max(0, min(page_count - 1, self._round_page + delta))
        if page == self._round_page:
            return
        self._round_page = page
        self._selected_uid = self._visible_uids[page * ROUND_PAGE_SIZE]
        self._refresh_round_page()
        self._render_selected_record()

    def _set_empty_state(self, empty: bool, *, filtered: bool = False) -> None:
        if empty:
            self.empty_state_title.setText(
                "没有符合条件的轮次" if filtered else "暂无轮次记录"
            )
            self.empty_state_detail.setText(
                "当前筛选结果为空" if filtered else "当前会话尚未产生运行结果"
            )
            self.empty_action_button.setText("清除筛选" if filtered else "开始前检查")
            self.empty_action_button.setVisible(filtered or self.empty_navigation is not None)
            self.round_splitter.hide()
            self.empty_state.show()
            return
        self.empty_state.hide()
        self.round_splitter.show()

    def _empty_action(self) -> None:
        if self._round_order:
            self.result_filter.setCurrentIndex(0)
            self.search_edit.clear()
        elif self.empty_navigation is not None:
            self.empty_navigation()

    def _on_round_selected(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        selected_uid = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if selected_uid == self._selected_uid:
            return
        self._selected_uid = selected_uid
        if selected_uid is not None:
            self._search_preferred_uid = selected_uid
        self._render_selected_record()
        if current is not None and self._compact:
            self.round_picker_button.setChecked(False)

    def _update_detail_header(self) -> None:
        record = self._selected_record()
        enabled = record is not None
        self.copy_round_button.setEnabled(enabled and bool(record and record.plain_lines) and not self._copy_busy
                                          and not self._closing_requested and not self._export_transfer.committing)
        can_correlate = bool(record and (not record.implicit or record.run_id is not None))
        self.related_logs_button.setEnabled(can_correlate)
        if record is None:
            self.detail_title_label.setText("请选择轮次")
            self.detail_time_label.clear()
            self.detail_status_label.clear()
            self.target_value_label.setText("-")
            self.seed_value_label.setText("-")
            self.locked_adv_value_label.setText("-")
            self.candidate_value_label.setText("-")
            self.round_summary.setText("目标 — · 候选 — · 锁定帧 —")
            self.warning_label.clear()
            self.warning_frame.hide()
            return

        round_title = (
            f"运行 {record.run_index} · 第 {record.cycle_index} 轮"
            if record.cycle_index is not None
            else f"运行 {record.run_index} · 会话记录"
        )
        self.detail_title_label.setText(f"{round_title} · {record.detail_title}")
        status = _record_status(record)
        status_color = {
            "有警告": "#A35F08",
            "疑似出闪": CLR_SHINY,
            "未出闪": CLR_LOCK,
            "无候选": "#687480",
        }.get(status, "#087C58")
        self.detail_status_label.setText(status)
        self.detail_status_label.setStyleSheet(
            ui_styles(f"color: {status_color}; background: #F0F3F6; border-radius: 3px; "
            "padding: 2px 6px; font-size: 12px; font-weight: 500;")
        )
        start = record.started_at.strftime("%Y-%m-%d %H:%M:%S")
        if record.updated_at.replace(microsecond=0) == record.started_at.replace(microsecond=0):
            time_text = start
        else:
            elapsed = max(0.0, (record.updated_at - record.started_at).total_seconds())
            time_text = f"{start} 至 {record.updated_at.strftime('%H:%M:%S')} · 用时 {elapsed:.1f} 秒"
        self.detail_time_label.setText(time_text)
        self.target_value_label.setText(record.target_label)
        self.seed_value_label.setText(record.seed_text or "-")
        self.locked_adv_value_label.setText(
            str(record.locked_advances) if record.locked_advances is not None else "-"
        )
        self.candidate_value_label.setText(f"{record.candidate_count} 个")
        locked = record.locked_advances if record.locked_advances is not None else "—"
        self.round_summary.setText(f"{record.target_label} · {record.candidate_count} 个候选 · 锁定帧 {locked}")
        if record.warnings:
            self.warning_label.setText("\n".join(record.warnings))
            self.warning_frame.show()
        else:
            self.warning_label.clear()
            self.warning_frame.hide()

    def _clear_feed_widgets(self) -> None:
        while self._feed_layout.count():
            item = self._feed_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render_selected_record(self) -> None:
        self._cancel_auto_follow()
        self._clear_feed_widgets()
        self._update_detail_header()
        record = self._selected_record()
        if record is None:
            self._fill_candidate_table(self.round_candidate_table, _FeedEntry(kind="table"))
            self.feed_pager.hide()
            return
        self._refresh_candidate_snapshot(record)
        self._feed_start = max(0, len(record.entries) - FEED_PAGE_SIZE)
        self._feed_at_end = True
        self._render_feed_page(record)
        self._scroll_top_timer.start(0)

    def _render_feed_page(self, record: _RoundRecord) -> None:
        self._clear_feed_widgets()
        self._rendering_record = True
        try:
            for entry in record.entries[self._feed_start:self._feed_start + FEED_PAGE_SIZE]:
                self._render_entry(entry)
        finally:
            self._rendering_record = False
        self._update_feed_pager(record)

    def _update_feed_pager(self, record: _RoundRecord) -> None:
        count = len(record.entries)
        self.feed_previous_button.setEnabled(self._feed_start > 0)
        self.feed_next_button.setEnabled(self._feed_start + FEED_PAGE_SIZE < count)
        self.feed_page_label.setText(f"{self._feed_start + 1}–{min(count, self._feed_start + FEED_PAGE_SIZE)} / {count}")
        self.feed_pager.setVisible(self.feed_toggle.isChecked() and count > FEED_PAGE_SIZE)

    def _change_feed_page(self, delta: int) -> None:
        record = self._selected_record()
        if record is None:
            return
        self._cancel_auto_follow()
        self._feed_start = max(0, min(max(0, len(record.entries) - FEED_PAGE_SIZE), self._feed_start + delta * FEED_PAGE_SIZE))
        self._feed_at_end = self._feed_start + FEED_PAGE_SIZE >= len(record.entries)
        self._render_feed_page(record)
        self.history_scroll.verticalScrollBar().setValue(0)

    def _refresh_candidate_snapshot(self, record) -> None:
        self._fill_candidate_table(self.round_candidate_table, record.candidate_snapshot or _FeedEntry(kind="table"))

    def _set_candidate_snapshot(self, record: _RoundRecord, entry: _FeedEntry | None) -> None:
        record.candidate_snapshot = entry
        if record.uid == self._selected_uid:
            self._refresh_candidate_snapshot(record)

    def _scroll_selected_record_to_top(self) -> None:
        self.detail_scroll.verticalScrollBar().setValue(0)
        self.history_scroll.verticalScrollBar().setValue(0)

    def _render_entry(self, entry: _FeedEntry) -> None:
        if entry.kind == "text":
            label = QLabel(entry.text, self._feed_body)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            weight = ui_styles("font-weight: 500;" if entry.bold else "")
            label.setStyleSheet(f"color: {entry.color}; padding: 1px 4px; {weight}")
            self._append_widget(label)
        elif entry.kind == "divider":
            line = QFrame(self._feed_body)
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFixedHeight(2 if entry.strong else 1)
            line.setStyleSheet(f"background: {entry.color}; border: none;")
            self._append_widget(line)
        elif entry.kind == "spacer":
            spacer = QWidget(self._feed_body)
            spacer.setFixedHeight(3)
            self._append_widget(spacer)
        elif entry.kind == "table":
            self._append_widget(self._create_candidate_table(entry)._container)

    def _record_entry(self, entry: _FeedEntry) -> None:
        record = self._ensure_record()
        record.entries.append(entry)
        record.updated_at = datetime.now()
        if record.uid == self._selected_uid:
            scroll_bar = self.history_scroll.verticalScrollBar()
            if self._feed_layout.count() >= FEED_PAGE_SIZE:
                following = self._follow_pending or scroll_bar.maximum() - scroll_bar.value() <= 4
                if not following:
                    self._feed_at_end = False
                if self._feed_at_end:
                    item = self._feed_layout.takeAt(0)
                    widget = item.widget()
                    if widget is not None:
                        widget.setParent(None)
                        widget.deleteLater()
                    self._feed_start += 1
            if self._feed_at_end:
                self._render_entry(entry)
            self._update_feed_pager(record)

    def _touch_record(self, record: _RoundRecord | None = None) -> None:
        record = record or self._active_record()
        if record is None:
            return
        record.updated_at = datetime.now()
        self._round_records.store(record)
        self._sync_round_record(record)

    def _add_warning(self, record: _RoundRecord, message: str) -> None:
        if message not in record.warnings:
            record.warnings.append(message)

    # ── 输出快捷方法 ──
    def _append_plain(self, text: str = "", color: str = CLR_BODY) -> None:
        record = self._ensure_record()
        record.plain_lines.append(text)
        self._all_plain_lines.count += 1
        record.updated_at = datetime.now()
        self.text_view.w(text, color)
        copying_allowed = not self._copy_busy and not self._closing_requested and not self._export_transfer.committing
        self.copy_button.setEnabled(copying_allowed)
        self.export_button.setEnabled(not self._export_busy and not self._closing_requested)
        if record.uid == self._selected_uid:
            self.copy_round_button.setEnabled(bool(record.plain_lines) and copying_allowed)

    def _append_widget(self, widget: QWidget) -> None:
        if self._rendering_record:
            self._feed_layout.addWidget(widget)
            return
        scroll_bar = self.history_scroll.verticalScrollBar()
        follow = self._follow_pending or scroll_bar.maximum() - scroll_bar.value() <= 4
        self._feed_layout.addWidget(widget)
        if follow and not self._follow_pending:
            self._follow_pending = True
            self._follow_origin = scroll_bar.value()
            self._follow_timer.start(0)

    def _append_text_label(self, text: str, color: str = CLR_BODY, *, bold: bool = False) -> None:
        self._record_entry(_FeedEntry(kind="text", text=text, color=color, bold=bold))

    def _append_divider(self, text: str, *, strong: bool = False) -> None:
        self._append_plain(text, CLR_SEP)
        self._record_entry(_FeedEntry(kind="divider", color=CLR_SEP, strong=strong))

    def _finish_auto_follow(self) -> None:
        if not self._follow_pending:
            return
        scroll_bar = self.history_scroll.verticalScrollBar()
        if scroll_bar.value() + 4 >= self._follow_origin:
            scroll_bar.setValue(scroll_bar.maximum())
        self._follow_pending = False

    def _cancel_auto_follow(self, *_args: object) -> None:
        self._follow_pending = False

    def _sep(self) -> None:
        self._append_divider(SEPARATOR_THICK, strong=True)

    def _sep_thin(self) -> None:
        self._append_divider(SEPARATOR_THIN)

    def _sep_end(self) -> None:
        self._append_divider(SEPARATOR_END)

    def _ts(self, text: str) -> None:
        line = _ts(text)
        self._append_plain(line)
        self._append_text_label(line)

    def _info(self, text: str) -> None:
        line = f"  {text}"
        self._append_plain(line)
        self._append_text_label(line)

    def _w(self, text: str, c: str = CLR_BODY) -> None:
        self._append_plain(text, c)
        self._append_text_label(text, c)

    def _blank(self) -> None:
        self._append_plain()
        self._record_entry(_FeedEntry(kind="spacer"))

    def _update_summary(self) -> None:
        self.summary_label.setText(f"{self._cycle_count} 轮 · {self._candidate_count} 条候选")

    def clear(self) -> None:
        self._history_search.cancel()
        self._copy_work.cancel()
        self._export_transfer.cancel(preserve_commit_notice=True)
        self._filter_start_timer.stop()
        self._search_inflight = False
        self._query_generation += 1
        self._keyword_matches.clear()
        self._searched_through_seq.clear()
        self._search_dirty.clear()
        self._search_preferred_uid = None
        self._copy_busy = False
        self._export_busy = self._export_transfer.committing
        self.copy_button.setText("复制全部")
        self.copy_round_button.setText("复制本轮")
        self.export_button.setText("正在保存…" if self._export_busy else "导出")
        self._archive_start_timer.stop()
        try:
            self._archive.clear()
        except TimeoutError as error:
            self._show_archive_notice(str(error))
            self.copy_button.setEnabled(bool(self._all_plain_lines) and not self._export_transfer.committing
                                        and not self._closing_requested)
            self.export_button.setEnabled(bool(self._all_plain_lines) and not self._export_busy and not self._closing_requested)
            self._update_detail_header()
            if self._keyword:
                self._search_dirty.update(self._round_records.search_summaries)
                self._filter_start_timer.start()
            return
        self._cancel_auto_follow()
        self._scroll_top_timer.stop()
        self._follow_timer.stop()
        self.text_view.clear()
        self._clear_feed_widgets()
        self.round_list.clear()
        self._round_items.clear()
        self._visible_uids.clear()
        self._visible_uid_set.clear()
        self._round_page = 0
        self._round_records.clear()
        self._record_by_uid.clear()
        self._round_order.clear()
        self._active_uid = None
        self._selected_uid = None
        self._round_uid_counter = 0
        self._run_index_counter = 0
        self._run_indexes.clear()
        self._select_next_record = False
        self._cycle_index = 0
        self._cycle_count = 0
        self._candidate_count = 0
        self._pid_ec_seen.clear()
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self._all_plain_lines.count = 0
        self._fill_candidate_table(self.round_candidate_table, _FeedEntry(kind="table"))
        self.feed_pager.hide()
        self._refresh_round_page()
        self.copy_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.copy_round_button.setEnabled(False)
        self.related_logs_button.setEnabled(False)
        self._update_summary()
        self.round_list_heading.setText("本次会话 · 0 条记录")
        self._set_empty_state(True)
        self._update_detail_header()

    def finish_run(
        self,
        run_id: object,
        outcome: str,
        *,
        round_id: object = None,
        target_label: str | None = None,
    ) -> None:
        """Finalize the latest matching record without replacing a concrete result."""

        record = None
        if round_id is not None:
            record = self._round_records.get(self._group_key(run_id, round_id))
        if record is None:
            run_key = self._group_component(run_id)
            for key in reversed(self._round_order):
                candidate = self._round_records[key]
                if self._group_component(candidate.run_id) == run_key:
                    record = candidate
                    break
        if record is None:
            record, _created = self._get_or_create_record(
                run_id,
                "run-session",
                cycle_index=None,
                target_label=str(target_label or self._current_target_label or "自动流程"),
                implicit=True,
            )
        if record.outcome is None:
            record.outcome = str(outcome or "已结束")
            record.detail_title = {
                "已完成": "运行完成",
                "失败": "运行失败",
                "已停止": "运行已停止",
                "继续重试": "本轮结束，继续重试",
            }.get(record.outcome, "运行结束")
        self._touch_record(record)

    def copy_all(self) -> None:
        self._copy_history(None, len(self._all_plain_lines))

    def copy_selected_round(self) -> None:
        record = self._selected_record()
        if record is not None and record.plain_lines:
            self._copy_history(record.uid, len(record.plain_lines))

    def _copy_history(self, uid: int | None, count: int) -> None:
        if not count or self._copy_busy or self._closing_requested or self._export_transfer.committing:
            return
        # Small histories have a hard work bound and retain the existing
        # immediate-copy API. Full candidate histories are prepared off-thread.
        if count <= 1000:
            text = "".join(line + "\n" for line in _plain_lines(self._archive, uid))
            QGuiApplication.clipboard().setText(text)
            return
        archive, snapshot = self._archive, self._archive.plain_snapshot()
        self._copy_busy = True
        self.copy_button.setEnabled(False)
        self.copy_round_button.setEnabled(False)
        (self.copy_button if uid is None else self.copy_round_button).setText("准备复制…")
        self._copy_work.submit(
            lambda cancellation: "".join(line + "\n" for line in _plain_lines(
                archive, uid, snapshot=snapshot, cancellation=cancellation,
            )), self._copy_finished, self._copy_failed,
        )

    def _copy_finished(self, text: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self._finish_copy()

    def _copy_failed(self, error: Exception) -> None:
        self._finish_copy()
        self._show_archive_notice(f"历史复制未完成，请重试：{error}")

    def _finish_copy(self) -> None:
        self._copy_busy = False
        self.copy_button.setText("复制全部")
        self.copy_round_button.setText("复制本轮")
        self.copy_button.setEnabled(bool(self._all_plain_lines) and not self._closing_requested
                                    and not self._export_transfer.committing)
        self._update_detail_header()

    def _request_related_logs(self) -> None:
        record = self._selected_record()
        if record is not None and (not record.implicit or record.run_id is not None):
            round_id = None if record.implicit else record.round_id
            self.related_logs_requested.emit(record.run_id, round_id)

    def export_to_file(self) -> None:
        if self._export_busy or self._closing_requested:
            return
        transfer = self._export_transfer
        generation = transfer.generation
        archive, snapshot, count = self._archive, self._archive.plain_snapshot(), len(self._all_plain_lines)
        path, _ = QFileDialog.getSaveFileName(self, "导出历史记录", "history.txt", "文本文件 (*.txt)")
        if path and not transfer._closed and transfer.generation == generation:
            destination = Path(path)

            def export(cancellation: SearchCancellation):
                staged = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                                     prefix=".history-export-", suffix=".tmp", delete=False) as output:
                        staged = PreparedExport(output.name, destination)
                        for line in _plain_lines(archive, None, snapshot=snapshot, cancellation=cancellation):
                            output.write(line + "\n")
                    cancellation.check()
                    return staged
                except BaseException:
                    if staged is not None:
                        staged.discard()
                    raise

            transfer.prepare_export(export, count)

    def _export_changed(self) -> None:
        self._export_busy = self._export_transfer.kind is not None
        committing = self._export_transfer.committing
        self.export_button.setText("正在保存…" if committing else "正在导出…" if self._export_busy else "导出")
        self.export_button.setEnabled(bool(self._all_plain_lines) and not self._export_busy and not self._closing_requested)
        self.copy_button.setEnabled(bool(self._all_plain_lines) and not self._copy_busy
                                    and not committing and not self._closing_requested)
        self._update_detail_header()

    def _export_finished(self, destination) -> None:
        self._show_archive_notice(f"历史已导出：{destination}")

    def _export_failed(self, error: Exception) -> None:
        self._show_archive_notice(f"历史导出未完成，请重试：{error}")

    def result_save_pending(self) -> bool:
        return self._export_transfer.committing

    def prepare_for_close(self) -> None:
        """Cancel preparation while retaining any file commit accepted by Qt."""
        self._closing_requested = True
        self._archive.stop_accepting()
        self._filter_start_timer.stop()
        self._history_search.cancel()
        self._copy_work.cancel()
        self._search_inflight = False
        self._copy_busy = False
        self.copy_button.setText("复制全部")
        self.copy_round_button.setText("复制本轮")
        self._export_transfer.cancel(preserve_commit_notice=True)

    def resume_after_close(self) -> None:
        self._closing_requested = False
        self._archive.resume_accepting()
        self._export_changed()
        if self._keyword:
            self._search_dirty.update(self._round_records.search_summaries)
            self._filter_start_timer.start()

    def shutdown(self) -> bool:
        """Close accepted history tasks without waiting on the GUI thread."""
        if self.result_save_pending():
            return False
        self._closing_requested = True
        self._filter_start_timer.stop()
        self._archive_start_timer.stop()
        self._history_search.close()
        self._copy_work.close()
        self._export_transfer.close()
        self._archive.close(wait=False)
        return True

    def _candidate_status(
        self,
        index: int,
        state: object,
        locked_index: int,
        sync_flags: list[str] | None,
    ) -> str:
        key = _pid_ec_key(state)
        duplicate = self._pid_ec_seen.get(key)
        self._pid_ec_seen[key] = index + 1
        tags: list[str] = []
        if index == locked_index:
            tags.append("锁定")
        if sync_flags and index < len(sync_flags) and sync_flags[index] == "sync":
            tags.append("同步")
        if duplicate:
            tags.append(f"同候选{duplicate}")
        return " · ".join(tags) if tags else "普通"

    @staticmethod
    def _candidate_row(index: int, state: object, status: str) -> list[str]:
        return [
            str(index + 1),
            status,
            str(_get_int(state, "advances")),
            _shiny_text(state),
            _nature_text(state),
            *_state_iv_values(getattr(state, "ivs", None)),
            str(_get_int(state, "ability")),
            _gender_text(state),
            _state_ec(state),
            _state_pid(state),
            str(_get_int(state, "height")),
            str(_get_int(state, "weight")),
        ]

    @staticmethod
    def _reverse_row(
        index: int,
        state: object,
        status: str,
        delay: int,
        characteristic: str | None,
    ) -> list[str]:
        return [
            str(index + 1),
            status,
            str(_get_int(state, "advances")),
            str(delay),
            _shiny_text(state),
            _nature_text(state),
            characteristic or "-",
            *_state_iv_values(getattr(state, "ivs", None)),
            str(_get_int(state, "ability")),
            _gender_text(state),
            _state_ec(state),
            _state_pid(state),
            str(_get_int(state, "height")),
            str(_get_int(state, "weight")),
        ]

    def _append_candidate_table(
        self,
        candidates: Sequence[object],
        locked_index: int = -1,
        sync_flags: list[str] | None = None,
        *,
        reverse: bool = False,
        delays: list[int] | None = None,
        characteristic: str | None = None,
        reservation: CandidateReservation | None = None,
    ) -> None:
        archived_rows = reservation.prepared_rows if reservation is not None else None
        if archived_rows is None:
            archived_rows = _CandidateRows(candidates, locked_index, sync_flags, delays,
                                           reverse=reverse, characteristic=characteristic)
        count = len(archived_rows.states)
        if count:
            batch, count = self._archive.create_candidate_batch(archived_rows, count, reservation=reservation)
            entry = _candidate_entry(self._archive, batch, count, reverse)
        else:
            entry = _FeedEntry(kind="table", reverse=reverse)
        record = self._ensure_record()
        if count:
            record.plain_lines.append_candidates(batch, count, reverse)
            self._all_plain_lines.count += count
            copying_allowed = not self._copy_busy and not self._closing_requested and not self._export_transfer.committing
            self.copy_button.setEnabled(copying_allowed)
            self.export_button.setEnabled(not self._export_busy and not self._closing_requested)
            if record.uid == self._selected_uid:
                self.copy_round_button.setEnabled(copying_allowed)
        if not reverse:
            record.candidate_count = count
            self._set_candidate_snapshot(record, entry)
        if not count:
            return
        self._candidate_count += count
        self._update_summary()
        self._record_entry(entry)
        self._archive_start_timer.start(0)

    def _create_candidate_table(self, entry: _FeedEntry) -> QTableWidget:
        headers = REVERSE_HEADERS if entry.reverse else CANDIDATE_HEADERS
        widths = REVERSE_WIDTHS if entry.reverse else CANDIDATE_WIDTHS
        container = QWidget(self._feed_body)
        container.setMinimumWidth(0)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        table = QTableWidget(0, len(headers), container)
        table._container = container
        layout.addWidget(table, 1)
        pager = QWidget(container)
        page_layout = QHBoxLayout(pager)
        page_layout.setContentsMargins(0, 0, 0, 0)
        table._previous_button = QPushButton("上一页", pager)
        table._next_button = QPushButton("下一页", pager)
        table._page_label = QLabel(pager)
        table._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        table._pager = pager
        table._previous_button.clicked.connect(lambda: self._show_candidate_page(table, table._page - 1))
        table._next_button.clicked.connect(lambda: self._show_candidate_page(table, table._page + 1))
        page_layout.addWidget(table._previous_button)
        page_layout.addWidget(table._page_label, 1)
        page_layout.addWidget(table._next_button)
        layout.addWidget(pager)
        table.setObjectName("HistoryCandidateTable")
        table.setAccessibleName("反查候选表" if entry.reverse else "候选结果表")
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.setItemDelegate(_CandidateRowDelegate(table))
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(32)
        header = table.horizontalHeader()
        header.setFixedHeight(34)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)

        self._fill_candidate_table(table, entry)

        visible_rows = min(len(entry.rows), 7)
        table.setFixedHeight(min(300, 34 + visible_rows * 32 + 22))
        table.setMinimumWidth(0)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setStyleSheet(
            ui_styles("QTableWidget#HistoryCandidateTable { background: #FFFFFF; alternate-background-color: #FAFBFC; "
            "border: 0; color: #202A33; }"
            "QTableWidget#HistoryCandidateTable::item { padding: 3px 6px; }"
            "QTableWidget#HistoryCandidateTable::item:selected { background: #DCEFE6; color: #202A33; }"
            "QTableWidget#HistoryCandidateTable QHeaderView::section { background: #F0F3F6; color: #687480; "
            "border: 0; border-bottom: 1px solid #F0F2F5; "
            "padding: 6px; font-weight: 500; }")
        )
        return table

    @staticmethod
    def _fill_candidate_table(table: QTableWidget, entry: _FeedEntry) -> None:
        table._entry = entry
        HistoryPanel._show_candidate_page(table, 0)

    @staticmethod
    def _show_candidate_page(table: QTableWidget, page: int) -> None:
        entry = table._entry
        count = len(entry.rows)
        pages = max(1, (count + CANDIDATE_PAGE_SIZE - 1) // CANDIDATE_PAGE_SIZE)
        page = max(0, min(pages - 1, page))
        table._page = page
        start = page * CANDIDATE_PAGE_SIZE
        stop = min(count, start + CANDIDATE_PAGE_SIZE)
        table.setUpdatesEnabled(False)
        blocker = QSignalBlocker(table)
        table.clearContents()
        table.setRowCount(stop - start)
        headers = REVERSE_HEADERS if entry.reverse else CANDIDATE_HEADERS
        shiny_column = headers.index("异色")
        status_column = headers.index("状态")
        for row_index, source_index in enumerate(range(start, stop)):
            values = entry.rows[source_index]
            locked = "锁定" in entry.statuses[source_index].split(" · ")
            synchronized = "同步" in entry.statuses[source_index].split(" · ")
            shiny = _get_int(entry.states[source_index], "shiny") > 0
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                alignment = Qt.AlignmentFlag.AlignVCenter
                alignment |= Qt.AlignmentFlag.AlignLeft if column == status_column else Qt.AlignmentFlag.AlignHCenter
                item.setTextAlignment(alignment)
                if locked:
                    item.setBackground(QColor(CLR_LOCK_BG))
                    item.setForeground(QColor(CLR_LOCK if column == status_column else CLR_LOCK_FG))
                if column == status_column and synchronized:
                    if not locked:
                        item.setBackground(QColor(CLR_SYNC_BG))
                    item.setForeground(QColor(CLR_SYNC_FG))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column == shiny_column and shiny:
                    item.setBackground(QColor(CLR_SHINY_BG))
                    item.setForeground(QColor(CLR_SHINY_FG))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row_index, column, item)
        del blocker
        table.setUpdatesEnabled(True)
        table._page_label.setText(f"{page + 1} / {pages} · {count} 条")
        table._previous_button.setEnabled(page > 0)
        table._next_button.setEnabled(page + 1 < pages)
        table._pager.setVisible(count > CANDIDATE_PAGE_SIZE)
        table.verticalScrollBar().setValue(0)

    # ── 事件方法 ───────────────────────────────────────────────

    def cycle_start(
        self,
        cycle_index: int,
        *,
        run_id: object = None,
        round_id: object = None,
        target_label: str | None = None,
    ) -> None:
        effective_run_id = self._current_run_context if run_id is None else run_id
        effective_round_id = cycle_index if round_id is None else round_id
        effective_target = str(target_label or self._current_target_label or "自动定点")
        if run_id is not None:
            self._current_run_context = run_id
        if target_label:
            self._current_target_label = str(target_label)
        record, created = self._get_or_create_record(
            effective_run_id,
            effective_round_id,
            cycle_index=cycle_index,
            target_label=effective_target,
            implicit=False,
        )
        record.detail_title = "轮次进行中"
        record.outcome = None
        if created:
            self._cycle_count += 1
        self._cycle_index = cycle_index
        self._update_summary()
        self._pid_ec_seen.clear()
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self._blank()
        self._sep()
        heading = f"第 {cycle_index} 轮  {_now()}"
        self._append_plain(heading, CLR_TS)
        self._append_text_label(heading, CLR_TS, bold=True)
        self._sep()
        self._touch_record(record)

    def seed_captured(self, seed_text: str, initial_advances: int, npc: int, max_advances: int) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.seed_text = str(seed_text)
        record.detail_title = "Seed 已捕捉"
        if self._is_first_seed:
            self._is_first_seed = False
            self._original_seed_text = seed_text
            self._original_seed_advances = initial_advances
            self._ts(f"原始 Seed: {seed_text}  初始帧: {initial_advances}  NPC: {npc}  最大搜索: {max_advances}")
        else:
            record.locked_advances = None
            record.candidate_count = 0
            self._set_candidate_snapshot(record, None)
            record.detail_title = "Seed 已重新捕获"
            self._sep_thin()
            self._ts(f"重新测种，当前 Seed: {seed_text}  初始帧: {initial_advances}  (原始 Seed: {self._original_seed_text})")
            self._sep_thin()
        self._touch_record(record)

    def auto_tid_log(self, message: str) -> None:
        record = self._ensure_auto_tid_record()
        record.detail_title = "自动 TID 运行消息"
        self._ts(f"[自动TID] {message}")
        self._touch_record(record)

    def candidates_found(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None,
        *, reservation: CandidateReservation | None = None,
    ) -> bool:
        return self._record_candidates(candidates, locked_index, sync_flags, reservation,
                                       "候选搜索完成", f"搜索到 {len(candidates)} 个候选")

    def candidates_refiltered(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None,
        *, reservation: CandidateReservation | None = None,
    ) -> bool:
        return self._record_candidates(candidates, locked_index, sync_flags, reservation,
                                       "候选重新筛选完成", f"剩余 {len(candidates)} 个候选")

    def _admit_candidates(self, count: int, reservation: CandidateReservation | None):
        if not count:
            if reservation is not None:
                reservation.release_unclaimed()
            return None
        try:
            if reservation is None:
                reservation = self._archive.reserve_candidate_batch(wait=False)
            elif not reservation.valid_for(self._archive):
                reservation.release_unclaimed()
                self._show_archive_notice("候选记录预留已失效，本次未受理；请重新提交。")
                return False
        except SearchCancelled:
            self._show_archive_notice("历史正在关闭，本次候选记录未受理。")
            return False
        if reservation is None:
            self._show_archive_notice("历史候选正在归档（最多 3 批），本次未受理；请归档完成后重试。")
            return False
        return reservation

    def _record_candidates(self, candidates, locked_index, sync_flags, reservation, title: str, message: str) -> bool:
        reservation = self._admit_candidates(len(candidates), reservation)
        if reservation is False:
            return False
        try:
            record = self._ensure_record(target_label="自动定点")
            record.detail_title = title
            prepared = reservation.prepared_rows if reservation is not None else None
            locked_states = prepared.states if prepared is not None else candidates
            record.locked_advances = _get_int(locked_states[locked_index], "advances") if 0 <= locked_index < len(locked_states) else None
            record.candidate_count = len(candidates)
            self._pid_ec_seen.clear()
            self._ts(message)
            self._append_candidate_table(candidates, locked_index, sync_flags, reservation=reservation)
            self._touch_record(record)
            return True
        finally:
            if reservation is not None:
                reservation.release_unclaimed()

    def _write_candidate(
        self,
        i: int,
        state: object,
        status: str,
        locked: bool,
    ) -> None:
        adv = _get_int(state, "advances")
        line = (
            f"  候选{i + 1} ({status}) adv={adv} "
            f"EC={_state_ec(state)} PID={_state_pid(state)} "
            f"{_state_iv_text(getattr(state, 'ivs', None))} "
            f"性格={_nature_text(state)} 异色={_shiny_text(state)} "
            f"特性={_get_int(state, 'ability')} 性别={_gender_text(state)} "
            f"身高={_get_int(state, 'height')} 体重={_get_int(state, 'weight')}"
        )
        self._append_plain(line, CLR_LOCK if locked else CLR_BODY)

    def cycle_no_candidate(self) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.outcome = "无候选"
        record.detail_title = "本轮搜索结束"
        record.candidate_count = 0
        record.locked_advances = None
        self._set_candidate_snapshot(record, None)
        self._sep_thin()
        self._w(_ts("本轮结果: 无候选"), CLR_TS)
        self._sep_thin()
        self._touch_record(record)

    def target_missed(self, missed_advances: int, current_advances: int) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.detail_title = "已错过目标，正在重新搜索"
        self._add_warning(record, f"错过目标：目标 Adv {missed_advances}，当前 Adv {current_advances}")
        self._sep_thin()
        self._w(_ts(f"错过 目标 (目标帧: {missed_advances} ≤ 目前帧数: {current_advances})"), CLR_ERROR)
        self._ts("重新搜索，排除已过帧…")
        self._sep_thin()
        self._touch_record(record)

    def starter_missed(self, reason: str, used_delay: int | None = None) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.outcome = "错过目标"
        record.detail_title = "御三家选择未执行"
        self._add_warning(record, str(reason))
        delay_text = str(used_delay) if used_delay is not None else "-"
        self._w(_ts(f"本轮结果: 错过目标  使用 delay: {delay_text}  原因: {reason}"), CLR_ERROR)
        self._touch_record(record)

    def cycle_result(
        self,
        is_shiny: bool,
        interval: float | None,
        trigger_advances: int | None = None,
        used_delay: int | None = None,
    ) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.outcome = "疑似出闪" if is_shiny else "未出闪"
        record.detail_title = "本轮结束"
        if trigger_advances is not None and record.locked_advances is None:
            record.locked_advances = int(trigger_advances)
        self._sep_thin()
        interval_text = f"{interval:.3f}s" if interval is not None else "-"
        trigger_text = str(trigger_advances) if trigger_advances is not None else "-"
        delay_text = str(used_delay) if used_delay is not None else "-"
        details = f"  脚本启动 Adv: {trigger_text}  使用 delay: {delay_text}"
        if is_shiny:
            self._w(_ts(f"本轮结果: 疑似出闪  间隔: {interval_text}{details}"), CLR_SHINY)
        else:
            self._ts(f"本轮结果: 未出闪  间隔: {interval_text}{details}")
        self._sep_thin()
        self._touch_record(record)

    def attempt_result(
        self,
        loop_index: int,
        attempt_index: int,
        interval: float | None,
        trigger_advances: int | None = None,
        used_delay: int | None = None,
    ) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.detail_title = "继续尝试本轮候选"
        if trigger_advances is not None and record.locked_advances is None:
            record.locked_advances = int(trigger_advances)
        interval_text = f"{interval:.3f}s" if interval is not None else "-"
        trigger_text = str(trigger_advances) if trigger_advances is not None else "-"
        delay_text = str(used_delay) if used_delay is not None else "-"
        self._sep_thin()
        self._ts(
            f"第 {loop_index} 轮 / 第 {attempt_index} 次撞闪: 未出闪  间隔: {interval_text}"
            f"  脚本启动 Adv: {trigger_text}  使用 delay: {delay_text}"
        )
        self._ts("准备运行逃跑脚本，完成后继续搜索本轮后续候选")
        self._sep_thin()
        self._touch_record(record)

    def reverse_lookup_results(
        self, candidates: list[object], characteristic: str | None = None,
        delays: list[int] | None = None, ocr_stats: dict | None = None,
        *, reservation: CandidateReservation | None = None,
    ) -> bool:
        reservation = self._admit_candidates(len(candidates), reservation)
        if reservation is False:
            return False
        try:
            self._record_reverse_lookup_results(candidates, characteristic, delays, ocr_stats, reservation)
            return True
        finally:
            if reservation is not None:
                reservation.release_unclaimed()

    def _record_reverse_lookup_results(self, candidates, characteristic, delays, ocr_stats, reservation) -> None:
        record = self._ensure_record(target_label="自动定点")
        count = len(candidates)
        record.detail_title = "自动反查完成"
        characteristic_match_failed = bool(
            ocr_stats and ocr_stats.get("characteristic_match_failed")
        )
        if characteristic_match_failed:
            self._add_warning(record, "个性匹配失败，已忽略个性条件")
            self._ts("个性匹配失败，已忽略个性条件")
            raw_characteristic = ocr_stats.get("characteristic_raw") if ocr_stats else None
            if raw_characteristic:
                self._info(f"OCR 个性原文: {raw_characteristic}")
        if count == 0:
            self._ts("反查结果: 未找到匹配个体")
            if ocr_stats:
                nature = ocr_stats.get("nature")
                chara = ocr_stats.get("characteristic") or characteristic
                if nature:
                    self._info(f"OCR 性格: {nature}")
                if chara and not characteristic_match_failed:
                    self._info(f"OCR 个性: {chara}")
                stats = ocr_stats.get("stats", {})
                iv_min = ocr_stats.get("iv_min", [])
                iv_max = ocr_stats.get("iv_max", [])
                if stats:
                    stat_text = " / ".join(f"{n}={stats.get(n, '?')}" for n in ("HP", "攻击", "防御", "特攻", "特防", "速度"))
                    self._info(f"OCR 能力值: {stat_text}")
                if iv_min and iv_max and len(iv_min) == 6 and len(iv_max) == 6:
                    iv_text = " / ".join(
                        f"{n}={iv_min[i]}" if iv_min[i] == iv_max[i] else f"{n}={iv_min[i]}-{iv_max[i]}"
                        for i, n in enumerate(("HP", "攻击", "防御", "特攻", "特防", "速度"))
                    )
                    self._info(f"OCR 反算个体值范围: {iv_text}")
        else:
            self._ts(f"反查结果 ({count} 个匹配):")
            self._pid_ec_seen.clear()
            self._append_candidate_table(candidates, reverse=True, delays=delays, characteristic=characteristic, reservation=reservation)
        self._sep_end()
        self._touch_record(record)
