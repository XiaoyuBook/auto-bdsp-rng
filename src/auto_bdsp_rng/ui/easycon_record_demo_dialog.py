"""A recording lesson driven through live EasyCon widgets and a memory device."""
from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QDialog, QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.automation.easycon.native.device import MemoryTransport, NintendoSwitchDevice
from auto_bdsp_rng.automation.easycon.native_backend import NativeEasyConBackend
from auto_bdsp_rng.ui.controller_overlay import ControllerStateOverlay
from auto_bdsp_rng.ui.easycon_panel import EasyConPanel


RECORD_START_MS = 5000
HOLD_MS = 1500
INPUT_ACTIONS = (
    (7400, Qt.Key.Key_W, "W", "UP", "向上 ↑"),
    (9900, Qt.Key.Key_A, "A", "LEFT", "向左 ←"),
    (12400, Qt.Key.Key_S, "S", "DOWN", "向下 ↓"),
    (14900, Qt.Key.Key_D, "D", "RIGHT", "向右 →"),
)


class _LessonPanel(EasyConPanel):
    """Use the real recorder, but never capture OS keys or save user files."""

    def refresh_ports(self) -> None:
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItem("演示设备", "mock")
        self.port_combo.blockSignals(False)

    def _activate_virtual_controller(self) -> bool:
        self.virtual_controller_enabled = True
        self._set_keyboard_controller_checked(True)
        return True

    def _deactivate_virtual_controller(self, **_kwargs) -> bool:
        self._release_virtual_controller_keys()
        self.virtual_controller_enabled = False
        self._set_keyboard_controller_checked(False)
        return True

    def save_script(self) -> Path | None:
        self._saved_editor_text = self.editor.toPlainText()
        self._update_dirty_indicator()
        self._append_log("info", "录制练习.ecs 已保存")
        return None


class _Keyboard(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(280, 196)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.pressed_key: str | None = None
        self.progress = 0.0
        self.pulse = 0.0
        self.caption = "W / A / S / D · 各 1.5 秒"

    @property
    def down(self) -> bool:
        return self.pressed_key is not None

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = self.font()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F1F7F3"))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        for key, x, y in (("W", 108, 8), ("A", 34, 76), ("S", 108, 76), ("D", 182, 76)):
            pressed = key == self.pressed_key
            r = QRectF(x, y + (3 if pressed else 0), 64, 56)
            if pressed:
                p.setPen(QPen(QColor(4, 184, 135, int(110 + self.pulse * 100)), 2.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                margin = 3 + self.pulse * 3
                p.drawRoundedRect(r.adjusted(-margin, -margin, margin, margin), 11, 11)
            p.setPen(QPen(QColor("#087C58" if pressed else "#CFDAD5"), 1.5))
            p.setBrush(QColor("#04A878" if pressed else "#FFFFFF"))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("white" if pressed else "#30483F"))
            font.setPixelSize(24)
            font.setBold(pressed)
            p.setFont(font)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, key)
        font.setPixelSize(14)
        font.setBold(False)
        p.setFont(font)
        p.setPen(QColor("#087C58"))
        p.drawText(QRectF(0, 142, 280, 26), Qt.AlignmentFlag.AlignCenter, self.caption)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#D4E6DC"))
        p.drawRoundedRect(QRectF(24, 179, 232, 6), 3, 3)
        p.setBrush(QColor("#04A878"))
        p.drawRoundedRect(QRectF(24, 179, 232 * self.progress, 6), 3, 3)


class _LessonView(QGraphicsView):
    """Scale live widgets together; all focus and cursor coordinates are native."""

    def __init__(self, scene, dialog) -> None:
        super().__init__(scene)
        self.lesson = dialog
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#18251F"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setInteractive(False)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def drawForeground(self, p: QPainter, bounds: QRectF) -> None:
        d = self.lesson
        if not hasattr(d, "panel"):
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mask = QPainterPath()
        mask.setFillRule(Qt.FillRule.OddEvenFill)
        mask.addRect(self.sceneRect())
        holes = d.highlight_rects()
        for r in holes:
            mask.addRoundedRect(r, 7, 7)
        p.fillPath(mask, QColor(0, 0, 0, 150))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#61D8AB"), 1.6))
        for r in holes:
            p.drawRoundedRect(r, 7, 7)
        if d.input_focus_active():
            p.setPen(QPen(QColor(97, 216, 171, int(130 + d.keyboard.pulse * 100)), 3))
            for target in ("keyboard", "vpad"):
                p.drawRoundedRect(d.rect_for(target), 7, 7)
            start = QPointF(d.rect_for("keyboard").center().x(), d.rect_for("keyboard").bottom() + 10)
            end = QPointF(start.x(), d.rect_for("vpad").top() - 10)
            p.drawLine(start, end)
            p.drawLine(end, end + QPointF(-7, -8))
            p.drawLine(end, end + QPointF(7, -8))
        cursor, ripple = d.cursor_state()
        if cursor is None:
            return
        if ripple > 0:
            p.setPen(QPen(QColor(8, 124, 88, int(200 * (1 - ripple))), 2))
            radius = 7 + ripple * 16
            p.drawEllipse(cursor, radius, radius)
        p.save()
        p.translate(cursor)
        p.setPen(QPen(QColor("#243D31"), 1.5))
        p.setBrush(QColor("white"))
        p.drawPolygon(QPolygonF([QPointF(0, 0), QPointF(3, 25), QPointF(10, 18),
                                QPointF(16, 29), QPointF(21, 26), QPointF(15, 16), QPointF(26, 15)]))
        p.restore()


@dataclass(frozen=True)
class _Step:
    duration: int
    title: str
    copy: str
    target: str


STEPS = (
    _Step(3500, "开启控制，显示虚拟手柄", "点击「控制」后，虚拟手柄出现；接下来可以开始录制。", "active"),
    _Step(3000, "点击「开始录制」", "按钮会变成「停止录制」，左侧状态显示「录制中」。", "record"),
    _Step(12000, "看右侧：依次按下 W、A、S、D", "每个方向按住 1.5 秒，再松开观察摇杆回中。新生成的脚本行会随操作逐行亮起。", "keyboard"),
    _Step(3500, "点击「停止录制」", "先结束本次录制。按钮会变回「开始录制」，已录下的命令会保留在脚本中。", "record"),
    _Step(4800, "读懂刚刚录下的四个方向", "LS UP / LEFT / DOWN / RIGHT 对应上、左、下、右；WAIT 1500 是按住 1.5 秒，LS RESET 是回中，WAIT 1000 是动作间隔。", "code"),
    _Step(2800, "关闭键盘控制", "结束操作后切回「关闭」，虚拟手柄收起，可以继续编辑脚本。", "off"),
    _Step(3400, "最后保存脚本", "保存后再到自动流程选择它。左侧「暂停」暂停录制；顶部「暂停」暂停脚本执行。", "save"),
)


class EasyConRecordDemoDialog(QDialog):
    learnedRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("5.2 · 伊机控脚本录制演示")
        self.setModal(True)
        self.setMinimumSize(600, 490)
        area = parent.screen().availableGeometry()
        self.resize(min(1320, area.width() - 40), min(900, area.height() - 60))
        self.setStyleSheet("QDialog {background:#F5F8F6;} QPushButton {padding:6px 15px; border:1px solid #CDDCD4; border-radius:6px; background:white; color:#315548;} QPushButton:hover {background:#EAF6EF;} QLabel {color:#34483D;}")
        self.elapsed = 0
        self.event_time = 0
        self.applied_events = 0
        self.playing = True
        self.submitting = self.acknowledged = self.closed = False
        device = NintendoSwitchDevice(transport_factory=lambda port, baud: MemoryTransport(port, baud))
        self.backend = NativeEasyConBackend(device=device)
        self.backend.connect("memory")
        self.panel = _LessonPanel(native_backend=self.backend, isolated_demo=True,
                                  recording_clock=lambda: 10 + self.event_time / 1000)
        self.panel.setFixedSize(1050, 650)
        self.panel._native_status_timer.stop()
        self.panel.execution.timer.stop()
        # No real keyboard shortcut, file drop or pointer can activate the demo panel.
        for action in self.panel.actions():
            action.setEnabled(False)
        self.panel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scene = QGraphicsScene(self)
        self.scene.setSceneRect(0, 0, 1410, 650)
        self.proxy = self.scene.addWidget(self.panel)
        self.keyboard = _Keyboard()
        self.keyboard_proxy = self.scene.addWidget(self.keyboard)
        self.keyboard_proxy.setPos(1090, 54)
        self.vpad = ControllerStateOverlay(self.backend.get_report, connected_provider=lambda: True)
        self.vpad.setWindowFlags(Qt.WindowType.Widget)
        self.vpad.setWindowOpacity(1)
        self.vpad.set_active(True)
        self.vpad_proxy = self.scene.addWidget(self.vpad)
        self.vpad_proxy.setPos(1105, 352)
        self.vpad_proxy.setScale(2.5)
        self.pad_title = self.scene.addText("虚拟手柄")
        self.pad_title.setDefaultTextColor(QColor("#BDD7C9"))
        self.pad_title.setPos(1100, 316)
        self.pad_caption = self.scene.addText("开启控制后显示虚拟手柄")
        self.pad_caption.setDefaultTextColor(QColor("#BDD7C9"))
        self.pad_caption.setPos(1100, 606)
        self.key_caption = self.scene.addText("键盘输入")
        self.key_caption.setDefaultTextColor(QColor("#BDD7C9"))
        self.key_caption.setPos(1090, 16)
        for label in (self.key_caption, self.pad_title, self.pad_caption):
            font = label.font()
            font.setPixelSize(16)
            font.setBold(True)
            label.setFont(font)
        self.view = _LessonView(self.scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self.view, 1)
        self.caption = QLabel()
        self.caption.setStyleSheet("color:#087C58; font-size:12px;")
        self.title = QLabel()
        self.title.setStyleSheet("font-size:17px; font-weight:600;")
        self.copy = QLabel()
        self.copy.setWordWrap(True)
        self.copy.setMinimumHeight(42)
        self.copy.setStyleSheet("font-size:13px;")
        for widget in (self.caption, self.title, self.copy):
            layout.addWidget(widget)
        actions = QHBoxLayout()
        self.note = QLabel("循环演示 · 学会后在自己的脚本中操作")
        self.note.setWordWrap(True)
        actions.addWidget(self.note, 1)
        self.close_button = QPushButton("暂时收起")
        self.close_button.clicked.connect(self.reject)
        self.replay_button = QPushButton("重新播放")
        self.replay_button.clicked.connect(self.restart)
        self.pause_button = QPushButton("暂停演示")
        self.pause_button.clicked.connect(self.toggle_playing)
        self.learned_button = QPushButton("我已学会")
        self.learned_button.setStyleSheet("background:#087C58; color:white; border-color:#087C58;")
        self.learned_button.clicked.connect(self._learned)
        for button in (self.close_button, self.replay_button, self.pause_button, self.learned_button):
            button.setAutoDefault(False)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.events = (
            (1900, lambda: self.panel.controller_mode_buttons["active"].click()),
            (RECORD_START_MS, self.panel.record_btn.click),
            *((at, lambda key=key, down=down: self.panel._handle_virtual_controller_key(key, down))
              for start, key, *_ in INPUT_ACTIONS
              for at, down in ((start, True), (start + HOLD_MS, False))),
            (20100, self.panel.record_btn.click),
            (28200, lambda: self.panel.controller_mode_buttons["off"].click()),
            (31200, self.panel.save_button.click),
        )
        self.total = sum(s.duration for s in STEPS)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)
        self.restart()

    def restart(self) -> None:
        self.panel._recording = False
        self.panel._deactivate_virtual_controller()
        self.panel._recorded_lines = []
        self.panel._last_record_ts = 0
        self.panel.record_btn.setText("开始录制")
        self.panel.pause_btn.setEnabled(False)
        self.panel.current_script_name = "录制练习.ecs"
        self.panel._saved_editor_text = "# 录制练习\n# 录下的操作会追加在下面\n\n"
        self.panel.editor.setPlainText(self.panel._saved_editor_text)
        self.panel._update_dirty_indicator()
        self.panel.log_view.clear()
        self.panel.latest_log_label.setText("准备录制")
        self.panel.execution.refresh_sources()
        self.elapsed = self.event_time = self.applied_events = 0
        self.playing = True
        self.submitting = False
        self.learned_button.setEnabled(True)
        self.pause_button.setText("暂停演示")
        self.last_tick = monotonic()
        self.present()
        self.timer.start()

    def advance_to(self, elapsed: int) -> None:
        """Apply crossed events once, with recording timestamps from this same clock."""
        self.elapsed = elapsed
        while self.applied_events < len(self.events) and self.events[self.applied_events][0] <= elapsed:
            self.event_time, action = self.events[self.applied_events]
            action()
            self.applied_events += 1
        self.present()

    def _tick(self) -> None:
        now = monotonic()
        dt = min(100, round((now - self.last_tick) * 1000))
        self.last_tick = now
        if self.playing:
            if self.elapsed + dt >= self.total:
                self.restart()
            else:
                self.advance_to(self.elapsed + dt)

    def step_position(self) -> tuple[int, int]:
        t = self.elapsed
        for i, step in enumerate(STEPS):
            if t < step.duration:
                return i, t
            t -= step.duration
        return len(STEPS) - 1, STEPS[-1].duration

    def rect_for(self, target: str) -> QRectF:
        if target == "keyboard":
            return self.keyboard_proxy.sceneBoundingRect().adjusted(-10, -40, 10, 6)
        if target == "vpad":
            return self.vpad_proxy.sceneBoundingRect().adjusted(-18, -40, 18, 34)
        if target == "live_code":
            editor = self.panel.editor
            document = editor.document()
            first = document.findBlockByNumber(document.blockCount() - len(self.panel._recorded_lines) - 1)
            last = document.lastBlock().previous()
            top = editor.blockBoundingGeometry(first).translated(editor.contentOffset()).top()
            bottom = editor.blockBoundingGeometry(last).translated(editor.contentOffset()).bottom()
            origin = editor.viewport().mapTo(self.panel, QPoint())
            lines = QRectF(origin.x(), origin.y() + top, editor.viewport().width(), bottom - top).adjusted(0, -3, 0, 3)
            viewport = QRectF(origin.x(), origin.y(), editor.viewport().width(), editor.viewport().height())
            return lines.intersected(viewport)
        widget = {"active": self.panel.controller_mode_buttons["active"],
                  "off": self.panel.controller_mode_buttons["off"],
                  "record": self.panel.record_btn, "save": self.panel.save_button,
                  "code": self.panel.editor}[target]
        return QRectF(widget.mapTo(self.panel, QPoint()), widget.size()).adjusted(-4, -4, 4, 4)

    def input_focus_active(self) -> bool:
        i, _ = self.step_position()
        return i == 2 or (i == 1 and self.panel._recording)

    def highlight_rects(self) -> list[QRectF]:
        i, _ = self.step_position()
        if self.input_focus_active():
            rects = [self.rect_for("keyboard"), self.rect_for("vpad")]
            if i == 2 and self.panel._recorded_lines:
                rects.append(self.rect_for("live_code"))
            return rects
        rects = [self.rect_for(STEPS[i].target)]
        if i == 0 and self.panel.virtual_controller_enabled:
            rects.append(self.rect_for("vpad"))
        return rects

    def cursor_state(self) -> tuple[QPointF | None, float]:
        i, t = self.step_position()
        # A keyboard press has no mouse click. Let the key and stick own this phase.
        if self.input_focus_active() and self.elapsed >= RECORD_START_MS + 500:
            return None, 0.0
        start = self.rect_for(STEPS[i - 1].target).center() if i else QPointF(520, 420)
        end = self.rect_for(STEPS[i].target).center()
        p = min(1, t / 1000)
        ease = p * p * (3 - 2 * p)
        cursor = start + (end - start) * ease
        click_at = {0: 1900, 1: 1500, 3: 1600, 5: 1400, 6: 1600}.get(i)
        ripple = (t - click_at) / 450 if click_at is not None and click_at < t < click_at + 450 else 0
        return cursor, ripple

    def present(self) -> None:
        i, _ = self.step_position()
        self.caption.setText(f"5.2 · 脚本录制演示　{i + 1} / {len(STEPS)}")
        self.title.setText(STEPS[i].title)
        self.copy.setText(STEPS[i].copy)
        if i == 1 and self.panel._recording:
            self.title.setText("录制已开始，看右侧键盘和手柄")
            self.copy.setText("接下来依次按 W、A、S、D，每个按住 1.5 秒。观察键盘、摇杆和新增脚本如何一起变化。")
        if i == 3 and not self.panel._recording:
            self.title.setText("录制已停止")
            self.copy.setText("按钮已变回「开始录制」。刚才的操作已经录好，接下来检查生成的脚本。")
        action = next((action for action in INPUT_ACTIONS if action[1] in self.panel.virtual_controller_keys), None)
        latest = next((action for action in reversed(INPUT_ACTIONS) if self.elapsed >= action[0]), None)
        self.keyboard.pressed_key = action[2] if action else None
        held_ms = max(0, min(HOLD_MS, self.elapsed - latest[0])) if latest else 0
        self.keyboard.progress = held_ms / HOLD_MS
        self.keyboard.pulse = (1 - cos(2 * pi * self.elapsed / 1400)) / 2
        self.keyboard.caption = (f"按住 {action[2]} · {held_ms / 1000:.1f} / 1.5 秒" if action else
                                 f"{latest[2]} 已松开 · 摇杆回中" if latest else "W / A / S / D · 各 1.5 秒")
        if i == 2 and action:
            self.title.setText(f"按住 {action[2]}，左摇杆{action[4]}")
            self.copy.setText(f"{action[2]} 键亮起，左摇杆{action[4]}，保持 1.5 秒。脚本立即新增 LS {action[3]}，已有录制内容继续亮着。")
        elif i == 2 and latest:
            self.title.setText(f"松开 {latest[2]}，观察左摇杆回中")
            self.copy.setText("按键恢复原色，左摇杆回到中央。新补上的 WAIT 1500 和 LS RESET 与前面的录制内容一起亮起。")
        self.keyboard.update()
        self.vpad.refresh_state()
        self.vpad_proxy.setVisible(self.panel.virtual_controller_enabled)
        self.pad_caption.setPlainText(f"左摇杆{action[4]} · 1.5 秒" if action else
                                      "左摇杆已回中" if self.panel.virtual_controller_enabled else "开启控制后显示虚拟手柄")
        for label in (self.key_caption, self.pad_title, self.pad_caption):
            label.setDefaultTextColor(QColor("#7AF0BD" if self.input_focus_active() else "#BDD7C9"))
        self.view.viewport().update()

    def toggle_playing(self) -> None:
        self.playing = not self.playing
        self.last_tick = monotonic()
        self.pause_button.setText("暂停演示" if self.playing else "继续播放")

    def _learned(self) -> None:
        if not self.submitting:
            self.submitting = True
            self.playing = False
            self.learned_button.setEnabled(False)
            self.learnedRequested.emit()

    def save_failed(self) -> None:
        self.submitting = False
        self.learned_button.setEnabled(True)
        self.pause_button.setText("继续播放")
        self.note.setText("无法保存引导进度，请检查设置目录后重试。")

    def finish_learning(self) -> None:
        self.acknowledged = True
        self.accept()

    def accept(self) -> None:
        if self.acknowledged:
            super().accept()

    def done(self, result: int) -> None:
        if not self.closed:
            self.closed = True
            self.timer.stop()
            self.panel._recording = False
            self.panel._deactivate_virtual_controller()
            self.vpad.shutdown()
            self.backend.close()
        super().done(result)
