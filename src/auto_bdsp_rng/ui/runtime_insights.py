"""Read-only runtime diagnostics; values come from runner events and frozen inputs."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)


def snapshot_value(value):
    """Detach nested mutable objects without keeping service or widget references."""
    if isinstance(value, Enum):
        return snapshot_value(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: snapshot_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): snapshot_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [snapshot_value(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


NEXT_STATIC = {
    "RUN_SEED_SCRIPT": "脚本完成后捕获 Seed。",
    "CAPTURE_SEED": "测种成功后搜索目标；失败时按已配置策略重试。",
    "SEARCH_TARGET": "找到可达候选后决策过帧；无候选时按运行模式结束或继续。",
    "DECIDE_ADVANCE": "根据剩余帧数选择过帧、校正或撞闪准备。",
    "RUN_ADVANCE_SCRIPT": "按实际过帧量选择校正或重新捕获 Seed。",
    "REIDENTIFY": "定位成功后重新决策；失败时按已配置策略处理。",
    "EXIT_RESEED": "过场及校正完成后重新搜索。",
    "FINAL_CALIBRATE": "校准成功后重新检查撞闪启动点。",
    "FINAL_WAIT": "到达撞闪启动点后自动执行后续准备或脚本。",
    "FINAL_ADJUST": "调整脚本等待帧数后执行撞闪脚本。",
    "RUN_HIT_SCRIPT": "根据判定结果及配置，结束、逃跑续搜或反查。",
    "RUN_ESCAPE_SCRIPT": "逃跑完成后校正，并在本轮继续搜索。",
    "REVERSE_LOOKUP": "记录反查结果后检查是否继续下一轮。",
    "LOOP_CHECK": "根据本轮结果与运行模式决定结束或重新测种。",
}
NEXT_TID = {
    "RUN_SEED_SCRIPT": "脚本完成后采集小卡比兽眨眼。",
    "CAPTURE_TIDSID": "恢复 Seed 后生成 ID 数据；测种失败时重新测种。",
    "SEARCH_TARGET": "匹配且触发帧可达时等待取名，否则重新测种。",
    "WAIT_NAME_TRIGGER": "眨眼计数到达触发帧后执行取名脚本。",
    "RUN_NAME_SCRIPT": "取名脚本完成后结束任务。",
}
LABELS = {
    "script_dir": "脚本目录", "seed_script_path": "测种脚本", "advance_script_path": "过帧脚本",
    "hit_script_path": "撞闪脚本", "escape_script_path": "逃跑脚本", "exit_script_path": "过场脚本",
    "reverse_script_path": "反查脚本", "record_script_path": "记录脚本", "name_script_path": "取名脚本",
    "seed_config_path": "Seed 配置文件", "reidentify_config_path": "校正配置文件",
    "fixed_delay": "固定 delay（帧）", "delay": "delay（帧）", "max_advances": "搜索范围（帧）",
    "frame_threshold": "ID 搜索上限（帧）", "target_display_tids": "目标 Display TID",
    "target_species": "目标物种编号", "max_wait_frames": "最大等待（帧）",
    "reseed_threshold_frames": "重新测种阈值（帧）", "reidentify_max_attempts": "校正尝试次数",
    "reidentify_failure_policy": "校正失败策略", "reidentify_seed_max_attempts": "重新捕获尝试次数",
    "reseeding_threshold": "预留帧阈值（帧）", "loop_mode": "运行模式", "loop_count": "运行次数",
    "start_phase": "开始方式", "debug_output": "调试日志", "auto_reverse": "自动反查",
    "escape_continue": "逃跑续搜", "reverse_lookup_window": "反查窗口（帧）",
    "shiny_threshold_seconds": "闪光判定阈值（秒）", "sync_mode": "同步模式", "sync_nature": "同步性格",
    "delay_strategy": "delay 策略", "delay_sample_window": "delay 样本窗口",
    "delay_multi_candidate_policy": "多候选样本策略", "delay_ewma_alpha": "EWMA 系数",
    "delay_dense_interval_width": "密集区间宽度（帧）", "delay_sample_rounds": "启动时 delay 样本",
    "capture": "采集参数", "npc": "NPC 数", "timeline_npc": "Timeline NPC 数",
    "pokemon_npc": "宝可梦 NPC 数", "white_delay": "时间延迟（秒）",
    "advance_delay": "帧数延迟", "advance_delay_2": "帧数延迟 2", "roi": "识别区域（像素）",
    "threshold": "识别阈值", "blink_count": "采集眨眼数", "profile": "训练家配置",
    "state_filter": "筛选条件", "record": "定点数据", "shiny_mode": "异色条件",
    "seed": "Seed", "initial_advances": "起始帧数", "offset": "偏移（帧）",
    "seed_text": "本轮 Seed", "trigger_advances": "脚本触发帧", "raw_target_advances": "目标帧数",
    "target_advances": "目标帧数", "final_flash_frames": "最终脚本等待（帧）",
    "seed_script": "已保存测种脚本", "advance_script": "已保存过帧脚本", "hit_script": "已保存撞闪脚本",
    "escape_script": "已保存逃跑脚本", "exit_script": "已保存过场脚本", "reverse_script": "已保存反查脚本",
    "name_script": "已保存取名脚本", "target_tids": "已保存目标 Display TID",
    "target_list_json": "已保存目标条件", "mode_index": "已保存运行模式选项",
}


class RuntimeInsights(QWidget):
    def __init__(self, panel, *, tid=False):
        super().__init__(panel)
        self.panel = panel
        self.tid = tid
        self.pending_inputs = {}
        self.active = {}
        self.round_values = {}
        self.phase = "IDLE"
        self.round_index = 0
        self.phase_started = time.monotonic()
        self.reason = ""
        self.last_event = ""
        self.last_signal = ""
        self._capture_count = None
        self._wait_text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.next_label = QLabel("下一步 · 完成准备检查后开始任务。")
        self.event_label = QLabel("最近事件 · 尚未开始")
        self.wait_label = QLabel()
        for label in (self.next_label, self.event_label, self.wait_label):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet("color: #626D79; font-size: 12px; font-weight: 400;")
            layout.addWidget(label)
        row = QHBoxLayout()
        self.snapshot_button = QPushButton("配置与本轮快照")
        self.snapshot_button.clicked.connect(self.show_snapshot)
        row.addStretch(1)
        row.addWidget(self.snapshot_button)
        layout.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._render_wait)
        panel.runStateChanged.connect(self._run_state_changed)
        self.wait_label.hide()

    def _run_state_changed(self, running):
        if not running:
            self.timer.stop()
            self.wait_label.hide()

    def start(self, config):
        self.active = {"启动时间": datetime.now().isoformat(timespec="seconds"),
                       "启动参数": snapshot_value(config),
                       "实际服务输入": snapshot_value(self.pending_inputs)}
        self.pending_inputs = {}
        self.round_values = {}
        self.round_index = 0
        self.phase = "IDLE"
        self.reason = self.last_event = self.last_signal = ""
        self._capture_count = None
        self.event_label.setText("最近事件 · 等待运行器反馈")

    def update_progress(self, progress):
        phase = progress.phase.name
        new_round = progress.loop_index != self.round_index
        if new_round:
            self.round_index = progress.loop_index
            self.round_values = {}
            self.reason = self.last_signal = ""
            self._capture_count = None
        if phase != self.phase or new_round:
            self.phase_started = time.monotonic()
            self.reason = progress.log_message or ""
            if phase in {"CAPTURE_SEED", "CAPTURE_TIDSID", "REIDENTIFY", "FINAL_CALIBRATE"}:
                self._capture_count = None
                self.last_signal = ""
        self.phase = phase
        next_text = (NEXT_TID if self.tid else NEXT_STATIC).get(phase, "以运行器后续反馈为准。")
        if phase in {"COMPLETED", "FAILED", "IDLE"}:
            next_text = "查看结果与日志；再次开始前可检查配置。"
            self.timer.stop()
        else:
            self.timer.start()
        self.next_label.setText("下一步 · " + next_text)
        if progress.log_message:
            self.reason = progress.log_message
            self.observe(progress.log_message)
        elif not self.last_event or new_round:
            self.observe("进入" + progress.phase.value)
        self._wait_text = ""
        if phase == "WAIT_NAME_TRIGGER":
            elapsed = progress.wait_elapsed_seconds
            target = progress.wait_target_at
            started = progress.wait_started_at
            if elapsed is not None:
                self._wait_text = f"等待已进行 {elapsed:.0f} 秒"
                if target is not None and started is not None:
                    self._wait_text += f" · 预计还需 {max(0, target - started - elapsed):.0f} 秒（眨眼模型）"
        if phase not in {"RUN_SEED_SCRIPT", "CAPTURE_SEED", "CAPTURE_TIDSID"}:
            for name in ("seed_text", "fixed_delay", "trigger_advances", "raw_target_advances", "target_advances", "final_flash_frames"):
                value = getattr(progress, name, None)
                if value is not None and value != "":
                    self.round_values[name] = snapshot_value(value)
        self._render_wait()

    def observe(self, message):
        self.last_event = f"{datetime.now():%H:%M:%S} · {message}"
        self.event_label.setText("最近事件 · " + self.last_event)
        self.event_label.setToolTip(self.last_event)

    def capture_signal(self, done, total):
        if done > 0 and (done, total) != self._capture_count:
            self._capture_count = (done, total)
            self.last_signal = f"{datetime.now():%H:%M:%S} · 已采集眨眼 {done}/{total}"
            self._render_wait()

    def _render_wait(self):
        active = self.phase not in {"IDLE", "COMPLETED", "FAILED"}
        self.wait_label.setVisible(active)
        if active:
            text = self._wait_text or f"本阶段已进行 {max(0, time.monotonic() - self.phase_started):.0f} 秒 · 剩余时间待运行反馈"
            if self.last_signal:
                text += "\n最近采集信号 · " + self.last_signal
            self.wait_label.setText(text)

    def snapshot(self):
        return snapshot_value({**self.active, "当前轮次": self.round_index,
                               "本轮已采用的动态值": self.round_values,
                               "当前动作": self.phase, "触发 / 重试说明": self.reason or "暂无额外说明"})

    def show_snapshot(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("配置与本轮快照")
        dialog.resize(780, 580)
        layout = QVBoxLayout(dialog)
        note = QLabel("编辑草稿用于下次启动；保存仅持久化。定点 delay 策略在下一轮解析。\n运行快照记录启动时实际输入及本轮已反馈的动态值；打开后保持不变。")
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        try:
            draft = snapshot_value(self.panel.build_config())
        except ValueError as exc:
            draft = {"草稿待补全": str(exc)}
            if self.tid:
                draft.update(frame_threshold=self.panel.frame_threshold.value(), delay=self.panel.delay.value(),
                             target_display_tids=list(self.panel.target_display_tids()),
                             seed_script_path=snapshot_value(self.panel._selected_path(self.panel.seed_script_combo)),
                             name_script_path=snapshot_value(self.panel._selected_path(self.panel.name_script_combo)))
        if hasattr(self.panel, "targets"):
            draft["目标条件"] = snapshot_value(self.panel.targets())
        saved = {key: snapshot_value(self.panel._settings.value(key)) for key in self.panel._settings.allKeys()
                 if not key.startswith(("table_workbench/", "workspace_layout/"))}
        datasets = (("编辑草稿", draft), ("已保存", saved or {"说明": "尚未写入配置，当前使用加载的默认值。"}),
                    ("本次运行", self.snapshot() if self.active else {"说明": "尚未启动，没有运行快照。"}))
        for title, values in datasets:
            tree = QTreeWidget()
            tree.setHeaderLabels(["配置项", "值"])
            tree.setColumnWidth(0, 285)
            def add(parent, data):
                pairs = data.items() if isinstance(data, dict) else enumerate(data, 1)
                for key, value in pairs:
                    nested = isinstance(value, (dict, list))
                    text = "" if nested else ("未设置" if value is None else "是" if value is True else "否" if value is False else str(value))
                    item = QTreeWidgetItem(parent, [LABELS.get(str(key), str(key)), text])
                    item.setToolTip(0, str(key))
                    item.setToolTip(1, text)
                    if nested:
                        add(item, value)
            add(tree, values)
            tree.expandToDepth(0)
            tabs.addTab(tree, title)
        tabs.setCurrentIndex(2 if self.active else 0)
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
