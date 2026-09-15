"""Execution position and source browsing inside the existing EasyCon page."""

from __future__ import annotations

from pathlib import Path
from os.path import normcase
from time import monotonic
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QStackedWidget,
    QStyledItemDelegate, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from auto_bdsp_rng.automation.easycon.native.trace import ExecutionSnapshot
from auto_bdsp_rng.automation.easycon.scripts import scan_builtin_scripts
from auto_bdsp_rng.ui.check_box import CheckmarkCheckBox
from auto_bdsp_rng.ui.workspace_controls import workspace_icon
from auto_bdsp_rng.ui.workspace_theme import ui_styles

if TYPE_CHECKING:
    from auto_bdsp_rng.ui.easycon_panel import EasyConPanel


SOURCE_ROLE = Qt.ItemDataRole.UserRole
CAPTION_ROLE = Qt.ItemDataRole.UserRole + 1
ACTIVE_ROLE = Qt.ItemDataRole.UserRole + 2


def source_key(source: str) -> str:
    return normcase(str(Path(source).resolve())) if source else ""


def normalized_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


class _SourceDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(100, 49 if index.data(CAPTION_ROLE) else 29)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        rect = option.rect.adjusted(1, 1, -1, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#E8F6ED"))
            painter.drawRoundedRect(rect, 5, 5)
        caption = index.data(CAPTION_ROLE)
        leaf = bool(index.data(SOURCE_ROLE))
        top = rect.top() + (7 if caption else 5)
        icon_name = "journal" if leaf else (
            "chevron-down" if option.state & QStyle.StateFlag.State_Open else "chevron-right"
        )
        icon = workspace_icon(icon_name, "#647880")
        icon.paint(painter, QRect(rect.left() + 7, top, 16, 16))
        text_rect = QRect(rect.left() + 30, top - 1, max(0, rect.width() - 45), 21)
        painter.setFont(option.font)
        painter.setPen(QColor("#15543D" if selected else "#27343C"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(str(index.data() or ""), Qt.TextElideMode.ElideMiddle, text_rect.width()))
        if caption:
            painter.setPen(QColor("#64737D"))
            text_rect.translate(0, 19)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, caption)
        if index.data(ACTIVE_ROLE):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#18805A"))
            painter.drawEllipse(rect.right() - 11, top + 6, 5, 5)
        painter.restore()


class ScriptSourceTree(QTreeWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("EasyConScriptSources")
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(True)
        self.setExpandsOnDoubleClick(False)
        self.library_expanded = False
        self.itemClicked.connect(self._toggle_group)
        self.itemExpanded.connect(self._group_changed)
        self.itemCollapsed.connect(self._group_changed)
        self.setIndentation(12)
        self.setColumnCount(1)
        self.setMinimumHeight(92)
        self.setMaximumHeight(220)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QTreeWidget { background: white; border: 1px solid #E3E8ED; border-radius: 7px; padding: 5px; }"
            "QTreeView::branch { background: white; }"
        )
        self.setItemDelegate(_SourceDelegate(self))
        self.setAccessibleName("当前脚本与 lib 脚本库")

    def _toggle_group(self, item: QTreeWidgetItem, _column: int) -> None:
        if item.childCount():
            item.setExpanded(not item.isExpanded())

    def _group_changed(self, item: QTreeWidgetItem) -> None:
        self.library_expanded = item.isExpanded()
        self.update_content_height()

    def update_content_height(self) -> None:
        def row_height(item: QTreeWidgetItem) -> int:
            height = 49 if item.data(0, CAPTION_ROLE) else 29
            if item.isExpanded():
                height += sum(row_height(item.child(i)) for i in range(item.childCount()))
            return height

        height = 14 + sum(row_height(self.topLevelItem(i)) for i in range(self.topLevelItemCount()))
        self.setFixedHeight(min(220, max(92, height)))


class ExecutionFollower(QObject):
    def __init__(self, panel: EasyConPanel) -> None:
        super().__init__(panel)
        self.panel = panel
        self.snapshot = ExecutionSnapshot()
        self.dismissed_run_id = 0
        self._trace_instance = None
        self.viewed_source = ""
        self.sources: dict[str, tuple[str, str]] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._loaded_view: tuple[str, str] | None = None
        self._busy = False
        self._last_point = None
        self._display_state = ""
        self._was_visible = False
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.poll)
        self.bar = QFrame()
        self.bar.setObjectName("EasyConExecutionBar")
        self.bar.setStyleSheet(
            "QFrame#EasyConExecutionBar { background: #F4FAF6; border: 1px solid #DCEBE1; border-radius: 7px; }"
            "QLabel { background: transparent; border: 0; }"
        )
        layout = QVBoxLayout(self.bar)
        layout.setContentsMargins(12, 8, 12, 9)
        layout.setSpacing(3)
        header = QHBoxLayout()
        self.state_label = QLabel("当前执行")
        self.state_label.setStyleSheet(ui_styles("color: #18805A; font-weight: 500; font-size: 12px;"))
        header.addWidget(self.state_label)
        header.addStretch(1)
        self.follow = CheckmarkCheckBox("跟随执行")
        self.follow.setChecked(True)
        self.follow.setStyleSheet("background: transparent; color: #52646D;")
        self.follow.setToolTip("运行时自动显示当前执行的脚本与语句，并滚动到执行行")
        self.follow.toggled.connect(self._follow_changed)
        header.addWidget(self.follow)
        self.locate = QPushButton("定位执行行")
        self.locate.setIcon(workspace_icon("locate-fixed", "#18805A"))
        self.locate.setStyleSheet(
            "QPushButton { background: transparent; color: #18805A; border: 0; padding: 3px 4px; }"
            "QPushButton:disabled { color: #96A39B; }"
        )
        self.locate.setToolTip("跳转到当前或停止前的最后执行位置")
        self.locate.clicked.connect(self.locate_point)
        header.addWidget(self.locate)
        layout.addLayout(header)
        self.action_label = QLabel()
        self.action_label.setWordWrap(True)
        self.action_label.setTextFormat(Qt.TextFormat.PlainText)
        self.action_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.action_label)
        self.loop_label = QLabel()
        self.loop_label.setTextFormat(Qt.TextFormat.PlainText)
        self.loop_label.setWordWrap(True)
        self.loop_label.setStyleSheet(ui_styles("color: #18805A; font-weight: 500; font-size: 12px;"))
        layout.addWidget(self.loop_label)
        self.location_label = QLabel()
        self.location_label.setTextFormat(Qt.TextFormat.PlainText)
        self.location_label.setWordWrap(True)
        self.location_label.setStyleSheet("font-size: 12px; color: #64737D;")
        layout.addWidget(self.location_label)
        self._show_idle()

        self.viewer = type(panel.editor)()
        self.viewer.setStyleSheet(panel.editor.styleSheet())
        self.viewer.setReadOnly(True)
        self.viewer.setAcceptDrops(False)
        self.stack = QStackedWidget()
        self.stack.addWidget(panel.editor)
        self.stack.addWidget(self.viewer)
        self.source_header = QLabel()
        self.source_header.setTextFormat(Qt.TextFormat.PlainText)
        self.source_header.setWordWrap(True)
        self.source_header.setStyleSheet("padding: 9px 0; color: #52646D; font-size: 12px;")
        self.source_header.hide()
        panel.script_sources.itemClicked.connect(self._source_clicked)
        panel.script_sources.itemActivated.connect(self._activate_source)
        panel.nativeScriptStarted.connect(self.prepare)
        self.timer.start()

    def current_source(self) -> str:
        return str(self.panel.current_script_path or (self.panel._current_native_script_dir() / self.panel.current_script_name))

    def _trace(self):
        return getattr(self.panel.native_backend, "execution_trace", None)

    def _show_idle(self) -> None:
        self._display_state = ""
        self.state_label.setText("等待运行")
        self.state_label.setStyleSheet(ui_styles("color: #18805A; font-weight: 500; font-size: 12px;"))
        self.action_label.setText("运行脚本后，将高亮当前语句并自动滚动到执行位置。")
        self.loop_label.clear()
        self.loop_label.hide()
        self.location_label.clear()
        self.location_label.setToolTip("")
        self.location_label.hide()
        self.locate.setEnabled(False)

    def clear(self) -> None:
        trace = self._trace()
        self._trace_instance = trace
        if trace is not None:
            self.dismissed_run_id = trace.snapshot().run_id
        self.snapshot = ExecutionSnapshot()
        self.sources = {}
        self._last_point = None
        self._loaded_view = None
        self.panel.editor.set_execution_line(None)
        self.viewer.set_execution_line(None)
        self._show_idle()
        self.show_current()
        self.refresh_sources()

    def prepare(self) -> None:
        self.clear()
        self.set_busy(True)

    def refresh_sources(self) -> None:
        tree = self.panel.script_sources
        expanded = tree.library_expanded
        tree.clear()
        self._items.clear()
        current = self.current_source()
        current_key = source_key(current)

        def add(source: str, caption: str, parent=None):
            item = QTreeWidgetItem(parent if parent is not None else tree, [Path(source).name])
            item.setData(0, SOURCE_ROLE, source)
            item.setData(0, CAPTION_ROLE, caption)
            item.setToolTip(0, source)
            self._items[source_key(source)] = item
            return item

        add(current, "当前脚本")
        if self.snapshot.source and source_key(self.snapshot.source) != current_key:
            add(self.snapshot.source, "本次运行")
        library = QTreeWidgetItem(tree, ["lib"])
        library.setToolTip(0, f"点击展开或收起脚本库：{self.panel.script_library_directory()}\n双击脚本打开并编辑")
        paths: dict[str, str] = {}
        try:
            paths.update({source_key(str(path)): str(path) for path in scan_builtin_scripts(self.panel.script_library_directory())})
        except OSError:
            pass
        for key, (source, _text) in self.sources.items():
            if key != source_key(self.snapshot.source):
                paths[key] = source
        for key in sorted(paths):
            if key not in self._items:
                add(paths[key], "脚本库", library)
        if not paths:
            empty = QTreeWidgetItem(library, ["暂无脚本库"])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
        library.setExpanded(expanded)
        tree.update_content_height()
        self._mark_source()

    def _activate_source(self, item: QTreeWidgetItem, _column: int) -> None:
        source = item.data(0, SOURCE_ROLE)
        if not source or self.panel._controller_script_running():
            return
        if source_key(source) == source_key(self.current_source()):
            self.show_current()
            return
        if self.panel.has_unsaved_script_changes():
            choice = QMessageBox.question(
                self.panel, "未保存的伊机控脚本", "打开其他脚本前，是否保存当前脚本的修改？",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Save:
                self.show_current()
                if self.panel.save_script() is None:
                    return
            elif choice != QMessageBox.StandardButton.Discard:
                return
        self.panel.load_script(Path(source))

    def _mark_source(self) -> None:
        point = self.snapshot.point
        library = self.panel.script_sources.topLevelItem(self.panel.script_sources.topLevelItemCount() - 1)
        if library is not None:
            library.setData(0, ACTIVE_ROLE, False)
        for key, item in self._items.items():
            active = bool(point and key == source_key(point.location.source))
            item.setData(0, ACTIVE_ROLE, active)
            if active and item.parent() is not None:
                item.parent().setData(0, ACTIVE_ROLE, True)
        selected = self._items.get(source_key(self.viewed_source or self.current_source()))
        if selected is not None:
            # Selecting a hidden child makes Qt expand its parent. Keep the user's
            # collapsed group intact while following a library's execution.
            if selected.parent() is not None and not selected.parent().isExpanded():
                selected = selected.parent()
            self.panel.script_sources.setCurrentItem(selected)

    def _source_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        source = item.data(0, SOURCE_ROLE)
        if not source:
            return
        self.follow.setChecked(False)
        if source_key(source) == source_key(self.current_source()):
            self.show_current()
        else:
            self.show_source(source)

    def show_current(self) -> None:
        changed = self.stack.currentWidget() is not self.panel.editor
        self.viewed_source = self.current_source()
        self.stack.setCurrentWidget(self.panel.editor)
        self.panel.editor_header.show()
        self.source_header.hide()
        self._mark_source()
        self._highlight(False)
        if changed:
            self.panel._refresh_script_action_buttons()

    def show_source(self, source: str) -> None:
        key = source_key(source)
        frozen = self.sources.get(key)
        if frozen is not None:
            original, text = frozen
        else:
            try:
                original, text = source, Path(source).read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                self.panel._append_log("warn", f"无法查看脚本库：{exc}")
                return
        if key == source_key(self.current_source()) and normalized_text(text) == self.panel.editor.toPlainText():
            self.show_current()
            return
        changed = self.stack.currentWidget() is not self.viewer
        self.viewed_source = original
        if self._loaded_view != (key, text):
            self.viewer.setPlainText(text)
            self._loaded_view = (key, text)
        self.stack.setCurrentWidget(self.viewer)
        self.panel.editor_header.hide()
        label = "本次运行源码 · 只读" if frozen else "脚本库 · 只读"
        self.source_header.setText(f"{Path(original).name}  ·  {label}")
        self.source_header.setToolTip(original)
        self.source_header.show()
        self._mark_source()
        if changed:
            self.panel._refresh_script_action_buttons()

    def locate_point(self) -> None:
        if self.snapshot.point is None:
            return
        self.show_source(self.snapshot.point.location.source)
        self._highlight(True)

    def _follow_changed(self, checked: bool) -> None:
        if checked:
            self.locate_point()

    def _highlight(self, follow: bool) -> None:
        point = self.snapshot.point
        self.panel.editor.set_execution_line(None)
        self.viewer.set_execution_line(None)
        if point is None or source_key(self.viewed_source) != source_key(point.location.source):
            return
        editor = self.stack.currentWidget()
        source = self.sources.get(source_key(point.location.source))
        if source is not None and normalized_text(source[1]) == editor.toPlainText():
            editor.set_execution_line(point.location.line, follow=follow)

    def editor_changed(self) -> None:
        if self.snapshot.point is not None:
            self._highlight(False)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.panel.editor.setReadOnly(busy)
        self.panel.open_button.setEnabled(not busy)
        self.panel.new_button.setEnabled(not busy)
        self.panel.save_button.setEnabled(not busy and not self.viewing_snapshot)

    @property
    def viewing_snapshot(self) -> bool:
        return self.stack.currentWidget() is self.viewer

    def poll(self) -> None:
        if self.panel._shutting_down:
            return
        trace = self._trace()
        if trace is None:
            return
        if trace is not self._trace_instance:
            self._trace_instance = trace
            self.dismissed_run_id = 0
        snapshot = trace.snapshot()
        if snapshot.run_id <= self.dismissed_run_id:
            return
        changed_sources = snapshot.run_id != self.snapshot.run_id or snapshot.sources is not self.snapshot.sources
        changed_state = snapshot.state != self.snapshot.state
        visible = self.panel.isVisible()
        became_visible = visible and not self._was_visible
        self._was_visible = visible
        self.snapshot = snapshot
        if changed_sources:
            self.sources = {source_key(source): (source, text) for source, text in snapshot.sources}
            self.refresh_sources()
        self.bar.show()
        self.location_label.show()
        state_text = {"running": "执行中", "completed": "已完成 · 保留最后位置",
                      "stopped": "已停止 · 保留最后位置", "failed": "执行失败 · 保留最后位置"}
        label = state_text.get(snapshot.state, "当前执行")
        self.state_label.setText(label if snapshot.point else label.split(" · ")[0])
        if self._display_state != snapshot.state:
            self._display_state = snapshot.state
            self.state_label.setStyleSheet(ui_styles(
                f"color: {'#18805A' if snapshot.state in {'running', 'completed'} else '#A6681B'}; font-weight: 500; font-size: 12px;"
            ))
        point = snapshot.point
        self.locate.setEnabled(point is not None)
        loops = point.loops if point is not None else ()
        self.loop_label.setVisible(bool(loops))
        if loops:
            captions = []
            for index, loop in enumerate(loops, start=1):
                prefix = "循环" if len(loops) == 1 else f"第 {index} 层循环"
                count = f"{loop.iteration} / {loop.total}" if loop.total is not None else str(loop.iteration)
                captions.append(f"{prefix} · 第 {count} 次")
            self.loop_label.setText("  →  ".join(captions))
            self.loop_label.setToolTip("\n".join(
                f"第 {index} 层：{Path(loop.location.source).name} · 第 {loop.location.line} 行"
                for index, loop in enumerate(loops, start=1)
            ))
        if point is None:
            self.action_label.setText("准备执行脚本" if snapshot.state == "running" else "本次运行未进入脚本语句")
            self.location_label.setText(Path(snapshot.source).name)
            self.panel.editor.set_execution_line(None)
            self.viewer.set_execution_line(None)
        else:
            action = f"第 {point.location.line} 行 · {point.action}"
            if point.duration_ms is not None and snapshot.state == "running":
                remaining = max(0, point.duration_ms / 1000 - (monotonic() - point.started_at))
                action += f" · 剩余约 {remaining:.1f} 秒"
            self.action_label.setText(action)
            location = f"{Path(point.location.source).name} · 第 {point.location.line} 行"
            if point.caller:
                location += f"   ← {Path(point.caller.source).name} · 第 {point.caller.line} 行"
            self.location_label.setText(location)
            self.location_label.setToolTip(point.location.source)
            if point is not self._last_point or became_visible:
                self._last_point = point
                if self.follow.isChecked():
                    self.show_source(point.location.source)
                self._highlight(self.follow.isChecked())
                self._mark_source()
        if changed_state or changed_sources:
            self.panel._refresh_script_action_buttons()
