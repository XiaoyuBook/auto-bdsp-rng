"""Render the real Qt workspace using isolated settings and simulated data.

Run each DPI in a fresh process. No splitter positions are changed here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--dpi", type=int, choices=(100, 150), default=100)
parser.add_argument("--output", type=Path, default=Path("logs/ui-review/redesign"))
args = parser.parse_args()
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = str(args.dpi / 100)
os.environ["AUTO_BDSP_RNG_DISABLE_UPDATES"] = "1"
repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))

import cv2
from pytest import MonkeyPatch
from PySide6.QtCore import QPoint, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QScrollArea, QWidget

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngPhase, AutoTidRngProgress
from auto_bdsp_rng.gen8_id.models import IDState8
from auto_bdsp_rng.rng_core import SeedState32
from tests.test_start_readiness import window


def settle(w):
    for _ in range(8):
        QApplication.processEvents()
        QTest.qWait(10)
    w._refresh_preview_presentation()


def geometry(widget):
    return [widget.width(), widget.height()]


def populate(w):
    seed = SeedState32(0x12345678, 0x9ABCDEF0, 0x13579BDF, 0x2468ACE0)
    for box, word in zip(w.seed32_inputs, seed.format_words()):
        box.setText(word)
    w._sync_seed64_from_state32()
    for target, source in zip(w.bdsp_seed64_inputs, w.seed64_outputs):
        target.setText(source.text())
    w.initial_advances.setText("12500")
    w.max_advances.setText("24")
    w.generate_results()  # Real, bounded generation; no hardware.
    assert w.table.rowCount() > 0, "The real static generation must complete."
    w.auto_rng_tab.apply_progress(AutoRngProgress(
        phase=AutoRngPhase.FINAL_WAIT, loop_index=2, current_advances=12568,
        raw_target_advances=13048, remaining_to_trigger=480, log_message="等待启动帧，当前候选已锁定。"))
    w.auto_rng_tab.set_candidate_targets(w._states, locked_index=2)
    tid = w.auto_tid_rng_tab
    tid.add_target_display_tid(123456)
    tid.add_target_display_tid(777777)
    tid.set_tid_seed(seed, generate=False)
    tid.apply_progress(AutoTidRngProgress(phase=AutoTidRngPhase.WAIT_NAME_TRIGGER, loop_index=2,
                                        current_advances=12568, target_advances=12740,
                                        trigger_advances=12720, wait_target_at=time.monotonic() + 86,
                                        target_display_tid=123456, remaining_to_trigger=172, log_message="等待目标 TID 的启动帧。"))
    tid.set_id_states([IDState8(12500 + i * 80, 32000 + i, 48000 + i, 1032, 123456 if i == 3 else 700000 + i)
                       for i in range(24)], elapsed_seconds=tuple(i * 4.5 for i in range(24)), measured_wall_time=1791525600)
    w.easycon_tab.editor.setPlainText("# 演示脚本：不连接设备，不执行\n" + "\n".join(
        f"WAIT {100 + i * 10}  # 第 {i + 1} 步" for i in range(28)))
    w.easycon_tab.script_name_label.setText("bdsp-workspace-demo.txt")
    h = w.history_tab
    h.begin_run("visual-review", "帝牙卢卡 · 异色 / 性格 / 个体值")
    for index in (1, 2):
        h.cycle_start(index)
        h.seed_captured(" / ".join(box.text() for box in w.seed64_outputs), 12500, 0, 300000)
        h.candidates_found(w._states[:8], 2)
    w.run_records_tab.set_active_round(2)
    for index in range(18):
        w._run_log_buffer.publish("自动定点" if index % 2 else "Seed 捕捉",
            f"演示消息 {index + 1}：已捕获完整 Seed 并生成候选，保留当前配置。长消息检查自动换行、来源筛选、复制及日志中心检索。",
            "WARNING" if index == 16 else "INFO")
    w.video_overlay.set_capture_progress(27, 40)
    w.video_overlay.set_match_score(.9826)
    w._update_auto_rng_header(advances=12568)


def capture(w, name, page, state, width, height, out):
    w.tabs.setCurrentWidget(page)
    w.easycon_tab.hide_tools()
    w.easycon_tab.output_toggle.setChecked(name == "script-output")
    w.capture_advanced_button.setChecked(name == "seed-advanced")
    w.auto_capture_config_toggle.setChecked(name == "seed-auto")
    w.history_tab.meta_toggle.setChecked(name == "records-meta")
    w.history_tab.feed_toggle.setChecked(name == "records-feed")
    if name.startswith("auto-"):
        page.local_views.setCurrentIndex(int(name == "auto-runtime"))
    elif name.startswith("tid-"):
        page.local_views.setCurrentIndex(int(name == "tid-runtime"))
    elif name.startswith("data"):
        w.query_toggle.setChecked(name != "data-results")
        if name != "data-results":
            w.query_groups.setCurrentIndex({"data-rng": 0, "data-encounter": 1, "data-filter": 2}.get(name, 0))
        w.bdsp_config_scroll.verticalScrollBar().setValue(0)
    elif name == "details":
        w.run_records_tab.show_logs(None)
    elif name.startswith("records"):
        w.run_records_tab.show_rounds()
        w.history_tab.round_picker_button.setChecked(name == "records-list")
        w.history_tab.detail_scroll.verticalScrollBar().setValue(0)
    elif name in ("script-control", "script-library"):
        w.easycon_tab.show_tools("control" if name == "script-control" else "library")
    settle(w)
    if name in ("records-meta", "records-feed"):
        target = w.history_tab.seed_value_label if name == "records-meta" else w.history_tab.history_scroll
        w.history_tab.detail_scroll.ensureWidgetVisible(target, 8, 8)
        settle(w)
    filename = f"{state}-{name}-{width}x{height}-{args.dpi}.png"
    w.grab().save(str(out / filename))
    scrolls = []
    for area in page.findChildren(QScrollArea):
        if area.isVisible():
            scrolls.append({"name": area.objectName() or area.widget().objectName(),
                           "size": geometry(area), "content": geometry(area.widget()),
                           "horizontal_maximum": area.horizontalScrollBar().maximum(),
                           "vertical_maximum": area.verticalScrollBar().maximum()})
    overflow = []
    for control in page.findChildren(QWidget):
        if not isinstance(control, (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit)):
            continue
        if not control.isVisible():
            continue
        bounds = QRect(control.mapTo(page, QPoint()), control.size())
        # Vertical clipping within a scroll area is intentional; horizontal
        # clipping of forms/actions is not.
        if bounds.left() < 0 or bounds.right() >= page.width():
            overflow.append({"name": control.objectName() or control.accessibleName() or type(control).__name__,
                             "bounds": [bounds.x(), bounds.y(), bounds.width(), bounds.height()]})
    return {"file": filename, "state": state, "page": name, "requested": [width, height],
            "window": geometry(w), "dpr": w.devicePixelRatioF(), "left": geometry(w.tabs),
            "right": geometry(w.monitor_sidebar), "video": geometry(w.preview_label),
            "preview_panel": geometry(w.monitor_preview_scroll), "live_logs": geometry(w.live_log_panel),
            "live_log_text_viewport": geometry(w.live_log_panel.text.viewport()),
            "preview_scroll_maximum": [w.monitor_preview_scroll.horizontalScrollBar().maximum(),
                                       w.monitor_preview_scroll.verticalScrollBar().maximum()],
            "video_image": [w.preview_label._pixmap_rect.width(), w.preview_label._pixmap_rect.height()],
            "overlay_seed": [w.seed_group.x(), w.seed_group.y(), w.seed_group.width(), w.seed_group.height()],
            "overlay_status": [w.video_overlay.status_panel.x(), w.video_overlay.status_panel.y(),
                               w.video_overlay.status_panel.width(), w.video_overlay.status_panel.height()],
            "scrolls": scrolls, "horizontal_overflow": overflow}


out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
report = []
with tempfile.TemporaryDirectory(prefix="bdsp-workspace-review-") as temp, MonkeyPatch.context() as mp:
    fixture = window.__wrapped__(mp, Path(temp))
    w = next(fixture)
    mp.setattr(w, "_screen_available_geometry", lambda: QRect(0, 0, 2400, 1600))
    mp.setattr(w, "_show_error", lambda title, error: (_ for _ in ()).throw(error))
    frame = cv2.imread(str(repo / "docs/assets/guide-eye/screenshot.jpg"))
    frame = cv2.resize(frame[574:1150, 518:1542], (1280, 720))
    w._latest_preview_frame = frame
    w._latest_annotated_preview_frame = frame.copy()
    w.monitor_source_status.setText("模拟帧 · 界面验收")
    w.monitor_frame_info.setText("1280 × 720")
    empty_pages = [("auto-settings", w.auto_rng_tab), ("tid-settings", w.auto_tid_rng_tab),
                   ("seed", w.project_xs_tab), ("data-rng", w.bdsp_tab), ("script", w.easycon_tab),
                   ("records", w.run_records_tab), ("details", w.run_records_tab)]
    sizes = [(860, 600), (1150, 900), (1440, 960), (854, 480)]
    for state in ("empty", "populated"):
        if state == "populated":
            populate(w)
        pages = empty_pages if state == "empty" else [
            ("auto-settings", w.auto_rng_tab), ("auto-runtime", w.auto_rng_tab),
            ("tid-settings", w.auto_tid_rng_tab), ("tid-runtime", w.auto_tid_rng_tab),
            ("seed", w.project_xs_tab), ("data-results", w.bdsp_tab), ("data-rng", w.bdsp_tab),
            ("data-encounter", w.bdsp_tab), ("data-filter", w.bdsp_tab), ("script", w.easycon_tab),
            ("records", w.run_records_tab), ("details", w.run_records_tab),
            ("script-control", w.easycon_tab), ("script-library", w.easycon_tab), ("script-output", w.easycon_tab),
            ("records-list", w.run_records_tab), ("records-meta", w.run_records_tab), ("records-feed", w.run_records_tab),
            ("seed-auto", w.project_xs_tab), ("seed-advanced", w.project_xs_tab)]
        for width, height in sizes:
            w.resize(width, height)
            for name, page in pages:
                report.append(capture(w, name, page, state, width, height, out))
    w.easycon_tab._saved_editor_text = w.easycon_tab.editor.toPlainText()
    try:
        next(fixture)
    except StopIteration:
        pass
(out / f"dimensions-{args.dpi}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"output": str(out), "captures": len(report),
                  "overflows": [(row["file"], row["horizontal_overflow"]) for row in report if row["horizontal_overflow"]]}, ensure_ascii=False))
