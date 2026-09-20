"""Native QQ notification settings and guided setup, sharing the same forms."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSignalBlocker, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QCheckBox, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton,
    QStackedWidget, QTabBar, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.notifications.qq_service import QQNotificationService
from auto_bdsp_rng.resources import app_icon_path
from auto_bdsp_rng.ui.qq_guide import QQRegistrationGuide
from auto_bdsp_rng.ui.workspace_layout import ColumnReflow, scroll_surface
from auto_bdsp_rng.ui.workspace_theme import primary_button_styles, ui_font, ui_styles


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


def label(text="", name="", *, wrap=True):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(wrap)
    if name:
        result.setObjectName(name)
    return result


def button(text, slot, name=""):
    result = QPushButton(text)
    if name:
        result.setObjectName(name)
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.clicked.connect(slot)
    return result


class NotificationSwitch(QCheckBox):
    def sizeHint(self):
        return QSize(42 + self.fontMetrics().horizontalAdvance(self.text()), 32)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else .45)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#087C58" if self.isChecked() else "#9CA8B3"))
        y = (self.height() - 18) / 2
        painter.drawRoundedRect(QRectF(0, y, 32, 18), 9, 9)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(16 if self.isChecked() else 3, y + 3, 12, 12))
        painter.setPen(QColor("#202A33"))
        painter.drawText(self.rect().adjusted(40, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setPen(QPen(QColor("#087C58"), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 4, 4)


class QQNotificationDialog(QDialog):
    def __init__(self, service: QQNotificationService, parent=None):
        super().__init__(parent)
        self.service = service
        self._guide = False
        self._phase = 0
        self._binding = ("", "", 0, 0)
        self._loading = True
        self._reflows = []
        self.setObjectName("QQNotificationDialog")
        self.setWindowTitle("QQ 通知")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.setFont(ui_font())
        self.setMinimumSize(660, 480)
        screen = self.screen().availableGeometry()
        self.resize(min(920, screen.width() - 60), min(750, screen.height() - 80))
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save_credentials)
        self._build()
        self._style()
        self.app_id.setText(service.settings.app_id)
        self.secret.setText(service.settings.secret)
        self._loading = False
        service.changed.connect(self.refresh)
        service.records_changed.connect(self._render_records)
        service.error.connect(self._error)
        service.operation_finished.connect(self._finished)
        service.setup.busy_changed.connect(self.refresh)
        service.setup.binding_changed.connect(self._binding_changed)
        service.setup.status.connect(self._status)
        self.refresh()
        self._navigate()
        self._render_records()
        if service.last_error:
            self._error(service.last_error)

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(22, 16, 22, 16)
        icon = QLabel()
        icon.setPixmap(notification_icon("#087C58").pixmap(34, 34))
        row.addWidget(icon)
        titles = QVBoxLayout()
        self.heading = label("QQ 通知", "Heading")
        self.subtitle = label("接收任务结果与运行截图", "Muted")
        titles.addWidget(self.heading)
        titles.addWidget(self.subtitle)
        row.addLayout(titles, 1)
        self.enabled = NotificationSwitch("未启用")
        self.enabled.setObjectName("EnableNotifications")
        self.enabled.toggled.connect(lambda value: self._change("enabled", value))
        row.addWidget(self.enabled)
        root.addWidget(header)
        self.settings_bar = QWidget()
        row = QHBoxLayout(self.settings_bar)
        row.setContentsMargins(22, 0, 22, 0)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        for text in ("接入设置", "通知规则", "发送记录"):
            self.tabs.addTab(text)
        self.tabs.currentChanged.connect(self._tab_changed)
        row.addWidget(self.tabs)
        row.addStretch()
        row.addWidget(button("注册与绑定教程", self.open_guide, "LinkButton"))
        root.addWidget(self.settings_bar)
        self.guide_bar = QWidget()
        row = QHBoxLayout(self.guide_bar)
        row.setContentsMargins(22, 6, 22, 6)
        row.addWidget(button("← 返回设置", self.return_to_settings, "LinkButton"))
        row.addStretch()
        self.phase_buttons = []
        for index, text in enumerate(("1 注册机器人", "2 连接与绑定", "3 图文验证")):
            control = button(text, lambda _=False, i=index: self._set_phase(i))
            control.setCheckable(True)
            self.phase_buttons.append(control)
            row.addWidget(control)
        root.addWidget(self.guide_bar)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self._build_forms()
        self._build_setup()
        self._build_rules()
        self._build_records()
        self._build_guide()
        footer = QWidget()
        footer.setObjectName("QQFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(22, 12, 22, 12)
        self.status = label("配置自动保存 · 自动通知未启用", "Muted")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.status, 1)
        self.cancel_button = button("取消当前操作", self.service.setup.cancel)
        row.addWidget(self.cancel_button)
        self.previous = button("上一步", self._previous)
        self.next = button("下一步 →", self._next, "PrimaryButton")
        self.done_button = button("完成", self.accept)
        for widget in (self.previous, self.next, self.done_button):
            row.addWidget(widget)
        root.addWidget(footer)
        self._place_forms()

    def _build_forms(self):
        self.credentials = QWidget()
        layout = QVBoxLayout(self.credentials)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.addWidget(label("① 机器人凭据", "SectionTitle"))
        self.credential_state = label("待验证", "Muted")
        row.addWidget(self.credential_state, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(row)
        self.app_id, self.secret = QLineEdit(), QLineEdit()
        self.app_id.setObjectName("QQAppID")
        self.secret.setObjectName("QQAppSecret")
        self.app_id.setPlaceholderText("QQ 开放平台的 AppID")
        self.secret.setPlaceholderText("填写 AppSecret")
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        for name, field in (("AppID", self.app_id), ("AppSecret", self.secret)):
            field_label = label(name)
            field_label.setBuddy(field)
            layout.addWidget(field_label)
            row = QHBoxLayout()
            row.addWidget(field)
            if field is self.secret:
                self.reveal = button("显示", self._reveal_secret)
                self.reveal.setFixedWidth(52)
                row.addWidget(self.reveal)
            layout.addLayout(row)
            field.textEdited.connect(self._credentials_edited)
        row = QHBoxLayout()
        self.remember = QCheckBox("记住密钥")
        self.remember.toggled.connect(lambda _: self._save_credentials())
        row.addWidget(self.remember)
        row.addWidget(label("仅当前 Windows 用户", "Muted"))
        row.addStretch()
        self.verify_button = button("验证凭据", lambda: self._act(self.service.verify))
        row.addWidget(self.verify_button)
        layout.addLayout(row)
        self.credentials_hint = label("未勾选时，密钥仅在本次打开期间使用。", "Muted")
        layout.addWidget(self.credentials_hint)
        layout.addStretch()
        self.recipients = QWidget()
        layout = QVBoxLayout(self.recipients)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        layout.addWidget(label("② 接收方", "SectionTitle"))
        layout.addWidget(label("可同时选择私聊和群聊", "Muted"))
        self.target_checks, self.target_states, self.bind_buttons = {}, {}, {}
        for kind, title in (("user", "QQ 私聊"), ("group", "QQ 群聊")):
            row = QHBoxLayout()
            check = QCheckBox(title)
            check.toggled.connect(lambda value, k=kind: self._change(k + "_enabled", value))
            self.target_checks[kind] = check
            row.addWidget(check)
            row.addStretch()
            control = button("绑定私聊" if kind == "user" else "绑定群聊", lambda _=False, k=kind: self._act(lambda: self.service.bind(k)))
            self.bind_buttons[kind] = control
            row.addWidget(control)
            layout.addLayout(row)
            state = label("未绑定", "Muted")
            self.target_states[kind] = state
            layout.addWidget(state)
        self.binding_box = QFrame()
        self.binding_box.setObjectName("BindingBox")
        inner = QVBoxLayout(self.binding_box)
        self.binding_title = label("等待绑定消息")
        inner.addWidget(self.binding_title)
        row = QHBoxLayout()
        self.code = label("", "BindingCode", wrap=False)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.code)
        row.addStretch()
        self.countdown = label("", "Countdown", wrap=False)
        row.addWidget(self.countdown)
        inner.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 60)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        inner.addWidget(self.progress)
        self.binding_instruction = label()
        inner.addWidget(self.binding_instruction)
        self.refresh_notice = label("到期自动换码并重新计时，旧码立即失效。", "Muted")
        inner.addWidget(self.refresh_notice)
        row = QHBoxLayout()
        self.copy_code = button("复制绑定码", lambda: QApplication.clipboard().setText(self._binding[1]))
        row.addWidget(self.copy_code)
        row.addStretch()
        row.addWidget(button("取消绑定", self.service.setup.cancel, "LinkButton"))
        inner.addLayout(row)
        layout.addWidget(self.binding_box)
        layout.addWidget(label("绑定时自动识别接收方，无需手动填写 OpenID。", "Muted"))
        layout.addStretch()
        self.test_panel = QFrame()
        self.test_panel.setObjectName("TestPanel")
        layout = QVBoxLayout(self.test_panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(label("③ 图文测试", "SectionTitle"))
        layout.addWidget(label("绑定后发送一次，检查文字和图片是否都能收到。", "Muted"))
        message = QFrame()
        message.setObjectName("MessagePreview")
        inner = QVBoxLayout(message)
        inner.addWidget(label("通知机器人"))
        inner.addWidget(label("发送内容预览", "Muted"))
        inner.addWidget(label("QQ 通知测试\n请确认同时收到本条文字和测试图片。", "MessageBubble"))
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(QPixmap(str(app_icon_path())).scaled(116, 116, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        inner.addWidget(logo)
        layout.addWidget(message)
        layout.addWidget(label("软件 Logo · 默认测试图片", "Muted"))
        self.send_button = button("发送图文测试", lambda: self._act(self.service.send_test), "PrimaryButton")
        layout.addWidget(self.send_button)
        self.test_result = label("将发送到已勾选的接收方", "Muted")
        layout.addWidget(self.test_result)
        self.confirm_button = button("我已收到文字和图片", self._confirm_received)
        self.confirm_button.hide()
        layout.addWidget(self.confirm_button)
        layout.addStretch()

    def _horizontal_page(self):
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(22, 20, 22, 20)
        row.setSpacing(24)
        area = scroll_surface(page)
        self._reflows.append(ColumnReflow(area, row, breakpoint=720))
        self.pages.addWidget(area)
        return area, row

    def _build_setup(self):
        self.setup_page, row = self._horizontal_page()
        main = QWidget()
        self.setup_form_layout = QVBoxLayout(main)
        self.setup_form_layout.setContentsMargins(0, 0, 0, 0)
        self.setup_form_layout.setSpacing(24)
        row.addWidget(main, 3)
        self.test_slot = QWidget()
        self.test_layout = QVBoxLayout(self.test_slot)
        self.test_layout.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.test_slot, 2)

    def _build_rules(self):
        self.rules_page, row = self._horizontal_page()
        self.rule_checks = {}
        for heading, options in (("何时发送", (("on_completed", "任务完成", "整项任务结束时发送结果；循环中的常规轮次不逐条推送。"), ("on_failed", "任务异常", "任务因错误而终止时，发送失败原因。"), ("on_stopped", "手动停止", "点击停止后也发送通知。"))), ("消息内容", (("attach_image", "附带运行截图", "使用任务结束时的最新画面；无可用画面时附带软件 Logo。"),))):
            column = QVBoxLayout()
            column.addWidget(label(heading, "SectionTitle"))
            if heading == "何时发送":
                column.addWidget(label("适用于自动定点乱数与自动 TID 乱数。", "Muted"))
            for key, title, detail in options:
                check = QCheckBox(title)
                check.toggled.connect(lambda checked, k=key: self._change(k, checked))
                self.rule_checks[key] = check
                column.addSpacing(12)
                column.addWidget(check)
                column.addWidget(label(detail, "Muted"))
            column.addStretch()
            row.addLayout(column, 1)

    def _build_records(self):
        self.records_page = QWidget()
        layout = QVBoxLayout(self.records_page)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.addWidget(label("最近发送 · 本次会话", "SectionTitle"))
        self.records_table = QTableWidget(0, 4)
        self.records_table.setHorizontalHeaderLabels(["时间", "类型", "接收方", "结果"])
        self.records_table.verticalHeader().hide()
        self.records_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.records_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.records_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.records_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.records_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.records_table.itemSelectionChanged.connect(self._record_details)
        layout.addWidget(self.records_table, 1)
        self.record_detail = QPlainTextEdit()
        self.record_detail.setReadOnly(True)
        self.record_detail.setPlaceholderText("选择记录查看发送详情。")
        self.record_detail.setMaximumHeight(110)
        layout.addWidget(self.record_detail)
        layout.addWidget(label("提交成功表示 QQ 接口已接受；是否收到请在 QQ 中确认。", "Muted"))
        self.pages.addWidget(self.records_page)

    def _build_guide(self):
        try:
            self.registration = QQRegistrationGuide()
            self.registration.step_changed.connect(lambda *_: self._guide_footer())
        except (OSError, ValueError, KeyError) as exc:
            self.registration = None
            self.registration_page = label(f"教程资源无法加载：{exc}")
        else:
            self.registration_page = self.registration
        self.registration_area = scroll_surface(self.registration_page)
        self.pages.addWidget(self.registration_area)
        self.connect_page, row = self._horizontal_page()
        self.guide_form_layouts = []
        for title, detail in (("从上一阶段获取凭据", "将开放平台中的 AppID 和 AppSecret 填在上方，点击「验证凭据」。"), ("在 QQ 中怎么操作", "私聊：向机器人直接发送六位绑定码。\n群聊：先把机器人加入群，再 @机器人 并发送绑定码。")):
            column = QWidget()
            layout = QVBoxLayout(column)
            layout.setContentsMargins(0, 0, 0, 0)
            slot = QVBoxLayout()
            layout.addLayout(slot)
            self.guide_form_layouts.append(slot)
            layout.addSpacing(16)
            layout.addWidget(label(title, "SectionTitle"))
            layout.addWidget(label(detail, "Muted"))
            if len(self.guide_form_layouts) == 1:
                layout.addWidget(button("查看获取凭据的步骤", self._review_credentials, "LinkButton"))
                layout.addWidget(button("打开 QQ 开放平台 ↗", lambda: QDesktopServices.openUrl(QUrl("https://q.qq.com/")), "LinkButton"))
            layout.addStretch()
            row.addWidget(column, 1)
        self.guide_test_page, row = self._horizontal_page()
        self.guide_test_layout = QVBoxLayout()
        row.addLayout(self.guide_test_layout, 1)
        checklist = QVBoxLayout()
        checklist.addWidget(label("收到文字和图片，才算验证完成", "SectionTitle"))
        for text in ("1  点击「发送图文测试」。", "2  打开 QQ，检查文字和 Logo 图片是否都收到。", "3  文字成功、图片失败时不算通过，可查看发送记录。", "4  验证完成后打开右上角开关，接收自动任务通知。"):
            checklist.addWidget(label(text, "Muted"))
        checklist.addStretch()
        row.addLayout(checklist, 1)

    def _place_forms(self):
        in_binding = self._guide and self._phase == 1
        for index, widget in enumerate((self.credentials, self.recipients)):
            destination = self.guide_form_layouts[index] if in_binding else self.setup_form_layout
            if destination.indexOf(widget) < 0:
                destination.addWidget(widget)
                widget.show()
        destination = self.guide_test_layout if self._guide and self._phase == 2 else self.test_layout
        if destination.indexOf(self.test_panel) < 0:
            destination.addWidget(self.test_panel)
            self.test_panel.show()

    def open_guide(self):
        self._guide = True
        self._navigate()

    def return_to_settings(self):
        self._guide = False
        self.tabs.setCurrentIndex(0)
        self._navigate()

    def _set_phase(self, phase):
        self._phase = phase
        self._navigate()

    def _tab_changed(self, index):
        if hasattr(self, "setup_page"):
            self._navigate()

    def _navigate(self):
        if self.service.operation == "bind" and ((self._guide and self._phase != 1) or (not self._guide and self.tabs.currentIndex() != 0)):
            self.service.setup.cancel()
        self._place_forms()
        self.settings_bar.setVisible(not self._guide)
        self.guide_bar.setVisible(self._guide)
        self.previous.setVisible(self._guide)
        self.next.setVisible(self._guide)
        self.done_button.setVisible(not self._guide)
        self.heading.setText("QQ 机器人注册与绑定" if self._guide else "QQ 通知")
        self.subtitle.setText("按步骤操作，直接完成配置与绑定" if self._guide else "接收任务结果与运行截图")
        page = (self.registration_area, self.connect_page, self.guide_test_page)[self._phase] if self._guide else (self.setup_page, self.rules_page, self.records_page)[self.tabs.currentIndex()]
        self.pages.setCurrentWidget(page)
        for index, control in enumerate(self.phase_buttons):
            control.setChecked(index == self._phase)
        if self._guide:
            self._guide_footer()
        elif not self.service.setup.busy:
            self._status("配置自动保存 · 自动通知" + ("已启用" if self.service.settings.enabled else "未启用"))
        self.refresh()

    def _guide_footer(self):
        index = self.registration.index if self.registration else 0
        self.previous.setEnabled(self._phase != 0 or index > 0)
        self.next.setText("下一步 →" if self._phase == 0 and index < 11 else "连接与绑定 →" if self._phase == 0 else "图文验证 →" if self._phase == 1 else "完成教程")
        if self._phase == 0 and self.registration:
            self._status(self.registration.steps[index]["result"])
        elif self._phase == 1:
            self._status("在当前页面验证凭据并绑定接收方，然后继续图文验证。")

    def _previous(self):
        if self._phase == 0 and self.registration:
            self.registration.set_step(self.registration.index - 1)
        else:
            self._set_phase(max(0, self._phase - 1))

    def _next(self):
        if self._phase == 0 and self.registration and self.registration.index < len(self.registration.steps) - 1:
            self.registration.set_step(self.registration.index + 1)
        elif self._phase < 2:
            self._set_phase(self._phase + 1)
        else:
            self.return_to_settings()

    def _review_credentials(self):
        if self.registration:
            self.registration.set_step(9)
        self._set_phase(0)

    def _credentials_edited(self):
        if self._loading:
            return
        self.credential_state.setText("待验证")
        self.service.verified = False
        self.confirm_button.hide()
        self._save_timer.start()
        self.refresh()

    def _save_credentials(self):
        if self._loading:
            return True
        self._save_timer.stop()
        try:
            self.service.update(app_id=self.app_id.text().strip(), secret=self.secret.text().strip(), remember_secret=self.remember.isChecked())
            return True
        except Exception as exc:
            self._error(str(exc))
            return False

    def _change(self, key, value):
        if self._loading:
            return
        try:
            if not self._save_credentials():
                return
            self.service.update(**{key: value})
            self.confirm_button.hide()
            self._status("配置已保存 · 自动通知" + ("已启用" if self.service.settings.enabled else "未启用"))
        except Exception as exc:
            self._error(str(exc))
        finally:
            self.refresh()

    def _act(self, action):
        if not self._save_credentials():
            return
        self.confirm_button.hide()
        try:
            action()
        except Exception as exc:
            self._error(str(exc))
        self.refresh()

    def _reveal_secret(self):
        hidden = self.secret.echoMode() == QLineEdit.EchoMode.Password
        self.secret.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.reveal.setText("隐藏" if hidden else "显示")

    def _binding_changed(self, kind, code, seconds, generation):
        self._binding = (kind, code, seconds, generation)
        self.binding_box.setVisible(bool(code))
        self.code.setText(code[:3] + " " + code[3:] if code else "")
        self.countdown.setText(f"剩余 {seconds:02d} 秒")
        self.progress.setValue(seconds)
        self.binding_title.setText("在目标 QQ 群中发送绑定码" if kind == "group" else "向机器人私聊发送绑定码")
        self.binding_instruction.setText(("在目标群里 @机器人，并发送 " if kind == "group" else "向机器人私聊发送 ") + code + "。")
        self.refresh_notice.setText(f"已自动更新第 {generation} 组绑定码，旧码已失效。" if generation > 1 else "到期自动换码并重新计时，旧码立即失效。")
        self.countdown.setStyleSheet("color: #906423;" if seconds <= 10 else "color: #087C58;")

    def _finished(self, operation, ok, message):
        self._status(message)
        if operation == "verify":
            self.credentials_hint.setText(message)
        elif operation == "test":
            self.test_result.setText("文字和图片均已提交，请在 QQ 中确认。" if ok else "图文测试未通过：" + message)
            self.confirm_button.setVisible(ok)
        self.refresh()

    def _confirm_received(self):
        self.test_result.setText("已确认收到文字和图片 · 测试通过")
        self.confirm_button.hide()
        self._status("图文测试通过 · 可启用自动通知")

    def _status(self, message):
        if self.service.operation == "bind" and "验证码：" in message:
            message = "等待 QQ 中的绑定消息 · 到期自动换码"
        self.status.setText(message)

    def _error(self, message):
        self._status(message)

    def refresh(self, *_):
        settings = self.service.settings
        busy = self.service.setup.busy
        pairs = [(self.enabled, settings.enabled), (self.remember, settings.remember_secret)]
        pairs += [(self.target_checks[k], getattr(settings, k + "_enabled")) for k in ("user", "group")]
        pairs += [(control, getattr(settings, key)) for key, control in self.rule_checks.items()]
        for control, checked in pairs:
            with QSignalBlocker(control):
                control.setChecked(checked)
        self.enabled.setText("已启用" if settings.enabled else "未启用")
        self.credential_state.setText("✓ 已验证" if self.service.verified else "待验证")
        for control in (self.app_id, self.secret, self.remember, self.verify_button, *self.target_checks.values()):
            control.setEnabled(not busy)
        self.enabled.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        for kind, control in self.bind_buttons.items():
            control.setEnabled(not busy and self.service.verified)
            oid = getattr(settings, kind + "_openid")
            control.setText("等待绑定…" if busy and kind == self._binding[0] else "重新绑定" if oid else "绑定私聊" if kind == "user" else "绑定群聊")
            self.target_states[kind].setText("已绑定 · OpenID …" + oid[-4:] if oid else "未绑定")
        self.send_button.setEnabled(not busy and settings.ready())
        self.binding_box.setVisible(bool(self._binding[1]))

    def _render_records(self):
        self.records_table.setRowCount(len(self.service.records))
        for row, record in enumerate(self.service.records):
            for column, text in enumerate((record.time, record.event, record.recipient, record.detail)):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if column == 3:
                    item.setForeground(QColor("#087C58" if record.success else "#AC4B42"))
                self.records_table.setItem(row, column, item)
            self.records_table.setRowHeight(row, 54)
        self._record_details()

    def _record_details(self):
        row = self.records_table.currentRow()
        self.record_detail.setPlainText(self.service.records[row].detail if 0 <= row < len(self.service.records) else "")

    def done(self, result):
        self._save_timer.stop()
        self.service.setup.cancel()
        self._save_credentials()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.reveal.setText("显示")
        super().done(result)

    def _style(self):
        self.setStyleSheet(ui_styles("""
            QDialog#QQNotificationDialog { background: #FFFFFF; color: #202A33; }
            QWidget { color: #202A33; }
            QScrollArea, QScrollArea > QWidget > QWidget { background: #FFFFFF; }
            QLabel#Heading { font-size: 20px; font-weight: 500; }
            QLabel#SectionTitle { font-size: 15px; font-weight: 500; }
            QLabel#Muted { color: #64707D; font-size: 12px; }
            QLabel#BindingCode { font-size: 28px; color: #087C58; font-weight: 500; }
            QLabel#Countdown { font-size: 12px; color: #087C58; }
            QPushButton { min-height: 30px; padding: 0 12px; border: 1px solid #E3E8ED; border-radius: 6px; background: #FFFFFF; }
            QPushButton:hover { background: #F2F4F7; border-color: #BAC8D2; }
            QPushButton:disabled { color: #9CA5AE; background: #F8F9FB; }
            QPushButton:checked { color: #087C58; background: #EAF7F1; border-color: #CDE9DD; }
            QPushButton#LinkButton { color: #087C58; border: none; background: transparent; padding: 0 3px; }
            QLineEdit { min-height: 32px; padding: 0 10px; border: 1px solid #E3E8ED; border-radius: 6px; }
            QLineEdit:focus { border: 1px solid #087C58; }
            QLineEdit:disabled { background: #F8F9FB; color: #64707D; }
            QCheckBox { spacing: 7px; min-height: 24px; }
            QCheckBox::indicator { width: 15px; height: 15px; }
            QTabBar::tab { padding: 11px 14px; color: #64707D; border-bottom: 2px solid transparent; }
            QTabBar::tab:selected { color: #087C58; border-bottom: 2px solid #087C58; }
            QFrame#BindingBox, QFrame#BindingBox QLabel { background: #EAF7F1; }
            QFrame#BindingBox { border-radius: 8px; }
            QFrame#TestPanel, QFrame#TestPanel > QLabel { background: #F8F9FB; }
            QFrame#TestPanel { border: 1px solid #E3E8ED; border-radius: 8px; }
            QFrame#MessagePreview { border: 1px solid #E3E8ED; border-radius: 8px; background: #FFFFFF; }
            QLabel#MessageBubble { padding: 10px; background: #F2F4F7; border-radius: 6px; font-size: 12px; }
            QProgressBar { border: none; background: #CEE5DB; border-radius: 2px; }
            QProgressBar::chunk { background: #087C58; border-radius: 2px; }
            QWidget#QQFooter { border-top: 1px solid #E3E8ED; }
            QListWidget#QQStepList { border: none; background: #F8F9FB; font-size: 12px; }
            QListWidget#QQStepList::item { padding: 10px 5px; }
            QListWidget#QQStepList::item:selected { background: #EAF7F1; color: #087C58; border-radius: 5px; }
            QTableWidget, QPlainTextEdit { border: 1px solid #E3E8ED; border-radius: 6px; gridline-color: #E3E8ED; }
            QHeaderView::section { border: none; border-bottom: 1px solid #E3E8ED; padding: 8px; background: #F8F9FB; color: #64707D; }
        """) + primary_button_styles("QPushButton#PrimaryButton"))
