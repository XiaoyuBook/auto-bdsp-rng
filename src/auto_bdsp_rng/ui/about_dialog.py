from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng import __version__
from auto_bdsp_rng.resources import app_icon_path, resource_path
from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox as QCheckBox
from auto_bdsp_rng.ui.sponsor_dialog import SponsorAssets, find_sponsor_assets
from auto_bdsp_rng.ui.workspace_theme import primary_button_styles, ui_font, ui_styles
from auto_bdsp_rng.ui.workspace_controls import workspace_icon


PROJECT_REPOSITORY_URL = "https://github.com/XiaoyuBook/auto-bdsp-rng"
EASYCON_URL = "https://github.com/EasyConNS/EasyCon"
PROJECT_XS_URL = "https://github.com/Lincoln-LM/Project_Xs"
POKEFINDER_URL = "https://github.com/Admiral-Fish/PokeFinder"
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
        self.setFont(ui_font())
        self.setWindowTitle("关于项目")
        self.setMinimumWidth(780)
        self.resize(800, 660)
        self._open_source = open_source
        self._open_sponsors = open_sponsors
        self._copy_text = copy_text
        self._sponsor_assets = sponsor_assets or find_sponsor_assets()
        self._dark = self.palette().window().color().lightness() < 128
        self._link_color = "#9fc8ad" if self._dark else "#087c58"
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(self._stylesheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(16)

        layout.addWidget(self._build_header())

        cards = QWidget()
        cards.setObjectName("AboutCards")
        grid = QGridLayout(cards)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        grid.addWidget(self._project_info_card(), 0, 0)
        grid.addWidget(self._open_source_card(),   0, 1)
        grid.addWidget(self._usage_card(),         1, 0)
        grid.addWidget(self._friend_links_card(),  1, 1)
        grid.addWidget(self._contact_card(),       2, 0)
        grid.addWidget(self._sponsor_card(),       2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll = QScrollArea()
        scroll.setObjectName("AboutScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(cards)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        build = QLabel("Official Open Source Build · 官方开源构建版")
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
        row.setSpacing(16)

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
        title_column.setSpacing(2)

        title_line = QHBoxLayout()
        title_line.setSpacing(10)
        title = QLabel("珍钻复刻自动乱数")
        title.setObjectName("AboutTitle")
        version = QLabel(f"Version {__version__}")
        version.setObjectName("VersionLabel")
        title_line.addWidget(title)
        title_line.addWidget(version)
        title_line.addStretch(1)
        title_column.addLayout(title_line)

        subtitle = QLabel("珍钻复刻定点自动化辅助工具")
        subtitle.setObjectName("AboutSubtitle")
        title_column.addWidget(subtitle)

        slogan = QLabel("永久免费 · 开源透明 · 请勿付费购买")
        slogan.setObjectName("SloganLabel")
        title_column.addWidget(slogan)

        row.addLayout(title_column, 1)
        return header

    def _project_info_card(self) -> QGroupBox:
        group = self._card("项目信息")
        grid = QGridLayout(group)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for row, (key, value) in enumerate(
            (
                ("作者", "晓宇"),
                ("GitHub", "XiaoyuBook"),
                ("License", "GPL-3.0-or-later"),
                ("界面字体", "MiSans · 小米"),
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
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(18)
        layout.addWidget(self._rule_column("允许", ("学习研究", "修改代码", "非商业分享"), True))
        layout.addWidget(self._rule_column("禁止", ("付费售卖", "打包分发牟利", "冒充官方发布"), False))
        return group

    def _open_source_card(self) -> QGroupBox:
        group = self._card("开源声明")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(9)

        lead = QLabel("本项目永久免费开源。")
        lead.setObjectName("StatementLead")
        layout.addWidget(lead)

        warning = QLabel("任何付费售卖均非官方行为，请勿购买。")
        warning.setObjectName("WarningText")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        hint = QLabel(f"请认准 <a href='{PROJECT_REPOSITORY_URL}' style='color:{self._link_color};'>GitHub 官方仓库</a> 获取最新版。")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        hint.setOpenExternalLinks(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return group

    def _friend_links_card(self) -> QGroupBox:
        group = self._card("友情链接")
        layout = QGridLayout(group)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)
        layout.setRowStretch(3, 1)

        assets_dir = resource_path("docs", "assets")

        projects = (
            (assets_dir / "friend_easycon.ico",    "伊机控",     "Switch 自动化控制\n脚本执行与串口连接", EASYCON_URL),
            (assets_dir / "friend_project_xs.png",  "Project_Xs", "眨眼测种",                               PROJECT_XS_URL),
            (assets_dir / "friend_pokefinder.ico",  "PokeFinder", "Gen 8 定点生成\n个体值与异色筛选",       POKEFINDER_URL),
        )

        for column, (icon_path, name, desc, url) in enumerate(projects):
            layout.setColumnStretch(column, 1)
            # 图标按钮：全部56x56
            icon_btn = QPushButton()
            icon_btn.setFixedSize(56, 56)
            icon_btn.setObjectName("FriendIconBtn")
            icon_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            icon_btn.clicked.connect(lambda checked, u=url: QDesktopServices.openUrl(QUrl(u)))
            if icon_path.exists():
                icon_btn.setIcon(QIcon(str(icon_path)))
                # The narrow feather needs more height than the round logos to
                # carry similar visual weight within the shared 56px surface.
                icon_btn.setIconSize(QSize(44, 44) if name == "Project_Xs" else QSize(40, 40))
            layout.addWidget(icon_btn, 0, column, Qt.AlignmentFlag.AlignCenter)

            # 名称
            name_lbl = QLabel(name)
            name_lbl.setObjectName("FriendLinkName")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(name_lbl, 1, column)

            # 描述
            desc_lbl = QLabel(desc)
            desc_lbl.setObjectName("FriendDesc")
            desc_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            # Keep the deliberate phrase breaks; the grid reserves each line's
            # natural width and shares the name row across all three projects.
            desc_lbl.setWordWrap(False)
            layout.addWidget(desc_lbl, 2, column)

        return group

    def _contact_card(self) -> QGroupBox:
        group = self._card("作者联系")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel("如需反馈 Bug、提出功能建议或优化需求，请尽量附上触发场景、复现步骤和截图，并通过以下方式联系作者：")
        intro.setWordWrap(True)
        intro.setObjectName("MutedLabel")
        layout.addWidget(intro)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        icon_color = "#B3BDC8" if self._dark else "#626D79"
        email_btn = QPushButton("发送邮件")
        email_btn.setIcon(workspace_icon("mail", icon_color))
        email_btn.setIconSize(QSize(16, 16))
        email_btn.setObjectName("ContactButton")
        email_btn.setToolTip(AUTHOR_EMAIL)
        email_btn.clicked.connect(self._handle_copy_email)
        btn_row.addWidget(email_btn, 1)

        bili_btn = QPushButton("B站")
        bili_btn.setIcon(workspace_icon("tv", icon_color))
        bili_btn.setIconSize(QSize(16, 16))
        bili_btn.setObjectName("ContactButton")
        bili_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_BILIBILI_URL)))
        btn_row.addWidget(bili_btn, 1)

        gh_btn = QPushButton("GitHub")
        gh_btn.setIcon(workspace_icon("github", icon_color))
        gh_btn.setIconSize(QSize(16, 16))
        gh_btn.setObjectName("ContactButton")
        gh_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_GITHUB_URL)))
        btn_row.addWidget(gh_btn, 1)

        layout.addLayout(btn_row)
        return group

    def _handle_copy_email(self) -> None:
        QApplication.clipboard().setText(AUTHOR_EMAIL)

    def _show_qr_popup(self, path: Path | None, title: str) -> None:
        pixmap = QPixmap(str(path)) if path is not None and path.exists() else QPixmap()
        dlg = QDialog(self)
        dlg.setObjectName("AboutQrDialog")
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(400, 440)
        dlg.setStyleSheet(
            "QDialog#AboutQrDialog { background: #ffffff; color: #202A33; }"
            " QDialog#AboutQrDialog QPushButton { background: #ffffff; color: #202A33;"
            " border: 1px solid #E0E5EB; border-radius: 7px; min-height: 32px; padding: 0 14px; }"
            " QDialog#AboutQrDialog QPushButton:hover { background: #F7F8FA;"
            " border-color: #B8C2CC; }"
        )
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)
        img = QLabel()
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if pixmap.isNull():
            message = "当前构建未包含此二维码" if path is None or not path.exists() else "二维码图片无法读取"
            img.setText(message)
            img.setWordWrap(True)
            img.setStyleSheet("color: #626D79; padding: 32px; border: 1px solid #E0E5EB; background: #F7F8FA;")
        else:
            img.setPixmap(pixmap.scaled(360, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(img, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        dlg.exec()

    def _sponsor_card(self) -> QGroupBox:
        group = self._card("支持项目")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        intro = QLabel("如果本项目对你有帮助，欢迎自愿赞助，支持后续维护。")
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
        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
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
            bg = "#20262E"
            panel = "#2A323D"
            card = "#252D37"
            border = "#434D5A"
            card_border = "#353F4B"
            text = "#EBEEF2"
            muted = "#B3BDC8"
            soft = "#9fc8ad"
            soft_bg = "#25352d"
            hover = "#343E4B"
            logo_bg = "#324238"
            warning = "#f0b96b"
        else:
            bg = "#F2F4F7"
            panel = "#ffffff"
            card = "#ffffff"
            border = "#E0E5EB"
            card_border = "#E9EDF2"
            text = "#202A33"
            muted = "#626D79"
            soft = "#087c58"
            soft_bg = "#EAF7F1"
            hover = "#F7F8FA"
            logo_bg = "#EAF7F1"
            warning = "#8a6818"

        return ui_styles(f"""
        QDialog {{
            background: {bg};
            color: {text};
            font-size: 14px;
            font-weight: 400;
        }}
        QLabel {{
            color: {text};
            font-size: 14px;
            font-weight: 400;
            background: transparent;
            border: 0;
        }}
        QScrollArea#AboutScroll, QWidget#AboutCards {{
            background: transparent;
            border: 0;
        }}
        QScrollBar:vertical {{
            background: {bg}; width: 8px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {border}; border-radius: 4px; min-height: 24px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QFrame#AboutHeader {{
            background: {panel};
            border: 1px solid {card_border};
            border-radius: 12px;
        }}
        QLabel#AppLogo {{
            background: {logo_bg};
            border: 1px solid {border};
            border-radius: 8px;
            color: {soft};
            font-size: 16px;
            font-weight: 500;
        }}
        QLabel#AboutTitle {{
            color: {text};
            font-size: 20px;
            font-weight: 500;
        }}
        QLabel#VersionLabel {{
            color: {muted};
            font-size: 12px;
            font-weight: 500;
            padding-top: 6px;
        }}
        QLabel#AboutSubtitle {{
            color: {muted};
            font-size: 14px;
        }}
        QLabel#SloganLabel {{
            color: {soft};
            font-size: 14px;
            font-weight: 500;
        }}
        QGroupBox#AboutCard {{
            background: {card};
            border: 1px solid {card_border};
            border-radius: 12px;
            margin-top: 0;
            padding: 40px 0 0 0;
            font-size: 16px;
            font-weight: 500;
            color: {soft};
        }}
        QGroupBox#AboutCard::title {{
            color: {text};
            subcontrol-origin: padding;
            subcontrol-position: top left;
            left: 16px;
            top: 12px;
            padding: 0;
            background: transparent;
        }}
        QLabel#MutedLabel {{
            color: {muted};
            font-size: 12px;
            line-height: 1.4;
        }}
        QLabel#ValueLabel, QLabel#RepoLabel {{
            color: {text};
            font-weight: 400;
        }}
        QLabel#StatementLead {{
            color: {text};
            font-size: 16px;
            font-weight: 500;
        }}
        QLabel#WarningText {{
            color: {warning};
            font-size: 14px;
            font-weight: 500;
        }}
        QLabel#RuleTitleAllowed, QLabel#RuleTitleDenied {{
            color: {text};
            font-weight: 500;
        }}
        QLabel#RuleAllowed {{
            color: {soft};
            font-weight: 400;
        }}
        QLabel#RuleDenied {{
            color: {warning};
            font-weight: 400;
        }}
        QLabel#BuildLabel {{
            color: {muted};
            font-size: 12px;
        }}
        QPushButton#FriendIconBtn {{
            background: {hover};
            border: 1px solid {card_border};
            border-radius: 10px;
            padding: 0;
            min-width: 56px; max-width: 56px;
            min-height: 56px; max-height: 56px;
            font-size: 22px;
        }}
        QPushButton#FriendIconBtn:hover {{
            background: {soft_bg};
            border-color: {soft};
        }}
        QLabel#FriendLinkName {{
            color: {text};
            font-weight: 500;
            font-size: 12px;
        }}
        QLabel#FriendDesc {{
            color: {muted};
            font-size: 12px;
            line-height: 1.4;
        }}
        QPushButton {{
            font-size: 14px;
            background: {panel};
            border: 1px solid {border};
            border-radius: 7px;
            min-height: 30px;
            padding: 4px 14px;
            color: {text};
            font-weight: 500;
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
            background: {panel};
            border: 1px solid {border};
            border-radius: 7px;
            min-height: 36px;
            padding: 6px 10px;
            color: {text};
            font-weight: 500;
            font-size: 14px;
        }}
        QPushButton#ContactButton:hover {{
            background: {soft};
            color: {bg if self._dark else '#ffffff'};
            border-color: {soft};
        }}
        """ + primary_button_styles("QPushButton#PrimaryButton"))


class StartupNoticeDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFont(ui_font())
        self.setWindowTitle("开源项目提示")
        self.setObjectName("StartupNoticeDialog")
        self.resize(460, 220)
        self.setStyleSheet(
            "QDialog#StartupNoticeDialog { background: #ffffff; color: #202A33; }"
            " QDialog#StartupNoticeDialog QLabel { background: transparent; color: #202A33; }"
            " QDialog#StartupNoticeDialog QPushButton { background: #087c58; color: #ffffff;"
            " border: 1px solid #087c58; border-radius: 7px; min-height: 32px; padding: 0 14px; }"
            " QDialog#StartupNoticeDialog QPushButton:hover { background: #066a4b;"
            " border-color: #066a4b; }"
            " QDialog#StartupNoticeDialog QCheckBox { color: #202A33; }"
        )
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        message = QLabel(
            "本软件永久免费且开源，更多请点击帮助。\n\n"
            "任何付费售卖均非官方行为，请勿购买。"
        )
        message.setWordWrap(True)
        message.setStyleSheet("font-size: 14px; color: #202A33;")
        layout.addWidget(message, 1)

        self.dont_show_again = QCheckBox("不再提示")
        layout.addWidget(self.dont_show_again)

        ok_button = QPushButton("知道了")
        ok_button.clicked.connect(self.accept)
        layout.addWidget(ok_button, alignment=Qt.AlignmentFlag.AlignRight)
