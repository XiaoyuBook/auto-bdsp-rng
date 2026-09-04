"""历史记录面板 —— 第 6 个 Tab。

记录自动定点乱数每轮循环的候选、锁定、错过、结果、反查信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QComboBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
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


def _state_characteristic(state: object, recognized: str | None = None) -> str:
    if recognized:
        return recognized
    try:
        ivs = [int(value) for value in getattr(state, "ivs", ())]
        ec = int(getattr(state, "ec", 0))
    except (TypeError, ValueError):
        return "-"
    if len(ivs) != 6:
        return "-"
    from auto_bdsp_rng.automation.auto_rng.pokemon_info_ocr import compute_characteristic

    return compute_characteristic(ec, ivs) or "-"


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


@dataclass(frozen=True)
class _CandidateSnapshot:
    advances: int
    ec: int | None
    pid: int | None
    ivs: tuple[int, ...]
    ability: int
    gender: int
    nature: int
    shiny: int
    height: int
    weight: int


def _snapshot_candidate(state: object) -> _CandidateSnapshot:
    try:
        ivs = tuple(int(value) for value in getattr(state, "ivs", ()))
    except (TypeError, ValueError):
        ivs = ()

    def optional_int(name: str) -> int | None:
        value = getattr(state, name, None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return _CandidateSnapshot(
        advances=_get_int(state, "advances"),
        ec=optional_int("ec"),
        pid=optional_int("pid"),
        ivs=ivs,
        ability=_get_int(state, "ability"),
        gender=_get_int(state, "gender"),
        nature=_get_int(state, "nature"),
        shiny=_get_int(state, "shiny"),
        height=_get_int(state, "height"),
        weight=_get_int(state, "weight"),
    )


@dataclass(frozen=True)
class _FeedEntry:
    kind: str
    text: str = ""
    color: str = CLR_BODY
    bold: bool = False
    strong: bool = False
    rows: tuple[tuple[str, ...], ...] = ()
    states: tuple[_CandidateSnapshot, ...] = ()
    statuses: tuple[str, ...] = ()
    reverse: bool = False


@dataclass
class _RoundRecord:
    uid: int
    run_id: object
    round_id: object
    run_index: int
    cycle_index: int | None
    target_label: str
    started_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    detail_title: str = "运行消息"
    outcome: str | None = None
    seed_text: str | None = None
    locked_advances: int | None = None
    candidate_count: int = 0
    warnings: list[str] = field(default_factory=list)
    entries: list[_FeedEntry] = field(default_factory=list)
    plain_lines: list[str] = field(default_factory=list)
    implicit: bool = False


class _RoundListRow(QWidget):
    def __init__(self, record: _RoundRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel(self)
        self.title_label.setStyleSheet("font-weight: 600; color: #202225;")
        self.status_label = QLabel(self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.title_label, 1)
        top.addWidget(self.status_label)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        self.target_label = QLabel(self)
        self.target_label.setStyleSheet("color: #737C88; font-size: 12px;")
        self.note_label = QLabel(self)
        self.note_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.note_label.setStyleSheet("color: #737C88; font-size: 12px;")
        bottom.addWidget(self.target_label, 1)
        bottom.addWidget(self.note_label)

        layout.addLayout(top)
        layout.addLayout(bottom)
        self.update_record(record)

    def update_record(self, record: _RoundRecord) -> None:
        if record.cycle_index is None:
            title = f"运行 {record.run_index} · 会话"
        else:
            title = f"运行 {record.run_index} · 第 {record.cycle_index} 轮"
        status = _record_status(record)
        status_color = {
            "有警告": "#A35F08",
            "疑似出闪": CLR_SHINY,
            "未出闪": CLR_LOCK,
            "无候选": "#606A76",
        }.get(status, "#176F5C")
        self.title_label.setText(title)
        self.status_label.setText(status)
        self.status_label.setStyleSheet(f"color: {status_color}; font-size: 12px; font-weight: 600;")
        self.target_label.setText(record.target_label)
        note = f"{record.candidate_count} 个候选" if record.candidate_count else record.updated_at.strftime("%H:%M:%S")
        self.note_label.setText(note)


def _record_status(record: _RoundRecord) -> str:
    if record.outcome:
        return record.outcome
    if record.warnings:
        return "有警告"
    return "记录中" if record.implicit else "进行中"


class HistoryPanel(QWidget):
    related_logs_requested = Signal(object, object)

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
        self._rendering_record = False
        self._round_uid_counter = 0
        self._run_index_counter = 0
        self._run_indexes: dict[object, int] = {}
        self._round_records: dict[tuple[object, object], _RoundRecord] = {}
        self._record_by_uid: dict[int, _RoundRecord] = {}
        self._round_order: list[tuple[object, object]] = []
        self._active_uid: int | None = None
        self._selected_uid: int | None = None
        self._current_run_context: object = None
        self._current_target_label = "自动定点"
        self._select_next_record = False
        self._build_ui()
        self._scroll_top_timer = QTimer(self)
        self._scroll_top_timer.setSingleShot(True)
        self._scroll_top_timer.timeout.connect(self._scroll_selected_record_to_top)
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.timeout.connect(self._finish_auto_follow)

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
        self.clear_button.setEnabled(False)
        self.clear_button.clicked.connect(self.clear)
        self.export_button = QPushButton("导出")
        self.export_button.setFixedHeight(34)
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_to_file)
        toolbar.addWidget(self.summary_label)
        toolbar.addStretch()
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.clear_button)
        toolbar.addWidget(self.export_button)
        layout.addLayout(toolbar)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setSpacing(8)
        result_label = QLabel("结果")
        result_label.setStyleSheet("color: #68717E; font-size: 12px;")
        self.result_filter = QComboBox(self)
        self.result_filter.setObjectName("HistoryResultFilter")
        self.result_filter.setMinimumWidth(126)
        for text, value in (
            ("全部结果", "all"),
            ("进行中", "active"),
            ("疑似出闪", "shiny"),
            ("未出闪", "not-shiny"),
            ("无候选", "no-candidate"),
            ("有警告", "warning"),
        ):
            self.result_filter.addItem(text, value)
        self.result_filter.currentIndexChanged.connect(self._refresh_round_list)
        search_label = QLabel("搜索轮次")
        search_label.setStyleSheet("color: #68717E; font-size: 12px;")
        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("HistoryRoundSearch")
        self.search_edit.setPlaceholderText("目标、Seed、Adv 或结果")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refresh_round_list)
        filters.addWidget(result_label)
        filters.addWidget(self.result_filter)
        filters.addSpacing(4)
        filters.addWidget(search_label)
        filters.addWidget(self.search_edit, 1)
        layout.addLayout(filters)

        self.empty_state = QFrame(self)
        self.empty_state.setObjectName("HistoryEmptyState")
        self.empty_state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.empty_state.setStyleSheet(
            "QFrame#HistoryEmptyState { background: #FAFBFC; border: 1px solid #E5E7EB; "
            "border-radius: 6px; }"
            "QFrame#HistoryEmptyState QLabel { background: transparent; border: 0; }"
        )
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(5)
        empty_layout.addStretch(1)
        self.empty_state_title = QLabel("暂无轮次记录", self.empty_state)
        self.empty_state_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_title.setStyleSheet("color: #4B5563; font-size: 16px; font-weight: 600;")
        self.empty_state_detail = QLabel("当前会话尚未产生运行结果", self.empty_state)
        self.empty_state_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state_detail.setStyleSheet("color: #8A939F; font-size: 12px;")
        empty_layout.addWidget(self.empty_state_title)
        empty_layout.addWidget(self.empty_state_detail)
        empty_layout.addStretch(1)
        layout.addWidget(self.empty_state, 1)

        self.round_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.round_splitter.setObjectName("HistoryRoundSplitter")
        self.round_splitter.setChildrenCollapsible(False)
        self.round_splitter.setHandleWidth(1)

        list_panel = QWidget(self.round_splitter)
        list_panel.setObjectName("HistoryRoundListPanel")
        list_panel.setMinimumWidth(220)
        list_panel.setMaximumWidth(330)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        self.round_list_heading = QLabel("本次会话 · 0 轮", list_panel)
        self.round_list_heading.setObjectName("HistoryRoundListHeading")
        self.round_list_heading.setFixedHeight(36)
        self.round_list_heading.setStyleSheet(
            "color: #5E6773; background: #F7F8FA; border: 1px solid #D8DDE3; "
            "border-bottom: 0; padding: 0 11px; font-size: 12px; font-weight: 600;"
        )
        self.round_list = QListWidget(list_panel)
        self.round_list.setObjectName("HistoryRoundList")
        self.round_list.setAccessibleName("轮次记录列表")
        self.round_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.round_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.round_list.setSpacing(0)
        self.round_list.setStyleSheet(
            "QListWidget#HistoryRoundList { background: #FAFBFC; color: #202225; "
            "border: 1px solid #D8DDE3; outline: 0; }"
            "QListWidget#HistoryRoundList::item { min-height: 62px; border-bottom: 1px solid #E6E9ED; }"
            "QListWidget#HistoryRoundList::item:hover { background: #F0F3F5; }"
            "QListWidget#HistoryRoundList::item:selected { background: #EAF6F2; "
            "border-left: 3px solid #0E8F70; }"
        )
        self.round_list.currentItemChanged.connect(self._on_round_selected)
        list_layout.addWidget(self.round_list_heading)
        list_layout.addWidget(self.round_list, 1)

        detail_panel = QWidget(self.round_splitter)
        detail_panel.setObjectName("HistoryRoundDetail")
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(14, 12, 12, 12)
        detail_layout.setSpacing(10)

        detail_header = QHBoxLayout()
        detail_header.setContentsMargins(0, 0, 0, 0)
        detail_heading = QVBoxLayout()
        detail_heading.setContentsMargins(0, 0, 0, 0)
        detail_heading.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        self.detail_title_label = QLabel("请选择轮次", detail_panel)
        self.detail_title_label.setObjectName("HistoryDetailTitle")
        self.detail_title_label.setStyleSheet("color: #202225; font-size: 16px; font-weight: 600;")
        self.detail_status_label = QLabel("", detail_panel)
        self.detail_status_label.setObjectName("HistoryDetailStatus")
        title_row.addWidget(self.detail_title_label)
        title_row.addWidget(self.detail_status_label)
        title_row.addStretch()
        self.detail_time_label = QLabel("", detail_panel)
        self.detail_time_label.setObjectName("HistoryDetailTime")
        self.detail_time_label.setStyleSheet("color: #707986; font-size: 12px;")
        detail_heading.addLayout(title_row)
        detail_heading.addWidget(self.detail_time_label)
        detail_header.addLayout(detail_heading, 1)

        self.copy_round_button = QPushButton("复制本轮", detail_panel)
        self.copy_round_button.setObjectName("HistoryCopyRound")
        self.copy_round_button.setFixedHeight(32)
        self.copy_round_button.setEnabled(False)
        self.copy_round_button.clicked.connect(self.copy_selected_round)
        self.related_logs_button = QPushButton("查看相关日志", detail_panel)
        self.related_logs_button.setObjectName("HistoryRelatedLogs")
        self.related_logs_button.setFixedHeight(32)
        self.related_logs_button.setEnabled(False)
        self.related_logs_button.clicked.connect(self._request_related_logs)
        self.related_logs_button.setStyleSheet(
            "QPushButton { color: #FFFFFF; background: #0E8F70; border: 1px solid #0E8F70; "
            "border-radius: 4px; padding: 0 10px; }"
            "QPushButton:hover { background: #0B7C61; }"
            "QPushButton:disabled { color: #9CA3AF; background: #E5E7EB; border-color: #D1D5DB; }"
        )
        detail_header.addWidget(self.copy_round_button)
        detail_header.addWidget(self.related_logs_button)
        detail_layout.addLayout(detail_header)

        meta_frame = QFrame(detail_panel)
        meta_frame.setObjectName("HistoryRoundMeta")
        meta_frame.setStyleSheet(
            "QFrame#HistoryRoundMeta { border-top: 1px solid #E1E5E9; "
            "border-bottom: 1px solid #E1E5E9; }"
        )
        meta_layout = QGridLayout(meta_frame)
        meta_layout.setContentsMargins(0, 8, 0, 8)
        meta_layout.setHorizontalSpacing(16)
        meta_layout.setVerticalSpacing(2)
        self.target_value_label = self._add_meta_column(meta_layout, 0, "目标")
        self.seed_value_label = self._add_meta_column(meta_layout, 1, "Seed")
        self.locked_adv_value_label = self._add_meta_column(meta_layout, 2, "锁定 Adv")
        self.candidate_value_label = self._add_meta_column(meta_layout, 3, "候选结果")
        for column in range(4):
            meta_layout.setColumnStretch(column, 1)
        detail_layout.addWidget(meta_frame)

        self.warning_frame = QFrame(detail_panel)
        self.warning_frame.setObjectName("HistoryWarning")
        self.warning_frame.setStyleSheet(
            "QFrame#HistoryWarning { color: #74400A; background: #FFF7E8; "
            "border: 0; border-left: 3px solid #D97706; border-radius: 3px; }"
        )
        warning_layout = QHBoxLayout(self.warning_frame)
        warning_layout.setContentsMargins(10, 8, 10, 8)
        self.warning_label = QLabel(self.warning_frame)
        self.warning_label.setObjectName("HistoryWarningText")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #74400A;")
        warning_layout.addWidget(self.warning_label)
        self.warning_frame.hide()
        detail_layout.addWidget(self.warning_frame)

        feed_heading = QHBoxLayout()
        feed_title = QLabel("处理详情", detail_panel)
        feed_title.setStyleSheet("color: #30353B; font-size: 13px; font-weight: 600;")
        feed_note = QLabel("候选数据为识别当时的快照", detail_panel)
        feed_note.setStyleSheet("color: #717A86; font-size: 12px;")
        feed_heading.addWidget(feed_title)
        feed_heading.addStretch()
        feed_heading.addWidget(feed_note)
        detail_layout.addLayout(feed_heading)

        self.history_scroll = QScrollArea(detail_panel)
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
        detail_layout.addWidget(self.history_scroll, 1)

        self.round_splitter.addWidget(list_panel)
        self.round_splitter.addWidget(detail_panel)
        self.round_splitter.setStretchFactor(0, 0)
        self.round_splitter.setStretchFactor(1, 1)
        self.round_splitter.setSizes([250, 760])
        layout.addWidget(self.round_splitter, 1)
        self.round_splitter.hide()

        # Plain-text mirror kept for exports and callers that use text_view.toPlainText().
        self.text_view = _CopyableTextEdit(self)
        self.text_view.setFont(QFont("Consolas", 10))
        self.text_view.hide()
        self.view = self.text_view

    @staticmethod
    def _add_meta_column(layout: QGridLayout, column: int, title: str) -> QLabel:
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #737C88; font-size: 12px;")
        value_label = QLabel("-")
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value_label.setWordWrap(True)
        value_label.setStyleSheet("color: #202225; font-weight: 600;")
        layout.addWidget(title_label, 0, column)
        layout.addWidget(value_label, 1, column)
        return value_label

    @property
    def current_run_id(self) -> object:
        record = self._active_record()
        return record.run_id if record is not None else self._current_run_context

    @property
    def current_round_id(self) -> object:
        record = self._active_record()
        return record.round_id if record is not None and not record.implicit else None

    @property
    def current_target_label(self) -> str:
        record = self._active_record()
        return record.target_label if record is not None else self._current_target_label

    @property
    def selected_run_id(self) -> object:
        record = self._selected_record()
        return record.run_id if record is not None else None

    @property
    def selected_round_id(self) -> object:
        record = self._selected_record()
        return record.round_id if record is not None and not record.implicit else None

    def begin_run(self, run_id: object, target_label: str = "自动定点") -> None:
        self._current_run_context = run_id
        self._run_index_for(run_id)
        self._current_target_label = str(target_label or "自动定点")
        self._active_uid = None
        self._select_next_record = True
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0

    @staticmethod
    def _group_component(value: object) -> object:
        try:
            hash(value)
        except TypeError:
            return ("object", id(value))
        return ("value", value)

    @classmethod
    def _group_key(cls, run_id: object, round_id: object) -> tuple[object, object]:
        return cls._group_component(run_id), cls._group_component(round_id)

    def _run_index_for(self, run_id: object) -> int:
        key = self._group_component(run_id)
        index = self._run_indexes.get(key)
        if index is None:
            self._run_index_counter += 1
            index = self._run_index_counter
            self._run_indexes[key] = index
        return index

    def _active_record(self) -> _RoundRecord | None:
        if self._active_uid is None:
            return None
        return self._record_by_uid.get(self._active_uid)

    def _selected_record(self) -> _RoundRecord | None:
        if self._selected_uid is None:
            return None
        return self._record_by_uid.get(self._selected_uid)

    def _get_or_create_record(
        self,
        run_id: object,
        round_id: object,
        *,
        cycle_index: int | None,
        target_label: str,
        implicit: bool,
    ) -> tuple[_RoundRecord, bool]:
        key = self._group_key(run_id, round_id)
        record = self._round_records.get(key)
        created = record is None
        was_following_active = self._selected_uid is None or self._selected_uid == self._active_uid
        if record is None:
            self._round_uid_counter += 1
            record = _RoundRecord(
                uid=self._round_uid_counter,
                run_id=run_id,
                round_id=round_id,
                run_index=self._run_index_for(run_id),
                cycle_index=cycle_index,
                target_label=target_label,
                detail_title="运行消息" if implicit else "轮次进行中",
                implicit=implicit,
            )
            self._round_records[key] = record
            self._record_by_uid[record.uid] = record
            self._round_order.append(key)
        else:
            record.target_label = target_label or record.target_label
            if cycle_index is not None:
                record.cycle_index = cycle_index
                record.implicit = False
        self._active_uid = record.uid
        if self._select_next_record or (created and was_following_active):
            self._selected_uid = record.uid
        self._select_next_record = False
        self._refresh_round_list()
        return record, created

    def _ensure_record(self, *, target_label: str | None = None, namespace: str = "session") -> _RoundRecord:
        record = self._active_record()
        if record is not None:
            return record
        label = str(self._current_target_label or target_label or "自动定点")
        round_id = f"{namespace}-session"
        record, _created = self._get_or_create_record(
            self._current_run_context,
            round_id,
            cycle_index=None,
            target_label=label,
            implicit=True,
        )
        return record

    def _ensure_auto_tid_record(self) -> _RoundRecord:
        record = self._active_record()
        if record is not None and "TID" in record.target_label.upper():
            return record
        if "TID" in self._current_target_label.upper():
            return self._ensure_record(target_label=self._current_target_label, namespace="auto-tid")
        record, _created = self._get_or_create_record(
            None,
            "auto-tid-session",
            cycle_index=None,
            target_label="自动 TID",
            implicit=True,
        )
        return record

    def _record_matches_filter(self, record: _RoundRecord) -> bool:
        result_filter = self.result_filter.currentData() or "all"
        status = _record_status(record)
        if result_filter == "warning" and not record.warnings:
            return False
        if result_filter == "active" and status not in {"进行中", "记录中"}:
            return False
        if result_filter == "shiny" and status != "疑似出闪":
            return False
        if result_filter == "not-shiny" and status != "未出闪":
            return False
        if result_filter == "no-candidate" and status != "无候选":
            return False
        keyword = self.search_edit.text().strip().casefold()
        if not keyword:
            return True
        searchable = " ".join(
            (
                str(record.run_id),
                str(record.round_id),
                f"运行 {record.run_index}",
                str(record.cycle_index or ""),
                record.target_label,
                record.seed_text or "",
                str(record.locked_advances or ""),
                status,
                record.detail_title,
                *record.warnings,
                *record.plain_lines,
            )
        ).casefold()
        return keyword in searchable

    def _refresh_round_list(self, *_args: object) -> None:
        if not hasattr(self, "round_list"):
            return
        previous_uid = self._selected_uid
        blocker = QSignalBlocker(self.round_list)
        self.round_list.clear()
        visible_uids: list[int] = []
        for key in reversed(self._round_order):
            record = self._round_records[key]
            if not self._record_matches_filter(record):
                continue
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, record.uid)
            row = _RoundListRow(record, self.round_list)
            accessible_text = (
                f"运行 {record.run_index}，"
                f"{'会话记录' if record.cycle_index is None else f'第 {record.cycle_index} 轮'}，"
                f"{record.target_label}，{_record_status(record)}"
            )
            row.setAccessibleName(accessible_text)
            item.setToolTip(accessible_text)
            item.setSizeHint(row.sizeHint())
            self.round_list.addItem(item)
            self.round_list.setItemWidget(item, row)
            visible_uids.append(record.uid)

        selected_uid: int | None = None
        if previous_uid in visible_uids:
            selected_uid = previous_uid
        elif self._active_uid in visible_uids:
            selected_uid = self._active_uid
        elif visible_uids:
            selected_uid = visible_uids[0]
        if selected_uid is not None:
            for row_index in range(self.round_list.count()):
                item = self.round_list.item(row_index)
                if item.data(Qt.ItemDataRole.UserRole) == selected_uid:
                    self.round_list.setCurrentItem(item)
                    break
        del blocker
        self.round_list_heading.setText(f"本次会话 · {len(self._round_order)} 条记录")
        self.clear_button.setEnabled(bool(self._round_records))
        self.export_button.setEnabled(bool(self.text_view.toPlainText()))
        self._set_empty_state(not visible_uids, filtered=bool(self._round_order))
        self._selected_uid = selected_uid
        if selected_uid != previous_uid:
            self._render_selected_record()
        else:
            self._update_detail_header()

    def _set_empty_state(self, empty: bool, *, filtered: bool = False) -> None:
        if empty:
            self.empty_state_title.setText(
                "没有符合条件的轮次" if filtered else "暂无轮次记录"
            )
            self.empty_state_detail.setText(
                "当前筛选结果为空" if filtered else "当前会话尚未产生运行结果"
            )
            self.round_splitter.hide()
            self.empty_state.show()
            return
        self.empty_state.hide()
        self.round_splitter.show()

    def _on_round_selected(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        selected_uid = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        if selected_uid == self._selected_uid:
            return
        self._selected_uid = selected_uid
        self._render_selected_record()

    def _update_detail_header(self) -> None:
        record = self._selected_record()
        enabled = record is not None
        self.copy_round_button.setEnabled(enabled and bool(record and record.plain_lines))
        can_correlate = bool(record and (not record.implicit or record.run_id is not None))
        self.related_logs_button.setEnabled(can_correlate)
        if record is None:
            self.detail_title_label.setText("请选择轮次")
            self.detail_time_label.clear()
            self.detail_status_label.clear()
            self.target_value_label.setText("-")
            self.seed_value_label.setText("-")
            self.locked_adv_value_label.setText("-")
            self.candidate_value_label.setText("-")
            self.warning_label.clear()
            self.warning_frame.hide()
            return

        round_title = (
            f"运行 {record.run_index} · 第 {record.cycle_index} 轮"
            if record.cycle_index is not None
            else f"运行 {record.run_index} · 会话记录"
        )
        self.detail_title_label.setText(f"{round_title} · {record.detail_title}")
        status = _record_status(record)
        status_color = {
            "有警告": "#A35F08",
            "疑似出闪": CLR_SHINY,
            "未出闪": CLR_LOCK,
            "无候选": "#606A76",
        }.get(status, "#176F5C")
        self.detail_status_label.setText(status)
        self.detail_status_label.setStyleSheet(
            f"color: {status_color}; background: #F3F4F6; border-radius: 3px; "
            "padding: 2px 6px; font-size: 12px; font-weight: 600;"
        )
        start = record.started_at.strftime("%Y-%m-%d %H:%M:%S")
        if record.updated_at.replace(microsecond=0) == record.started_at.replace(microsecond=0):
            time_text = start
        else:
            elapsed = max(0.0, (record.updated_at - record.started_at).total_seconds())
            time_text = f"{start} 至 {record.updated_at.strftime('%H:%M:%S')} · 用时 {elapsed:.1f} 秒"
        self.detail_time_label.setText(time_text)
        self.target_value_label.setText(record.target_label)
        self.seed_value_label.setText(record.seed_text or "-")
        self.locked_adv_value_label.setText(
            str(record.locked_advances) if record.locked_advances is not None else "-"
        )
        self.candidate_value_label.setText(f"{record.candidate_count} 个")
        if record.warnings:
            self.warning_label.setText("\n".join(record.warnings))
            self.warning_frame.show()
        else:
            self.warning_label.clear()
            self.warning_frame.hide()

    def _clear_feed_widgets(self) -> None:
        while self._feed_layout.count():
            item = self._feed_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render_selected_record(self) -> None:
        self._cancel_auto_follow()
        self._clear_feed_widgets()
        self._update_detail_header()
        record = self._selected_record()
        if record is None:
            return
        self._rendering_record = True
        try:
            for entry in record.entries:
                self._render_entry(entry)
        finally:
            self._rendering_record = False
        self._scroll_top_timer.start(0)

    def _scroll_selected_record_to_top(self) -> None:
        self.history_scroll.verticalScrollBar().setValue(0)

    def _render_entry(self, entry: _FeedEntry) -> None:
        if entry.kind == "text":
            label = QLabel(entry.text, self._feed_body)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setWordWrap(True)
            weight = "font-weight: 700;" if entry.bold else ""
            label.setStyleSheet(f"color: {entry.color}; padding: 1px 4px; {weight}")
            self._append_widget(label)
        elif entry.kind == "divider":
            line = QFrame(self._feed_body)
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFixedHeight(2 if entry.strong else 1)
            line.setStyleSheet(f"background: {entry.color}; border: none;")
            self._append_widget(line)
        elif entry.kind == "spacer":
            spacer = QWidget(self._feed_body)
            spacer.setFixedHeight(3)
            self._append_widget(spacer)
        elif entry.kind == "table":
            self._append_widget(self._create_candidate_table(entry))

    def _record_entry(self, entry: _FeedEntry) -> None:
        record = self._ensure_record()
        record.entries.append(entry)
        record.updated_at = datetime.now()
        if record.uid == self._selected_uid:
            self._render_entry(entry)

    def _touch_record(self, record: _RoundRecord | None = None) -> None:
        record = record or self._active_record()
        if record is None:
            return
        record.updated_at = datetime.now()
        self._refresh_round_list()

    def _add_warning(self, record: _RoundRecord, message: str) -> None:
        if message not in record.warnings:
            record.warnings.append(message)

    # ── 输出快捷方法 ──
    def _append_plain(self, text: str = "", color: str = CLR_BODY) -> None:
        record = self._ensure_record()
        record.plain_lines.append(text)
        record.updated_at = datetime.now()
        self.text_view.w(text, color)
        self.copy_button.setEnabled(bool(self.text_view.toPlainText()))
        self.export_button.setEnabled(bool(self.text_view.toPlainText()))
        if record.uid == self._selected_uid:
            self.copy_round_button.setEnabled(bool(record.plain_lines))

    def _append_widget(self, widget: QWidget) -> None:
        if self._rendering_record:
            self._feed_layout.addWidget(widget)
            return
        scroll_bar = self.history_scroll.verticalScrollBar()
        follow = self._follow_pending or scroll_bar.maximum() - scroll_bar.value() <= 4
        self._feed_layout.addWidget(widget)
        if follow and not self._follow_pending:
            self._follow_pending = True
            self._follow_origin = scroll_bar.value()
            self._follow_timer.start(0)

    def _append_text_label(self, text: str, color: str = CLR_BODY, *, bold: bool = False) -> None:
        self._record_entry(_FeedEntry(kind="text", text=text, color=color, bold=bold))

    def _append_divider(self, text: str, *, strong: bool = False) -> None:
        self._append_plain(text, CLR_SEP)
        self._record_entry(_FeedEntry(kind="divider", color=CLR_SEP, strong=strong))

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
        self._record_entry(_FeedEntry(kind="spacer"))

    def _update_summary(self) -> None:
        self.summary_label.setText(f"{self._cycle_count} 轮 · {self._candidate_count} 条候选")

    def clear(self) -> None:
        self._cancel_auto_follow()
        self._scroll_top_timer.stop()
        self._follow_timer.stop()
        self.text_view.clear()
        self._clear_feed_widgets()
        self.round_list.clear()
        self._round_records.clear()
        self._record_by_uid.clear()
        self._round_order.clear()
        self._active_uid = None
        self._selected_uid = None
        self._round_uid_counter = 0
        self._run_index_counter = 0
        self._run_indexes.clear()
        self._select_next_record = False
        self._cycle_index = 0
        self._cycle_count = 0
        self._candidate_count = 0
        self._pid_ec_seen.clear()
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self.copy_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.copy_round_button.setEnabled(False)
        self.related_logs_button.setEnabled(False)
        self._update_summary()
        self.round_list_heading.setText("本次会话 · 0 条记录")
        self._set_empty_state(True)
        self._update_detail_header()

    def finish_run(
        self,
        run_id: object,
        outcome: str,
        *,
        round_id: object = None,
        target_label: str | None = None,
    ) -> None:
        """Finalize the latest matching record without replacing a concrete result."""

        record = None
        if round_id is not None:
            record = self._round_records.get(self._group_key(run_id, round_id))
        if record is None:
            run_key = self._group_component(run_id)
            for key in reversed(self._round_order):
                candidate = self._round_records[key]
                if self._group_component(candidate.run_id) == run_key:
                    record = candidate
                    break
        if record is None:
            record, _created = self._get_or_create_record(
                run_id,
                "run-session",
                cycle_index=None,
                target_label=str(target_label or self._current_target_label or "自动流程"),
                implicit=True,
            )
        if record.outcome is None:
            record.outcome = str(outcome or "已结束")
            record.detail_title = {
                "已完成": "运行完成",
                "失败": "运行失败",
                "已停止": "运行已停止",
                "继续重试": "本轮结束，继续重试",
            }.get(record.outcome, "运行结束")
        self._touch_record(record)

    def copy_all(self) -> None:
        text = self.text_view.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

    def copy_selected_round(self) -> None:
        record = self._selected_record()
        if record is not None and record.plain_lines:
            QGuiApplication.clipboard().setText("\n".join(record.plain_lines) + "\n")

    def _request_related_logs(self) -> None:
        record = self._selected_record()
        if record is not None and (not record.implicit or record.run_id is not None):
            round_id = None if record.implicit else record.round_id
            self.related_logs_requested.emit(record.run_id, round_id)

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
        snapshots = tuple(_snapshot_candidate(state) for state in states)
        entry = _FeedEntry(
            kind="table",
            rows=tuple(tuple(row) for row in rows),
            states=snapshots,
            statuses=tuple(statuses),
            reverse=reverse,
        )
        record = self._ensure_record()
        record.candidate_count = len(rows)
        self._candidate_count += len(rows)
        self._update_summary()
        self._record_entry(entry)

    def _create_candidate_table(self, entry: _FeedEntry) -> QTableWidget:
        headers = REVERSE_HEADERS if entry.reverse else CANDIDATE_HEADERS
        widths = REVERSE_WIDTHS if entry.reverse else CANDIDATE_WIDTHS
        table = QTableWidget(len(entry.rows), len(headers), self._feed_body)
        table.setObjectName("HistoryCandidateTable")
        table.setAccessibleName("反查候选表" if entry.reverse else "候选结果表")
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
        for row_index, values in enumerate(entry.rows):
            locked = "锁定" in entry.statuses[row_index].split(" · ")
            synchronized = "同步" in entry.statuses[row_index].split(" · ")
            shiny = _get_int(entry.states[row_index], "shiny") > 0
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

        visible_rows = min(len(entry.rows), 7)
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
        return table

    # ── 事件方法 ───────────────────────────────────────────────

    def cycle_start(
        self,
        cycle_index: int,
        *,
        run_id: object = None,
        round_id: object = None,
        target_label: str | None = None,
    ) -> None:
        effective_run_id = self._current_run_context if run_id is None else run_id
        effective_round_id = cycle_index if round_id is None else round_id
        effective_target = str(target_label or self._current_target_label or "自动定点")
        if run_id is not None:
            self._current_run_context = run_id
        if target_label:
            self._current_target_label = str(target_label)
        record, created = self._get_or_create_record(
            effective_run_id,
            effective_round_id,
            cycle_index=cycle_index,
            target_label=effective_target,
            implicit=False,
        )
        record.detail_title = "轮次进行中"
        record.outcome = None
        if created:
            self._cycle_count += 1
        self._cycle_index = cycle_index
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
        self._touch_record(record)

    def seed_captured(self, seed_text: str, initial_advances: int, npc: int, max_advances: int) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.seed_text = str(seed_text)
        record.detail_title = "Seed 已捕捉"
        if self._is_first_seed:
            self._is_first_seed = False
            self._original_seed_text = seed_text
            self._original_seed_advances = initial_advances
            self._ts(f"原始 Seed: {seed_text}  初始帧: {initial_advances}  NPC: {npc}  最大搜索: {max_advances}")
        else:
            record.locked_advances = None
            record.candidate_count = 0
            record.detail_title = "Seed 已重新捕获"
            self._sep_thin()
            self._ts(f"重新测种，当前 Seed: {seed_text}  初始帧: {initial_advances}  (原始 Seed: {self._original_seed_text})")
            self._sep_thin()
        self._touch_record(record)

    def auto_tid_log(self, message: str) -> None:
        record = self._ensure_auto_tid_record()
        record.detail_title = "自动 TID 运行消息"
        self._ts(f"[自动TID] {message}")
        self._touch_record(record)

    def candidates_found(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None,
    ) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.detail_title = "候选搜索完成"
        if 0 <= locked_index < len(candidates):
            record.locked_advances = _get_int(candidates[locked_index], "advances")
        record.candidate_count = len(candidates)
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
        self._touch_record(record)

    def candidates_refiltered(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None,
    ) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.detail_title = "候选重新筛选完成"
        if 0 <= locked_index < len(candidates):
            record.locked_advances = _get_int(candidates[locked_index], "advances")
        record.candidate_count = len(candidates)
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
        self._touch_record(record)

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
        record = self._ensure_record(target_label="自动定点")
        record.outcome = "无候选"
        record.detail_title = "本轮搜索结束"
        record.candidate_count = 0
        self._sep_thin()
        self._w(_ts("本轮结果: 无候选"), CLR_TS)
        self._sep_thin()
        self._touch_record(record)

    def target_missed(self, missed_advances: int, current_advances: int) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.detail_title = "已错过目标，正在重新搜索"
        self._add_warning(record, f"错过目标：目标 Adv {missed_advances}，当前 Adv {current_advances}")
        self._sep_thin()
        self._w(_ts(f"错过 目标 (目标帧: {missed_advances} ≤ 目前帧数: {current_advances})"), CLR_ERROR)
        self._ts("重新搜索，排除已过帧…")
        self._sep_thin()
        self._touch_record(record)

    def cycle_result(
        self,
        is_shiny: bool,
        interval: float | None,
        trigger_advances: int | None = None,
        used_delay: int | None = None,
    ) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.outcome = "疑似出闪" if is_shiny else "未出闪"
        record.detail_title = "本轮结束"
        if trigger_advances is not None and record.locked_advances is None:
            record.locked_advances = int(trigger_advances)
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
        self._touch_record(record)

    def attempt_result(
        self,
        loop_index: int,
        attempt_index: int,
        interval: float | None,
        trigger_advances: int | None = None,
        used_delay: int | None = None,
    ) -> None:
        record = self._ensure_record(target_label="自动定点")
        record.detail_title = "继续尝试本轮候选"
        if trigger_advances is not None and record.locked_advances is None:
            record.locked_advances = int(trigger_advances)
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
        self._touch_record(record)

    def reverse_lookup_results(
        self, candidates: list[object], characteristic: str | None = None,
        delays: list[int] | None = None, ocr_stats: dict | None = None,
    ) -> None:
        record = self._ensure_record(target_label="自动定点")
        count = len(candidates)
        record.candidate_count = count
        record.detail_title = "自动反查完成"
        characteristic_match_failed = bool(
            ocr_stats and ocr_stats.get("characteristic_match_failed")
        )
        if characteristic_match_failed:
            self._add_warning(record, "个性匹配失败，已忽略个性条件")
            self._ts("个性匹配失败，已忽略个性条件")
            raw_characteristic = ocr_stats.get("characteristic_raw") if ocr_stats else None
            if raw_characteristic:
                self._info(f"OCR 个性原文: {raw_characteristic}")
        if count == 0:
            self._ts("反查结果: 未找到匹配个体")
            if ocr_stats:
                nature = ocr_stats.get("nature")
                chara = ocr_stats.get("characteristic") or characteristic
                if nature:
                    self._info(f"OCR 性格: {nature}")
                if chara and not characteristic_match_failed:
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
                candidate_characteristic = _state_characteristic(state, characteristic)
                key = _pid_ec_key(state)
                duplicate = self._pid_ec_seen.get(key)
                self._pid_ec_seen[key] = i + 1
                status = f"匹配 · 同候选{duplicate}" if duplicate else "匹配"
                statuses.append(status)
                rows.append(self._reverse_row(i, state, status, delay, candidate_characteristic))
                chara_text = f" 个性={candidate_characteristic}" if candidate_characteristic != "-" else ""
                self._append_plain(
                    f"反查候选{i + 1}: adv={adv} delay={delay} EC={_state_ec(state)} PID={_state_pid(state)} "
                    f"{_state_iv_text(getattr(state, 'ivs', None))} "
                    f"性格={_nature_text(state)}{chara_text} 特性={_get_int(state, 'ability')} "
                    f"性别={_gender_text(state)} 异色={_shiny_text(state)} "
                    f"身高={_get_int(state, 'height')} 体重={_get_int(state, 'weight')}"
                )
            self._append_candidate_table(rows, candidates, statuses, reverse=True)
        self._sep_end()
        self._touch_record(record)
