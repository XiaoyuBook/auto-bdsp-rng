"""A recording lesson driven through live EasyCon widgets and a memory device."""
from __future__ import annotations

from dataclasses import dataclass
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
        self.setFixedSize(224, 164)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.down = False

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self.font())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F1F7F3"))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        for key, x, y in (("W", 80, 6), ("A", 22, 64), ("S", 80, 64), ("D", 138, 64)):
            pressed = key == "W" and self.down
            r = QRectF(x, y + (3 if pressed else 0), 50, 48)
            p.setPen(QPen(QColor("#087C58" if pressed else "#CFDAD5"), 1.5))
            p.setBrush(QColor("#087C58" if pressed else "#FFFFFF"))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("white" if pressed else "#30483F"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, key)
        p.setPen(QColor("#087C58"))
        p.drawText(QRectF(0, 129, 224, 28), Qt.AlignmentFlag.AlignCenter,
                   "W 按住 · 左摇杆向上" if self.down else "默认映射 · WASD 控制左摇杆")


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
        mask.addRect(QRectF(self.lesson.panel.rect()))
        holes = d.highlight_rects()
        for r in holes:
            mask.addRoundedRect(r, 7, 7)
        p.fillPath(mask, QColor(0, 0, 0, 125))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#61D8AB"), 1.6))
        for r in holes:
            p.drawRoundedRect(r, 7, 7)
        cursor, ripple = d.cursor_state()
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
    _Step(3500, "按住 W，看命令实时出现", "按下 W 立即出现 LS UP；松开后回中，并补上 WAIT 保持时长和 LS RESET。", "record"),
    _Step(3500, "停止录制，保留已生成的脚本", "命令已实时追加到编辑器。停止录制后可以编辑，内容不会重复插入。", "record"),
    _Step(4800, "读懂刚刚录下的三行", "LS UP：左摇杆向上；WAIT 1800：保持 1.8 秒；LS RESET：松开并回中。", "code"),
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
        self.scene.setSceneRect(0, 0, 1320, 650)
        self.proxy = self.scene.addWidget(self.panel)
        self.keyboard = _Keyboard()
        self.keyboard_proxy = self.scene.addWidget(self.keyboard)
        self.keyboard_proxy.setPos(1074, 50)
        self.vpad = ControllerStateOverlay(self.backend.get_report, connected_provider=lambda: True)
        self.vpad.setWindowFlags(Qt.WindowType.Widget)
        self.vpad.setWindowOpacity(1)
        self.vpad.set_active(True)
        self.vpad_proxy = self.scene.addWidget(self.vpad)
        self.vpad_proxy.setPos(1090, 314)
        self.vpad_proxy.setScale(2)
        self.pad_title = self.scene.addText("虚拟手柄")
        self.pad_title.setDefaultTextColor(QColor("#BDD7C9"))
        self.pad_title.setPos(1080, 278)
        self.pad_caption = self.scene.addText("开启控制后显示虚拟手柄")
        self.pad_caption.setDefaultTextColor(QColor("#BDD7C9"))
        self.pad_caption.setPos(1078, 546)
        self.key_caption = self.scene.addText("键盘输入")
        self.key_caption.setDefaultTextColor(QColor("#BDD7C9"))
        self.key_caption.setPos(1080, 16)
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
            (5000, self.panel.record_btn.click),
            (7400, lambda: self.panel._handle_virtual_controller_key(Qt.Key.Key_W, True)),
            (9200, lambda: self.panel._handle_virtual_controller_key(Qt.Key.Key_W, False)),
            (11600, self.panel.record_btn.click),
            (19700, lambda: self.panel.controller_mode_buttons["off"].click()),
            (22700, self.panel.save_button.click),
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
        widget = {"active": self.panel.controller_mode_buttons["active"],
                  "off": self.panel.controller_mode_buttons["off"],
                  "record": self.panel.record_btn, "save": self.panel.save_button,
                  "code": self.panel.editor}[target]
        return QRectF(widget.mapTo(self.panel, QPoint()), widget.size()).adjusted(-4, -4, 4, 4)

    def highlight_rects(self) -> list[QRectF]:
        i, _ = self.step_position()
        rects = [self.rect_for(STEPS[i].target)]
        if i in (2, 3):
            rects.append(self.rect_for("code"))
        return rects

    def cursor_state(self) -> tuple[QPointF, float]:
        i, t = self.step_position()
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
        self.keyboard.down = Qt.Key.Key_W in self.panel.virtual_controller_keys
        self.keyboard.update()
        self.vpad.refresh_state()
        self.vpad_proxy.setVisible(self.panel.virtual_controller_enabled)
        self.pad_caption.setPlainText("左摇杆 ↑" if self.keyboard.down else
                                      "左摇杆已回中" if self.panel.virtual_controller_enabled else "开启控制后显示虚拟手柄")
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
