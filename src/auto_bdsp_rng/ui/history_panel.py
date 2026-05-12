"""历史记录面板 —— 第 5 个 Tab。

记录自动定点乱数每轮循环的候选、锁定、错过、结果、反查信息。
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

class _CopyableTextEdit(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.setUndoRedoEnabled(False)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        if menu is None or menu.isEmpty():
            from PySide6.QtWidgets import QMenu
            from PySide6.QtGui import QAction
            menu = QMenu(self)
            menu.addAction("复制", self.copy, QAction.Shortcut("Ctrl+C"))
            menu.addAction("全选", self.selectAll, QAction.Shortcut("Ctrl+A"))
        menu.exec(event.globalPos())

SEPARATOR_THICK = "═" * 54
SEPARATOR_THIN = "─" * 54
SEPARATOR_END = "─" * 4 + "\n"


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _state_iv_text(ivs: object) -> str:
    if ivs is None:
        return "?"
    values = tuple(int(v) for v in ivs)
    if len(values) != 6:
        return "?"
    names = ("HP", "攻击", "防御", "特攻", "特防", "速度")
    return " / ".join(f"{n}={v}" for n, v in zip(names, values))


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
    11: "急躁", 12: "认真", 13: "爽朗", 14: "天真", 15: "害羞",
    16: "温顺", 17: "冷静", 18: "内敛", 19: "慢吞吞", 20: "马虎",
    21: "温和", 22: "自大", 23: "慎重", 24: "浮躁",
}
GENDER_MAP = {0: "雄", 1: "雌", 2: "无"}
SHINY_MAP = {0: "否", 1: "星闪", 2: "方闪"}


def _nature_text(state: object) -> str:
    n = _get_int(state, "nature")
    return NATURE_ZH_MAP.get(n, str(n))


def _gender_text(state: object) -> str:
    g = _get_int(state, "gender")
    return GENDER_MAP.get(g, str(g))


def _shiny_text(state: object) -> str:
    s = _get_int(state, "shiny")
    return SHINY_MAP.get(s, str(s))


def _format_candidate(state: object, advances: int, delay: int, same_as: str = "") -> str:
    lines = []
    tag = f" (同个体: {same_as})" if same_as else ""
    lines.append(f"  ── 候选 ──{tag}")
    lines.append(f"  advances: {advances}  delay: {delay}")
    lines.append(f"  EC: {_state_ec(state)}  PID: {_state_pid(state)}")
    lines.append(f"  {_state_iv_text(getattr(state, 'ivs', None))}")
    lines.append(f"  性格: {_nature_text(state)}  特性: {_get_int(state, 'ability')}  性别: {_gender_text(state)}  异色: {_shiny_text(state)}")
    height = _get_int(state, "height")
    weight = _get_int(state, "weight")
    if height or weight:
        lines.append(f"  身高: {height}  体重: {weight}")
    return "\n".join(lines)


def _format_reverse_candidate(state: object, advances: int, delay: int, characteristic: str | None = None) -> str:
    lines = []
    lines.append(f"  ── 反查候选 ──")
    lines.append(f"  advances: {advances}  delay: {delay}  EC: {_state_ec(state)}  PID: {_state_pid(state)}")
    lines.append(f"  {_state_iv_text(getattr(state, 'ivs', None))}")
    nature = _nature_text(state)
    chara_text = f"  个性: {characteristic}" if characteristic else ""
    lines.append(f"  性格: {nature}{chara_text}")
    lines.append(f"  特性: {_get_int(state, 'ability')}  性别: {_gender_text(state)}  身高: {_get_int(state, 'height')}  体重: {_get_int(state, 'weight')}")
    return "\n".join(lines)


class HistoryPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._cycle_index = 0
        self._pid_ec_seen: dict[str, int] = {}  # pid:ec → 候选编号

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 工具栏
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

        # 文本区
        self.text_view = _CopyableTextEdit()
        self.text_view.setFont(QFont("Consolas", 10))
        self.text_view.setStyleSheet("QPlainTextEdit { padding: 12px; }")
        layout.addWidget(self.text_view, 1)

    def _w(self, line: str = "") -> None:
        self.text_view.appendPlainText(line)

    def clear(self) -> None:
        self.text_view.clear()
        self._cycle_index = 0
        self._pid_ec_seen.clear()

    def export_to_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出历史记录", "history.txt", "文本文件 (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.text_view.toPlainText())

    # ── 事件方法 ───────────────────────────────────────────────

    def cycle_start(self, cycle_index: int) -> None:
        self._cycle_index = cycle_index
        self._pid_ec_seen.clear()
        self._is_first_seed = True
        self._original_seed_text = ""
        self._original_seed_advances = 0
        self._w()
        self._w(SEPARATOR_THICK)
        self._w(f"第 {cycle_index} 轮  {_now()}")
        self._w(SEPARATOR_THICK)

    def seed_captured(self, seed_text: str, initial_advances: int, npc: int, max_advances: int) -> None:
        if self._is_first_seed:
            self._is_first_seed = False
            self._original_seed_text = seed_text
            self._original_seed_advances = initial_advances
            self._w(f"[{_now()}] 原始 Seed: {seed_text}  初始帧: {initial_advances}  NPC: {npc}  最大搜索: {max_advances}")
        else:
            self._w(SEPARATOR_THIN)
            self._w(f"[{_now()}] 重新测种，当前 Seed: {seed_text}  初始帧: {initial_advances}  (原始 Seed: {self._original_seed_text})")
            self._w(SEPARATOR_THIN)

    def candidates_found(self, candidates: list[object], locked_index: int) -> None:
        self._pid_ec_seen.clear()
        self._w(f"[{_now()}] 搜索到 {len(candidates)} 个候选")
        self._w()
        for i, state in enumerate(candidates):
            adv = _get_int(state, "advances")
            delay = adv  # runner 层会传入正确 delay
            tag = ""
            if i == locked_index:
                tag = "最低帧, 已锁定"
            key = _pid_ec_key(state)
            if key in self._pid_ec_seen:
                tag = f"同个体: 候选{self._pid_ec_seen[key]}"
            else:
                self._pid_ec_seen[key] = i + 1
            label = f"  ── 候选 {i + 1}"
            if tag:
                label += f" ({tag})"
            label += " ──"
            self._w(label)
            self._w(f"  advances: {adv}  delay: {delay}")
            self._w(f"  EC: {_state_ec(state)}  PID: {_state_pid(state)}")
            ivs = getattr(state, "ivs", None)
            self._w(f"  {_state_iv_text(ivs)}")
            self._w(f"  性格: {_nature_text(state)}  特性: {_get_int(state, 'ability')}  性别: {_gender_text(state)}  异色: {_shiny_text(state)}")
            h = _get_int(state, "height")
            w = _get_int(state, "weight")
            self._w(f"  身高: {h}  体重: {w}")
            self._w()

    def target_missed(self, missed_advances: int, current_advances: int) -> None:
        self._w(SEPARATOR_THIN)
        self._w(f"[{_now()}] 错过 目标 (目标帧: {missed_advances} ≤ 目前帧数: {current_advances})")
        self._w(f"[{_now()}] 重新搜索，排除已过帧...")
        self._w(SEPARATOR_THIN)

    def candidates_refiltered(self, candidates: list[object], locked_index: int) -> None:
        """错过目标后，重新筛选的候选列表。"""
        self._pid_ec_seen.clear()
        self._w(f"[{_now()}] 剩余 {len(candidates)} 个候选")
        self._w()
        for i, state in enumerate(candidates):
            adv = _get_int(state, "advances")
            delay = adv
            tag = ""
            if i == locked_index:
                tag = "最低帧, 已锁定"
            key = _pid_ec_key(state)
            if key in self._pid_ec_seen:
                tag = f"同个体: 候选{self._pid_ec_seen[key]}"
            else:
                self._pid_ec_seen[key] = i + 1
            label = f"  ── 候选 {i + 1}"
            if tag:
                label += f" ({tag})"
            label += " ──"
            self._w(label)
            self._w(f"  advances: {adv}  delay: {delay}")
            self._w(f"  EC: {_state_ec(state)}  PID: {_state_pid(state)}")
            ivs = getattr(state, "ivs", None)
            self._w(f"  {_state_iv_text(ivs)}")
            self._w(f"  性格: {_nature_text(state)}  特性: {_get_int(state, 'ability')}  性别: {_gender_text(state)}  异色: {_shiny_text(state)}")
            self._w(f"  身高: {_get_int(state, 'height')}  体重: {_get_int(state, 'weight')}")
            self._w()

    def cycle_result(self, is_shiny: bool, interval: float | None, used_delay: int | None = None) -> None:
        self._w(SEPARATOR_THIN)
        interval_text = f"{interval:.3f}s" if interval is not None else "-"
        delay_text = f"  使用 delay: {used_delay}" if used_delay is not None else ""
        if is_shiny:
            self._w(f"[{_now()}] 本轮结果: 疑似出闪  间隔: {interval_text}{delay_text}")
        else:
            self._w(f"[{_now()}] 本轮结果: 未出闪  间隔: {interval_text}{delay_text}")
        self._w(SEPARATOR_THIN)

    def reverse_lookup_results(self, candidates: list[object], characteristic: str | None = None, delays: list[int] | None = None) -> None:
        count = len(candidates)
        if count == 0:
            self._w(f"[{_now()}] 反查结果: 未找到匹配个体")
        else:
            self._w(f"[{_now()}] 反查结果 ({count} 个匹配):")
            self._w()
            for i, state in enumerate(candidates):
                adv = _get_int(state, "advances")
                delay = delays[i] if delays else adv
                self._w(f"  ── 反查候选 {i + 1} ──")
                self._w(f"  advances: {adv}  delay: {delay}  EC: {_state_ec(state)}  PID: {_state_pid(state)}")
                ivs = getattr(state, "ivs", None)
                self._w(f"  {_state_iv_text(ivs)}")
                nature = _nature_text(state)
                chara_text = f"  个性: {characteristic}" if characteristic else ""
                self._w(f"  性格: {nature}{chara_text}")
                self._w(f"  特性: {_get_int(state, 'ability')}  性别: {_gender_text(state)}  身高: {_get_int(state, 'height')}  体重: {_get_int(state, 'weight')}")
                self._w()
        self._w(SEPARATOR_END)
