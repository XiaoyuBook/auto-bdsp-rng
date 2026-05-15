from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng import __version__
from auto_bdsp_rng.resources import app_icon_path
from auto_bdsp_rng.ui.sponsor_dialog import SponsorAssets, find_sponsor_assets


PROJECT_REPOSITORY_URL = "https://github.com/XiaoyuBook/auto-bdsp-rng"
EASYCON_URL = "https://github.com/EasyConNS/EasyCon"
PROJECT_XS_URL = "https://github.com/HaKu76/Project_Xs_CHN"
AUTHOR_BILIBILI_URL = "https://space.bilibili.com/269020915"
AUTHOR_GITHUB_URL = "https://github.com/XiaoyuBook"
AUTHOR_EMAIL = "kesong2003@qq.com"


class AboutDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        open_source: Callable[[], object] | None = None,
        open_sponsors: Callable[[], object] | None = None,
        copy_text: Callable[[str], object] | None = None,
        sponsor_assets: SponsorAssets | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于项目")
        self.setMinimumWidth(760)
        self.resize(780, 660)
        self._open_source = open_source
        self._open_sponsors = open_sponsors
        self._copy_text = copy_text
        self._sponsor_assets = sponsor_assets or find_sponsor_assets()
        self._dark = self.palette().window().color().lightness() < 128
        self._link_color = "#9fc8ad" if self._dark else "#23936b"
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(self._stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        layout.addWidget(self._build_header())

        content = QHBoxLayout()
        content.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._project_info_card())
        left.addWidget(self._usage_card())
        left.addWidget(self._contact_card())

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._open_source_card())
        right.addWidget(self._friend_links_card())
        right.addWidget(self._sponsor_card())

        content.addLayout(left, 5)
        content.addLayout(right, 6)
        layout.addLayout(content)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        build = QLabel("Official Open Source Build")
        build.setObjectName("BuildLabel")
        footer.addWidget(build)
        footer.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setObjectName("PrimaryButton")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("AboutHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(18, 16, 18, 16)
        row.setSpacing(14)

        logo = QLabel()
        logo.setObjectName("AppLogo")
        logo.setFixedSize(54, 54)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_path = app_icon_path()
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                logo.setPixmap(pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                logo.setText("BD")
        else:
            logo.setText("BD")
        row.addWidget(logo)

        title_column = QVBoxLayout()
        title_column.setSpacing(4)

        title_line = QHBoxLayout()
        title_line.setSpacing(10)
        title = QLabel("珍钻复刻定点自动乱数")
        title.setObjectName("AboutTitle")
        version = QLabel(f"v{__version__}")
        version.setObjectName("VersionLabel")
        title_line.addWidget(title)
        title_line.addWidget(version)
        title_line.addStretch(1)
        title_column.addLayout(title_line)

        slogan = QLabel("永久免费 · 开源项目 · 谨防倒卖")
        slogan.setObjectName("SloganLabel")
        title_column.addWidget(slogan)

        row.addLayout(title_column, 1)
        return header

    def _project_info_card(self) -> QGroupBox:
        group = self._card("项目信息")
        grid = QGridLayout(group)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for row, (key, value) in enumerate(
            (
                ("作者", "晓宇"),
                ("GitHub", "XiaoyuBook"),
                ("License", "GPL-3.0-or-later"),
            )
        ):
            label = QLabel(f"{key}：")
            label.setObjectName("MutedLabel")
            grid.addWidget(label, row, 0)
            value_label = QLabel(value)
            value_label.setObjectName("ValueLabel")
            grid.addWidget(value_label, row, 1)
        grid.setColumnStretch(1, 1)
        return group

    def _usage_card(self) -> QGroupBox:
        group = self._card("使用说明")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(18)
        layout.addWidget(self._rule_column("允许", ("学习", "修改", "非商业传播"), True))
        layout.addWidget(self._rule_column("禁止", ("二次售卖", "付费分发", "商业倒卖"), False))
        return group

    def _open_source_card(self) -> QGroupBox:
        group = self._card("开源声明")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)

        lead = QLabel("本项目永久免费开源。")
        lead.setObjectName("StatementLead")
        layout.addWidget(lead)

        warning = QLabel("如果你是付费购买，\n说明你可能被骗。")
        warning.setObjectName("WarningText")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        hint = QLabel(f"请通过 <a href='{PROJECT_REPOSITORY_URL}' style='color:{self._link_color};'>GitHub 官方渠道</a> 获取最新版。")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        hint.setOpenExternalLinks(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return group

    def _friend_links_card(self) -> QGroupBox:
        group = self._card("友情链接")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        links = (
            ("伊机控 (EasyCon)", EASYCON_URL),
            ("Project_Xs", PROJECT_XS_URL),
        )
        for name, url in links:
            link = QLabel(f"<a href='{url}' style='text-decoration:none;'>{name}</a>")
            link.setOpenExternalLinks(True)
            link.setObjectName("FriendLink")
            layout.addWidget(link)

        return group

    def _contact_card(self) -> QGroupBox:
        group = self._card("作者联系")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        intro = QLabel("如有 Bug 反馈、功能建议或优化需求，欢迎通过以下方式联系作者：")
        intro.setWordWrap(True)
        intro.setObjectName("MutedLabel")
        layout.addWidget(intro)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        email_btn = QPushButton("📧 邮箱")
        email_btn.setObjectName("ContactButton")
        email_btn.setToolTip(AUTHOR_EMAIL)
        email_btn.clicked.connect(self._handle_copy_email)
        btn_row.addWidget(email_btn, 1)

        bili_btn = QPushButton("📺 B站主页")
        bili_btn.setObjectName("ContactButton")
        bili_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_BILIBILI_URL)))
        btn_row.addWidget(bili_btn, 1)

        gh_btn = QPushButton("🐙 GitHub")
        gh_btn.setObjectName("ContactButton")
        gh_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_GITHUB_URL)))
        btn_row.addWidget(gh_btn, 1)

        layout.addLayout(btn_row)
        return group

    def _handle_copy_email(self) -> None:
        QApplication.clipboard().setText(AUTHOR_EMAIL)

    def _show_qr_popup(self, path: Path | None, title: str) -> None:
        if path is None or not path.exists():
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(400, 440)
        dlg.setStyleSheet("QDialog { background: #fff; }")
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        img = QLabel()
        img.setPixmap(pixmap.scaled(360, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(img, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        dlg.exec()

    def _sponsor_card(self) -> QGroupBox:
        group = self._card("支持项目")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        intro = QLabel("如果项目对你有帮助，欢迎赞助支持持续维护。")
        intro.setWordWrap(True)
        intro.setObjectName("MutedLabel")
        layout.addWidget(intro)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        wechat_btn = QPushButton("微信赞赏")
        wechat_btn.clicked.connect(lambda: self._show_qr_popup(self._sponsor_assets.wechat, "微信赞赏"))
        btn_row.addWidget(wechat_btn, 1)

        alipay_btn = QPushButton("支付宝赞赏")
        alipay_btn.clicked.connect(lambda: self._show_qr_popup(self._sponsor_assets.alipay, "支付宝赞赏"))
        btn_row.addWidget(alipay_btn, 1)

        layout.addLayout(btn_row)

        sponsors = QPushButton("赞助名单")
        sponsors.clicked.connect(self._handle_open_sponsors)
        layout.addWidget(sponsors)
        return group

    def _rule_column(self, title: str, items: tuple[str, ...], allowed: bool) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        title_label = QLabel(title)
        title_label.setObjectName("RuleTitleAllowed" if allowed else "RuleTitleDenied")
        layout.addWidget(title_label)

        mark = "✓" if allowed else "✗"
        for item in items:
            row = QLabel(f"{mark} {item}")
            row.setObjectName("RuleAllowed" if allowed else "RuleDenied")
            layout.addWidget(row)
        layout.addStretch(1)
        return widget

    @staticmethod
    def _card(title: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setObjectName("AboutCard")
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        return group

    def _handle_open_source(self) -> None:
        if self._open_source is not None:
            self._open_source()

    def _handle_open_sponsors(self) -> None:
        if self._open_sponsors is not None:
            self._open_sponsors()

    def _handle_copy_repository(self) -> None:
        if self._copy_text is not None:
            self._copy_text(PROJECT_REPOSITORY_URL)
        else:
            QGuiApplication.clipboard().setText(PROJECT_REPOSITORY_URL)

    def _stylesheet(self) -> str:
        if self._dark:
            bg = "#202221"
            panel = "#2a2d2b"
            card = "#252826"
            border = "#424843"
            text = "#e8ece9"
            muted = "#aeb8b1"
            soft = "#9fc8ad"
            soft_bg = "#25352d"
            hover = "#343934"
            logo_bg = "#324238"
            warning = "#f0b96b"
        else:
            bg = "#f2f1ee"
            panel = "#ffffff"
            card = "#ffffff"
            border = "#c8c6c0"
            text = "#1a1a1a"
            muted = "#6f756f"
            soft = "#23936b"
            soft_bg = "#e8f2ec"
            hover = "#e8e6e1"
            logo_bg = "#e8f2ec"
            warning = "#9b5b1f"

        return f"""
        QDialog {{
            background: {bg};
            color: {text};
            font-size: 12px;
        }}
        QLabel {{
            background: transparent;
            border: 0;
        }}
        QFrame#AboutHeader {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 6px;
        }}
        QLabel#AppLogo {{
            background: {logo_bg};
            border: 1px solid {border};
            border-radius: 8px;
            color: {soft};
            font-size: 16px;
            font-weight: 800;
        }}
        QLabel#AboutTitle {{
            color: {text};
            font-size: 22px;
            font-weight: 800;
        }}
        QLabel#VersionLabel {{
            color: {muted};
            font-size: 13px;
            font-weight: 700;
            padding-top: 5px;
        }}
        QLabel#SloganLabel {{
            color: {soft};
            font-size: 13px;
            font-weight: 700;
        }}
        QGroupBox#AboutCard {{
            background: {card};
            border: 1px solid {border};
            border-radius: 6px;
            margin-top: 10px;
            padding-top: 12px;
            font-weight: 800;
            color: {soft};
        }}
        QGroupBox#AboutCard::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 5px;
            background: {bg};
        }}
        QLabel#MutedLabel {{
            color: {muted};
            line-height: 1.4;
        }}
        QLabel#ValueLabel, QLabel#RepoLabel {{
            color: {text};
            font-weight: 600;
        }}
        QLabel#StatementLead {{
            color: {text};
            font-size: 15px;
            font-weight: 800;
        }}
        QLabel#WarningText {{
            color: {warning};
            font-size: 14px;
            font-weight: 800;
        }}
        QLabel#RuleTitleAllowed, QLabel#RuleTitleDenied {{
            color: {text};
            font-weight: 800;
        }}
        QLabel#RuleAllowed {{
            color: {soft};
            font-weight: 700;
        }}
        QLabel#RuleDenied {{
            color: {warning};
            font-weight: 700;
        }}
        QLabel#BuildLabel {{
            color: {muted};
            font-size: 11px;
        }}
        QLabel#FriendLink {{
            color: {soft};
            font-weight: 600;
            font-size: 13px;
        }}
        QLabel#FriendLink:hover {{
            color: {text};
        }}
        QPushButton {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 4px;
            min-height: 30px;
            padding: 4px 14px;
            color: {text};
            font-weight: 700;
        }}
        QPushButton:hover {{
            background: {hover};
            border-color: {muted};
        }}
        QPushButton#PrimaryButton {{
            background: {soft};
            border-color: {soft};
            color: #ffffff;
            min-width: 86px;
        }}
        QPushButton#PrimaryButton:hover {{
            background: #1e7d5a;
            border-color: #1e7d5a;
        }}
        QPushButton#ContactButton {{
            background: {soft_bg};
            border: 1px solid {border};
            border-radius: 6px;
            min-height: 36px;
            padding: 6px 10px;
            color: {soft};
            font-weight: 700;
            font-size: 13px;
        }}
        QPushButton#ContactButton:hover {{
            background: {soft};
            color: #ffffff;
            border-color: {soft};
        }}
        """


class StartupNoticeDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("开源项目提示")
        self.resize(460, 220)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        message = QLabel(
            "本软件永久免费且开源，更多请点击帮助。\n\n"
            "如果你是付费获得，说明你可能被骗。"
        )
        message.setWordWrap(True)
        message.setStyleSheet("font-size: 13px; color: #222;")
        layout.addWidget(message, 1)

        self.dont_show_again = QCheckBox("不再提示")
        layout.addWidget(self.dont_show_again)

        ok_button = QPushButton("知道了")
        ok_button.clicked.connect(self.accept)
        layout.addWidget(ok_button, alignment=Qt.AlignmentFlag.AlignRight)
