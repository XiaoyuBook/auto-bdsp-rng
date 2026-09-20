"""Minimal GUI for independently testing QQ bot notifications."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QImageReader
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from client import QQClient, QQError


DEFAULT_IMAGE = Path(__file__).resolve().parent / "assets/test-notification.png"


def default_config_path():
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config")))
    return root / "AutoBdspRng" / "QQNotifyTest" / "config.json"


class MainWindow(QMainWindow):
    def __init__(self, *, config_path=None, client=None):
        super().__init__()
        self.setWindowTitle("QQ 通知测试工具")
        available_height = QApplication.primaryScreen().availableGeometry().height()
        self.resize(780, min(830, available_height - 60))
        self.setMinimumSize(590, 600)
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.client = client or QQClient(self)
        self._image_path = ""
        self._actions = []
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        heading = QLabel("QQ 通知测试")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        intro = QLabel("填写机器人凭据 → 绑定接收方 → 发送测试通知")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        intro_row = QHBoxLayout()
        intro_row.addWidget(intro, 1)
        self.guide_button = QPushButton("注册教程 ↗")
        self.guide_button.clicked.connect(self.open_registration_guide)
        intro_row.addWidget(self.guide_button)
        layout.addLayout(intro_row)
        self.status_label = QLabel("等待填写机器人凭据。")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)
        outer_layout = layout
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        account = QGroupBox("1 · 机器人凭据")
        form = QFormLayout(account)
        self.app_id = QLineEdit()
        self.app_id.setPlaceholderText("QQ 开放平台的 AppID")
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("AppSecret，仅在本次打开期间使用")
        form.addRow("AppID", self.app_id)
        form.addRow("AppSecret", self.secret)
        row = QHBoxLayout()
        self.show_secret = QCheckBox("显示 AppSecret")
        self.show_secret.toggled.connect(lambda visible: self.secret.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password))
        row.addWidget(self.show_secret)
        row.addStretch()
        self.verify_button = self._button("验证凭据", self.verify)
        row.addWidget(self.verify_button)
        form.addRow(row)
        layout.addWidget(account)

        recipients = QGroupBox("2 · 接收方")
        form = QFormLayout(recipients)
        self.user_id = QLineEdit()
        self.user_id.setPlaceholderText("绑定后自动填写，也可手动输入用户 OpenID")
        self.group_id = QLineEdit()
        self.group_id.setPlaceholderText("绑定后自动填写，也可手动输入群 OpenID")
        for label, field, kind in (("用户 OpenID", self.user_id, "user"), ("群 OpenID", self.group_id, "group")):
            row = QHBoxLayout()
            row.addWidget(field)
            row.addWidget(self._button("绑定私聊" if kind == "user" else "绑定群聊", lambda k=kind: self.bind(k)))
            form.addRow(label, row)
        tip = QLabel("绑定私聊：向机器人发送验证码。绑定群聊：在目标群 @机器人并发送验证码。")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        form.addRow(tip)
        row = QHBoxLayout()
        self.target = QComboBox()
        self.target.addItems(["只发送到私聊", "只发送到群聊", "同时发送到私聊和群聊"])
        row.addWidget(self.target)
        row.addStretch()
        row.addWidget(self._button("保存配置", self.save_config))
        form.addRow("发送目标", row)
        layout.addWidget(recipients)

        notification = QGroupBox("3 · 测试消息")
        message_layout = QVBoxLayout(notification)
        self.message = QPlainTextEdit("QQ 通知测试：请确认同时收到本条文字和测试图片，以验证通知是否正常。")
        self.message.setPlaceholderText("输入要发送的通知文字，也可以只发送图片")
        self.message.setMaximumHeight(75)
        message_layout.addWidget(self.message)
        row = QHBoxLayout()
        self.image_label = QLabel("默认图片：软件 Logo")
        self.image_label.setWordWrap(True)
        row.addWidget(self.image_label, 1)
        row.addWidget(self._button("更换图片", self.select_image))
        row.addWidget(self._button("恢复默认", self.reset_image))
        message_layout.addLayout(row)
        row = QHBoxLayout()
        self.send_button = self._button("发送测试通知", self.send)
        self.send_button.setObjectName("primary")
        row.addWidget(self.send_button)
        self.cancel_button = QPushButton("取消当前操作")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.client.cancel)
        row.addWidget(self.cancel_button)
        row.addStretch()
        action_row = row
        layout.addWidget(notification)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(body)
        layout = outer_layout
        layout.addWidget(scroll, 1)
        layout.addLayout(action_row)

        row = QHBoxLayout()
        row.addWidget(QLabel("运行日志"))
        row.addStretch()
        clear = QPushButton("清空日志")
        row.addWidget(clear)
        layout.addLayout(row)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(500)
        self.logs.setFixedHeight(90)
        clear.clicked.connect(self.logs.clear)
        layout.addWidget(self.logs)
        self.setCentralWidget(root)
        self.setStyleSheet("""
            QMainWindow, QScrollArea, QWidget { background: #f5f7fb; color: #213047; }
            QLabel#heading { font-size: 22px; font-weight: 700; }
            QLabel#muted { color: #66758b; }
            QGroupBox { border: 1px solid #d9e1eb; border-radius: 8px; margin-top: 10px; padding: 10px 10px 6px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit, QPlainTextEdit, QComboBox { background: white; border: 1px solid #cbd5e1; border-radius: 5px; padding: 5px; }
            QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 5px; padding: 5px 10px; }
            QPushButton:hover { background: #e8eef8; }
            QPushButton#primary { background: #2563eb; color: white; border-color: #2563eb; }
            QPushButton:disabled { color: #96a1b1; background: #edf0f5; }
            QLabel#status { background: #e7eefc; border-radius: 6px; padding: 10px; }
        """)
        self.client.status.connect(self.status_label.setText)
        self.client.log.connect(self.log)
        self.client.busy_changed.connect(self._busy_changed)
        self.client.bound.connect(self._bound)
        self.load_config()

    def _button(self, text, callback):
        button = QPushButton(text)
        button.clicked.connect(lambda _checked=False: callback())
        self._actions.append(button)
        return button

    def log(self, message):
        self.logs.appendPlainText(f"{datetime.now():%H:%M:%S}  {message}")

    def _busy_changed(self, busy):
        for widget in [*self._actions, self.app_id, self.secret, self.user_id, self.group_id, self.target, self.message]:
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def _perform(self, action):
        try:
            self.client.configure(self.app_id.text(), self.secret.text())
            action()
        except Exception as exc:
            message = self.client._redact(exc)
            self.status_label.setText(message)
            self.log(message)

    def verify(self):
        self._perform(self.client.check_credentials)

    def open_registration_guide(self):
        guide = Path(__file__).resolve().parents[2] / "docs/assets/guide-qq/index.html"
        if not guide.is_file():
            self.log("未找到注册教程，请保留项目中的 docs/assets/guide-qq 目录。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(guide))):
            self.log("浏览器未能打开教程，可手动打开 docs/assets/guide-qq/index.html。")

    def bind(self, kind):
        self._perform(lambda: self.client.bind(kind))

    def _bound(self, kind, open_id):
        (self.user_id if kind == "user" else self.group_id).setText(open_id)
        self.save_config()

    def select_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择测试图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self._image_path = path
            self.image_label.setText(Path(path).name)
            self.image_label.setToolTip(path)

    def reset_image(self):
        self._image_path = ""
        self.image_label.setText("默认图片：软件 Logo")
        self.image_label.setToolTip("")

    def _image_bytes(self):
        image_path = Path(self._image_path) if self._image_path else DEFAULT_IMAGE
        if not image_path.is_file():
            raise QQError("测试图片不存在，请更换图片或恢复工具的 assets 目录。")
        reader = QImageReader(str(image_path))
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid() or size.width() * size.height() > 20_000_000:
            raise QQError("图片无效或超过 2000 万像素，请选择较小的图片。")
        image = reader.read()
        if image.isNull():
            raise QQError("图片读取失败：" + reader.errorString())
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "JPEG", 90):
            raise QQError("图片转换为 JPEG 失败。")
        result = bytes(buffer.data())
        if len(result) > 10 * 1024 * 1024:
            raise QQError("JPEG 图片超过 10 MB，请选择较小的图片。")
        return result

    def send(self):
        def action():
            targets = []
            if self.target.currentIndex() in (0, 2):
                targets.append(("user", self.user_id.text()))
            if self.target.currentIndex() in (1, 2):
                targets.append(("group", self.group_id.text()))
            self.client.send(targets, self.message.toPlainText(), self._image_bytes())
        self._perform(action)

    def save_config(self):
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"app_id": self.app_id.text().strip(), "user_openid": self.user_id.text().strip(),
                    "group_openid": self.group_id.text().strip(), "target": self.target.currentIndex()}
            temporary = self.config_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.config_path)
            self.log("已保存 AppID、OpenID 和发送目标；AppSecret 不写入文件。")
        except OSError as exc:
            self.log(f"配置保存失败：{exc}")

    def load_config(self):
        try:
            if not self.config_path.exists():
                return
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.app_id.setText(str(data.get("app_id", "")))
            self.user_id.setText(str(data.get("user_openid", "")))
            self.group_id.setText(str(data.get("group_openid", "")))
            self.target.setCurrentIndex(max(0, min(2, int(data.get("target", 0)))))
            self.log("已加载接收方配置，请重新填写 AppSecret。")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.log(f"配置读取失败，可重新填写：{exc}")

    def closeEvent(self, event):
        self.client.cancel()
        self.save_config()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
