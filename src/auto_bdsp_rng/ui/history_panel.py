"""历史记录面板 —— 第 5 个 Tab。

记录自动定点乱数每轮循环的候选、锁定、错过、结果、反查信息。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ── 配色 ──────────────────────────────────────────────────
CLR_SEP     = "#9CA3AF"   # 分隔线
CLR_TS      = "#6B7280"   # 时间戳
CLR_BODY    = "#E5E7EB"   # 正文
CLR_LOCK    = "#16A34A"   # 锁定 / 成功
CLR_SYNC    = "#22D3EE"   # 同步标签
CLR_SHINY   = "#F59E0B"   # 出闪
CLR_ERROR   = "#F87171"   # 错过 / 失败


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
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", fmt)
        self.setTextCursor(cursor)
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
    if ivs is None:
        return "?"
    values = tuple(int(v) for v in ivs)
    if len(values) != 6:
        return "?"
    return " / ".join(f"{n}={v}" for n, v in zip(("HP", "攻击", "防御", "特攻", "特防", "速度"), values))


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
        self._build_ui()
        self._cycle_index = 0
        self._pid_ec_seen: dict[str, int] = {}
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        clear_btn = QPushButton("清空")
        clear_btn.setFixedHeight(30)
        clear_btn.clicked.connect(self.clear)
        export_btn = QPushButton("导出")
        export_btn.setFixedHeight(30)
        export_btn.clicked.connect(self.export_to_file)
        toolbar.addStretch()
        toolbar.addWidget(clear_btn)
        toolbar.addWidget(export_btn)
        layout.addLayout(toolbar)

        self.view = _CopyableTextEdit()
        self.view.setFont(QFont("Consolas", 10))
        self.view.setStyleSheet("QPlainTextEdit { padding: 12px; }")
        layout.addWidget(self.view, 1)

    # ── 输出快捷方法 ──
    def _sep(self) -> None:            self.view.w(SEPARATOR_THICK, CLR_SEP)
    def _sep_thin(self) -> None:       self.view.w(SEPARATOR_THIN, CLR_SEP)
    def _sep_end(self) -> None:        self.view.w(SEPARATOR_END, CLR_SEP)
    def _ts(self, text: str) -> None:  self.view.w(_ts(text), CLR_BODY)
    def _info(self, text: str) -> None: self.view.w(f"  {text}", CLR_BODY)
    def _w(self, text: str, c: str = CLR_BODY) -> None: self.view.w(text, c)
    def _blank(self) -> None:          self.view.w()

    def clear(self) -> None:
        self.view.clear()
        self._cycle_index = 0
        self._pid_ec_seen.clear()

    def export_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出历史记录", "history.txt", "文本文件 (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.view.toPlainText())

    # ── 事件方法 ───────────────────────────────────────────────

    def cycle_start(self, cycle_index: int) -> None:
        self._cycle_index = cycle_index
        self._pid_ec_seen.clear()
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self._blank()
        self._sep()
        self._w(f"第 {cycle_index} 轮  {_now()}", CLR_TS)
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

    def candidates_found(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None, candidate_delay: int | None = None,
    ) -> None:
        self._pid_ec_seen.clear()
        self._ts(f"搜索到 {len(candidates)} 个候选")
        for i, state in enumerate(candidates):
            self._write_candidate(i, state, locked_index, sync_flags, candidate_delay)

    def candidates_refiltered(
        self, candidates: list[object], locked_index: int,
        sync_flags: list[str] | None = None, candidate_delay: int | None = None,
    ) -> None:
        self._pid_ec_seen.clear()
        self._ts(f"剩余 {len(candidates)} 个候选")
        for i, state in enumerate(candidates):
            self._write_candidate(i, state, locked_index, sync_flags, candidate_delay)

    def _write_candidate(
        self, i: int, state: object, locked_index: int,
        sync_flags: list[str] | None, candidate_delay: int | None,
    ) -> None:
        adv = _get_int(state, "advances")
        delay = candidate_delay if candidate_delay is not None else adv
        key = _pid_ec_key(state)
        dup = self._pid_ec_seen.get(key)
        self._pid_ec_seen[key] = i + 1

        tags = []
        if i == locked_index:
            tags.append("锁定")
        if sync_flags and i < len(sync_flags) and sync_flags[i] == "sync":
            tags.append("同步")
        if dup:
            tags.append(f"同候选{dup}")
        tag_str = f"({', '.join(tags)}) " if tags else ""

        line = (
            f"  候选{i + 1} {tag_str}adv={adv} delay={delay} "
            f"EC={_state_ec(state)} PID={_state_pid(state)} "
            f"{_state_iv_text(getattr(state, 'ivs', None))} "
            f"性格={_nature_text(state)} 异色={_shiny_text(state)} "
            f"身高={_get_int(state, 'height')} 体重={_get_int(state, 'weight')}"
        )
        # 锁定行绿色，普通行白色
        color = CLR_LOCK if i == locked_index else CLR_BODY
        self._w(line, color)

    def target_missed(self, missed_advances: int, current_advances: int) -> None:
        self._sep_thin()
        self._w(_ts(f"错过 目标 (目标帧: {missed_advances} ≤ 目前帧数: {current_advances})"), CLR_ERROR)
        self._ts("重新搜索，排除已过帧…")
        self._sep_thin()

    def cycle_result(self, is_shiny: bool, interval: float | None, used_delay: int | None = None) -> None:
        self._sep_thin()
        interval_text = f"{interval:.3f}s" if interval is not None else "-"
        delay_text = f"  使用 delay: {used_delay}" if used_delay is not None else ""
        if is_shiny:
            self._w(_ts(f"本轮结果: 疑似出闪  间隔: {interval_text}{delay_text}"), CLR_SHINY)
        else:
            self._ts(f"本轮结果: 未出闪  间隔: {interval_text}{delay_text}")
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
            for i, state in enumerate(candidates):
                adv = _get_int(state, "advances")
                delay = delays[i] if delays else adv
                chara_text = f" 个性={characteristic}" if characteristic else ""
                self._info(
                    f"反查候选{i + 1}: adv={adv} delay={delay} EC={_state_ec(state)} PID={_state_pid(state)} "
                    f"{_state_iv_text(getattr(state, 'ivs', None))} "
                    f"性格={_nature_text(state)}{chara_text} 特性={_get_int(state, 'ability')} "
                    f"性别={_gender_text(state)} 异色={_shiny_text(state)} "
                    f"身高={_get_int(state, 'height')} 体重={_get_int(state, 'weight')}"
                )
        self._sep_end()
