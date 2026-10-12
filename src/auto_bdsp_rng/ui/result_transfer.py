"""Bounded, cancellable preparation of complete result copies and exports."""
from __future__ import annotations

import csv
import io
import os
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject

from auto_bdsp_rng.rng_core.cancellation import SearchCancellation
from auto_bdsp_rng.ui.background_search import BackgroundSearch


SYNCHRONOUS_RESULT_LIMIT = 1000


def _rows_with_cancellation(rows, cancellation):
    for index, row in enumerate(rows):
        if cancellation is not None and index % 1024 == 0:
            cancellation.check()
            # Date/characteristic formatting is Python work. Yield explicitly
            # while GC stays enabled, including on the first cold-cache copy.
            time.sleep(0.001)
        yield row
    if cancellation is not None:
        cancellation.check()


def prepare_result_text(rows, cancellation=None):
    return "\n".join("\t".join(row) for row in _rows_with_cancellation(rows, cancellation))


class PreparedExport:
    def __init__(self, temporary: str, destination: Path):
        self.temporary = temporary
        self.destination = destination

    def commit(self, cancellation=None):
        if cancellation is not None:
            cancellation.check()
        # Replacement is the commit point. Preparation never opens the user's
        # existing destination, and a failed replacement leaves it intact.
        try:
            os.replace(self.temporary, self.destination)
        except BaseException:
            # Error tracebacks can retain this object long after delivery.
            # Clean explicitly rather than waiting for its destructor/GC.
            self.discard()
            raise
        self.temporary = None
        return self.destination

    def discard(self):
        temporary, self.temporary = self.temporary, None
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def __del__(self):
        # A superseded BackgroundSearch result is never delivered to the GUI.
        # It still owns its staging file and must remove it when discarded.
        try:
            self.discard()
        except OSError:
            pass


def prepare_result_export(rows, destination, *, csv_format, cancellation=None):
    destination = Path(destination)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8", dir=destination.parent,
            prefix=".rng-export-", suffix=".tmp", delete=False,
        ) as handle:
            staged = PreparedExport(handle.name, destination)
            output = handle.file
            buffer = io.StringIO(newline="")
            if csv_format:
                writer = csv.writer(buffer)
                for index, row in enumerate(_rows_with_cancellation(rows, cancellation)):
                    writer.writerow(row)
                    if index % 1024 == 1023:
                        output.write(buffer.getvalue())
                        buffer.seek(0)
                        buffer.truncate(0)
            else:
                for index, row in enumerate(_rows_with_cancellation(rows, cancellation)):
                    if index:
                        buffer.write("\n")
                    buffer.write("\t".join(row))
                    if index % 1024 == 1023:
                        output.write(buffer.getvalue())
                        buffer.seek(0)
                        buffer.truncate(0)
            # Buffered chunks avoid a file write/GIL handoff per result row.
            output.write(buffer.getvalue())
        if cancellation is not None:
            cancellation.check()
        return staged
    except BaseException:
        if staged is not None:
            staged.discard()
        raise


class ResultTransfer(QObject):
    """One worker and at most one latest request, separate from result search.

    Row factories are captured on Qt and contain only immutable Python inputs.
    Export preparation is acknowledged on Qt before a second worker operation
    commits it; cancelling before that acknowledgement preserves the old file.
    """

    def __init__(self, parent, *, changed, copied, exported, failed):
        # The application keeps an accepted commit alive even if an owner is
        # explicitly deleted. Normal window close waits for its terminal state.
        super().__init__(QCoreApplication.instance() or parent)
        self._search = BackgroundSearch(self)
        self.kind = None
        self.count = 0
        self.generation = 0
        self.committing = False
        self._ignore_completion = False
        self._changed = changed
        self._copied = copied
        self._exported = exported
        self._failed = failed
        self._closed = False
        self._orphaned = False
        self.last_outcome = None
        parent.destroyed.connect(self._owner_destroyed)

    def _owner_destroyed(self):
        if self._closed:
            return
        if self.committing:
            self._orphaned = True
            self._changed = lambda: None
            self._copied = self._exported = self._failed = lambda *args: None
        else:
            self.close()

    def _begin(self, kind, count):
        if self._closed or self.committing:
            return False
        self._search.cancel()
        self.generation += 1
        self.kind, self.count = kind, count
        self._ignore_completion = False
        self._changed()
        return True

    def copy(self, rows, count):
        if not self._begin("copy", count):
            return
        if count <= SYNCHRONOUS_RESULT_LIMIT:
            try:
                self._copy_finished(prepare_result_text(rows()))
            except Exception as error:
                self._finish_failed(error)
        else:
            self._search.submit(
                lambda token: prepare_result_text(rows(), token),
                self._copy_finished, self._finish_failed,
            )

    def export(self, rows, count, destination, *, csv_format=True):
        if not self._begin("export", count):
            return
        destination = Path(destination)
        if count <= SYNCHRONOUS_RESULT_LIMIT:
            try:
                staged = prepare_result_export(rows(), destination, csv_format=csv_format)
                self._export_finished(staged.commit())
            except Exception as error:
                self._finish_failed(error)
        else:
            self._submit_export(
                lambda token: prepare_result_export(rows(), destination, csv_format=csv_format, cancellation=token),
            )

    def prepare_export(self, work, count):
        """Prepare a custom export off-thread, returning a PreparedExport."""
        if self._begin("export", count):
            self._submit_export(work)

    def _submit_export(self, work):
        self._search.submit(work, self._export_prepared, self._finish_failed)

    def _export_prepared(self, staged):
        # Poll only calls this for the current generation on the GUI thread.
        # The filesystem commit itself stays off the GUI thread.
        # This GUI acknowledgement is the commit boundary. Cancellation is
        # no longer offered once an export is accepted for atomic replacement.
        # In particular, never cancel a token between its final check and the
        # rename and then misleadingly report that the old file was preserved.
        self.committing = True
        self._changed()
        self._search.submit(lambda _token: staged.commit(), self._export_finished, self._finish_failed)

    def _finish(self):
        self.kind = None
        self.committing = False
        self._changed()

    def _copy_finished(self, text):
        count = self.count
        self._finish()
        self._copied(text, count)

    def _export_finished(self, destination):
        self.last_outcome = ("exported", destination)
        ignored = self._ignore_completion
        self._finish()
        if not ignored:
            self._exported(destination)
        if self._orphaned:
            self.close()

    def _finish_failed(self, error):
        self.last_outcome = ("failed", error)
        ignored = self._ignore_completion
        self._finish()
        if not ignored:
            self._failed(error)
        if self._orphaned:
            self.close()

    def cancel(self, *, preserve_commit_notice=False):
        self.generation += 1
        if self.committing:
            # A new domain task may suppress the old completion notice, but
            # cannot revoke a file commit already accepted by Qt.
            if not preserve_commit_notice:
                self._ignore_completion = True
            return False
        self._search.cancel()
        if not self._closed:
            self._finish()
        return True

    def close(self):
        if self._closed:
            return True
        if self.committing:
            return False
        self._closed = True
        self._search.close()
        self.kind = None
        self._changed = self._copied = self._exported = self._failed = None
        self.deleteLater()
        return True
