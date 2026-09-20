"""Offline QQ settings: approved HTML layout connected to the Qt service."""
from __future__ import annotations

import json
from dataclasses import asdict

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from auto_bdsp_rng.notifications.qq_service import QQNotificationService
from auto_bdsp_rng.resources import app_icon_path, resource_path
from auto_bdsp_rng.ui.qq_guide import load_qq_steps


def notification_icon(color="#64707D"):
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    path = QPainterPath()
    path.moveTo(9, 28)
    path.cubicTo(13, 24, 12, 22, 12, 16)
    path.cubicTo(12, 5, 28, 5, 28, 16)
    path.cubicTo(28, 22, 27, 24, 31, 28)
    path.closeSubpath()
    painter.drawPath(path)
    painter.drawArc(16, 28, 8, 7, 180 * 16, 180 * 16)
    painter.end()
    return QIcon(pixmap)


class _LocalPage(QWebEnginePage):
    """Only our packaged page can own the credential bridge."""

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url == self.parent().entry_url


class _NotificationBridge(QObject):
    changed = Signal(str)

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog

    @Slot(result=str)
    def initialize(self):
        dialog = self.dialog
        steps = load_qq_steps()
        for step in steps:
            step["image"] = QUrl.fromLocalFile(str(step.pop("path"))).toString()
        return json.dumps({"state": dialog.snapshot(), "credentials": dialog._draft,
                           "steps": steps, "logo": QUrl.fromLocalFile(str(app_icon_path())).toString()}, ensure_ascii=False)

    @Slot(str, str)
    def command(self, action, payload):
        try:
            args = json.loads(payload)
            if not isinstance(args, dict):
                raise ValueError("无效的操作参数")
            self.dialog.command(action, args)
        except Exception as exc:
            self.dialog._error(str(exc))


class QQNotificationDialog(QDialog):
    def __init__(self, service: QQNotificationService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("QQNotificationDialog")
        self.setWindowTitle("QQ 通知")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.setMinimumSize(600, 480)
        screen = self.screen().availableGeometry()
        self.resize(min(880, screen.width() - 60), min(850, screen.height() - 80))
        self._view = {"guide": False, "phase": "register", "tab": "setup", "step": 0}
        self._binding = {"kind": "", "code": "", "seconds": 0, "generation": 0}
        self._draft = {"app_id": service.settings.app_id, "secret": service.settings.secret,
                       "remember_secret": service.settings.remember_secret}
        self._dirty = False
        self._test = "idle"
        self._test_detail = ""
        self._feedback = service.last_error
        self._feedback_error = bool(service.last_error)
        self._credentials_feedback = ""
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_draft)

        self.web = QWebEngineView(self)
        self.web.entry_url = QUrl.fromLocalFile(str(resource_path("docs", "assets", "qq-notifications", "index.html")))
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # The default-constructed profile is off the record, including form state.
        # The view (and its page) must be destroyed before its profile.
        self.profile = QWebEngineProfile(self)
        self.page = _LocalPage(self.profile, self.web)
        self.web.setPage(self.page)
        self.page.setBackgroundColor(QColor("#FFFFFF"))
        settings = self.page.settings()
        for attribute in (QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                          QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
                          QWebEngineSettings.WebAttribute.LocalStorageEnabled):
            settings.setAttribute(attribute, False)
        self.bridge = _NotificationBridge(self)
        self.channel = QWebChannel(self.page)
        self.channel.registerObject("notifications", self.bridge)
        self.page.setWebChannel(self.channel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        QApplication.instance().aboutToQuit.connect(self.deleteLater)
        service.changed.connect(self.refresh)
        service.records_changed.connect(self.refresh)
        service.error.connect(self._error)
        service.operation_finished.connect(self._finished)
        service.setup.busy_changed.connect(self.refresh)
        service.setup.binding_changed.connect(self._binding_changed)
        service.setup.status.connect(self._status)
        self.web.load(self.web.entry_url)

    def snapshot(self):
        settings = asdict(self.service.settings)
        settings.pop("secret")
        return {"settings": settings, "view": self._view, "binding": self._binding,
                "verified": self.service.verified and not self._dirty,
                "ready": self.service.settings.ready() and not self._dirty,
                "busy": self.service.setup.busy, "operation": self.service.operation,
                "test": self._test, "test_detail": self._test_detail,
                "feedback": self._feedback, "feedback_error": self._feedback_error,
                "credentials_feedback": self._credentials_feedback,
                "records": [asdict(record) for record in self.service.records]}

    def refresh(self, *_):
        self.bridge.changed.emit(json.dumps(self.snapshot(), ensure_ascii=False))

    def _save_draft(self):
        self._save_timer.stop()
        if not self._dirty:
            return True
        try:
            self.service.update(**self._draft)
        except Exception as exc:
            self._error(str(exc))
            return False
        self._dirty = False
        self.refresh()
        return True

    def command(self, action, args):
        if action == "credentials":
            if self.service.setup.busy:
                return
            draft = {"app_id": str(args["app_id"]).strip(), "secret": str(args["secret"]).strip(),
                     "remember_secret": bool(args["remember_secret"])}
            credentials_changed = (draft["app_id"], draft["secret"]) != (self._draft["app_id"], self._draft["secret"])
            self._draft = draft
            self._dirty = True
            self._test = "idle"
            if credentials_changed:
                self.service.verified = False
                self._credentials_feedback = "凭据已更改，请重新验证"
            self._save_timer.start()
            self.refresh()
            return
        if action == "navigate":
            view = {**self._view, **args}
            if view["phase"] not in ("register", "bind", "test") or view["tab"] not in ("setup", "rules", "records"):
                raise ValueError("无效页面")
            view["step"] = max(0, min(11, int(view["step"])))
            self._view = view
            binding_visible = view["phase"] == "bind" if view["guide"] else view["tab"] == "setup"
            if self.service.operation == "bind" and not binding_visible:
                self.service.setup.cancel()
            self.refresh()
            return
        if action == "cancel":
            self.service.setup.cancel()
        elif action == "copy":
            QApplication.clipboard().setText(self._binding["code"])
            self._status("已复制当前绑定码")
        elif action == "platform":
            QDesktopServices.openUrl(QUrl("https://q.qq.com/#/apps"))
        elif action == "confirm":
            if self._test == "sent":
                self._test = "confirmed"
                self._status("图文测试通过 · 可启用自动通知")
        elif action == "close":
            self.close()
        else:
            if not self._save_draft():
                return
            if action == "update":
                key = args.get("key")
                if key not in ("enabled", "user_enabled", "group_enabled", "on_completed", "on_failed", "on_stopped", "attach_image"):
                    raise ValueError("未知的通知设置")
                self.service.update(**{key: bool(args["value"])})
                if key in ("user_enabled", "group_enabled"):
                    self._test = "idle"
                self._status("配置已保存 · 自动通知" + ("已启用" if self.service.settings.enabled else "未启用"))
            elif action in ("verify", "bind", "test"):
                self._test = "idle"
                self._feedback_error = False
                if action == "verify":
                    self.service.verify()
                elif action == "bind":
                    if args.get("kind") not in ("user", "group") or not self.service.verified:
                        raise ValueError("请先验证凭据，再绑定接收方。")
                    self.service.bind(args["kind"])
                else:
                    self.service.send_test()
            else:
                raise ValueError("未知的通知操作")
        self.refresh()

    def open_guide(self):
        self.command("navigate", {"guide": True})

    def return_to_settings(self):
        self.command("navigate", {"guide": False, "tab": "setup"})

    def _set_phase(self, phase):
        self.command("navigate", {"phase": ("register", "bind", "test")[phase]})

    def _binding_changed(self, kind, code, seconds, generation):
        self._binding = {"kind": kind, "code": code, "seconds": seconds, "generation": generation}
        self.refresh()

    def _finished(self, operation, ok, message):
        if operation == "verify":
            self._credentials_feedback = message
        elif operation == "test":
            self._test = "sent" if ok else "failure"
            self._test_detail = message
        self._feedback = message
        self._feedback_error = not ok and not message.startswith("操作已取消")
        self.refresh()

    def _status(self, message):
        if self.service.operation == "bind" and "验证码：" in message:
            message = "等待 QQ 中的绑定消息 · 到期自动换码"
        self._feedback = message
        self._feedback_error = False
        self.refresh()

    def _error(self, message):
        self._feedback = message
        self._feedback_error = True
        self.refresh()

    def done(self, result):
        self._save_draft()
        if self.service.setup.busy:
            self.service.setup.cancel()
        super().done(result)

    def closeEvent(self, event):
        self._save_draft()
        if self.service.setup.busy:
            self.service.setup.cancel()
        super().closeEvent(event)
