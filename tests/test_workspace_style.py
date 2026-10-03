from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.parametrize("platform_style", ["Windows", "windowsvista", "windows11", "Fusion"])
@pytest.mark.parametrize("scale", ["1", "1.25", "1.5"])
def test_workspace_style_bounds_arrows_at_different_dpi_and_native_styles(platform_style, scale):
    # Fresh processes avoid cached platform scale and QApplication style state.
    code = textwrap.dedent("""
        import sys
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtGui import QImage, QPainter, QPalette
        from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory, QStyleOption
        from auto_bdsp_rng.ui.workspace_style import configure_workspace_style

        app = QApplication([])
        requested = sys.argv[1]
        if requested.casefold() not in {name.casefold() for name in QStyleFactory.keys()}:
            sys.exit(77)
        app.setStyle(requested)
        configure_workspace_style(app)
        style = app.style()
        assert style.baseStyle().objectName().casefold() == 'fusion'
        configure_workspace_style(app)
        assert app.style() is style
        assert app.palette().color(QPalette.ColorRole.Window).lightness() > 200
        factor = float(sys.argv[2])
        for primitive in (QStyle.PrimitiveElement.PE_IndicatorArrowDown,
                          QStyle.PrimitiveElement.PE_IndicatorArrowUp,
                          QStyle.PrimitiveElement.PE_IndicatorArrowLeft,
                          QStyle.PrimitiveElement.PE_IndicatorArrowRight,
                          QStyle.PrimitiveElement.PE_IndicatorButtonDropDown):
            for enabled in (False, True):
                image = QImage(round(80 * factor), round(32 * factor), QImage.Format.Format_ARGB32)
                image.setDevicePixelRatio(factor)
                image.fill(Qt.GlobalColor.transparent)
                option = QStyleOption()
                option.rect = QRect(0, 0, 80, 32)
                option.palette = app.palette()
                option.state = QStyle.StateFlag.State_Enabled if enabled else QStyle.StateFlag.State_None
                painter = QPainter(image)
                style.drawPrimitive(primitive, option, painter)
                painter.end()
                ink = [(x, y) for y in range(image.height()) for x in range(image.width())
                       if image.pixelColor(x, y).alpha() > 0]
                assert ink, (primitive, enabled)
                xs, ys = zip(*ink)
                assert max(xs) - min(xs) + 1 <= 12 * factor
                assert max(ys) - min(ys) + 1 <= 12 * factor
                assert abs((min(xs) + max(xs) + 1) / 2 - 40 * factor) <= factor
                assert abs((min(ys) + max(ys) + 1) / 2 - 16 * factor) <= factor
    """)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", code, platform_style, scale],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if result.returncode == 77:
        pytest.skip(f"Qt style {platform_style} is not available")
    assert result.returncode == 0, result.stdout + result.stderr
