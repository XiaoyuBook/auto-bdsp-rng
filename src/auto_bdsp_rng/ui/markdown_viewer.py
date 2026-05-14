from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout


def read_markdown_text(path: Path, missing_message: str = "暂无更新日志") -> str:
    if not path.exists():
        return missing_message
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return missing_message
    return text or missing_message


class MarkdownViewerDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 520)

        layout = QVBoxLayout(self)
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlainText(text)
        layout.addWidget(self.text_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
