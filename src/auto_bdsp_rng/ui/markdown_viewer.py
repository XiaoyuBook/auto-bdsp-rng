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
        self.setObjectName("MarkdownViewerDialog")
        self.resize(720, 520)
        self.setStyleSheet(
            "QDialog#MarkdownViewerDialog { background: #ffffff; color: #24312d; }"
            " QDialog#MarkdownViewerDialog QPlainTextEdit {"
            " background: #ffffff; color: #24312d; border: 1px solid #e2e8e4;"
            " border-radius: 4px; padding: 8px; }"
            " QDialog#MarkdownViewerDialog QDialogButtonBox QPushButton {"
            " background: #ffffff; color: #24312d; border: 1px solid #e2e8e4;"
            " border-radius: 4px; min-height: 32px; padding: 0 14px; }"
            " QDialog#MarkdownViewerDialog QDialogButtonBox QPushButton:hover {"
            " background: #f6f8f7; border-color: #bfcfc6; }"
        )

        layout = QVBoxLayout(self)
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlainText(text)
        layout.addWidget(self.text_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
