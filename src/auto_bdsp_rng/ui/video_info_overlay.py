"""Copyable, interactive information anchored to the displayed video image."""

from PySide6.QtCore import QObject, QRect
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)


class _ThresholdInput(QDoubleSpinBox):
    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()


_PANEL_STYLE = """
    QWidget {
        background: transparent; color: #ADB9B1;
        font-size: 11px; font-weight: 400;
    }
    QFrame#VideoSeedOverlay, QFrame#VideoStatusOverlay {
        background: rgba(20, 26, 24, 232);
        border: 1px solid rgba(89, 100, 92, 153);
        border-radius: 4px;
    }
    QLabel {
        background: transparent; border: 0; padding: 0;
        color: #ADB9B1; font-size: 11px; font-weight: 400;
    }
    QLabel#CaptureStatusValue, QLabel#VideoMatchScore, QLineEdit {
        background: transparent; border: 0; padding: 0;
        min-height: 0; max-height: 16px;
        color: #A6D8B8; font-family: "Consolas"; font-size: 11px; font-weight: 400;
        selection-background-color: #38694C;
    }
    QLabel#VideoMatchScore[blinking="true"] { color: #F4D083; }
    QDoubleSpinBox {
        background: #1A2320; color: #CBD5CF;
        border: 1px solid #58685F; border-radius: 3px; padding: 1px 3px;
        min-height: 0; max-height: 22px;
        font-family: "Consolas"; font-size: 11px; font-weight: 400;
    }
    QDoubleSpinBox:disabled { color: #829188; }
"""


class VideoInfoOverlay(QObject):
    """Use two child panels, leaving the rest of the preview hit-test transparent.

    Positions use the fitted pixmap rectangle, including letterboxing and DPR.
    No information is painted into the capture buffer or exported screenshots.
    """

    def __init__(self, preview, seed_panel, seed_fields, progress_label, progress_value, threshold):
        super().__init__(preview)
        self.seed_panel = seed_panel
        self.seed_panel.setParent(preview)
        self.seed_panel.setObjectName("VideoSeedOverlay")
        self.seed_panel.setAccessibleName("视频帧数与 Seed")
        self.seed_panel.setStyleSheet(_PANEL_STYLE)
        self._seed_fields = seed_fields
        self._frame_caption = seed_panel.layout().itemAtPosition(0, 0).widget()
        self._progress_caption = progress_label
        for field in seed_fields:
            field.textChanged.connect(self._refresh)
        self.progress_value = progress_value
        self._threshold_source = threshold
        self._frame_rect = QRect()
        self._selecting = False
        self._seed_available = True
        self._automatic = False
        self.capture_active = False
        self._score = None

        self.status_panel = QFrame(preview)
        self.status_panel.setObjectName("VideoStatusOverlay")
        self.status_panel.setStyleSheet(_PANEL_STYLE)
        status_layout = QVBoxLayout(self.status_panel)
        status_layout.setContentsMargins(7, 5, 7, 5)
        status_layout.setSpacing(4)
        self.match_row = QWidget()
        match_layout = QHBoxLayout(self.match_row)
        match_layout.setContentsMargins(0, 0, 0, 0)
        match_layout.setSpacing(4)
        self.score_value = QLabel("—")
        self.score_value.setObjectName("VideoMatchScore")
        self.score_value.setAccessibleName("实时匹配分数")
        self.score_value.setToolTip("当前视频画面的眼睛模板匹配分数")
        self.threshold = _ThresholdInput()
        self.threshold.setLocale(threshold.locale())
        self.threshold.setRange(threshold.minimum(), threshold.maximum())
        self.threshold.setDecimals(threshold.decimals())
        self.threshold.setSingleStep(0.01)
        self.threshold.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.threshold.setKeyboardTracking(False)
        self.threshold.setFixedSize(48, 22)
        self.threshold.setAccessibleName("视频眨眼匹配阈值")
        self.threshold.setToolTip("与 Seed 捕捉配置的识别阈值同步；捕捉期间不可修改")
        self.threshold.setValue(threshold.value())
        self.threshold.valueChanged.connect(threshold.setValue)
        threshold.valueChanged.connect(self.threshold.setValue)
        threshold.valueChanged.connect(self._refresh_score_color)
        self.match_caption = QLabel("匹配")
        self.threshold_caption = QLabel("阈值")
        match_layout.addWidget(self.match_caption)
        match_layout.addWidget(self.score_value)
        match_layout.addSpacing(4)
        match_layout.addWidget(self.threshold_caption)
        match_layout.addWidget(self.threshold)
        status_layout.addWidget(self.match_row)

        self.progress_row = QWidget()
        self.progress_row.setAccessibleName("眨眼捕捉进度")
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(12)
        progress_layout.addWidget(progress_label)
        progress_layout.addStretch(1)
        progress_layout.addWidget(progress_value)
        status_layout.addWidget(self.progress_row)
        self._refresh()

    def set_frame_rect(self, rect: QRect) -> None:
        self._frame_rect = QRect(rect)
        self._refresh()

    def set_selecting(self, selecting: bool) -> None:
        self._selecting = selecting
        self._refresh()

    def set_match_score(self, score: float | None) -> None:
        self._score = score
        self.score_value.setText("—" if score is None else f"{score:.4f}")
        self._refresh_score_color()
        self._refresh()

    def _refresh_score_color(self) -> None:
        blinking = self._score is not None and 0.01 < self._score < self.threshold.value()
        if self.score_value.property("blinking") != blinking:
            self.score_value.setProperty("blinking", blinking)
            self.score_value.style().unpolish(self.score_value)
            self.score_value.style().polish(self.score_value)

    def set_automatic(self, automatic: bool) -> None:
        self._automatic = automatic
        self._refresh()

    def begin_capture(self, *, automatic: bool = False) -> None:
        self._automatic = automatic
        self._seed_available = not automatic
        if not self.capture_active:
            self.progress_value.setText("—")
            self._score = None
        self.capture_active = True
        self._refresh()

    def set_capture_progress(self, done: int, total: int, *, automatic: bool = False) -> None:
        self.begin_capture(automatic=automatic)
        self.progress_value.setText(f"{done}/{total}")
        self._refresh()

    def finish_capture(self, *, success: bool = False) -> None:
        self.capture_active = False
        if success:
            self._seed_available = True
        self._refresh()

    def _refresh(self) -> None:
        has_frame = self._frame_rect.isValid()
        # Keep a completed manual result copyable after disconnecting video.
        bounds = self._frame_rect if has_frame else self.parent().contentsRect()
        visible = bounds.isValid() and not self._selecting
        visible = visible and (has_frame or (not self._automatic and any(field.text() for field in self._seed_fields)))
        show_seed = visible and self._seed_available
        show_match = self._score is not None and not self._automatic
        self.seed_panel.setVisible(show_seed)
        self.match_row.setVisible(show_match)
        self.progress_row.setVisible(self.capture_active)
        show_status = visible and has_frame and (show_match or self.capture_active)
        self.status_panel.setVisible(show_status)
        self.threshold.setEnabled(not self.capture_active and not self._automatic
                                  and self._threshold_source.isEnabled())
        if not visible:
            return

        bounds = bounds.adjusted(6, 6, -6, -6)
        compact = bounds.width() < 350
        self._frame_caption.setText("帧" if compact else "当前帧数")
        self._progress_caption.setText("进度" if compact else "眨眼进度")
        self.threshold.setVisible(not compact)
        self.threshold_caption.setVisible(not compact)
        self.match_caption.setVisible(not compact)
        self.match_row.setToolTip(f"匹配分数 / 阈值 {self._threshold_source.value():.2f}")
        if show_status:
            self.status_panel.layout().activate()
            self.status_panel.adjustSize()
            self.status_panel.move(bounds.right() - self.status_panel.width() + 1, bounds.top())
            self.status_panel.setVisible(bounds.contains(self.status_panel.geometry()))
            self.status_panel.raise_()
        if show_seed:
            for field in self._seed_fields:
                field.ensurePolished()
                field.setFixedWidth(field.fontMetrics().horizontalAdvance("0" * 16) + 6)
            self.seed_panel.layout().activate()
            self.seed_panel.adjustSize()
            top = bounds.top()
            # Like the reference's narrow-video rule, keep full seeds readable
            # below the status when both panels do not fit on the same line.
            if show_status and self.seed_panel.width() + self.status_panel.width() + 6 > bounds.width():
                top += self.status_panel.height() + 6
            self.seed_panel.move(bounds.left(), top)
            self.seed_panel.setVisible(bounds.contains(self.seed_panel.geometry()))
            self.seed_panel.raise_()
