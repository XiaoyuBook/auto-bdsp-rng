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
        from PySide6.QtGui import QFont, QFontDatabase, QRawFont, QTextLayout
        from PySide6.QtWidgets import QApplication
        from auto_bdsp_rng.ui import workspace_theme as theme

        location, root = sys.argv[1:]
        app = QApplication([])
        assert theme.BUNDLED_FONT_FAMILY not in QFontDatabase.families()
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
            assert theme.BUNDLED_FONT_FAMILY not in QFontDatabase.families()
        else:
            assert len(ids) == 2
            for style, weight, filename in zip(
                ('Regular', 'Medium'), (QFont.Weight.Normal, QFont.Weight.Medium), theme.BUNDLED_FONT_FILES
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
                    assert raw.weight() == weight
                    assert 0 not in run.glyphIndexes()
                    # Compare outlines and metrics with the packaged face, not
                    # just the requested family name reported by QFontInfo.
                    assert raw.fontTable('CFF ') == source.fontTable('CFF ')
                    assert raw.fontTable('hmtx') == source.fontTable('hmtx')
                assert font.featureValue(QFont.Tag('tnum')) == 1
    """)
    env = dict(os.environ, QT_QPA_PLATFORM="windows" if sys.platform == "win32" else "offscreen", PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", code, location, str(tmp_path)],
        env=env, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if location == "missing":
        assert "using font fallback" in result.stderr
