from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.delay_strategy import (
    DelayEstimate,
    DelaySampleRound,
    DelaySampleStatus,
    DelayStrategy,
    DelayStrategyConfig,
    MultiCandidatePolicy,
    evaluate_delay_samples,
)
from auto_bdsp_rng.ui.numeric_locale import set_c_locale


QT_INT_MAX = 2_147_483_647
DELAY_HISTORY_PAGE_SIZE = 10
DELAY_STRATEGY_LABELS = (
    (DelayStrategy.FIXED, "固定 delay"),
    (DelayStrategy.LAST, "上次实际 delay"),
    (DelayStrategy.MODE, "众数"),
    (DelayStrategy.MEDIAN, "中位数"),
    (DelayStrategy.ROLLING_MEAN, "滚动平均值"),
    (DelayStrategy.EWMA, "指数平滑"),
    (DelayStrategy.TRIMMED_MEAN, "截尾平均"),
    (DelayStrategy.DENSE_INTERVAL, "密集区间"),
)
DELAY_STRATEGY_LABEL_BY_ID = {
    strategy.value: label for strategy, label in DELAY_STRATEGY_LABELS
}

_SAMPLE_STATUS_LABELS = {
    DelaySampleStatus.EXCLUDED: "已划除",
    DelaySampleStatus.EMPTY: "无有效候选",
    DelaySampleStatus.FIXED_STRATEGY: "固定策略不使用",
    DelaySampleStatus.AMBIGUOUS: "已忽略 · 多个候选",
    DelaySampleStatus.OUTSIDE_WINDOW: "超出统计窗口",
    DelaySampleStatus.USED: "已使用",
}

_LUCIDE_PATHS = {
    "settings-2": (
        '<path d="M20 7h-9"/><path d="M14 17H5"/>'
        '<circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>'
    ),
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "arrow-up-right": '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
    "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
    "trash-2": (
        '<path d="M3 6h18"/><path d="M19 6l-1 14H6L5 6"/>'
        '<path d="M8 6V4h8v2"/><path d="M10 11v6"/><path d="M14 11v6"/>'
    ),
    "rotate-ccw": (
        '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>'
    ),
}


def delay_lucide_icon(name: str, color: str = "#5F6C66", size: int = 18) -> QIcon:
    """Render the small Lucide line icons used by the delay controls."""

    paths = _LUCIDE_PATHS[name]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        'viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round">{paths}</svg>'
    )
    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def _format_candidates(candidates: tuple[int, ...]) -> str:
    return " / ".join(str(value) for value in candidates) if candidates else "-"


def _format_sample_time(observed_at: str | None) -> str:
    if not observed_at:
        return "时间未记录"
    try:
        return datetime.fromisoformat(observed_at).strftime("%Y-%m-%d\n%H:%M:%S")
    except ValueError:
        return "时间未记录"


def _sample_statuses(
    config: DelayStrategyConfig,
    samples: list[DelaySampleRound],
) -> dict[int, DelaySampleStatus]:
    evaluations = evaluate_delay_samples(config, samples)
    statuses = {
        evaluation.sample.round_number: evaluation.status
        for evaluation in evaluations
    }
    for index, sample in enumerate(samples):
        if not sample.excluded:
            continue
        restored = list(samples)
        restored[index] = replace(sample, excluded=False)
        restored_evaluations = evaluate_delay_samples(config, restored)
        statuses[sample.round_number] = restored_evaluations[index].status
    return statuses


class _DelayComboBox(QComboBox):
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = "#202A28" if self.isEnabled() else "#A5AEA9"
        painter.setPen(QPen(QColor(color), 1.5))
        center_x = self.width() - 17
        center_y = self.height() // 2
        painter.drawLine(center_x - 4, center_y - 2, center_x, center_y + 2)
        painter.drawLine(center_x, center_y + 2, center_x + 4, center_y - 2)
        painter.end()


class _DelayCheckBox(QCheckBox):
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.isChecked():
            option = QStyleOptionButton()
            self.initStyleOption(option)
            indicator = self.style().subElementRect(
                QStyle.SubElement.SE_CheckBoxIndicator, option, self
            )
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
            left, top = indicator.x(), indicator.y()
            painter.drawLine(left + 3, top + 7, left + 6, top + 10)
            painter.drawLine(left + 6, top + 10, left + 11, top + 4)


class DelaySummaryButton(QPushButton):
    """Keep the settings icon at the trailing edge of the summary field."""

    def paintEvent(self, event) -> None:  # noqa: N802
        option = QStyleOptionButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""
        option.icon = QIcon()
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        text_rect = self.rect().adjusted(12, 0, -38, 0)
        painter.setPen(option.palette.buttonText().color())
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width()),
        )
        self.icon().paint(
            painter, QRect(self.width() - 26, (self.height() - 16) // 2, 16, 16)
        )


class _SampleDelegate(QStyledItemDelegate):
    """Draw the timestamp as secondary text without changing the record text."""

    def paint(self, painter, option, index) -> None:
        if index.column() != 0 or "\n" not in str(index.data()):
            super().paint(painter, option, index)
            return
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        lines = cell.text.splitlines()
        cell.text = ""
        cell.widget.style().drawControl(
            QStyle.ControlElement.CE_ItemViewItem, cell, painter, cell.widget
        )
        painter.save()
        painter.setClipRect(cell.rect)
        main_font = QFont(cell.font)
        main_font.setPixelSize(12)
        time_font = QFont(main_font)
        time_font.setPixelSize(11)
        main_height = QFontMetrics(main_font).height()
        time_height = QFontMetrics(time_font).height()
        height = main_height + 3 + time_height * (len(lines) - 1)
        text_rect = cell.rect.adjusted(6, 0, -6, 0)
        top = cell.rect.y() + (cell.rect.height() - height) // 2
        painter.setFont(main_font)
        painter.setPen(cell.palette.text().color())
        painter.drawText(
            QRect(text_rect.x(), top, text_rect.width(), main_height),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            lines[0],
        )
        painter.setFont(time_font)
        painter.setPen(QColor("#86918B" if main_font.strikeOut() else "#5F6C66"))
        top += main_height + 3
        for line in lines[1:]:
            painter.drawText(
                QRect(text_rect.x(), top, text_rect.width(), time_height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
            top += time_height
        painter.restore()

    def updateEditorGeometry(self, editor, option, index) -> None:  # noqa: N802
        if index.column() == 3:
            editor.setGeometry(
                option.rect.right() - 39,
                option.rect.y() + (option.rect.height() - 34) // 2,
                34,
                34,
            )
        else:
            super().updateEditorGeometry(editor, option, index)


class _SampleTable(QTableWidget):
    _COLUMN_RATIOS = (0.27, 0.26, 0.34, 0.13)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(4)
        self.setHorizontalHeaderLabels(("轮次", "反查 delay", "本次计算", "操作"))
        self.horizontalHeaderItem(3).setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.setItemDelegate(_SampleDelegate(self))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalHeader().hide()
        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(0)
        header.setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        header.setFixedHeight(34)

    def content_height(self) -> int:
        return 2 + self.horizontalHeader().height() + sum(
            self.rowHeight(row) for row in range(self.rowCount())
        )

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(590, self.content_height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        available = max(0, self.viewport().width())
        assigned = 0
        for column, ratio in enumerate(self._COLUMN_RATIOS[:-1]):
            width = round(available * ratio)
            self.setColumnWidth(column, width)
            assigned += width
        self.setColumnWidth(3, max(42, available - assigned))


def _populate_sample_table(
    table: QTableWidget,
    samples: list[DelaySampleRound],
    statuses: Mapping[int, DelaySampleStatus],
    *,
    show_time: bool,
    toggle_callback: Callable[[int, bool], None],
) -> None:
    for row in range(table.rowCount()):
        action = table.cellWidget(row, 3)
        if action is not None:
            action.hide()
            table.removeCellWidget(row, 3)
            action.deleteLater()
    table.clearContents()
    table.setRowCount(len(samples))
    muted_brush = QBrush(QColor("#86918b"))
    accent_brush = QBrush(QColor("#087958"))
    for row, sample in enumerate(samples):
        round_text = f"第 {sample.round_number} 轮"
        if show_time:
            round_text += f"\n{_format_sample_time(sample.observed_at)}"
        status = statuses.get(sample.round_number, DelaySampleStatus.EMPTY)
        values = (
            round_text,
            _format_candidates(sample.candidates),
            _SAMPLE_STATUS_LABELS.get(status, "-"),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            if sample.excluded:
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
                item.setForeground(muted_brush)
            elif column == 2:
                item.setForeground(
                    accent_brush if status is DelaySampleStatus.USED else muted_brush
                )
            table.setItem(row, column, item)

        table.setItem(row, 3, QTableWidgetItem(""))

        action = QToolButton(table)
        action.setObjectName("DelaySampleActionButton")
        action.setProperty("restoreAction", sample.excluded)
        action.setAutoRaise(True)
        action.setFixedSize(34, 34)
        action.setIcon(
            delay_lucide_icon(
                "rotate-ccw" if sample.excluded else "trash-2",
                "#087958" if sample.excluded else "#5F6C66",
                15,
            )
        )
        action.setIconSize(QSize(15, 15))
        action_name = (
            f"恢复第 {sample.round_number} 轮样本"
            if sample.excluded
            else f"删除第 {sample.round_number} 轮样本"
        )
        action.setToolTip(action_name)
        action.setAccessibleName(action_name)
        action.clicked.connect(
            lambda _checked=False, number=sample.round_number, excluded=sample.excluded: (
                toggle_callback(number, not excluded)
            )
        )
        table.setCellWidget(row, 3, action)
        table.setRowHeight(row, 72 if show_time else 50)
    table.updateGeometry()


class _DelayPageStack(QStackedWidget):
    def minimumSizeHint(self) -> QSize:  # noqa: N802
        page = self.currentWidget()
        return page.minimumSizeHint() if page is not None else QSize()

    def sizeHint(self) -> QSize:  # noqa: N802
        page = self.currentWidget()
        return page.sizeHint() if page is not None else QSize()


class _TitleBar(QFrame):
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class _FieldRow(QWidget):
    def __init__(
        self,
        label_text: str,
        field: QWidget,
        *,
        below: QWidget | None = None,
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.label = QLabel(label_text)
        self.label.setObjectName("DelayFieldLabel")
        self.label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.label.setFixedWidth(132)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(5)
        layout.setColumnMinimumWidth(0, 132)
        layout.setColumnStretch(1, 1)
        layout.addWidget(self.label, 0, 0)
        layout.addWidget(field, 0, 1)
        if below is not None:
            layout.addWidget(below, 1, 1)
        if tooltip:
            self.label.setToolTip(tooltip)
            field.setToolTip(tooltip)


class DelayHistoryPage(QWidget):
    sampleExclusionRequested = Signal(int, bool)
    showSampleTimeChanged = Signal(bool)
    backRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DelayHistoryPage")
        self._samples: list[DelaySampleRound] = []
        self._statuses: dict[int, DelaySampleStatus] = {}
        self._page_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 14, 22, 18)
        layout.setSpacing(0)

        self.back_button = QPushButton("返回策略设置")
        self.back_button.setObjectName("DelayLinkButton")
        self.back_button.setAccessibleName("返回策略设置")
        self.back_button.setIcon(delay_lucide_icon("arrow-left", "#087958", 14))
        self.back_button.setIconSize(QSize(14, 14))
        self.back_button.setFixedHeight(29)
        back_row = QHBoxLayout()
        back_row.setContentsMargins(0, 0, 0, 0)
        back_row.addWidget(self.back_button)
        back_row.addStretch(1)
        layout.addLayout(back_row)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 13, 0, 9)
        heading.setSpacing(12)
        self.species_label = QLabel("-")
        self.species_label.setObjectName("DelayHistorySpecies")
        self.count_label = QLabel("全部 0 轮")
        self.count_label.setObjectName("DelayMutedLabel")
        heading.addWidget(self.species_label)
        heading.addWidget(self.count_label)
        heading.addStretch(1)
        layout.addLayout(heading)

        self.runtime_label = QLabel("本轮尚未运行 · 下轮预计 100（固定 delay）")
        self.runtime_label.setObjectName("DelayHistoryEstimate")
        self.runtime_label.setFixedHeight(38)
        layout.addWidget(self.runtime_label)
        layout.addSpacing(12)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 9)
        self.show_time_check = _DelayCheckBox("显示时间")
        self.show_time_check.setFixedHeight(34)
        controls.addWidget(self.show_time_check)
        controls.addStretch(1)
        order_label = QLabel("按记录时间倒序")
        order_label.setObjectName("DelayMutedLabel")
        controls.addWidget(order_label)
        layout.addLayout(controls)

        self.table = _SampleTable()
        self.table.setObjectName("DelayHistoryTable")
        self.table.setMinimumHeight(180)
        layout.addWidget(self.table, 1)

        self.empty_label = QLabel("当前精灵暂无样本")
        self.empty_label.setObjectName("DelayEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setFixedHeight(54)
        self.empty_label.hide()
        layout.addWidget(self.empty_label)

        pagination = QHBoxLayout()
        pagination.setContentsMargins(0, 14, 0, 0)
        pagination.setSpacing(8)
        self.page_summary = QLabel("第 0 条，共 0 条")
        self.page_summary.setObjectName("DelayMutedLabel")
        pagination.addWidget(self.page_summary)
        pagination.addStretch(1)
        self.previous_button = QPushButton("上一页")
        self.next_button = QPushButton("下一页")
        self.previous_button.setFixedHeight(34)
        self.next_button.setFixedHeight(34)
        pagination.addWidget(self.previous_button)
        pagination.addWidget(self.next_button)
        layout.addLayout(pagination)

        self.back_button.clicked.connect(self.backRequested.emit)
        self.show_time_check.toggled.connect(self._show_time_toggled)
        self.previous_button.clicked.connect(lambda: self._change_page(-1))
        self.next_button.clicked.connect(lambda: self._change_page(1))

    def set_runtime_state(
        self,
        *,
        species_name: str,
        active_delay: int | None,
        estimate: DelayEstimate,
        strategy_label: str,
        samples: list[DelaySampleRound],
        statuses: Mapping[int, DelaySampleStatus],
    ) -> None:
        self._samples = list(samples)
        self._statuses = dict(statuses)
        self.species_label.setText(species_name)
        deleted_count = sum(sample.excluded for sample in samples)
        count_text = f"全部 {len(samples)} 轮"
        if deleted_count:
            count_text += f" · 已删除 {deleted_count} 轮"
        self.count_label.setText(count_text)
        active_text = "尚未运行" if active_delay is None else str(active_delay)
        self.runtime_label.setText(
            f"本轮 {active_text} · 下轮预计 {estimate.value}（{strategy_label}）"
        )
        max_page = max(0, (len(samples) - 1) // DELAY_HISTORY_PAGE_SIZE)
        self._page_index = min(self._page_index, max_page)
        self._render_page()

    def set_show_time(self, show: bool) -> None:
        self.show_time_check.blockSignals(True)
        self.show_time_check.setChecked(bool(show))
        self.show_time_check.blockSignals(False)
        self._render_page()

    def reset_page(self) -> None:
        self._page_index = 0
        self._render_page()

    def accept(self) -> None:
        """Compatibility alias for callers that used the former dialog."""

        self.backRequested.emit()

    @Slot(bool)
    def _show_time_toggled(self, checked: bool) -> None:
        self.showSampleTimeChanged.emit(bool(checked))
        self._render_page()

    def _change_page(self, offset: int) -> None:
        max_page = max(0, (len(self._samples) - 1) // DELAY_HISTORY_PAGE_SIZE)
        self._page_index = min(max(0, self._page_index + offset), max_page)
        self._render_page()

    def _render_page(self) -> None:
        newest_first = list(reversed(self._samples))
        start = self._page_index * DELAY_HISTORY_PAGE_SIZE
        page = newest_first[start : start + DELAY_HISTORY_PAGE_SIZE]
        _populate_sample_table(
            self.table,
            page,
            self._statuses,
            show_time=self.show_time_check.isChecked(),
            toggle_callback=self.sampleExclusionRequested.emit,
        )
        total = len(newest_first)
        self.table.setVisible(bool(total))
        self.empty_label.setVisible(not total)
        if page:
            height = self.table.content_height()
            self.table.setMinimumHeight(min(180, height))
            self.table.setMaximumHeight(height)
        if total:
            self.page_summary.setText(
                f"第 {start + 1}–{start + len(page)} 条，共 {total} 条"
            )
        else:
            self.page_summary.setText("第 0 条，共 0 条")
        self.previous_button.setEnabled(start > 0)
        self.next_button.setEnabled(start + len(page) < total)
        dialog = self.window()
        if isinstance(dialog, DelayStrategyDialog) and dialog.isVisible():
            QTimer.singleShot(0, dialog._resize_for_current_page)


class DelayStrategyDialog(QDialog):
    """Prototype-faithful delay settings and per-species sample history."""

    settingsEdited = Signal()
    settingsSaveRequested = Signal(object)
    clearSamplesRequested = Signal()
    sampleExclusionRequested = Signal(int, bool)
    showSampleTimeChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DelayStrategyDialog")
        self.setWindowTitle("delay 策略设置")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumHeight(500)
        self.setMinimumWidth(684)
        self.setMaximumWidth(684)
        self.resize(684, 884)

        self._sample_rounds: list[DelaySampleRound] = []
        self._sample_statuses: dict[int, DelaySampleStatus] = {}
        self._recommended_delay: int | None = None
        self._auto_reverse_enabled = False
        self._active_delay: int | None = None
        self._estimate = DelayEstimate(
            value=DelayStrategyConfig().baseline_delay,
            strategy=DelayStrategy.FIXED,
            effective_strategy=DelayStrategy.FIXED,
            valid_round_count=0,
            candidate_count=0,
            used_fallback=False,
        )
        self._species_name = "未选择"
        self._saved_config = DelayStrategyConfig()
        self._feedback = ""

        self.setStyleSheet(self._stylesheet())
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(24, 24, 24, 24)
        window_layout.setSpacing(0)

        self.surface = QFrame()
        self.surface.setObjectName("DelayDialogSurface")
        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)
        window_layout.addWidget(self.surface)

        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(24, 47, 33, 24))
        self.surface.setGraphicsEffect(shadow)

        self.title_bar = _TitleBar()
        self.title_bar.setObjectName("DelayTitleBar")
        self.title_bar.setFixedHeight(56)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(22, 11, 15, 11)
        title_layout.setSpacing(8)
        self.title_label = QLabel("delay 策略设置")
        self.title_label.setObjectName("DelayDialogTitle")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)
        self.close_button = QToolButton()
        self.close_button.setObjectName("DelayCloseButton")
        self.close_button.setFixedSize(34, 34)
        self.close_button.setIcon(delay_lucide_icon("x", "#202A28", 17))
        self.close_button.setIconSize(QSize(17, 17))
        self.close_button.setToolTip("关闭设置")
        self.close_button.setAccessibleName("关闭设置")
        title_layout.addWidget(self.close_button)
        surface_layout.addWidget(self.title_bar)

        self.page_stack = _DelayPageStack()
        self.page_stack.setObjectName("DelayPageStack")
        surface_layout.addWidget(self.page_stack, 1)

        self.settings_page = QWidget()
        self.settings_page.setObjectName("DelaySettingsPage")
        settings_page_layout = QVBoxLayout(self.settings_page)
        settings_page_layout.setContentsMargins(0, 0, 0, 0)
        settings_page_layout.setSpacing(0)

        self.body_scroll = QScrollArea()
        self.body_scroll.setObjectName("DelaySettingsScroll")
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.body_widget = QWidget()
        self.body_widget.setObjectName("DelaySettingsBody")
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(22, 19, 22, 0)
        body_layout.setSpacing(0)
        body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.body_scroll.setWidget(self.body_widget)
        settings_page_layout.addWidget(self.body_scroll, 1)

        owner_row = QHBoxLayout()
        owner_row.setContentsMargins(0, 0, 0, 0)
        owner_row.setSpacing(12)
        owner_caption = QLabel("当前精灵")
        owner_caption.setObjectName("DelayMutedLabel")
        self.species_name_label = QLabel("-")
        self.species_name_label.setObjectName("DelaySpeciesName")
        self.species_scope_label = QLabel("独立保存设置与样本")
        self.species_scope_label.setObjectName("DelayMutedLabel")
        owner_row.addWidget(owner_caption)
        owner_row.addWidget(self.species_name_label)
        owner_row.addStretch(1)
        owner_row.addWidget(self.species_scope_label)
        body_layout.addLayout(owner_row)
        body_layout.addSpacing(23)

        self.form = QVBoxLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(14)
        self._form_rows: dict[QWidget, _FieldRow] = {}

        self.strategy_combo = _DelayComboBox()
        self.strategy_combo.setFixedHeight(34)
        for strategy, label in DELAY_STRATEGY_LABELS:
            self.strategy_combo.addItem(label, strategy.value)
        self.strategy_description = QLabel()
        self.strategy_description.setObjectName("DelayFieldNote")
        self.strategy_description.setWordWrap(True)
        self.strategy_description.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._add_field_row(
            "delay 策略",
            self.strategy_combo,
            below=self.strategy_description,
            tooltip="选择下一轮使用的 delay 计算方式。",
        )

        self.baseline_delay = self._spin(0, DelayStrategyConfig().baseline_delay)
        baseline_field = self._number_field(self.baseline_delay, "帧")
        self.recommendation_widget = QWidget()
        self.recommendation_widget.setMinimumHeight(29)
        recommendation_row = QHBoxLayout(self.recommendation_widget)
        recommendation_row.setContentsMargins(0, 0, 0, 0)
        recommendation_row.setSpacing(12)
        self.recommendation_label = QLabel("推荐 delay：暂无推荐")
        self.recommendation_label.setObjectName("DelayMutedLabel")
        self.use_recommendation_button = QPushButton("使用推荐值")
        self.use_recommendation_button.setObjectName("DelayLinkButton")
        self.use_recommendation_button.setFixedHeight(29)
        recommendation_row.addWidget(self.recommendation_label)
        recommendation_row.addWidget(self.use_recommendation_button)
        recommendation_row.addStretch(1)
        self._add_field_row(
            "基准 delay",
            self.baseline_delay,
            visible_field=baseline_field,
            below=self.recommendation_widget,
            tooltip="固定策略直接使用此值；动态策略没有有效样本时回退到此值。",
        )

        self.window_size = self._spin(1, DelayStrategyConfig().window_size, maximum=1000)
        self._add_field_row(
            "统计窗口",
            self.window_size,
            visible_field=self._number_field(self.window_size, "轮"),
            tooltip="使用最近 N 个有效轮次。",
        )

        self.multi_candidate_widget = _DelayComboBox()
        self.multi_candidate_widget.setFixedHeight(34)
        self.multi_candidate_widget.addItem("忽略该轮", MultiCandidatePolicy.IGNORE.value)
        self.multi_candidate_widget.addItem("按轮加权", MultiCandidatePolicy.WEIGHTED.value)
        self._add_field_row(
            "多个候选",
            self.multi_candidate_widget,
            tooltip="决定一轮反查出多个 delay 时是否参与统计。",
        )

        self.ewma_weight_percent = self._spin(
            1,
            round(DelayStrategyConfig().ewma_alpha * 100),
            maximum=100,
        )
        self._add_field_row(
            "最新样本权重",
            self.ewma_weight_percent,
            visible_field=self._number_field(self.ewma_weight_percent, "%"),
            tooltip="权重越高，指数平滑越跟随最近一轮。",
        )

        self.dense_interval_width = self._spin(
            0,
            DelayStrategyConfig().dense_interval_width,
            maximum=100,
        )
        self._add_field_row(
            "密集区间跨度",
            self.dense_interval_width,
            visible_field=self._number_field(self.dense_interval_width, "帧"),
            tooltip="密集区间允许的最大候选跨度。",
        )
        body_layout.addLayout(self.form)

        # Compatibility controls retained for callers that selected the former
        # segmented policy buttons directly. They are not part of the visible UI.
        self.ignore_multi_candidate_button = QPushButton(self)
        self.ignore_multi_candidate_button.setCheckable(True)
        self.ignore_multi_candidate_button.hide()
        self.weight_multi_candidate_button = QPushButton(self)
        self.weight_multi_candidate_button.setCheckable(True)
        self.weight_multi_candidate_button.hide()

        body_layout.addSpacing(23)
        self.runtime_summary = QFrame()
        self.runtime_summary.setObjectName("DelayRuntimeSummary")
        runtime_layout = QHBoxLayout(self.runtime_summary)
        runtime_layout.setContentsMargins(0, 14, 0, 14)
        runtime_layout.setSpacing(0)
        current_column = self._runtime_column("本轮使用", next_value=False)
        next_column = self._runtime_column("下轮预计", next_value=True)
        runtime_layout.addWidget(current_column, 100)
        divider = QFrame()
        divider.setObjectName("DelayRuntimeDivider")
        divider.setFixedWidth(1)
        runtime_layout.addWidget(divider)
        runtime_layout.addWidget(next_column, 145)
        body_layout.addWidget(self.runtime_summary)
        body_layout.addSpacing(13)

        self.valid_sample_count = QLabel("0 轮", self)
        self.valid_sample_count.hide()

        self.samples_frame = QFrame()
        self.samples_frame.setObjectName("DelaySamplesFrame")
        samples_layout = QVBoxLayout(self.samples_frame)
        samples_layout.setContentsMargins(0, 0, 0, 12)
        samples_layout.setSpacing(0)
        samples_header = QHBoxLayout()
        samples_header.setContentsMargins(0, 0, 0, 0)
        samples_header.setSpacing(10)
        self.samples_toggle = QToolButton()
        self.samples_toggle.setObjectName("DelaySampleToggle")
        self.samples_toggle.setText("当前精灵的样本")
        self.samples_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.samples_toggle.setIcon(
            delay_lucide_icon("chevron-down", "#5F6C66", 14)
        )
        self.samples_toggle.setIconSize(QSize(14, 14))
        self.samples_toggle.setCheckable(True)
        self.samples_toggle.setChecked(True)
        self.samples_toggle.setMinimumHeight(47)
        self.sample_summary_label = QLabel("共 0 轮 · 本次使用 0 轮")
        self.sample_summary_label.setObjectName("DelayMutedLabel")
        samples_header.addWidget(self.samples_toggle)
        samples_header.addStretch(1)
        samples_header.addWidget(self.sample_summary_label)
        samples_layout.addLayout(samples_header)

        self.sample_details = QWidget()
        details_layout = QVBoxLayout(self.sample_details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(0)
        sample_controls = QHBoxLayout()
        sample_controls.setContentsMargins(0, 0, 0, 9)
        sample_controls.setSpacing(12)
        self.show_sample_time_check = _DelayCheckBox("显示时间")
        self.show_sample_time_check.setFixedHeight(34)
        self.view_all_samples_button = QPushButton("查看全部 0 轮")
        self.view_all_samples_button.setObjectName("DelayLinkButton")
        self.view_all_samples_button.setFixedHeight(29)
        self.view_all_samples_button.setIcon(
            delay_lucide_icon("arrow-up-right", "#087958", 14)
        )
        self.view_all_samples_button.setIconSize(QSize(14, 14))
        self.view_all_samples_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        sample_controls.addWidget(self.show_sample_time_check)
        sample_controls.addStretch(1)
        sample_controls.addWidget(self.view_all_samples_button)
        details_layout.addLayout(sample_controls)

        self.recent_samples_table = _SampleTable()
        self.recent_samples_table.setObjectName("DelayRecentSamplesTable")
        self.recent_samples_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        details_layout.addWidget(self.recent_samples_table)

        self.empty_samples_label = QLabel("暂无样本，等待自动反查积累")
        self.empty_samples_label.setObjectName("DelayEmptyLabel")
        self.empty_samples_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_samples_label.setFixedHeight(54)
        details_layout.addWidget(self.empty_samples_label)

        sample_footer = QHBoxLayout()
        sample_footer.setContentsMargins(0, 7, 0, 0)
        sample_footer.setSpacing(10)
        self.recent_samples_note = QLabel("仅保存当前精灵的反查结果")
        self.recent_samples_note.setObjectName("DelayMutedLabel")
        self.clear_samples_button = QPushButton("清空当前精灵样本")
        self.clear_samples_button.setObjectName("DelayDangerLinkButton")
        self.clear_samples_button.setFixedHeight(29)
        sample_footer.addWidget(self.recent_samples_note)
        sample_footer.addStretch(1)
        sample_footer.addWidget(self.clear_samples_button)
        details_layout.addLayout(sample_footer)

        self.clear_confirm_frame = QFrame()
        self.clear_confirm_frame.setObjectName("DelayClearConfirm")
        clear_layout = QHBoxLayout(self.clear_confirm_frame)
        clear_layout.setContentsMargins(0, 11, 0, 0)
        clear_layout.setSpacing(8)
        self.clear_confirm_label = QLabel("清空当前精灵的全部样本？")
        self.clear_confirm_label.setWordWrap(True)
        clear_layout.addWidget(self.clear_confirm_label, 1)
        self.keep_samples_button = QPushButton("保留样本")
        self.confirm_clear_button = QPushButton("确认清空")
        self.confirm_clear_button.setObjectName("DelayDangerButton")
        clear_layout.addWidget(self.keep_samples_button)
        clear_layout.addWidget(self.confirm_clear_button)
        self.clear_confirm_frame.hide()
        details_layout.addWidget(self.clear_confirm_frame)

        samples_layout.addWidget(self.sample_details)
        body_layout.addWidget(self.samples_frame)

        self.recent_samples = QLineEdit("暂无样本", self)
        self.recent_samples.setReadOnly(True)
        self.recent_samples.hide()

        self.apply_status = QLabel("修改保存后从下一轮生效")
        self.apply_status.setObjectName("DelayApplyStatus")
        self.apply_status.setWordWrap(True)
        self.apply_status.setMinimumHeight(45)
        body_layout.addWidget(self.apply_status)

        self.footer = QFrame()
        self.footer.setObjectName("DelayFooter")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(22, 13, 22, 13)
        footer_layout.setSpacing(9)
        self.save_state_label = QLabel("未选择的设置")
        self.save_state_label.setObjectName("DelayMutedLabel")
        footer_layout.addWidget(self.save_state_label)
        footer_layout.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.ok_button = QPushButton("保存设置")
        self.ok_button.setObjectName("DelayPrimaryButton")
        self.ok_button.setMinimumWidth(96)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.ok_button)
        settings_page_layout.addWidget(self.footer)
        self.button_box = self.footer
        self.restore_defaults_button = QPushButton("恢复默认值", self)
        self.restore_defaults_button.hide()

        self.page_stack.addWidget(self.settings_page)
        self.history_page = DelayHistoryPage()
        self.history_dialog = self.history_page
        self.page_stack.addWidget(self.history_page)

        self.close_button.clicked.connect(self.reject)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self._save_settings)
        self.restore_defaults_button.clicked.connect(self.restore_defaults)
        self.strategy_combo.currentIndexChanged.connect(self._strategy_changed)
        for spin in (
            self.baseline_delay,
            self.window_size,
            self.ewma_weight_percent,
            self.dense_interval_width,
        ):
            spin.valueChanged.connect(self._numeric_setting_changed)
        self.multi_candidate_widget.currentIndexChanged.connect(
            self._policy_changed
        )
        self.ignore_multi_candidate_button.clicked.connect(
            lambda: self._select_compat_policy(MultiCandidatePolicy.IGNORE)
        )
        self.weight_multi_candidate_button.clicked.connect(
            lambda: self._select_compat_policy(MultiCandidatePolicy.WEIGHTED)
        )
        self.use_recommendation_button.clicked.connect(self._use_recommendation)
        self.samples_toggle.toggled.connect(self._samples_toggled)
        self.show_sample_time_check.toggled.connect(
            self._show_sample_time_toggled
        )
        self.view_all_samples_button.clicked.connect(self._open_history)
        self.clear_samples_button.clicked.connect(self._show_clear_confirmation)
        self.keep_samples_button.clicked.connect(self._hide_clear_confirmation)
        self.confirm_clear_button.clicked.connect(self._confirm_clear_samples)
        self.history_page.backRequested.connect(self._show_settings)
        self.history_page.sampleExclusionRequested.connect(
            self._request_sample_exclusion
        )
        self.history_page.showSampleTimeChanged.connect(
            self._history_show_time_changed
        )
        self._update_strategy_rows()
        self._update_recent_table_height()

    @staticmethod
    def _stylesheet() -> str:
        return """
            QDialog#DelayStrategyDialog { background: transparent; }
            QFrame#DelayDialogSurface {
                background: #FFFFFF;
                border: 1px solid #DCE4DF;
                border-radius: 10px;
            }
            QFrame#DelayDialogSurface QWidget {
                background: transparent;
                color: #202A28;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
                font-size: 14px;
                font-weight: 400;
            }
            QFrame#DelayDialogSurface QLabel { background: transparent; border: 0; }
            QFrame#DelayTitleBar {
                background: #FFFFFF;
                border: 0;
                border-bottom: 1px solid #DCE4DF;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }
            QFrame#DelayDialogSurface QLabel#DelayDialogTitle {
                font-size: 15px; font-weight: 500;
            }
            QToolButton#DelayCloseButton {
                background: transparent; border: 0; border-radius: 6px; padding: 0;
            }
            QToolButton#DelayCloseButton:hover { background: #F3F6F4; }
            QScrollArea#DelaySettingsScroll, QScrollArea#DelaySettingsScroll > QWidget,
            QWidget#DelaySettingsBody, QWidget#DelaySettingsPage,
            QWidget#DelayHistoryPage, QStackedWidget#DelayPageStack {
                background: #FFFFFF; border: 0;
            }
            QFrame#DelayDialogSurface QLabel#DelayMutedLabel,
            QFrame#DelayDialogSurface QLabel#DelayFieldNote,
            QFrame#DelayDialogSurface QLabel#DelayApplyStatus {
                color: #5F6C66; font-size: 12px;
            }
            QFrame#DelayDialogSurface QLabel#DelaySpeciesName,
            QFrame#DelayDialogSurface QLabel#DelayHistorySpecies {
                font-size: 15px; font-weight: 500;
            }
            QFrame#DelayDialogSurface QLabel#DelayFieldLabel { padding-top: 7px; }
            QFrame#DelayDialogSurface QComboBox,
            QFrame#DelayDialogSurface QSpinBox {
                background: #FFFFFF;
                border: 1px solid #DCE4DF;
                border-radius: 6px;
                min-height: 32px; max-height: 32px;
                padding: 0 10px;
                color: #202A28;
                selection-background-color: #EAF6EF;
            }
            QFrame#DelayDialogSurface QComboBox:focus,
            QFrame#DelayDialogSurface QSpinBox:focus { border-color: #087958; }
            QFrame#DelayDialogSurface QComboBox::drop-down {
                subcontrol-origin: border; subcontrol-position: top right;
                width: 34px; border: 0;
                border-top-right-radius: 6px; border-bottom-right-radius: 6px;
            }
            QFrame#DelayDialogSurface QComboBox::down-arrow { image: none; }
            QFrame#DelayDialogSurface QComboBox QAbstractItemView {
                background: #FFFFFF; color: #202A28;
                border: 1px solid #DCE4DF;
                selection-background-color: #EAF6EF;
                selection-color: #087958;
                padding: 4px;
            }
            QFrame#DelayDialogSurface QComboBox QAbstractItemView::item {
                min-height: 30px; padding: 0 6px;
            }
            QFrame#DelayDialogSurface QSpinBox QLineEdit {
                background: transparent; border: 0; padding: 0;
                min-height: 0; max-height: 16777215px;
                color: #202A28; font-size: 14px;
            }
            QFrame#DelayDialogSurface QPushButton {
                background: #FFFFFF;
                border: 1px solid #DCE4DF;
                border-radius: 6px;
                min-height: 32px;
                padding: 0 13px;
                font-weight: 400;
                color: #202A28;
            }
            QFrame#DelayDialogSurface QPushButton:hover { background: #F3F6F4; }
            QFrame#DelayDialogSurface QPushButton:disabled {
                color: #A5AEA9; background: #F7F9F8;
            }
            QFrame#DelayDialogSurface QPushButton:focus {
                border-color: #087958;
            }
            QFrame#DelayDialogSurface QPushButton#DelayLinkButton,
            QFrame#DelayDialogSurface QPushButton#DelayDangerLinkButton {
                background: transparent; border: 0; padding: 0;
                min-height: 29px; max-height: 29px; font-size: 12px;
            }
            QFrame#DelayDialogSurface QPushButton#DelayLinkButton { color: #087958; }
            QFrame#DelayDialogSurface QPushButton#DelayDangerLinkButton { color: #B74536; }
            QFrame#DelayDialogSurface QPushButton#DelayLinkButton:hover,
            QFrame#DelayDialogSurface QPushButton#DelayDangerLinkButton:hover {
                background: transparent;
            }
            QFrame#DelayDialogSurface QPushButton#DelayLinkButton:disabled,
            QFrame#DelayDialogSurface QPushButton#DelayDangerLinkButton:disabled {
                color: #A5AEA9; background: transparent;
            }
            QFrame#DelayDialogSurface QFrame#DelayRuntimeSummary {
                background: #F3F6F4; border: 0; border-radius: 7px;
            }
            QFrame#DelayDialogSurface QFrame#DelayRuntimeDivider {
                background: #DCE4DF; border: 0;
            }
            QFrame#DelayDialogSurface QLabel#DelayRuntimeCaption,
            QFrame#DelayDialogSurface QLabel#DelayRuntimeNote,
            QFrame#DelayDialogSurface QLabel#DelayRuntimeUnit {
                color: #5F6C66; font-size: 12px;
            }
            QFrame#DelayDialogSurface QLabel#DelayRuntimeValue,
            QFrame#DelayDialogSurface QLabel#DelayRuntimeNextValue {
                font-size: 23px; font-weight: 500;
            }
            QFrame#DelayDialogSurface QLabel#DelayRuntimeNextValue { color: #087958; }
            QFrame#DelaySamplesFrame {
                background: #FFFFFF; border: 0; border-bottom: 1px solid #DCE4DF;
            }
            QToolButton#DelaySampleToggle {
                background: transparent; border: 0; padding: 0;
                color: #202A28; font-weight: 400;
            }
            QFrame#DelayDialogSurface QCheckBox {
                background: transparent; spacing: 7px; font-size: 12px;
            }
            QFrame#DelayDialogSurface QCheckBox::indicator {
                width: 12px; height: 12px; border-radius: 2px;
                border: 1px solid #A5AEA9; background: #FFFFFF;
            }
            QFrame#DelayDialogSurface QCheckBox::indicator:checked {
                background: #087958; border-color: #087958;
            }
            QFrame#DelayDialogSurface QCheckBox::indicator:hover,
            QFrame#DelayDialogSurface QCheckBox::indicator:focus {
                border-color: #087958;
            }
            QFrame#DelayDialogSurface QTableWidget#DelayRecentSamplesTable,
            QFrame#DelayDialogSurface QTableWidget#DelayHistoryTable {
                background: #FFFFFF;
                alternate-background-color: #FFFFFF;
                border: 0;
                gridline-color: transparent;
                color: #202A28;
                font-size: 12px;
            }
            QTableWidget#DelayRecentSamplesTable::item,
            QTableWidget#DelayHistoryTable::item {
                padding: 5px 6px;
                border: 0;
                border-bottom: 1px solid #DCE4DF;
            }
            QTableWidget#DelayRecentSamplesTable QHeaderView::section,
            QTableWidget#DelayHistoryTable QHeaderView::section {
                background: #F3F6F4;
                color: #5F6C66;
                border: 0;
                border-bottom: 1px solid #DCE4DF;
                padding: 0 6px;
                font-weight: 400;
                font-size: 12px;
            }
            QToolButton#DelaySampleActionButton {
                background: transparent; border: 0; border-radius: 6px;
                color: #5F6C66; padding: 0; font-size: 20px;
            }
            QToolButton#DelaySampleActionButton:hover { background: #F3F6F4; }
            QToolButton#DelaySampleActionButton:focus {
                background: #EAF6EF;
            }
            QToolButton#DelaySampleActionButton[restoreAction="true"] { color: #087958; }
            QFrame#DelayDialogSurface QLabel#DelayEmptyLabel {
                color: #5F6C66; font-size: 12px; padding: 11px 0;
            }
            QFrame#DelayClearConfirm {
                background: #FFFFFF; border: 0; border-top: 1px solid #DCE4DF;
            }
            QFrame#DelayDialogSurface QPushButton#DelayDangerButton { color: #B74536; }
            QFrame#DelayDialogSurface QLabel#DelayApplyStatus { padding: 12px 0; }
            QFrame#DelayFooter {
                background: #FFFFFF; border: 0; border-top: 1px solid #DCE4DF;
                border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;
            }
            QFrame#DelayDialogSurface QPushButton#DelayPrimaryButton {
                background: #09805C; border-color: #09805C;
                color: #FFFFFF; font-weight: 500;
            }
            QFrame#DelayDialogSurface QPushButton#DelayPrimaryButton:hover {
                background: #087958; border-color: #087958;
            }
            QFrame#DelayDialogSurface QLabel#DelayHistoryEstimate {
                color: #5F6C66; background: #F3F6F4;
                border: 0; border-radius: 5px; padding: 0 11px; font-size: 12px;
            }
        """

    @staticmethod
    def _spin(minimum: int, value: int, *, maximum: int = QT_INT_MAX) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setFixedHeight(34)
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_c_locale(spin)
        return spin

    @staticmethod
    def _number_field(spin: QSpinBox, unit: str) -> QWidget:
        field = QWidget()
        layout = QHBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        layout.addWidget(spin, 1)
        unit_label = QLabel(unit)
        unit_label.setObjectName("DelayMutedLabel")
        unit_label.setMinimumWidth(13)
        layout.addWidget(unit_label)
        return field

    def _add_field_row(
        self,
        label_text: str,
        field: QWidget,
        *,
        visible_field: QWidget | None = None,
        below: QWidget | None = None,
        tooltip: str = "",
    ) -> None:
        row = _FieldRow(
            label_text,
            visible_field or field,
            below=below,
            tooltip=tooltip,
        )
        self._form_rows[field] = row
        self.form.addWidget(row)

    def _runtime_column(self, caption: str, *, next_value: bool) -> QWidget:
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(17, 0, 17, 0)
        layout.setSpacing(3)
        caption_label = QLabel(caption)
        caption_label.setObjectName("DelayRuntimeCaption")
        layout.addWidget(caption_label)
        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(6)
        value_label = QLabel("100" if next_value else "—")
        value_label.setObjectName(
            "DelayRuntimeNextValue" if next_value else "DelayRuntimeValue"
        )
        unit_label = QLabel("帧")
        unit_label.setObjectName("DelayRuntimeUnit")
        value_row.addWidget(value_label)
        value_row.addWidget(unit_label)
        value_row.addStretch(1)
        layout.addLayout(value_row)
        note = QLabel("固定使用基准 delay" if next_value else "尚未开始本轮")
        note.setObjectName("DelayRuntimeNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        if next_value:
            self.next_delay_value = value_label
            self.next_delay_note = note
        else:
            self.current_delay_value = value_label
            self.current_delay_note = note
        return column

    def values(self) -> DelayStrategyConfig:
        return DelayStrategyConfig(
            strategy=str(self.strategy_combo.currentData()),
            baseline_delay=self.baseline_delay.value(),
            multi_candidate_policy=str(self.multi_candidate_widget.currentData()),
            window_size=self.window_size.value(),
            ewma_alpha=self.ewma_weight_percent.value() / 100.0,
            dense_interval_width=self.dense_interval_width.value(),
        )

    def set_values(self, config: DelayStrategyConfig) -> None:
        controls = (
            self.strategy_combo,
            self.baseline_delay,
            self.window_size,
            self.ewma_weight_percent,
            self.dense_interval_width,
            self.multi_candidate_widget,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            strategy_index = self.strategy_combo.findData(config.strategy.value)
            self.strategy_combo.setCurrentIndex(max(0, strategy_index))
            self.baseline_delay.setValue(config.baseline_delay)
            self.window_size.setValue(config.window_size)
            self.ewma_weight_percent.setValue(round(config.ewma_alpha * 100))
            self.dense_interval_width.setValue(config.dense_interval_width)
            policy_index = self.multi_candidate_widget.findData(
                config.multi_candidate_policy.value
            )
            self.multi_candidate_widget.setCurrentIndex(max(0, policy_index))
        finally:
            for control in controls:
                control.blockSignals(False)
        self._sync_compat_policy_buttons()
        self._saved_config = config
        self._feedback = ""
        self._update_strategy_rows()
        self._update_presentation()
        self.settingsEdited.emit()

    def set_show_time(self, show: bool) -> None:
        self.show_sample_time_check.blockSignals(True)
        self.show_sample_time_check.setChecked(bool(show))
        self.show_sample_time_check.blockSignals(False)
        self.history_page.set_show_time(bool(show))
        self._render_recent_samples()

    @Slot()
    def restore_defaults(self) -> None:
        self.set_values(DelayStrategyConfig())

    @Slot(int)
    def _strategy_changed(self, _index: int) -> None:
        self._feedback = ""
        self._update_strategy_rows()
        self.settingsEdited.emit()

    @Slot(int)
    def _numeric_setting_changed(self, _value: int) -> None:
        self._feedback = ""
        self._update_strategy_rows()
        self.settingsEdited.emit()

    @Slot(int)
    def _policy_changed(self, _index: int) -> None:
        self._feedback = ""
        self._sync_compat_policy_buttons()
        self.settingsEdited.emit()

    def _select_compat_policy(self, policy: MultiCandidatePolicy) -> None:
        index = self.multi_candidate_widget.findData(policy.value)
        if index >= 0:
            self.multi_candidate_widget.setCurrentIndex(index)

    def _sync_compat_policy_buttons(self) -> None:
        policy = str(self.multi_candidate_widget.currentData())
        self.ignore_multi_candidate_button.setChecked(
            policy == MultiCandidatePolicy.IGNORE.value
        )
        self.weight_multi_candidate_button.setChecked(
            policy == MultiCandidatePolicy.WEIGHTED.value
        )

    def _update_strategy_rows(self) -> None:
        strategy = DelayStrategy(str(self.strategy_combo.currentData()))
        statistical = strategy not in (DelayStrategy.FIXED, DelayStrategy.LAST)
        self._set_field_visible(self.window_size, statistical)
        self._set_field_visible(self.multi_candidate_widget, statistical)
        self._set_field_visible(
            self.ewma_weight_percent,
            strategy is DelayStrategy.EWMA
        )
        self._set_field_visible(
            self.dense_interval_width,
            strategy is DelayStrategy.DENSE_INTERVAL
        )
        window = self.window_size.value()
        descriptions = {
            DelayStrategy.FIXED: "始终使用基准 delay；样本会继续保留。",
            DelayStrategy.LAST: "使用最近一次唯一候选；多个候选的轮次自动跳过。",
            DelayStrategy.MODE: f"取最近 {window} 个有效轮次中累计权重最高的 delay。",
            DelayStrategy.MEDIAN: (
                f"取最近 {window} 个有效轮次的中间值，减弱偶发偏差的影响。"
            ),
            DelayStrategy.ROLLING_MEAN: (
                f"取最近 {window} 个有效轮次的平均值，每轮权重相同。"
            ),
            DelayStrategy.EWMA: (
                f"从基准值开始，逐轮融合最近 {window} 个有效轮次；"
                "权重越高越跟随新样本。"
            ),
            DelayStrategy.TRIMMED_MEAN: (
                "两端各去掉一轮权重后求平均；不足 5 个有效轮次时使用滚动平均。"
            ),
            DelayStrategy.DENSE_INTERVAL: "寻找跨度内最集中的候选，再取其中位数。",
        }
        description = descriptions[strategy]
        self.strategy_description.setText(description)
        self.strategy_combo.setToolTip(description)
        self._form_rows[self.strategy_combo].label.setToolTip(description)
        self._fit_strategy_description()
        self._update_presentation()
        if self.isVisible():
            QTimer.singleShot(0, self._resize_for_current_page)

    def _fit_strategy_description(self) -> None:
        width = self.strategy_description.width()
        if width <= 0:
            width = 416
        # QLabel.heightForWidth includes its current minimum height. Measure
        # unconstrained text so repeated refreshes do not add one pixel each.
        self.strategy_description.setMinimumHeight(0)
        required_height = self.strategy_description.heightForWidth(width)
        if required_height >= 0:
            self.strategy_description.setMinimumHeight(required_height + 1)
        self.strategy_description.updateGeometry()
        self.form.invalidate()

    def _set_field_visible(self, field: QWidget, visible: bool) -> None:
        field.setVisible(visible)
        self._form_rows[field].setVisible(visible)

    def showEvent(self, event) -> None:  # noqa: N802
        self._show_settings()
        self.clear_confirm_frame.hide()
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_strategy_description)
        QTimer.singleShot(0, self._resize_for_current_page)

    def set_runtime_state(
        self,
        *,
        species_name: str,
        recommended_delay: int | None,
        auto_reverse_enabled: bool,
        active_delay: int | None,
        estimate: DelayEstimate,
        sample_rounds: list[DelaySampleRound],
    ) -> None:
        self._species_name = species_name
        self._recommended_delay = recommended_delay
        self._auto_reverse_enabled = bool(auto_reverse_enabled)
        self._active_delay = active_delay
        self._estimate = estimate
        self._sample_rounds = list(sample_rounds)
        self._sample_statuses = _sample_statuses(self.values(), self._sample_rounds)
        self.species_name_label.setText(species_name)
        if recommended_delay is None:
            self.recommendation_label.setText("推荐 delay：暂无推荐")
            self.use_recommendation_button.hide()
        else:
            self.recommendation_label.setText(f"推荐 delay：{recommended_delay}")
            self.use_recommendation_button.show()
            self.use_recommendation_button.setEnabled(
                self.baseline_delay.value() != recommended_delay
            )
        self.current_delay_value.setText("—" if active_delay is None else str(active_delay))
        self.current_delay_note.setText(
            "尚未开始本轮" if active_delay is None else "本轮已锁定"
        )
        self.next_delay_value.setText(str(estimate.value))
        self.next_delay_note.setText(self._estimate_note(estimate))
        self.valid_sample_count.setText(f"{estimate.valid_round_count} 轮")

        recent_text = " / ".join(
            self._format_sample_round(row.candidates) for row in sample_rounds[-5:]
        )
        self.recent_samples.setText(recent_text or "暂无样本")
        self.recent_samples.setToolTip(recent_text)
        self.clear_samples_button.setEnabled(bool(sample_rounds))
        self.view_all_samples_button.setText(f"查看全部 {len(sample_rounds)} 轮")
        self.view_all_samples_button.setEnabled(bool(sample_rounds))
        deleted_count = sum(sample.excluded for sample in sample_rounds)
        summary = f"共 {len(sample_rounds)} 轮"
        if deleted_count:
            summary += f" · 已删除 {deleted_count} 轮"
        summary += f" · 本次使用 {estimate.valid_round_count} 轮"
        self.sample_summary_label.setText(summary)
        self.recent_samples_note.setText(
            "仅展示最近 5 轮" if sample_rounds else "仅保存当前精灵的反查结果"
        )
        self.clear_confirm_label.setText(
            f"清空{species_name}的全部样本？基准值与策略保留。"
        )
        self._render_recent_samples()

        strategy_label = DELAY_STRATEGY_LABEL_BY_ID[self.values().strategy.value]
        self.history_page.set_runtime_state(
            species_name=species_name,
            active_delay=active_delay,
            estimate=estimate,
            strategy_label=strategy_label,
            samples=sample_rounds,
            statuses=self._sample_statuses,
        )
        self._update_presentation()

    @staticmethod
    def _format_sample_round(candidates: tuple[int, ...]) -> str:
        if len(candidates) <= 1:
            return str(candidates[0]) if candidates else "-"
        if candidates == tuple(range(candidates[0], candidates[-1] + 1)):
            return f"{candidates[0]}~{candidates[-1]}"
        return ",".join(str(value) for value in candidates)

    def _estimate_note(self, estimate: DelayEstimate) -> str:
        if estimate.used_fallback:
            return "无有效样本 · 使用基准 delay"
        if estimate.strategy is DelayStrategy.FIXED:
            return "固定使用基准 delay"
        if (
            estimate.strategy is DelayStrategy.TRIMMED_MEAN
            and estimate.effective_strategy is DelayStrategy.ROLLING_MEAN
        ):
            label = "不足 5 轮，使用滚动平均"
        else:
            label = DELAY_STRATEGY_LABEL_BY_ID[estimate.effective_strategy.value]
        return f"{label} · 使用 {estimate.valid_round_count} 个有效轮次"

    def _render_recent_samples(self) -> None:
        recent = list(reversed(self._sample_rounds[-5:]))
        _populate_sample_table(
            self.recent_samples_table,
            recent,
            self._sample_statuses,
            show_time=self.show_sample_time_check.isChecked(),
            toggle_callback=self._request_sample_exclusion,
        )
        self.recent_samples_table.setVisible(bool(recent))
        self.empty_samples_label.setVisible(not recent)
        self._update_recent_table_height()
        if self.isVisible() and self.page_stack.currentWidget() is self.settings_page:
            QTimer.singleShot(0, self._resize_for_current_page)

    @Slot()
    def _use_recommendation(self) -> None:
        if self._recommended_delay is None:
            return
        self.baseline_delay.setValue(self._recommended_delay)
        self._feedback = "推荐值已填入基准 delay，保存后生效"
        self._update_presentation()

    @Slot(bool)
    def _samples_toggled(self, expanded: bool) -> None:
        self.samples_toggle.setIcon(
            delay_lucide_icon(
                "chevron-down" if expanded else "chevron-right",
                "#5F6C66",
                14,
            )
        )
        self.sample_details.setVisible(expanded)
        self.body_widget.updateGeometry()
        if self.isVisible():
            QTimer.singleShot(0, self._resize_for_current_page)

    def _update_recent_table_height(self) -> None:
        rows = min(5, len(self._sample_rounds))
        if not rows:
            return
        self.recent_samples_table.setFixedHeight(
            self.recent_samples_table.content_height()
        )
        self.sample_details.layout().invalidate()
        self.samples_frame.layout().invalidate()
        self.body_widget.layout().invalidate()

    @Slot(bool)
    def _show_sample_time_toggled(self, checked: bool) -> None:
        self.history_page.set_show_time(checked)
        self.showSampleTimeChanged.emit(bool(checked))
        self._render_recent_samples()

    @Slot(bool)
    def _history_show_time_changed(self, checked: bool) -> None:
        self.show_sample_time_check.blockSignals(True)
        self.show_sample_time_check.setChecked(bool(checked))
        self.show_sample_time_check.blockSignals(False)
        self.showSampleTimeChanged.emit(bool(checked))
        self._render_recent_samples()

    def _request_sample_exclusion(self, round_number: int, excluded: bool) -> None:
        if excluded:
            self._feedback = (
                f"已划除第 {round_number} 轮样本，不参与统计，可在原行恢复"
            )
        else:
            self._feedback = f"已恢复第 {round_number} 轮样本，下轮预计值已重算"
        self.sampleExclusionRequested.emit(round_number, excluded)
        QTimer.singleShot(0, lambda: self._focus_sample_action(round_number))

    def _focus_sample_action(self, round_number: int) -> None:
        table = (
            self.history_page.table
            if self.page_stack.currentWidget() is self.history_page
            else self.recent_samples_table
        )
        expected = f"第 {round_number} 轮"
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None or item.text().splitlines()[0] != expected:
                continue
            action = table.cellWidget(row, 3)
            if action is not None:
                action.setFocus()
            return

    @Slot()
    def _open_history(self) -> None:
        if not self._sample_rounds:
            return
        self.history_page.reset_page()
        self.page_stack.setCurrentWidget(self.history_page)
        self.title_label.setText("delay 样本记录")
        self.history_page.back_button.setFocus()
        QTimer.singleShot(0, self._resize_for_current_page)

    @Slot()
    def _show_settings(self) -> None:
        self.page_stack.setCurrentWidget(self.settings_page)
        self.title_label.setText("delay 策略设置")
        if self.isVisible():
            self.view_all_samples_button.setFocus()
            QTimer.singleShot(0, self._resize_for_current_page)

    def _resize_for_current_page(self) -> None:
        screen = self.screen()
        available_height = (
            screen.availableGeometry().height() - 8 if screen is not None else 950
        )
        if self.page_stack.currentWidget() is self.history_page:
            self.history_page.layout().activate()
            desired = 56 + self.history_page.sizeHint().height() + 48
        else:
            self._fit_strategy_description()
            self.body_widget.layout().activate()
            body_width = self.body_scroll.viewport().width()
            body_height = self.body_widget.layout().totalHeightForWidth(body_width)
            if body_height < 0:
                body_height = self.body_widget.sizeHint().height()
            desired = (
                56
                + body_height
                + self.footer.sizeHint().height()
                + 50
            )
            self.body_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                if desired <= available_height
                else Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
        self.page_stack.updateGeometry()
        self.layout().activate()
        self.resize(684, min(max(500, desired), max(500, available_height)))
        if self.isVisible() and screen is not None:
            work_area = screen.availableGeometry()
            self.move(
                min(max(self.x(), work_area.left()), work_area.right() - self.width() + 1),
                min(max(self.y(), work_area.top()), work_area.bottom() - self.height() + 1),
            )

    @Slot()
    def _show_clear_confirmation(self) -> None:
        if self._sample_rounds:
            self.clear_confirm_frame.show()
            QTimer.singleShot(0, self._resize_for_current_page)

    @Slot()
    def _hide_clear_confirmation(self) -> None:
        self.clear_confirm_frame.hide()
        QTimer.singleShot(0, self._resize_for_current_page)

    @Slot()
    def _confirm_clear_samples(self) -> None:
        if not self._sample_rounds:
            return
        self.clear_confirm_frame.hide()
        self._feedback = (
            f"已清空{self._species_name}的样本，基准值与策略保留"
        )
        self.clearSamplesRequested.emit()

    @Slot()
    def _save_settings(self) -> None:
        config = self.values()
        self._saved_config = config
        self.settingsSaveRequested.emit(config)
        self._saved_config = config
        self._feedback = f"已保存{self._species_name}的设置，从下一轮生效"
        self._update_presentation()

    def _update_presentation(self) -> None:
        dirty = self.values() != self._saved_config
        self.save_state_label.setText(
            f"{self._species_name} · 有未保存修改"
            if dirty
            else f"{self._species_name}的设置"
        )
        status = self._feedback or (
            "下轮预计为当前草稿；保存后从下一轮生效"
            if dirty
            else "修改保存后从下一轮生效"
        )
        if self.values().strategy is not DelayStrategy.FIXED and not self._auto_reverse_enabled:
            status += "；自动反查已关闭，新样本不会自动积累"
        self.apply_status.setText(status)
        if self._recommended_delay is not None:
            self.use_recommendation_button.setEnabled(
                self.baseline_delay.value() != self._recommended_delay
            )
