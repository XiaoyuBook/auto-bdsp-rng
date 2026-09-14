"""Compact guide card, including search-range choices backed by the real field."""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QStyle, QStyleOptionButton, QVBoxLayout, QWidget,
)


class RangeChoiceButton(QPushButton):
    def __init__(self, label: str, value: str) -> None:
        super().__init__(label + " · " + value)
        self.label, self.value_label = label, value
        self.setObjectName("GuideChoice")
        self.setCheckable(True)
        self.setAutoDefault(False)
        self.setMinimumHeight(70)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(136, 70)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 70)

    def paintEvent(self, event) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#087C58" if self.isChecked() else "#52606D"))
        painter.drawText(QRect(12, 10, self.width() - 20, 22), Qt.AlignmentFlag.AlignLeft, self.label)
        if self.isChecked():
            painter.drawText(QRect(self.width() - 20, 4, 16, 18), Qt.AlignmentFlag.AlignCenter, "✓")
        font.setPixelSize(17)
        painter.setFont(font)
        painter.setPen(QColor("#202A33"))
        painter.drawText(QRect(12, 34, self.width() - 20, 28), Qt.AlignmentFlag.AlignLeft, self.value_label)


class GuideTip(QFrame):
    resized = Signal()
    rangeValidityChanged = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("GuideTip")
        self.setFixedWidth(338)
        self.setStyleSheet("""
            QFrame#GuideTip { background: #FCFEFD; border: 1px solid #CFE2D7; border-radius: 16px; }
            QFrame#GuideTip QLabel, QFrame#GuideTip QPushButton, QFrame#GuideTip QLineEdit {
                font-family: 'Microsoft YaHei UI'; font-size: 13px; font-weight: 400; color: #52606D;
                background: transparent; border: 0;
            }
            QFrame#GuideTip QLabel#GuideStep { color: #087C58; font-size: 12px; }
            QFrame#GuideTip QLabel#GuideTitle { color: #202A33; font-size: 18px; font-weight: 500; }
            QFrame#GuideTip QPushButton { border: 1px solid #E0E5EB; border-radius: 6px; padding: 6px 10px; min-height: 20px; max-height: 20px; }
            QFrame#GuideTip QPushButton:hover { border-color: #A9CEBE; background: #F4F9F6; }
            QFrame#GuideTip QPushButton:disabled { color: #9AA7A0; background: #F7F9F8; }
            QFrame#GuideTip QPushButton#GuideNext { background: #087C58; color: white; border-color: #087C58; }
            QFrame#GuideTip QPushButton#GuideNext:disabled { background: #F7F9F8; color: #9AA7A0; border-color: #E0E5EB; }
            QFrame#GuideTip QPushButton#GuideClose { border: 0; padding: 0; font-size: 19px; min-height: 28px; max-height: 28px; }
            QFrame#GuideTip QPushButton#GuideSkip { border: 0; padding: 0; font-size: 12px; min-height: 34px; max-height: 34px; }
            QFrame#GuideTip QPushButton#GuideChoice { background: white; border-radius: 8px; padding: 0; min-height: 68px; max-height: 68px; }
            QFrame#GuideTip QPushButton#GuideChoice:checked { border-color: #087C58; background: #F0F8F4; }
            QFrame#GuideTip QPushButton:focus, QFrame#GuideTip QLineEdit:focus { border: 1px solid #087C58; outline: 0; }
            QFrame#GuideTip QLabel#GuideNote { color: #78847D; font-size: 11px; }
            QFrame#GuideTip QLabel#GuideFeedback { color: #087C58; font-size: 11px; }
            QFrame#GuideTip QLabel#GuideTime { color: #64707D; font-size: 12px; background: #F7F9F8; border-radius: 7px; padding: 10px; }
            QFrame#GuideTip QLineEdit { background: white; border: 1px solid #B7CFC3; border-radius: 7px; font-size: 16px; padding: 6px 10px; min-height: 20px; max-height: 20px; }
            QFrame#GuideTip QScrollArea, QWidget#GuideBody { background: white; border: 0; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(10)
        top = QHBoxLayout()
        self.step = QLabel()
        self.step.setObjectName("GuideStep")
        top.addWidget(self.step, 1)
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("GuideClose")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setAccessibleName("暂时收起引导")
        self.close_button.setToolTip("收起提示，保留进度")
        top.addWidget(self.close_button)
        layout.addLayout(top)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.verticalScrollBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body.setObjectName("GuideBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        self.body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.title = QLabel()
        self.title.setObjectName("GuideTitle")
        self.title.setWordWrap(True)
        self.copy = QLabel()
        self.copy.setWordWrap(True)
        self.body_layout.addWidget(self.title)
        self.body_layout.addWidget(self.copy)
        self.range_options = QWidget()
        range_layout = QVBoxLayout(self.range_options)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(10)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self.presets: dict[str, RangeChoiceButton] = {}
        for i, (key, label, value) in enumerate((
            ("regular", "普通闪光", "10 万帧"), ("starter", "御三家闪光", "5000 帧"),
            ("rare", "体型／6V 闪光", "1000 万帧起"), ("custom", "自定义帧数", "自己填写"),
        )):
            button = RangeChoiceButton(label, value)
            self.presets[key] = button
            button.clicked.connect(lambda _checked=False, choice=key: self._choose(choice))
            grid.addWidget(button, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        range_layout.addLayout(grid)
        self.custom_container = QWidget()
        custom_layout = QVBoxLayout(self.custom_container)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(5)
        custom_layout.addWidget(QLabel("搜索帧数"))
        custom_row = QHBoxLayout()
        self.custom_input = QLineEdit()
        self.custom_input.setAccessibleName("自定义搜索帧数")
        self.custom_input.setPlaceholderText("输入整数帧数")
        self.custom_input.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.custom_input.textEdited.connect(self._custom_edited)
        custom_row.addWidget(self.custom_input, 1)
        custom_row.addWidget(QLabel("帧"))
        custom_layout.addLayout(custom_row)
        range_layout.addWidget(self.custom_container)
        self.custom_container.hide()
        self.feedback = QLabel("也可以沿用当前值，直接下一步。")
        self.feedback.setObjectName("GuideFeedback")
        self.feedback.setWordWrap(True)
        range_layout.addWidget(self.feedback)
        time_note = QLabel("过帧速度与图鉴数量有关。\n全国图鉴全满时，100 万帧约需 10 分钟。")
        time_note.setObjectName("GuideTime")
        time_note.setWordWrap(True)
        range_layout.addWidget(time_note)
        self.body_layout.addWidget(self.range_options)
        self.range_options.hide()
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.previous_button = QPushButton("上一步")
        self.next_button = QPushButton("下一步")
        self.next_button.setObjectName("GuideNext")
        self.skip_button = QPushButton("跳过讲解")
        self.skip_button.setObjectName("GuideSkip")
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)
        controls.addStretch(1)
        controls.addWidget(self.skip_button)
        for button in (self.previous_button, self.next_button, self.skip_button, self.close_button):
            button.setAutoDefault(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addLayout(controls)
        note = QLabel("可随时收起提示，稍后继续。")
        note.setObjectName("GuideNote")
        layout.addWidget(note)
        self.range_field = None
        self.range_valid = True
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 45))
        self.setGraphicsEffect(shadow)

    def fit_height(self, available: int) -> None:
        self.ensurePolished()
        width = self.width() - 40
        self.body_layout.invalidate()
        natural = self.body_layout.totalHeightForWidth(width)
        natural = max(natural, self.body_layout.sizeHint().height())
        chrome = 34 + 28 + 36 + max(34, self.next_button.sizeHint().height()) + 18
        height = min(natural + chrome, max(chrome + 50, available))
        scrolling = natural + chrome > height
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if scrolling else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if scrolling:
            natural = max(natural, self.body_layout.totalHeightForWidth(width - 16))
        self.body.setMinimumHeight(natural)
        self.setFixedHeight(height)

    def show_step(self, spec, *, search: bool = False) -> None:
        self.step.setText(spec.caption)
        self.title.setText(spec.title)
        self.copy.setText(spec.copy)
        self.range_options.setVisible(search)
        self.scroll.verticalScrollBar().setValue(0)

    def reset_range(self) -> None:
        for button in self.presets.values():
            button.setChecked(False)
        self.custom_container.hide()
        self.range_valid = True
        self.feedback.setText("也可以沿用当前值，直接下一步。")
        self.rangeValidityChanged.emit(True)

    def _choose(self, choice: str) -> None:
        if self.range_field is None:
            return
        for key, button in self.presets.items():
            button.setChecked(key == choice)
        self.range_valid = True
        self.rangeValidityChanged.emit(True)
        self.custom_container.setVisible(choice == "custom")
        if choice == "custom":
            self.custom_input.setText(str(self.range_field.value()))
            self.feedback.setText("填写后自动同步到搜索范围。")
            self.custom_input.setFocus()
            self.custom_input.selectAll()
        else:
            value = {"regular": 100_000, "starter": 5000, "rare": 10_000_000}[choice]
            self.range_field.setValue(value)
            self.feedback.setText("已填入 1000 万帧，可通过自定义继续调高。" if choice == "rare" else f"已填入 {value:,} 帧")
        self.resized.emit()

    def _custom_edited(self, text: str) -> None:
        cleaned = text.strip()
        # Bound parsing too: a pasted long integer must not reach Python's digit limit.
        valid = bool(cleaned) and len(cleaned) <= 10 and cleaned.isascii() and cleaned.isdecimal()
        value = int(cleaned) if valid else -1
        valid = valid and self.range_field.minimum() <= value <= self.range_field.maximum()
        self.range_valid = valid
        if valid:
            self.range_field.setValue(value)
        self.feedback.setText(f"已填入 {value:,} 帧" if valid else "请输入 0～10 亿之间的整数帧数。")
        self.rangeValidityChanged.emit(valid)
        self.resized.emit()

    def sync_range(self, value: int) -> None:
        if not self.custom_input.hasFocus():
            self.feedback.setText(f"当前搜索范围：{value:,} 帧")
        if self.custom_container.isVisible() and not self.custom_input.hasFocus():
            self.custom_input.setText(str(value))
            self.range_valid = True
            self.rangeValidityChanged.emit(True)
        selected = next((key for key, button in self.presets.items() if button.isChecked()), None)
        if selected and selected != "custom" and value != {"regular": 100_000, "starter": 5000, "rare": 10_000_000}[selected]:
            self.presets[selected].setChecked(False)
