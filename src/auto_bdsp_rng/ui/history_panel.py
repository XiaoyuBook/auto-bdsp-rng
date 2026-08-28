"""历史记录面板 —— 第 6 个 Tab。

记录自动定点乱数每轮循环的候选、锁定、错过、结果、反查信息。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# ── 配色 ──────────────────────────────────────────────────
CLR_SEP     = "#9CA3AF"   # 分隔线
CLR_TS      = "#6B7280"   # 时间戳
CLR_BODY    = "#1F2937"   # 正文
CLR_LOCK    = "#15803D"   # 锁定 / 成功
CLR_SHINY   = "#B45309"   # 出闪
CLR_ERROR   = "#DC2626"   # 错过 / 失败
CLR_LOCK_BG = "#DCFCE7"   # 锁定行背景
CLR_LOCK_FG = "#166534"   # 锁定行文字
CLR_SYNC_BG = "#CFFAFE"   # 同步状态背景
CLR_SYNC_FG = "#155E75"   # 同步状态文字
CLR_SHINY_BG = "#FEF3C7"   # 异色单元格背景
CLR_SHINY_FG = "#92400E"   # 异色单元格文字


CANDIDATE_HEADERS = (
    "#", "状态", "Adv", "异色", "性格",
    "HP", "攻", "防", "特攻", "特防", "速",
    "特性", "性别", "EC", "PID", "身高", "体重",
)
CANDIDATE_WIDTHS = (42, 126, 92, 62, 66, 48, 48, 48, 54, 54, 48, 54, 54, 104, 104, 58, 58)
REVERSE_HEADERS = (
    "#", "状态", "Adv", "实际 delay", "异色", "性格", "个性",
    "HP", "攻", "防", "特攻", "特防", "速",
    "特性", "性别", "EC", "PID", "身高", "体重",
)
REVERSE_WIDTHS = (42, 94, 92, 88, 62, 66, 104, 48, 48, 48, 54, 54, 48, 54, 54, 104, 104, 58, 58)


class _CopyableTextEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setUndoRedoEnabled(False)
        self.setObjectName("LogView")

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if menu is None or menu.isEmpty():
            from PySide6.QtWidgets import QMenu
            from PySide6.QtGui import QAction
            menu = QMenu(self)
            menu.addAction("复制", self.copy, QAction.Shortcut("Ctrl+C"))
            menu.addAction("全选", self.selectAll, QAction.Shortcut("Ctrl+A"))
        menu.exec(event.globalPos())

    def w(self, text: str = "", color: str = CLR_BODY) -> None:
        scroll_bar = self.verticalScrollBar()
        follow = scroll_bar.maximum() - scroll_bar.value() <= 4
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self.setTextCursor(cursor)
        if follow:
            self.ensureCursorVisible()


SEPARATOR_THICK = "═" * 54
SEPARATOR_THIN  = "─" * 54
SEPARATOR_END   = "─" * 4


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _ts(text: str = "") -> str:
    """带时间戳前缀的文本"""
    return f"[{_now()}] {text}"


def _state_iv_text(ivs: object) -> str:
    values = _state_iv_values(ivs)
    if values == ("?",) * 6:
        return "?"
    return " / ".join(f"{n}={v}" for n, v in zip(("HP", "攻击", "防御", "特攻", "特防", "速度"), values))


def _state_iv_values(ivs: object) -> tuple[str, str, str, str, str, str]:
    if ivs is not None:
        try:
            values = tuple(str(int(value)) for value in ivs)
        except (TypeError, ValueError):
            values = ()
        if len(values) == 6:
            return values  # type: ignore[return-value]
    return ("?", "?", "?", "?", "?", "?")


def _state_ec(state: object) -> str:
    v = getattr(state, "ec", None)
    return f"{int(v):08X}" if v is not None else "?"


def _state_pid(state: object) -> str:
    v = getattr(state, "pid", None)
    return f"{int(v):08X}" if v is not None else "?"


def _get_int(state: object, name: str) -> int:
    v = getattr(state, name, None)
    return int(v) if v is not None else 0


def _pid_ec_key(state: object) -> str:
    return f"{_state_pid(state)}:{_state_ec(state)}"


NATURE_ZH_MAP: dict[int, str] = {
    0: "勤奋", 1: "怕寂寞", 2: "勇敢", 3: "固执", 4: "顽皮", 5: "大胆",
    6: "坦率", 7: "悠闲", 8: "淘气", 9: "乐天", 10: "胆小",
    11: "急躁", 12: "认真", 13: "爽朗", 14: "天真", 15: "内敛",
    16: "慢吞吞", 17: "冷静", 18: "害羞", 19: "马虎", 20: "温和",
    21: "温顺", 22: "自大", 23: "慎重", 24: "浮躁",
}
GENDER_MAP = {0: "雄", 1: "雌", 2: "无"}
SHINY_MAP  = {0: "否", 1: "星闪", 2: "方闪"}


def _nature_text(state: object) -> str:
    n = _get_int(state, "nature")
    return NATURE_ZH_MAP.get(n, str(n))


def _gender_text(state: object) -> str:
    g = _get_int(state, "gender")
    return GENDER_MAP.get(g, str(g))


def _shiny_text(state: object) -> str:
    s = _get_int(state, "shiny")
    return SHINY_MAP.get(s, str(s))


class HistoryPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cycle_index = 0
        self._cycle_count = 0
        self._candidate_count = 0
        self._pid_ec_seen: dict[str, int] = {}
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self._follow_pending = False
        self._follow_origin = 0
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        self.summary_label = QLabel("0 轮 · 0 条候选")
        self.summary_label.setObjectName("HistorySummary")
        self.summary_label.setStyleSheet("color: #6B7280; font-weight: 600;")
        self.copy_button = QPushButton("复制全部")
        self.copy_button.setFixedHeight(34)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_all)
        self.clear_button = QPushButton("清空")
        self.clear_button.setFixedHeight(34)
        self.clear_button.clicked.connect(self.clear)
        self.export_button = QPushButton("导出")
        self.export_button.setFixedHeight(34)
        self.export_button.clicked.connect(self.export_to_file)
        toolbar.addWidget(self.summary_label)
        toolbar.addStretch()
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.export_button)
        layout.addLayout(toolbar)

        self.history_scroll = QScrollArea(self)
        self.history_scroll.setObjectName("HistoryScroll")
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.history_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_scroll.setStyleSheet(
            "QScrollArea#HistoryScroll { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 6px; }"
            "QWidget#HistoryFeed { background: #FFFFFF; }"
        )
        self._feed_body = QWidget(self.history_scroll)
        self._feed_body.setObjectName("HistoryFeed")
        self._feed_body.setMinimumWidth(0)
        self._feed_body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._feed_layout = QVBoxLayout(self._feed_body)
        self._feed_layout.setContentsMargins(12, 12, 12, 12)
        self._feed_layout.setSpacing(7)
        self._feed_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._feed_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        self.history_scroll.setWidget(self._feed_body)
        scroll_bar = self.history_scroll.verticalScrollBar()
        scroll_bar.sliderPressed.connect(self._cancel_auto_follow)
        scroll_bar.actionTriggered.connect(self._cancel_auto_follow)
        layout.addWidget(self.history_scroll, 1)

        # Plain-text mirror kept for exports and callers that use text_view.toPlainText().
        self.text_view = _CopyableTextEdit(self)
        self.text_view.setFont(QFont("Consolas", 10))
        self.text_view.hide()
        self.view = self.text_view

    # ── 输出快捷方法 ──
    def _append_plain(self, text: str = "", color: str = CLR_BODY) -> None:
        self.text_view.w(text, color)
        self.copy_button.setEnabled(bool(self.text_view.toPlainText()))

    def _append_widget(self, widget: QWidget) -> None:
        scroll_bar = self.history_scroll.verticalScrollBar()
        follow = self._follow_pending or scroll_bar.maximum() - scroll_bar.value() <= 4
        self._feed_layout.addWidget(widget)
        if follow and not self._follow_pending:
            self._follow_pending = True
            self._follow_origin = scroll_bar.value()
            QTimer.singleShot(0, self._finish_auto_follow)

    def _append_text_label(self, text: str, color: str = CLR_BODY, *, bold: bool = False) -> None:
        label = QLabel(text, self._feed_body)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        weight = "font-weight: 700;" if bold else ""
        label.setStyleSheet(f"color: {color}; padding: 1px 4px; {weight}")
        self._append_widget(label)

    def _append_divider(self, text: str, *, strong: bool = False) -> None:
        self._append_plain(text, CLR_SEP)
        line = QFrame(self._feed_body)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(2 if strong else 1)
        line.setStyleSheet(f"background: {CLR_SEP}; border: none;")
        self._append_widget(line)

    def _finish_auto_follow(self) -> None:
        if not self._follow_pending:
            return
        scroll_bar = self.history_scroll.verticalScrollBar()
        if scroll_bar.value() + 4 >= self._follow_origin:
            scroll_bar.setValue(scroll_bar.maximum())
        self._follow_pending = False

    def _cancel_auto_follow(self, *_args: object) -> None:
        self._follow_pending = False

    def _sep(self) -> None:
        self._append_divider(SEPARATOR_THICK, strong=True)

    def _sep_thin(self) -> None:
        self._append_divider(SEPARATOR_THIN)

    def _sep_end(self) -> None:
        self._append_divider(SEPARATOR_END)

    def _ts(self, text: str) -> None:
        line = _ts(text)
        self._append_plain(line)
        self._append_text_label(line)

    def _info(self, text: str) -> None:
        line = f"  {text}"
        self._append_plain(line)
        self._append_text_label(line)

    def _w(self, text: str, c: str = CLR_BODY) -> None:
        self._append_plain(text, c)
        self._append_text_label(text, c)

    def _blank(self) -> None:
        self._append_plain()
        spacer = QWidget(self._feed_body)
        spacer.setFixedHeight(3)
        self._append_widget(spacer)

    def _update_summary(self) -> None:
        self.summary_label.setText(f"{self._cycle_count} 轮 · {self._candidate_count} 条候选")

    def clear(self) -> None:
        self._cancel_auto_follow()
        self.text_view.clear()
        while self._feed_layout.count():
            item = self._feed_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cycle_index = 0
        self._cycle_count = 0
        self._candidate_count = 0
        self._pid_ec_seen.clear()
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self.copy_button.setEnabled(False)
        self._update_summary()

    def copy_all(self) -> None:
        text = self.text_view.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

    def export_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出历史记录", "history.txt", "文本文件 (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text_view.toPlainText())

    def _candidate_status(
        self,
        index: int,
        state: object,
        locked_index: int,
        sync_flags: list[str] | None,
    ) -> str:
        key = _pid_ec_key(state)
        duplicate = self._pid_ec_seen.get(key)
        self._pid_ec_seen[key] = index + 1
        tags: list[str] = []
        if index == locked_index:
            tags.append("锁定")
        if sync_flags and index < len(sync_flags) and sync_flags[index] == "sync":
            tags.append("同步")
        if duplicate:
            tags.append(f"同候选{duplicate}")
        return " · ".join(tags) if tags else "普通"

    @staticmethod
    def _candidate_row(index: int, state: object, status: str) -> list[str]:
        return [
            str(index + 1),
            status,
            str(_get_int(state, "advances")),
            _shiny_text(state),
            _nature_text(state),
            *_state_iv_values(getattr(state, "ivs", None)),
            str(_get_int(state, "ability")),
            _gender_text(state),
            _state_ec(state),
            _state_pid(state),
            str(_get_int(state, "height")),
            str(_get_int(state, "weight")),
        ]

    @staticmethod
    def _reverse_row(
        index: int,
        state: object,
        status: str,
        delay: int,
        characteristic: str | None,
    ) -> list[str]:
        return [
            str(index + 1),
            status,
            str(_get_int(state, "advances")),
            str(delay),
            _shiny_text(state),
            _nature_text(state),
            characteristic or "-",
            *_state_iv_values(getattr(state, "ivs", None)),
            str(_get_int(state, "ability")),
            _gender_text(state),
            _state_ec(state),
            _state_pid(state),
            str(_get_int(state, "height")),
            str(_get_int(state, "weight")),
        ]

    def _append_candidate_table(
        self,
        rows: list[list[str]],
        states: list[object],
        statuses: list[str],
        *,
        reverse: bool = False,
    ) -> None:
        if not rows:
            return
        headers = REVERSE_HEADERS if reverse else CANDIDATE_HEADERS
        widths = REVERSE_WIDTHS if reverse else CANDIDATE_WIDTHS
        table = QTableWidget(len(rows), len(headers), self._feed_body)
        table.setObjectName("HistoryCandidateTable")
        table.setAccessibleName("反查候选表" if reverse else "候选结果表")
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.TextElideMode.ElideNone)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(32)
        header = table.horizontalHeader()
        header.setFixedHeight(34)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        for column, width in enumerate(widths):
            table.setColumnWidth(column, width)

        shiny_column = headers.index("异色")
        status_column = headers.index("状态")
        for row_index, values in enumerate(rows):
            locked = "锁定" in statuses[row_index].split(" · ")
            synchronized = "同步" in statuses[row_index].split(" · ")
            shiny = _get_int(states[row_index], "shiny") > 0
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                alignment = Qt.AlignmentFlag.AlignVCenter
                alignment |= Qt.AlignmentFlag.AlignLeft if column == status_column else Qt.AlignmentFlag.AlignHCenter
                item.setTextAlignment(alignment)
                if locked:
                    item.setBackground(QColor(CLR_LOCK_BG))
                    item.setForeground(QColor(CLR_LOCK_FG))
                if column == status_column and synchronized:
                    if not locked:
                        item.setBackground(QColor(CLR_SYNC_BG))
                    item.setForeground(QColor(CLR_SYNC_FG))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if column == shiny_column and shiny:
                    item.setBackground(QColor(CLR_SHINY_BG))
                    item.setForeground(QColor(CLR_SHINY_FG))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row_index, column, item)

        visible_rows = min(len(rows), 7)
        table.setFixedHeight(min(300, 34 + visible_rows * 32 + 22))
        table.setMinimumWidth(0)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.setStyleSheet(
            "QTableWidget#HistoryCandidateTable { background: #FFFFFF; alternate-background-color: #F9FAFB; "
            "border: 1px solid #E5E7EB; gridline-color: #EEF0F3; color: #111827; }"
            "QTableWidget#HistoryCandidateTable::item { padding: 3px 6px; }"
            "QTableWidget#HistoryCandidateTable::item:selected { background: #0E8F70; color: #FFFFFF; }"
            "QTableWidget#HistoryCandidateTable QHeaderView::section { background: #F3F4F6; color: #4B5563; "
            "border: 0; border-right: 1px solid #E5E7EB; border-bottom: 1px solid #D1D5DB; "
            "padding: 6px; font-weight: 700; }"
        )
        self._candidate_count += len(rows)
        self._update_summary()
        self._append_widget(table)

    # ── 事件方法 ───────────────────────────────────────────────

    def cycle_start(self, cycle_index: int) -> None:
        self._cycle_index = cycle_index
        self._cycle_count += 1
        self._update_summary()
        self._pid_ec_seen.clear()
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self._blank()
        self._sep()
        heading = f"第 {cycle_index} 轮  {_now()}"
        self._append_plain(heading, CLR_TS)
        self._append_text_label(heading, CLR_TS, bold=True)
        self._sep()

    def seed_captured(self, seed_text: str, initial_advances: int, npc: int, max_advances: int) -> None:
        if self._is_first_seed:
            self._is_first_seed = False
            self._original_seed_text = seed_text
            self._original_seed_advances = initial_advances
            self._ts(f"原始 Seed: {seed_text}  初始帧: {initial_advances}  NPC: {npc}  最大搜索: {max_advances}")
        else:
            self._sep_thin()
            self._ts(f"重新测种，当前 Seed: {seed_text}  初始帧: {initial_advances}  (原始 Seed: {self._original_seed_text})")
            self._sep_thin()

    def auto_tid_log(self, message: str) -> None:
        self._ts(f"[自动TID] {message}")

    def candidates_found(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None,
    ) -> None:
        self._pid_ec_seen.clear()
        self._ts(f"搜索到 {len(candidates)} 个候选")
        statuses: list[str] = []
        rows: list[list[str]] = []
        for i, state in enumerate(candidates):
            status = self._candidate_status(i, state, locked_index, sync_flags)
            statuses.append(status)
            rows.append(self._candidate_row(i, state, status))
            self._write_candidate(i, state, status, i == locked_index)
        self._append_candidate_table(rows, candidates, statuses)

    def candidates_refiltered(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None,
    ) -> None:
        self._pid_ec_seen.clear()
        self._ts(f"剩余 {len(candidates)} 个候选")
        statuses: list[str] = []
        rows: list[list[str]] = []
        for i, state in enumerate(candidates):
            status = self._candidate_status(i, state, locked_index, sync_flags)
            statuses.append(status)
            rows.append(self._candidate_row(i, state, status))
            self._write_candidate(i, state, status, i == locked_index)
        self._append_candidate_table(rows, candidates, statuses)

    def _write_candidate(
        self,
        i: int,
        state: object,
        status: str,
        locked: bool,
    ) -> None:
        adv = _get_int(state, "advances")
        line = (
            f"  候选{i + 1} ({status}) adv={adv} "
            f"EC={_state_ec(state)} PID={_state_pid(state)} "
            f"{_state_iv_text(getattr(state, 'ivs', None))} "
            f"性格={_nature_text(state)} 异色={_shiny_text(state)} "
            f"特性={_get_int(state, 'ability')} 性别={_gender_text(state)} "
            f"身高={_get_int(state, 'height')} 体重={_get_int(state, 'weight')}"
        )
        self._append_plain(line, CLR_LOCK if locked else CLR_BODY)

    def cycle_no_candidate(self) -> None:
        self._sep_thin()
        self._w(_ts("本轮结果: 无候选"), CLR_TS)
        self._sep_thin()

    def target_missed(self, missed_advances: int, current_advances: int) -> None:
        self._sep_thin()
        self._w(_ts(f"错过 目标 (目标帧: {missed_advances} ≤ 目前帧数: {current_advances})"), CLR_ERROR)
        self._ts("重新搜索，排除已过帧…")
        self._sep_thin()

    def cycle_result(
        self,
        is_shiny: bool,
        interval: float | None,
        trigger_advances: int | None = None,
        used_delay: int | None = None,
    ) -> None:
        self._sep_thin()
        interval_text = f"{interval:.3f}s" if interval is not None else "-"
        trigger_text = str(trigger_advances) if trigger_advances is not None else "-"
        delay_text = str(used_delay) if used_delay is not None else "-"
        details = f"  脚本启动 Adv: {trigger_text}  使用 delay: {delay_text}"
        if is_shiny:
            self._w(_ts(f"本轮结果: 疑似出闪  间隔: {interval_text}{details}"), CLR_SHINY)
        else:
            self._ts(f"本轮结果: 未出闪  间隔: {interval_text}{details}")
        self._sep_thin()

    def attempt_result(
        self,
        loop_index: int,
        attempt_index: int,
        interval: float | None,
        trigger_advances: int | None = None,
        used_delay: int | None = None,
    ) -> None:
        interval_text = f"{interval:.3f}s" if interval is not None else "-"
        trigger_text = str(trigger_advances) if trigger_advances is not None else "-"
        delay_text = str(used_delay) if used_delay is not None else "-"
        self._sep_thin()
        self._ts(
            f"第 {loop_index} 轮 / 第 {attempt_index} 次撞闪: 未出闪  间隔: {interval_text}"
            f"  脚本启动 Adv: {trigger_text}  使用 delay: {delay_text}"
        )
        self._ts("准备运行逃跑脚本，完成后继续搜索本轮后续候选")
        self._sep_thin()

    def reverse_lookup_results(
        self, candidates: list[object], characteristic: str | None = None,
        delays: list[int] | None = None, ocr_stats: dict | None = None,
    ) -> None:
        count = len(candidates)
        if count == 0:
            self._ts("反查结果: 未找到匹配个体")
            if ocr_stats:
                nature = ocr_stats.get("nature")
                chara = ocr_stats.get("characteristic") or characteristic
                if nature:
                    self._info(f"OCR 性格: {nature}")
                if chara:
                    self._info(f"OCR 个性: {chara}")
                stats = ocr_stats.get("stats", {})
                iv_min = ocr_stats.get("iv_min", [])
                iv_max = ocr_stats.get("iv_max", [])
                if stats:
                    stat_text = " / ".join(f"{n}={stats.get(n, '?')}" for n in ("HP", "攻击", "防御", "特攻", "特防", "速度"))
                    self._info(f"OCR 能力值: {stat_text}")
                if iv_min and iv_max and len(iv_min) == 6 and len(iv_max) == 6:
                    iv_text = " / ".join(
                        f"{n}={iv_min[i]}" if iv_min[i] == iv_max[i] else f"{n}={iv_min[i]}-{iv_max[i]}"
                        for i, n in enumerate(("HP", "攻击", "防御", "特攻", "特防", "速度"))
                    )
                    self._info(f"OCR 反算个体值范围: {iv_text}")
        else:
            self._ts(f"反查结果 ({count} 个匹配):")
            self._pid_ec_seen.clear()
            statuses: list[str] = []
            rows: list[list[str]] = []
            for i, state in enumerate(candidates):
                adv = _get_int(state, "advances")
                delay = delays[i] if delays is not None and i < len(delays) else adv
                key = _pid_ec_key(state)
                duplicate = self._pid_ec_seen.get(key)
                self._pid_ec_seen[key] = i + 1
                status = f"匹配 · 同候选{duplicate}" if duplicate else "匹配"
                statuses.append(status)
                rows.append(self._reverse_row(i, state, status, delay, characteristic))
                chara_text = f" 个性={characteristic}" if characteristic else ""
                self._append_plain(
                    f"反查候选{i + 1}: adv={adv} delay={delay} EC={_state_ec(state)} PID={_state_pid(state)} "
                    f"{_state_iv_text(getattr(state, 'ivs', None))} "
                    f"性格={_nature_text(state)}{chara_text} 特性={_get_int(state, 'ability')} "
                    f"性别={_gender_text(state)} 异色={_shiny_text(state)} "
                    f"身高={_get_int(state, 'height')} 体重={_get_int(state, 'weight')}"
                )
            self._append_candidate_table(rows, candidates, statuses, reverse=True)
        self._sep_end()
