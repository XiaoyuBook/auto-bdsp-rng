"""Target/status overview and the observed static RNG flow."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QBoxLayout, QScrollArea, QSizePolicy, QWidget

from auto_bdsp_rng.automation.auto_rng.models import AutoRngPhase
from auto_bdsp_rng.ui.workspace_theme import ui_font


class OverviewCards(QWidget):
    """Keep both cards in the same workspace at every window width."""

    def __init__(self, target: QWidget, status: QWidget) -> None:
        super().__init__()
        self.setObjectName("AutoRngOverview")
        self.row = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(12)
        self.row.addWidget(target, 1)
        self.row.addWidget(status, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def resizeEvent(self, event) -> None:  # noqa: N802
        direction = (QBoxLayout.Direction.LeftToRight if self.width() >= 570
                     else QBoxLayout.Direction.TopToBottom)
        if self.row.direction() != direction:
            self.row.setDirection(direction)
        super().resizeEvent(event)


class TargetConditions(QScrollArea):
    """Fit short filters, while keeping long/multiple filters scrollable."""

    def fit_content(self) -> None:
        if self.widget() is None:
            return
        layout = self.widget().layout()
        height = layout.totalHeightForWidth(max(1, self.viewport().width()))
        desired = max(30, min(102, height + 2))
        if self.height() != desired:
            self.setFixedHeight(desired)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self, self.fit_content)


_NODES = {
    "seed": ("测种", .09, .49), "search": ("搜索", .30, .19),
    "advance": ("过帧", .30, .78), "calibrate": ("校正", .51, .49),
    "wait": ("等待", .72, .19), "hit": ("撞帧判闪", .91, .49),
    "result": ("结果", .72, .78),
}
_EDGES = (
    ("seed", "search"), ("search", "advance"), ("advance", "calibrate"),
    ("calibrate", "search"), ("calibrate", "wait"), ("search", "wait"),
    ("wait", "hit"), ("hit", "result"), ("advance", "seed"),
)
_PHASE_NODE = {
    AutoRngPhase.RUN_SEED_SCRIPT: "seed", AutoRngPhase.CAPTURE_SEED: "seed",
    AutoRngPhase.SEARCH_TARGET: "search", AutoRngPhase.DECIDE_ADVANCE: "advance",
    AutoRngPhase.RUN_ADVANCE_SCRIPT: "advance", AutoRngPhase.REIDENTIFY: "calibrate",
    AutoRngPhase.EXIT_RESEED: "calibrate", AutoRngPhase.FINAL_CALIBRATE: "calibrate",
    AutoRngPhase.FINAL_WAIT: "wait", AutoRngPhase.FINAL_ADJUST: "hit",
    AutoRngPhase.RUN_HIT_SCRIPT: "hit", AutoRngPhase.RUN_ESCAPE_SCRIPT: "result",
    AutoRngPhase.REVERSE_LOOKUP: "result", AutoRngPhase.LOOP_CHECK: "result",
    AutoRngPhase.COMPLETED: "result",
}


class StaticFlowMap(QWidget):
    """Highlight observed nodes/edges; never invent a predicted branch."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("StaticFlowMap")
        self.setFixedHeight(142)
        self.setMinimumWidth(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.current_node: str | None = None
        self.visited_edges: set[tuple[str, str]] = set()
        self.failed = False
        self.setToolTip("高亮节点为当前阶段，绿色连线为本轮实际经过的路径；浅线表示可能的流程分支。")
        self.set_phase(AutoRngPhase.IDLE)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(260, 142)

    def set_phase(self, phase: AutoRngPhase) -> None:
        self.failed = phase == AutoRngPhase.FAILED
        if phase == AutoRngPhase.IDLE:
            self.current_node = None
            self.visited_edges.clear()
        elif phase != AutoRngPhase.FAILED:
            node = _PHASE_NODE.get(phase)
            if node and self.current_node and node != self.current_node:
                self.visited_edges.add((self.current_node, node))
            self.current_node = node
        label = _NODES[self.current_node][0] if self.current_node else "等待开始"
        self.setAccessibleName(f"自动定点流程：{label}" + ("，运行失败" if self.failed else ""))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(ui_font(11))
        width, height = self.width(), self.height() - 8
        points = {node: QPointF(x * width, y * height) for node, (_, x, y) in _NODES.items()}
        for source, target in dict.fromkeys((*_EDGES, *sorted(self.visited_edges))):
            a, b = points[source], points[target]
            offset = b - a
            distance = max(1.0, (offset.x() ** 2 + offset.y() ** 2) ** .5)
            offset *= 10 / distance
            observed = (source, target) in self.visited_edges
            painter.setPen(QPen(QColor("#8BC6AE" if observed else "#E0E7E4"), 1.5 if observed else 1))
            painter.drawLine(a + offset, b - offset)
        for node, (label, _x, _y) in _NODES.items():
            point = points[node]
            active = node == self.current_node
            accent = QColor("#AC4B42" if self.failed else "#087C58")
            if active:
                halo = QRadialGradient(point, 22)
                halo.setColorAt(0, QColor("#F3DCD5" if self.failed else "#C6E9D8"))
                halo.setColorAt(1, QColor(255, 255, 255, 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(halo)
                painter.drawEllipse(point, 22, 22)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor("#DDADA1" if self.failed else "#A0CFBA"), 1))
                painter.drawEllipse(point, 11, 11)
            painter.setPen(QPen(accent if active else QColor("#A8B6AF"), 1.2))
            painter.setBrush(accent if active else QColor("#FFFFFF"))
            painter.drawEllipse(point, 5.5, 5.5)
            painter.setPen(accent if active else QColor("#7B8881"))
            above = node in ("search", "wait")
            label_y = point.y() - 27 if above else point.y() + 13
            # Break the rightmost label before it can touch the card edge.
            text = "撞帧\n判闪" if node == "hit" and width < 350 else label
            label_width = 48 if node == "hit" else 44
            rect = QRectF(max(0, min(width - label_width, point.x() - label_width / 2)),
                          label_y, label_width, 30 if "\n" in text else 18)
            painter.drawText(rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
