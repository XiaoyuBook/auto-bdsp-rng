from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from auto_bdsp_rng import __version__


class AboutDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        open_source=None,
        open_sponsors=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("关于项目")
        self.resize(520, 420)
        self._open_source = open_source
        self._open_sponsors = open_sponsors
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"珍钻复刻定点自动乱数 v{__version__}")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #111;")
        layout.addWidget(title)

        warning = QLabel("本项目永久免费且开源。\n如果你是付费购买，说明你可能被骗。")
        warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        warning.setStyleSheet("color: #16794f; font-weight: 700; padding: 8px;")
        layout.addWidget(warning)

        body = QLabel(
            "作者：晓宇\n"
            "GitHub：XiaoyuBook\n"
            "License：GPL-3.0-or-later\n\n"
            "允许：学习、修改、非商业传播\n"
            "禁止：二次售卖、付费分发、商业倒卖"
        )
        body.setWordWrap(True)
        body.setStyleSheet("font-size: 13px; color: #333;")
        layout.addWidget(body, 1)

        link_row = QHBoxLayout()
        self.github_button = QPushButton("GitHub 源码")
        self.github_button.clicked.connect(self._handle_open_source)
        link_row.addWidget(self.github_button)
        self.sponsors_button = QPushButton("赞助名单")
        self.sponsors_button.clicked.connect(self._handle_open_sponsors)
        link_row.addWidget(self.sponsors_button)
        layout.addLayout(link_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _handle_open_source(self) -> None:
        if self._open_source is not None:
            self._open_source()

    def _handle_open_sponsors(self) -> None:
        if self._open_sponsors is not None:
            self._open_sponsors()


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

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
