from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("location", ["source", "external", "internal", "missing"])
def test_fonts_load_without_a_system_installation(location, tmp_path):
    """A fresh Qt process proves both face identity and frozen resource lookup."""
    if location in ("external", "internal"):
        target = tmp_path if location == "external" else tmp_path / "_internal"
        shutil.copytree(ROOT / "docs/assets/fonts", target / "docs/assets/fonts")
    code = textwrap.dedent("""
        from pathlib import Path
        import sys
        from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QRawFont, QTextLayout
        from PySide6.QtWidgets import QApplication, QLabel
        from auto_bdsp_rng.ui import workspace_theme as theme

        location, root = sys.argv[1:]
        app = QApplication([])
        original_families = QFontDatabase.families()
        if location != 'source':
            sys.frozen = True
            sys.executable = str(Path(root) / 'app.exe')
            sys._MEIPASS = str(Path(root) / '_internal')
        ids = theme.ensure_ui_fonts()
        assert theme.ensure_ui_fonts() == ids
        if location == 'missing':
            assert ids == ()
            font = theme.ui_font()
            assert QRawFont.fromFont(font).isValid()
            assert QFontDatabase.families() == original_families
            assert font.weight() == QFont.Weight.Normal
            assert theme.ui_styles('font-weight: 400;') == 'font-weight: 400;'
        else:
            assert len(ids) == 2
            for style, weight, native_weight, filename in zip(
                ('Regular', 'Medium'), (QFont.Weight.Normal, QFont.Weight.Medium),
                (330, 380), theme.BUNDLED_FONT_FILES
            ):
                font = theme.ui_font(28, weight)
                source = QRawFont(str(theme.resource_path('docs','assets','fonts',filename)), 28)
                text = QTextLayout('中文精灵帧0123456789', font)
                text.beginLayout()
                text.createLine()
                text.endLayout()
                runs = text.glyphRuns()
                assert runs
                for run in runs:
                    raw = run.rawFont()
                    assert raw.familyName() == theme.BUNDLED_FONT_FAMILY
                    assert raw.styleName() == style
                    assert raw.weight() == native_weight
                    assert 0 not in run.glyphIndexes()
                    # Compare outlines and metrics with the packaged face, not
                    # just the requested family name reported by QFontInfo.
                    assert not source.fontTable('CFF ').isEmpty()
                    assert raw.fontTable('CFF ') == source.fontTable('CFF ')
                    assert raw.fontTable('hmtx') == source.fontTable('hmtx')
                assert font.featureValue(QFont.Tag('tnum')) == 1
                metrics = QFontMetricsF(font)
                assert len({metrics.horizontalAdvance(d) for d in '0123456789'}) == 1
                # QSS can override a correct QFont. Check the actual face after
                # both stylesheet inheritance and a widget-specific weight rule.
                parent = QLabel()
                parent.setFont(theme.ui_font())
                parent.setStyleSheet(theme.ui_styles('QLabel { font-family: MiSans; font-weight: 400; }'))
                label = QLabel('中文精灵帧0123456789', parent)
                label.setFont(theme.ui_font(28, weight))
                label.setStyleSheet(theme.ui_styles(f'font-size: 28px; font-weight: {int(weight)};'))
                label.ensurePolished()
                styled = QRawFont.fromFont(label.font())
                assert styled.styleName() == style
                assert styled.weight() == native_weight
                assert styled.fontTable('CFF ') == source.fontTable('CFF ')
                assert len({QFontMetricsF(label.font()).horizontalAdvance(d) for d in '0123456789'}) == 1
    """)
    env = dict(os.environ, QT_QPA_PLATFORM="windows" if sys.platform == "win32" else "offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", code, location, str(tmp_path)],
        env=env, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if location == "missing":
        assert "using font fallback" in result.stderr
