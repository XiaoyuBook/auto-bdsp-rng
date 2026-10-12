"""Render paged static/TID results with isolated settings and no hardware.

Run in a fresh process for each scale, for example:
python scripts/render_search_results_review.py --dpi 125
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dpi", type=int, choices=(100, 125, 150), default=100)
    parser.add_argument("--output", type=Path, default=Path("logs/ui-review/search-performance"))
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = str(args.dpi / 100)
    os.environ["AUTO_BDSP_RNG_DISABLE_UPDATES"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from pytest import MonkeyPatch
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from auto_bdsp_rng.gen8_id import IDState8
    from tests.test_search_performance import _finish, _states
    from tests.test_start_readiness import window
    from tests.ui_wait import wait_search

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = []
    with tempfile.TemporaryDirectory(prefix="bdsp-search-review-") as temp, MonkeyPatch.context() as mp:
        fixture = window.__wrapped__(mp, Path(temp))
        w = next(fixture)
        mp.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 2400, 1600))
        _finish(w, _states(100_000))
        w.static_table_tools.set_pinned(True)
        w.table.pager.set_page(3)
        w.table.setCurrentCell(27, 8)
        tid = w.auto_tid_rng_tab
        tid._clear_targets()
        tid.add_target_display_tid(3)
        tid.set_id_states([IDState8(i, i % 65536, 20, 1, i % 17) for i in range(100_000)],
                          elapsed_seconds=tuple(i * 1.01 for i in range(100_000)),
                          measured_wall_time=1_700_000_000)
        wait_search(tid.id_table.pager._search)
        tid.id_table.pager.set_page(3)
        tid.update_tid_current_advances(650)
        tid.local_views.setCurrentIndex(1)
        for width, height in ((860, 600), (1150, 900)):
            w.resize(width, height)
            for name, page, table in (("static", w.bdsp_tab, w.table), ("tid", tid, tid.id_table)):
                w.tabs.setCurrentWidget(page)
                QApplication.processEvents()
                QTest.qWait(80)
                pager = table.pager
                if name == "tid":
                    # The existing runtime form scrolls vertically at 860x600.
                    # Verify that its pagination controls remain reachable.
                    tid.runtime_scroll.ensureWidgetVisible(pager.controls, 0, 8)
                    QApplication.processEvents()
                    QTest.qWait(20)
                assert table.rowCount() == 200 and pager.total == 100_000
                assert pager.controls.isVisible()
                bounds = QRect(pager.controls.mapTo(w, QPoint()), pager.controls.size())
                assert w.rect().contains(bounds), (name, bounds, w.rect())
                assert pager.previous_button.isVisible() and pager.next_button.isVisible()
                filename = f"{name}-{width}x{height}-{args.dpi}.png"
                assert w.grab().save(str(output / filename))
                report.append({"file": filename, "requested": [width, height],
                               "window": [w.width(), w.height()], "dpr": w.devicePixelRatioF(),
                               "table": [table.width(), table.height()],
                               "pager": [bounds.x(), bounds.y(), bounds.width(), bounds.height()],
                               "page": pager.page + 1, "pages": pager.page_count,
                               "rows_in_qt": table.rowCount(), "full_results": pager.total})
        try:
            next(fixture)
        except StopIteration:
            pass
    (output / f"dimensions-{args.dpi}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "captures": report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
