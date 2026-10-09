"""A stable light Qt style, independent of the Windows theme and style plugin."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle


_ARROWS = {
    QStyle.PrimitiveElement.PE_IndicatorArrowDown: ((-4, -2), (0, 2), (4, -2)),
    QStyle.PrimitiveElement.PE_IndicatorButtonDropDown: ((-4, -2), (0, 2), (4, -2)),
    QStyle.PrimitiveElement.PE_IndicatorArrowUp: ((-4, 2), (0, -2), (4, 2)),
    QStyle.PrimitiveElement.PE_IndicatorArrowLeft: ((2, -4), (-2, 0), (2, 4)),
    QStyle.PrimitiveElement.PE_IndicatorArrowRight: ((-2, -4), (2, 0), (-2, 4)),
}


class WorkspaceStyle(QProxyStyle):
    """Keep native widget behavior while drawing bounded vector chevrons."""

    def __init__(self) -> None:
        # Fusion is built into Qt Widgets; packaged Windows style plugins and
        # the host OS version no longer select the application's base metrics.
        super().__init__("Fusion")
        self.setObjectName("BdspWorkspace")

    def drawPrimitive(self, element, option, painter, widget=None) -> None:  # noqa: N802
        points = _ARROWS.get(element)
        if points is None:
            super().drawPrimitive(element, option, painter, widget)
            return
        rect = QRectF(option.rect)
        if rect.isEmpty():
            return
        center = rect.center()
        scale = min(1.0, max(0.0, (min(rect.width(), rect.height()) - 2) / 8))
        group = (
            QPalette.ColorGroup.Active
            if option.state & QStyle.StateFlag.State_Enabled
            else QPalette.ColorGroup.Disabled
        )
        color = option.palette.color(group, QPalette.ColorRole.ButtonText)
        path = QPainterPath()
        for index, (x, y) in enumerate(points):
            point = center + QPointF(x * scale, y * scale)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.restore()


def configure_workspace_style(app: QApplication | None) -> None:
    """Install once, before constructing the main window and its dialogs."""
    if app is None or getattr(app, "_bdsp_workspace_style", None) is not None:
        return
    # Lightweight application doubles used by startup tests do not expose the
    # optional style hook; the real QApplication always does.
    if not callable(getattr(app, "setStyle", None)):
        return
    style = WorkspaceStyle()
    app.setStyle(style)
    # Use the base style's light palette for controls not covered by QSS too.
    app.setPalette(style.standardPalette())
    app._bdsp_workspace_style = style
