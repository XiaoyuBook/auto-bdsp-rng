from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from auto_bdsp_rng.resources import resource_path


@dataclass(frozen=True)
class SponsorAssets:
    alipay: Path | None = None
    wechat: Path | None = None

    @property
    def available(self) -> bool:
        return self.alipay is not None or self.wechat is not None


def _existing_asset(name: str) -> Path | None:
    path = resource_path("private_assets", "sponsor", name)
    return path if path.exists() else None


def find_sponsor_assets() -> SponsorAssets:
    return SponsorAssets(
        alipay=_existing_asset("alipay.jpg"),
        wechat=_existing_asset("wechat.jpg"),
    )


class SponsorDialog(QDialog):
    def __init__(self, parent=None, assets: SponsorAssets | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("支持项目")
        self.resize(820, 720)
        self.assets = assets or find_sponsor_assets()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        intro = QLabel(
            "本软件永久免费且开源，赞助完全自愿。\n"
            "赞助不会解锁额外功能，也不是购买软件。\n\n"
            "如果这个工具帮到了你，可以通过下方二维码支持项目继续维护。\n\n"
            "如果希望出现在公开赞助名单中，或有功能建议、使用反馈、问题复现信息，\n"
            "也欢迎通过“帮助 -> 作者联系”联系作者。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #333; font-size: 13px;")
        layout.addWidget(intro)

        if self.assets.available:
            row = QHBoxLayout()
            row.setSpacing(18)
            row.addWidget(self._qr_block("支付宝", self.assets.alipay))
            row.addWidget(self._qr_block("微信", self.assets.wechat))
            layout.addLayout(row, 1)
        else:
            missing = QLabel("当前构建未包含赞助二维码。\n如需支持项目或联系作者，请通过“帮助 -> 作者联系”联系作者。")
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            missing.setStyleSheet("color: #666; padding: 32px; border: 1px solid #c8c6c0; background: #f7f6f3;")
            layout.addWidget(missing, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _qr_block(self, title: str, path: Path | None) -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-weight: 700; color: #222;")
        layout.addWidget(label)

        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setMinimumSize(340, 440)
        image.setStyleSheet("background: #fff; border: 1px solid #c8c6c0;")
        if path is not None and path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                image.setPixmap(pixmap.scaled(320, 420, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                image.setText("二维码图片无法读取")
        else:
            image.setText("当前构建未包含此二维码")
        layout.addWidget(image, 1)
        return block
