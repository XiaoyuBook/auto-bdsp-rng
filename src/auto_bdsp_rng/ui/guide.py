"""Resumable spotlights on the existing workspace and its settings dialogs."""
from __future__ import annotations

import math

from shiboken6 import isValid
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF, QRegion
from PySide6.QtWidgets import (
    QApplication, QDialog, QMenu, QMessageBox, QScrollArea, QStyle,
    QStyleOptionTab, QToolButton, QWidget,
)

from auto_bdsp_rng.app_settings import GUIDE_STEPS, LEGACY_GUIDE_STEPS, advance_guide_progress, get_guide_progress, start_guide_progress
from auto_bdsp_rng.ui.guide_steps import GuideStep, connection_dialog_steps, dialog_steps, workspace_step
from auto_bdsp_rng.ui.guide_tip import GuideTip
from auto_bdsp_rng.ui.eye_guide import EyeGuide
from auto_bdsp_rng.ui.script_guide import ScriptGuide
from auto_bdsp_rng.ui.easycon_record_demo_dialog import EasyConRecordDemoDialog


class GuideSpotlight(QWidget):
    paused = Signal()

    def __init__(self, window, dialog: QDialog | None = None) -> None:
        super().__init__(dialog if dialog is not None else window.centralWidget())
        self.main_window = window
        self.dialog = dialog
        self.target_button = window.auto_rng_tab.target_button
        self.target_card = self.target_button.parentWidget()
        self.tab_bar = window.tabs.tabBar()
        self.page_widget = window.auto_rng_tab
        self.waiting_for_page = False
        self.suspended = False
        self.setObjectName("GuideSpotlight")
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hole = QRectF()
        self.holes: tuple[QRectF, ...] = ()
        self.arrow_start = QPointF()
        self.arrow_end = QPointF()
        self.tip = GuideTip(self)
        self.close_button = self.tip.close_button
        self.title, self.copy = self.tip.title, self.tip.copy
        self.close_button.clicked.connect(self.paused)
        self.spec = workspace_step(window.auto_rng_tab, "target_selection")
        self.relayout = QTimer(self)
        self.relayout.setSingleShot(True)
        self.relayout.timeout.connect(self.reposition)
        self.tip.resized.connect(lambda: self.relayout.start(0))
        self._watched: set[QWidget] = set()
        self.hide()

    @property
    def focus_target(self) -> QWidget:
        return self.tab_bar if self.waiting_for_page else self.spec.target

    def configure(self, spec: GuideStep, *, waiting_for_page: bool = False) -> None:
        self.spec = spec
        self.waiting_for_page = waiting_for_page
        self.suspended = False
        self.tip.show_step(spec, search=spec.key == "search_range" and not waiting_for_page)
        if waiting_for_page:
            page_name = self.main_window.tabs.tabText(self.main_window.tabs.indexOf(self.page_widget))
            self.title.setText("先进入操作页面")
            self.copy.setText(f"点击亮起的「{page_name}」标签，进入这次乱数的操作页面。")
        for widget in self._watched:
            if isValid(widget):
                widget.removeEventFilter(self)
        self._watched.clear()
        for target in (self.tab_bar,) if waiting_for_page else spec.highlights:
            ancestor = target
            while ancestor is not None:
                if ancestor not in self._watched:
                    ancestor.installEventFilter(self)
                    self._watched.add(ancestor)
                ancestor = ancestor.parentWidget()
        # Expanding the advanced rows changes the scroll area's content height
        # asynchronously. The controller schedules a second pass after layout.

    def reveal(self) -> None:
        self.suspended = False
        self.show()
        self.raise_()
        if self.reposition():
            self.tip.show()
            self.tip.raise_()
        target = self.focus_target
        if target.focusPolicy() == Qt.FocusPolicy.NoFocus:
            target = next((child for child in target.findChildren(QWidget)
                           if child.isVisible() and child.isEnabled() and child.focusPolicy() != Qt.FocusPolicy.NoFocus), self.close_button)
        target.setFocus(Qt.FocusReason.OtherFocusReason)
        self.relayout.start(0)

    def shade_all(self) -> None:
        self.suspended = True
        self.tip.hide()
        self.reposition()

    def hideEvent(self, event) -> None:
        self.tip.hide()
        self.relayout.stop()
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        if self.isVisible() and event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.relayout.start(0)
        return False

    def _target_rect(self, widget: QWidget) -> QRectF:
        if widget is self.tab_bar:
            index = self.main_window.tabs.indexOf(self.page_widget)
            option = QStyleOptionTab()
            self.tab_bar.initStyleOption(option, index)
            style = self.tab_bar.style()
            text_area = style.subElementRect(QStyle.SubElement.SE_TabBarTabText, option, self.tab_bar)
            # tabRect includes the trailing 25px gap. Frame the actual text.
            rect = style.itemTextRect(
                option.fontMetrics, text_area, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextShowMnemonic,
                bool(option.state & QStyle.StateFlag.State_Enabled), option.text,
            ).adjusted(-10, -8, 10, 8)
            origin = self.mapFromGlobal(self.tab_bar.mapToGlobal(rect.topLeft()))
            return QRectF(QRect(origin, rect.size()))
        origin = self.mapFromGlobal(widget.mapToGlobal(QPoint()))
        rect = QRectF(QRect(origin, widget.size())).adjusted(-4, -4, 4, 4)
        ancestor = widget.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                viewport = ancestor.viewport()
                origin = self.mapFromGlobal(viewport.mapToGlobal(QPoint()))
                rect = rect.intersected(QRectF(QRect(origin, viewport.size())))
            ancestor = ancestor.parentWidget()
        return rect

    def _ensure_target_visible(self) -> None:
        """Follow the current anchor after layout/scroll changes, without stale timers."""
        if self.waiting_for_page:
            return
        target = self.focus_target
        if self.spec.script_editing:
            easycon = self.main_window.easycon_tab
            easycon.sidebar_scroll.ensureWidgetVisible(easycon.record_btn, 12, 12)
        ancestor = target.parentWidget()
        ancestors = []
        while ancestor is not None:
            ancestors.append(ancestor)
            ancestor = ancestor.parentWidget()
        for ancestor in reversed(ancestors):
            if ancestor.layout() is not None:
                ancestor.layout().activate()
        for ancestor in ancestors:
            if isinstance(ancestor, QScrollArea):
                ancestor.ensureWidgetVisible(target, 12, 12)

    def _editing_tip_rect(self, bounds, gap):
        """Fit the card in a free column while keeping every editing tool open."""
        holes = [self._target_rect(w) for w in self.spec.highlights if w.isVisible()]
        width = self.tip.width()
        xs = {bounds.left(), bounds.right() - width + 1}
        for rect in holes:
            xs.update((int(rect.right()) + gap, int(rect.left()) - width - gap))
        spaces = []
        for x in sorted(xs):
            if x < bounds.left() or x + width > bounds.right() + 1:
                continue
            intervals = sorted((max(bounds.top(), int(r.top()) - gap),
                                min(bounds.bottom() + 1, int(r.bottom()) + gap))
                               for r in holes if r.right() > x and r.left() < x + width)
            top = bounds.top()
            for start, end in intervals + [(bounds.bottom() + 1, bounds.bottom() + 1)]:
                if start > top:
                    spaces.append((start - top, x, top))
                top = max(top, end)
        for available, x, y in sorted(spaces, key=lambda r: (-r[0], r[1])):
            self.tip.fit_height(available)
            if self.tip.height() <= available:
                hole = QRectF()
                for rect in holes:
                    hole = hole.united(rect)
                self.hole = hole
                return QRect(x, y, width, self.tip.height())
        return None

    def reposition(self) -> bool:
        self.setGeometry(self.parentWidget().rect())
        if self.suspended:
            self.hole = QRectF()
            self.holes = ()
            self.setMask(QRegion(self.rect()))
            self.update()
            return True
        self._ensure_target_visible()
        margin = 20 if self.dialog is not None else 12
        bounds = self.rect().adjusted(margin, margin, -margin, -margin)
        script_running = (self.spec.key == "auto_script_config" and not self.waiting_for_page
                          and self.spec.separate_highlights)
        width = 250 if script_running else 300 if self.spec.target is self.main_window.preview_label else (400 if self.dialog is not None else 338)
        side_gap = 10 if script_running else 44
        self.tip.setFixedWidth(min(width, bounds.width()))
        self.tip.fit_height(bounds.height())
        chosen = None
        target_groups = ((self.tab_bar,),) if self.waiting_for_page else (self.spec.highlights, (self.spec.target,))
        if self.spec.script_editing and not self.waiting_for_page:
            chosen = self._editing_tip_rect(bounds, side_gap)
            if chosen is not None:
                target_groups = ()
                group = self.spec.highlights
        for group in target_groups:
            hole = QRectF()
            for target in group:
                if target.isVisible():
                    hole = hole.united(self._target_rect(target))
            hole = hole.intersected(QRectF(self.rect()))
            if hole.isEmpty():
                continue
            width, height = self.tip.width(), self.tip.height()
            x = max(bounds.left(), min(int(hole.center().x()) - width // 2, bounds.right() - width + 1))
            y = max(bounds.top(), min(int(hole.top()), bounds.bottom() - height + 1))
            candidates = (
                QRect(int(hole.right()) + side_gap, y, width, height),
                QRect(int(hole.left()) - width - side_gap, y, width, height),
                QRect(x, int(hole.bottom()) + 24, width, height),
                QRect(x, int(hole.top()) - height - 24, width, height),
            )
            if self.waiting_for_page or self.dialog is not None:
                candidates = candidates[2:] + candidates[:2]
            chosen = next((r for r in candidates if bounds.contains(r)), None)
            if chosen is None:
                below = bounds.bottom() - int(hole.bottom()) - 24
                above = int(hole.top()) - bounds.top() - 24
                available = max(above, below)
                if available >= 210:
                    self.tip.fit_height(available)
                    height = self.tip.height()
                    chosen = QRect(x, int(hole.bottom()) + 24 if below >= above else int(hole.top()) - height - 24, width, height)
            if chosen is not None:
                self.hole = hole
                break
        if chosen is None:
            # Do not draw an opening at an unclipped coordinate: it could expose
            # an unrelated control while the anchor is outside its viewport.
            self.hole = QRectF()
            self.holes = ()
            self.tip.hide()
            self.setMask(QRegion(self.rect()))
            self.update()
            return False
        self.tip.setGeometry(chosen)
        if self.isVisible():
            self.tip.show()
        target_center = self._target_rect(self.focus_target).center()
        arrow_rect = self._target_rect(self.focus_target) if self.spec.script_editing else self.hole
        if chosen.left() > arrow_rect.right():
            y = max(chosen.top() + 20, min(target_center.y(), chosen.bottom() - 20))
            self.arrow_start = QPointF(min(chosen.left(), self.width() - 3), y)
            self.arrow_end = QPointF(arrow_rect.right() + 7, target_center.y())
        elif chosen.right() < arrow_rect.left():
            y = max(chosen.top() + 20, min(target_center.y(), chosen.bottom() - 20))
            self.arrow_start = QPointF(max(chosen.right(), 3), y)
            self.arrow_end = QPointF(arrow_rect.left() - 7, target_center.y())
        else:
            x = max(chosen.left() + 20, min(target_center.x(), chosen.right() - 20))
            below = chosen.top() > arrow_rect.bottom()
            self.arrow_start = QPointF(x, chosen.top() if below else chosen.bottom())
            self.arrow_end = QPointF(target_center.x(), arrow_rect.bottom() + 7 if below else arrow_rect.top() - 7)
        self.holes = tuple(self._target_rect(widget).intersected(QRectF(self.rect()))
                           for widget in group if widget.isVisible()) if self.spec.separate_highlights and not self.waiting_for_page else (self.hole,)
        opening = QPainterPath()
        opening.setFillRule(Qt.FillRule.WindingFill)
        for rect in self.holes:
            opening.addRoundedRect(rect.adjusted(2, 2, -2, -2), 9, 9)
        self.setMask(QRegion(self.rect()).subtracted(QRegion(opening.toFillPolygon().toPolygon())))
        self.update()
        return True

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        opening = QPainterPath()
        opening.setFillRule(Qt.FillRule.WindingFill)
        for rect in self.holes:
            opening.addRoundedRect(rect, 11, 11)
        shade = shade.subtracted(opening)
        painter.fillPath(shade, QColor(0, 0, 0, 168))
        if self.hole.isEmpty():
            return
        painter.setPen(QPen(QColor("#83E8BF"), 2))
        painter.drawPath(opening)
        painter.setPen(QPen(QColor("#A2EBCD"), 2))
        painter.drawLine(self.arrow_start, self.arrow_end)
        delta = self.arrow_end - self.arrow_start
        unit = delta / max(1, math.hypot(delta.x(), delta.y()))
        base = self.arrow_end - unit * 7
        cross = QPointF(-unit.y(), unit.x()) * 4
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#A2EBCD"))
        painter.drawPolygon(QPolygonF([self.arrow_end, base + cross, base - cross]))


class GuideController(QObject):
    def __init__(self, window, button: QToolButton) -> None:
        super().__init__(window)
        self.window, self.button = window, button
        self.panel = window.auto_rng_tab
        self.overlay: GuideSpotlight | None = None
        self.dialog_overlay: GuideSpotlight | None = None
        self.active = False
        self.step = "target_selection"
        self.detail = ""
        self._dialog_key: str | None = None
        self._resume_after_dialog = False
        self._config_saved = False
        self._dialog_layout_state = None
        self._connection_timer = QTimer(self)
        self._connection_timer.setInterval(250)
        self._connection_timer.timeout.connect(self._connection_status_changed)
        self.panel.window().easycon_tab.connectionPresentationChanged.connect(self._connection_status_changed)
        self.menu = QMenu(button)
        self.restart_action = self.menu.addAction("重新开始引导", self.restart)
        button.clicked.connect(self.begin_or_resume)
        self.panel.targetDialogOpened.connect(self._target_opened)
        self.panel.targetDialogClosed.connect(self._target_closed)
        self.panel.guideDialogOpened.connect(self._dialog_opened)
        self.panel.guideDialogClosed.connect(self._dialog_closed)
        self.panel.configSaved.connect(self._saved)
        self.panel.configSaveFailed.connect(self._save_failed)
        self.panel.configEdited.connect(self._config_edited)
        self.panel.runStateChanged.connect(self._run_state_changed)
        self.panel.delay_strategy_dialog.strategy_combo.currentIndexChanged.connect(self._dialog_values_changed)
        self.panel.strategy_dialog.reidentify_failure_policy.currentIndexChanged.connect(self._dialog_values_changed)
        window.tabs.currentChanged.connect(self._page_changed)
        window.picture_in_picture_button.clicked.connect(self._preview_opened)
        window.video_source_header_button.clicked.connect(lambda: self._connection_dialog_opened("video_source"))
        window.easycon_header_button.clicked.connect(lambda: self._connection_dialog_opened("easycon"))
        self.eye_guide = EyeGuide(self)
        self.script_guide = ScriptGuide(self)
        self.easycon_demo_dialog: EasyConRecordDemoDialog | None = None
        self.refresh()

    def refresh(self) -> None:
        resumable = get_guide_progress() is not None
        self.button.setText("继续引导" if resumable else "开始引导")
        self.button.setMenu(self.menu if resumable else None)
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup if resumable else QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.button.setProperty("resumable", resumable)
        self.button.setAccessibleName(self.button.text())
        self.button.setToolTip("接着上次的步骤继续；右侧箭头可重新开始" if resumable else "从设置目标精灵开始一次新的引导")
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)
        self.button.update()

    def begin_or_resume(self) -> None:
        self._begin(restart=False)

    def restart(self) -> None:
        self._begin(restart=True)

    def _begin(self, *, restart: bool) -> None:
        if self.window._is_closing or not self.button.isEnabled():
            return
        try:
            progress = get_guide_progress()
            new_session = restart or progress is None
            if new_session:
                progress = start_guide_progress()
        except OSError:
            QMessageBox.warning(self.window, "无法开始引导", "无法保存引导进度，请检查设置目录是否可写后重试。")
            return
        self.step = progress["step"]
        self.detail = progress.get("detail", "") if isinstance(progress.get("detail", ""), str) else ""
        # Older builds used a visible task_configured page after saving. It is
        # now only a compatibility marker; resume directly at step three.
        if self.step == "task_configured":
            try:
                progress = advance_guide_progress("connect_devices", "video_source")
            except (OSError, ValueError):
                QMessageBox.warning(self.window, "无法继续引导", "无法更新引导进度，请检查设置目录是否可写后重试。")
                return
            self.step = progress["step"]
            self.detail = progress["detail"]
        elif self.step == "capture_overview_done":
            try:
                progress = advance_guide_progress("auto_flow_config")
            except (OSError, ValueError):
                QMessageBox.warning(self.window, "无法继续引导", "无法更新引导进度，请检查设置目录是否可写后重试。")
                return
            self.step = progress["step"]
            self.detail = progress.get("detail", "")
        elif self.step == "easycon_recording" and self.detail == "preview":
            # This session already finished the introduction and recording.
            # The preview now precedes them; resume at script configuration.
            try:
                progress = advance_guide_progress("auto_script_config")
            except (OSError, ValueError):
                QMessageBox.warning(self.window, "无法继续引导", "无法更新引导进度，请检查设置目录是否可写后重试。")
                return
            self.step = progress["step"]
            self.detail = progress.get("detail", "")
        elif self.step in LEGACY_GUIDE_STEPS:
            try:
                progress = advance_guide_progress("seed_capture_config")
            except (OSError, ValueError):
                QMessageBox.warning(self.window, "无法继续引导", "无法更新引导进度，请检查设置目录是否可写后重试。")
                return
            self.step = progress["step"]
            self.detail = progress.get("detail", "")
        self.active = True
        if self.step == "easycon_recording" and not self.detail:
            self.detail = "demo"
        self._connection_timer.start()
        self.refresh()
        if self.overlay is None:
            self.overlay = self._new_overlay()
            self.overlay.tip.range_field = self.panel.max_advances
            self.panel.max_advances.valueChanged.connect(self.overlay.tip.sync_range)
        if new_session:
            self.overlay.tip.reset_range()
        if self.step == "task_configured" and not self.panel.config_saved_label.property("saved"):
            if not self._persist("save_config"):
                self.pause()
                return
        QApplication.instance().installEventFilter(self)
        self._show_workspace()

    def _new_overlay(self, dialog=None) -> GuideSpotlight:
        overlay = GuideSpotlight(self.window, dialog)
        overlay.paused.connect(self.pause)
        overlay.tip.previous_button.clicked.connect(self.previous)
        overlay.tip.next_button.clicked.connect(self.next)
        overlay.tip.skip_button.clicked.connect(self.skip)
        overlay.tip.rangeValidityChanged.connect(lambda _valid: self._navigation())
        return overlay

    def _persist(self, step: str, detail: str = "") -> bool:
        try:
            advance_guide_progress(step, detail)
        except (OSError, ValueError):
            self._current_overlay().copy.setText("无法保存引导进度，请检查设置目录是否可写后重试。当前步骤与参数已保留。")
            self._current_overlay().relayout.start(0)
            return False
        if step != self.step:
            self._config_saved = False
            self.eye_guide.config_saved = False
        self.step, self.detail = step, detail
        return True

    def _go(self, step: str, detail: str = "") -> None:
        if not self._persist(step, detail):
            return
        self._show_workspace()

    def _show_workspace(self) -> None:
        if not self.active or self.window._is_closing:
            return
        if self.step in ("seed_capture_page", "seed_capture_config", "seed_capture_actions", "seed_capture_tools", "seed_capture_save", "auto_flow_config", "script_preview"):
            page = self.window.project_xs_tab
        elif self.step == "auto_script_config":
            page = self.script_guide.page()
        elif self.step in ("easycon_intro", "easycon_recording", "easycon_script_config"):
            page = self.window.easycon_tab
        else:
            page = self.panel
        if self.step == "seed_capture_page" and self.window.tabs.currentWidget() is page:
            self._go("seed_capture_config")
            return
        waiting = self.window.tabs.currentWidget() is not page
        self.overlay.page_widget = page
        if self.script_guide.prepare():
            return
        if not waiting and self.eye_guide.prepare():
            return
        if not waiting and self.step == "easycon_recording" and self.detail == "demo":
            self.overlay.shade_all()
            if self.easycon_demo_dialog is None:
                self.easycon_demo_dialog = EasyConRecordDemoDialog(self.window)
                self.easycon_demo_dialog.learnedRequested.connect(self._easycon_demo_learned)
                self.easycon_demo_dialog.finished.connect(self._easycon_demo_closed)
                self.easycon_demo_dialog.open()
            return
        if not waiting and self.step in ("shiny_threshold", "sync", "auto_reverse", "correction_strategy"):
            self.panel.more_strategy_button.setChecked(True)
            self.panel.strategy_group.layout().activate()
        if self.step == "connect_devices" and self.detail not in ("video_source", "easycon"):
            self.detail = "video_source"
        spec = workspace_step(self.panel, self.step, self.detail)
        self.overlay.configure(spec, waiting_for_page=waiting)
        if waiting and self.step == "script_preview":
            self.overlay.title.setText("先打开独立预览")
            self.overlay.copy.setText("先点击亮起的「Seed 捕捉」标签，接着点击该页中的「独立预览」。")
        self.overlay.reveal()
        self._navigation()
        self.script_guide.present()
        if self.step == "seed_capture_save":
            self.eye_guide.show_save_status()
        if self.step == "save_config" and self._config_saved:
            self.overlay.title.setText("配置已保存")
            self.overlay.copy.setText("本次任务参数已保存。点击下一步完成第 2 步。")
            self.overlay.relayout.start(0)
        if not waiting and self.step in ("delay_strategy", "correction_strategy") and self.detail:
            QTimer.singleShot(0, self._open_current_dialog)

    def _open_current_dialog(self) -> None:
        if not self.active or self._dialog_key is not None or self.overlay.waiting_for_page:
            return
        if self.step == "delay_strategy":
            self.panel.open_delay_strategy_dialog()
        elif self.step == "correction_strategy":
            self.panel.open_strategy_dialog()

    def _current_overlay(self) -> GuideSpotlight:
        return self.dialog_overlay if self._dialog_key is not None and self.dialog_overlay is not None else self.overlay

    def _navigation(self) -> None:
        if self.overlay is None:
            return
        overlay = self._current_overlay()
        tip = overlay.tip
        waiting = overlay.waiting_for_page
        valid = self.overlay.tip.range_valid if self.step == "search_range" else True
        if self.step == "connect_devices":
            if self._dialog_key:
                valid = self.detail not in ("connect", "confirm") or self._connection_ready(self._dialog_key)
            else:
                valid = self._connection_ready(self.detail or "video_source")
        tip.previous_button.setEnabled(not waiting and self.step != "target_selection")
        tip.previous_button.setText("上一步")
        tip.previous_button.show()
        tip.skip_button.setText("跳过讲解")
        tip.skip_button.show()
        tip.next_button.setEnabled(not waiting and valid and self.step not in ("preview_opened",) and (self.step != "save_config" or self._config_saved))
        tip.skip_button.setEnabled(not waiting and valid and self.step not in ("connect_devices", "devices_connected", "save_config", "task_configured", "auto_flow_config", "easycon_script_config"))
        if self.step == "seed_capture_tools":
            tip.next_button.setText("观看演示" if not self.detail else "下一步")
            tip.next_button.setEnabled(not waiting and not self.detail)
        elif self.step == "script_preview":
            tip.next_button.setText("下一步")
            tip.next_button.setEnabled(False)
            tip.skip_button.setEnabled(False)
        elif self.step == "easycon_recording" and self.detail == "demo":
            tip.next_button.setText("下一步")
            tip.next_button.setEnabled(False)
        else:
            tip.next_button.setText("下一步")
        if self.step == "seed_capture_save":
            tip.next_button.setEnabled(not waiting and self.eye_guide.config_saved)
            tip.skip_button.setEnabled(False)
        self.script_guide.navigation()

    def next(self) -> None:
        if not self.active or not self._current_overlay().tip.next_button.isEnabled():
            return
        if self.step == "auto_script_config":
            self.script_guide.next()
        elif self._dialog_key:
            steps = connection_dialog_steps(self.panel, self._dialog_key) if self._dialog_key in ("video_source", "easycon") else dialog_steps(self.panel, self._dialog_key)
            index = next((i for i, spec in enumerate(steps) if spec.key == self.detail), 0)
            if index + 1 < len(steps):
                if self._persist(self.step, steps[index + 1].key):
                    self._show_dialog_step()
            elif self._dialog_key in ("video_source", "easycon"):
                self._finish_connection_dialog()
            else:
                self._leave_dialog(GUIDE_STEPS[GUIDE_STEPS.index(self.step) + 1])
        elif self.step == "connect_devices":
            detail = self.detail or "video_source"
            if detail == "video_source":
                self._persist(self.step, "easycon")
                self._show_workspace()
            else:
                self._go("devices_connected")
        elif self.step in ("delay_strategy", "correction_strategy"):
            self._open_current_dialog()
        elif self.step == "save_config":
            self._go("connect_devices", "video_source")
        elif self.step == "auto_flow_config":
            self._go("script_preview")
        elif self.step == "easycon_intro":
            self._go("easycon_recording", "demo")
        elif self.step == "easycon_recording":
            self._go("auto_script_config")
        elif self.step == "easycon_script_config":
            self.pause()
        elif self.step == "seed_capture_tools":
            self.eye_guide.next()
        else:
            self._go(GUIDE_STEPS[GUIDE_STEPS.index(self.step) + 1])

    def previous(self) -> None:
        if not self.active or not self._current_overlay().tip.previous_button.isEnabled():
            return
        if self.step == "seed_capture_tools" and self.detail:
            self.eye_guide.previous()
        elif self.step == "easycon_recording" and self.detail == "practice":
            self._go("easycon_intro")
        elif self.step == "auto_script_config":
            self.script_guide.previous()
        elif self.step == "easycon_script_config":
            self._go("auto_script_config")
        elif self._dialog_key:
            steps = connection_dialog_steps(self.panel, self._dialog_key) if self._dialog_key in ("video_source", "easycon") else dialog_steps(self.panel, self._dialog_key)
            index = next((i for i, spec in enumerate(steps) if spec.key == self.detail), 0)
            if index > 0:
                if self._persist(self.step, steps[index - 1].key):
                    self._show_dialog_step()
            elif self._dialog_key in ("video_source", "easycon"):
                self._close_connection_dialog(resume=True)
            else:
                self._leave_dialog(GUIDE_STEPS[GUIDE_STEPS.index(self.step) - 1])
        elif self.step == "connect_devices":
            if (self.detail or "video_source") == "easycon":
                self._persist(self.step, "video_source")
                self._show_workspace()
            else:
                self._go("delay_strategy")
        else:
            self._go(GUIDE_STEPS[GUIDE_STEPS.index(self.step) - 1])

    def skip(self) -> None:
        if not self.active or not self._current_overlay().tip.skip_button.isEnabled():
            return
        self.eye_guide.cancel_selection()
        if self.step == "auto_script_config":
            self.script_guide.skip()
            return
        if self.step == "target_selection":
            destination = "search_range"
        elif self.step in ("seed_capture_page", "seed_capture_config", "seed_capture_actions", "seed_capture_tools"):
            destination = "seed_capture_save"
        elif self.step in ("easycon_intro", "easycon_recording"):
            destination = "auto_script_config"
        else:
            destination = "save_config"
        if self._dialog_key:
            self._leave_dialog(destination)
        else:
            self._go(destination)

    def _page_changed(self, index: int) -> None:
        if self.active and self._dialog_key is None and not self._resume_after_dialog:
            self._show_workspace()

    def _preview_opened(self) -> None:
        if (self.active and self.step == "script_preview"
                and self.window.tabs.currentWidget() is self.window.project_xs_tab
                and self.window._picture_in_picture is not None
                and self.window._picture_in_picture.isVisible()):
            self._go("easycon_intro")

    def pause(self) -> None:
        self.eye_guide.cancel_selection()
        self.active = False
        self.eye_guide.pause()
        self.script_guide.pause()
        if self.easycon_demo_dialog is not None:
            self.easycon_demo_dialog.reject()
        self._connection_timer.stop()
        QApplication.instance().removeEventFilter(self)
        self._resume_after_dialog = False
        for overlay in (self.overlay, self.dialog_overlay):
            if overlay is not None and isValid(overlay):
                overlay.hide()
        self._restore_dialog_layout()
        if self._dialog_key:
            self._dialog().setFocus()
        else:
            self.button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _easycon_demo_learned(self) -> None:
        if self._persist("easycon_recording", "practice"):
            if self.easycon_demo_dialog is not None:
                self.easycon_demo_dialog.finish_learning()
        elif self.easycon_demo_dialog is not None:
            self.easycon_demo_dialog.save_failed()

    def _easycon_demo_closed(self, result: int) -> None:
        dialog, self.easycon_demo_dialog = self.easycon_demo_dialog, None
        if dialog is not None:
            dialog.deleteLater()
        if not self.active:
            return
        if result == QDialog.DialogCode.Accepted:
            QTimer.singleShot(0, self._show_workspace)
        else:
            self.pause()

    def _run_state_changed(self, running: bool) -> None:
        if running:
            self.pause()
        self.button.setEnabled(not running)

    def _target_opened(self) -> None:
        self._resume_after_dialog = self.active
        if self._resume_after_dialog:
            QApplication.instance().removeEventFilter(self)
            self.overlay.shade_all()

    def _target_closed(self) -> None:
        if self._resume_after_dialog:
            self._resume_after_dialog = False
            QTimer.singleShot(0, self.begin_or_resume)

    def _dialog(self) -> QDialog:
        if self._dialog_key == "video_source":
            return self.window.video_source_dialog
        if self._dialog_key == "easycon":
            return self.window.easycon_tab.connection_dialog
        return self.panel.delay_strategy_dialog if self._dialog_key == "delay_strategy" else self.panel.strategy_dialog

    def _connection_dialog_opened(self, device: str) -> None:
        if not self.active or self.step != "connect_devices" or self._dialog_key is not None:
            return
        self._dialog_key = device
        self.detail = "device" if device == "video_source" else "port"
        dialog = self._dialog()
        self._dialog_layout_state = (dialog, dialog.size(), dialog.minimumHeight(), dialog.layout().alignment())
        dialog.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
        available_height = dialog.screen().availableGeometry().height() - 48
        dialog.setMinimumHeight(min(610, available_height))
        dialog.resize(dialog.width(), max(dialog.height(), dialog.minimumHeight()))
        self.overlay.shade_all()
        QTimer.singleShot(0, self._show_dialog_step)

    def _close_connection_dialog(self, *, resume: bool = False) -> None:
        dialog = self._dialog()
        key = self._dialog_key
        self._restore_dialog_layout()
        self._dialog_key = None
        if self.dialog_overlay is not None:
            self.dialog_overlay.hide()
            self.dialog_overlay.deleteLater()
            self.dialog_overlay = None
        if dialog.isVisible():
            dialog.hide()
        if resume and key:
            self._show_workspace()

    def _finish_connection_dialog(self) -> None:
        key = self._dialog_key
        if key == "video_source" and self._connection_ready("video_source"):
            self._close_connection_dialog()
            self._persist(self.step, "easycon")
            self._show_workspace()
        elif key == "easycon" and self._connection_ready("easycon"):
            self._close_connection_dialog()
            self._go("devices_connected")

    def _dialog_opened(self, key: str) -> None:
        if not self.active or self.step != key:
            return
        self._dialog_key = key
        if key == "correction_strategy":
            dialog = self._dialog()
            self._dialog_layout_state = (dialog, dialog.size(), dialog.minimumHeight(), dialog.layout().alignment())
            # Leave room for the teaching card inside this compact form. Keep
            # its real fields together; restore the normal layout on exit.
            dialog.layout().setAlignment(Qt.AlignmentFlag.AlignTop)
            dialog.setMinimumHeight(min(610, dialog.screen().availableGeometry().height() - 48))
        self.overlay.shade_all()
        QTimer.singleShot(0, self._show_dialog_step)

    def _restore_dialog_layout(self) -> None:
        if self._dialog_layout_state is not None:
            dialog, size, minimum, alignment = self._dialog_layout_state
            self._dialog_layout_state = None
            dialog.layout().setAlignment(alignment)
            dialog.setMinimumHeight(minimum)
            dialog.resize(size)

    def _show_dialog_step(self) -> None:
        if not self.active or not self._dialog_key or not self._dialog().isVisible():
            return
        if self.dialog_overlay is None:
            self.dialog_overlay = self._new_overlay(self._dialog())
        steps = connection_dialog_steps(self.panel, self._dialog_key) if self._dialog_key in ("video_source", "easycon") else dialog_steps(self.panel, self._dialog_key)
        spec = next((spec for spec in steps if spec.key == self.detail), steps[0])
        if spec.key != self.detail and not self._persist(self.step, spec.key):
            self.pause()
            return
        self.dialog_overlay.configure(spec)
        self.dialog_overlay.reveal()
        self._navigation()

    def _dialog_values_changed(self, *args) -> None:
        if self.active and self._dialog_key:
            QTimer.singleShot(0, self._show_dialog_step)

    def _leave_dialog(self, destination: str) -> None:
        if self._persist(destination):
            # The existing opener commits the draft before emitting its closed signal.
            self._dialog().accept()

    def _dialog_closed(self, key: str, result: int) -> None:
        if self._dialog_key != key:
            return
        self._restore_dialog_layout()
        self._dialog_key = None
        if self.dialog_overlay is not None:
            self.dialog_overlay.hide()
            self.dialog_overlay.deleteLater()
            self.dialog_overlay = None
        if not self.active:
            return
        if self.step == key:
            destination = GUIDE_STEPS[GUIDE_STEPS.index(key) + 1] if result == QDialog.DialogCode.Accepted else key
            if not self._persist(destination):
                self.pause()
                return
        QTimer.singleShot(0, self._show_workspace)

    def _saved(self) -> None:
        if self.active and self.step == "save_config":
            self._config_saved = True
            self._show_workspace()

    def _save_failed(self, message: str) -> None:
        if self.active and self.step == "save_config":
            self._config_saved = False
            self.overlay.title.setText("配置尚未保存")
            self.overlay.copy.setText(message)
            self.overlay.relayout.start(0)
            self._navigation()

    def _config_edited(self) -> None:
        self._config_saved = False
        if self.active and self.step in ("save_config", "task_configured"):
            if self.step == "task_configured":
                self._go("save_config")
            else:
                self._show_workspace()

    def _connection_ready(self, detail: str) -> bool:
        if detail == "easycon":
            return bool(self.panel.window().easycon_tab._native_is_connected())
        return bool(self.panel.window()._video_source_connected)

    def _connection_status_changed(self, *args) -> None:
        if self.active and self.step == "auto_script_config":
            self.script_guide.poll()
        if self.active and self.step == "connect_devices":
            if self._dialog_key in ("video_source", "easycon"):
                if self.detail == "connect" and self._connection_ready(self._dialog_key):
                    self._finish_connection_dialog()
                elif not self._dialog().isVisible():
                    self._close_connection_dialog(resume=True)
                return
            self._navigation()

    def _focusable(self, overlay: GuideSpotlight) -> list[QWidget]:
        target = overlay.focus_target
        # Preserve the original target → close order, then add navigation and choices.
        targets = (target,) if overlay.waiting_for_page else overlay.spec.highlights
        candidates = [widget for item in targets for widget in (item, *item.findChildren(QWidget))]
        candidates.extend((overlay.close_button, *overlay.tip.findChildren(QWidget)))
        result = []
        for widget in candidates:
            if widget not in result and widget.isVisible() and widget.isEnabled() and widget.focusPolicy() != Qt.FocusPolicy.NoFocus:
                result.append(widget)
        return result

    def eventFilter(self, obj, event):
        if not self.active or self.overlay is None or not isValid(self.overlay):
            return False
        overlay = self._current_overlay()
        if not overlay.isVisible() or overlay.suspended:
            return False
        if self.script_guide.controlling and event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            return False  # Let the real virtual controller receive mapped keys and Esc.
        if event.type() == QEvent.Type.Shortcut:
            return True
        if not isinstance(obj, QWidget):
            return False
        owner = QWidget.window(obj)
        owners = (self.window, overlay.tip, self._dialog() if self._dialog_key else self.window)
        if owner not in owners:
            return False  # Native combo popups keep their own keyboard handling.
        event_type = event.type()
        if event_type == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.pause()
            return True
        if event_type == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
                return True
            order = self._focusable(overlay)
            if order:
                current = obj if obj in order else QApplication.focusWidget()
                index = order.index(current) if current in order else -1
                back = event.key() == Qt.Key.Key_Backtab or event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                order[(index + (-1 if back else 1)) % len(order)].setFocus(Qt.FocusReason.TabFocusReason)
            return True
        if event_type in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride, QEvent.Type.Wheel):
            if overlay.waiting_for_page and obj is overlay.tab_bar:
                if event_type == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                    self.window.tabs.setCurrentWidget(overlay.page_widget)
                return True
            target = overlay.focus_target
            allowed = (target,) if overlay.waiting_for_page else overlay.spec.highlights
            if any(obj is item or item.isAncestorOf(obj) for item in (*allowed, overlay.tip)):
                # Prevent QDialog's default button from accepting an unrelated row on Enter.
                if self._dialog_key and event_type == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not isinstance(obj, QToolButton):
                    from PySide6.QtWidgets import QPushButton
                    if not isinstance(obj, QPushButton):
                        return True
                return False
            return True
        return False
