"""Teaching copy and real control anchors for the task configuration guide."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from auto_bdsp_rng.automation.auto_rng.delay_strategy import DelayStrategy


@dataclass(frozen=True)
class GuideStep:
    key: str
    caption: str
    title: str
    copy: str
    target: QWidget
    highlights: tuple[QWidget, ...]


def _row(form, field: QWidget) -> tuple[QWidget, ...]:
    label = form.labelForField(field)
    return (label, field) if label is not None else (field,)


def workspace_step(panel, key: str) -> GuideStep:
    if key == "target_selection":
        return GuideStep(key, "第 1 步 · 设置目标精灵", "先选好这次的目标",
                         "点击亮起区域里的「设置」，选择这次想乱的精灵，以及你希望得到的结果。\n"
                         "设置好后，点击下一步调整任务配置。",
                         panel.target_button, (panel.target_button.parentWidget(),))
    entries = {
        "search_range": ("2.1 · 搜索范围", "选一个搜索范围",
                         "范围越大，能查找更远的候选；推进到较远目标也需要更长时间。", panel.max_advances),
        "delay_strategy": ("2.2 · delay 策略", "设置这次的 delay",
                           "delay 用来补偿脚本操作到实际遇敌之间的帧数。数值越大，撞闪脚本启动得越早。\n"
                           "点击亮起的按钮，在设置窗口中选择策略并确认参数。", panel.delay_settings_button),
        "max_wait": ("2.3 · 最大等待", "何时改为实时等待",
                     "距离撞闪脚本启动帧不超过这个值时，软件停止使用过帧脚本，改为实时等待。\n"
                     "数值越大，越早进入实时等待；越小，越依赖过帧脚本接近目标。可以先保留默认 300 帧。", panel.max_wait_frames),
        "shiny_threshold": ("2.4 · 闪光阈值", "设置疑似闪光的判定时间",
                            "软件用 OCR 测量「出现了！」到「去吧／上吧」的文本间隔。达到阈值时，判为疑似闪光并停止自动流程。\n"
                            "请按实际战斗文本耗时设置；可在 Seed 页使用「校准闪光判定」。设为 0 关闭自动 OCR 判闪。", panel.shiny_threshold_seconds),
        "sync": ("2.5 · 同步", "让设置与队首精灵一致",
                 "需要利用同步特性筛选性格时，按当前队首选择「首位普通精灵」或「首位同步精灵」，并填写同步精灵的性格。\n"
                 "不使用同步时保持关闭。开启后，流程会按目标需要配合过帧脚本切换队首。", panel.sync_field),
        "auto_reverse": ("2.6 · 自动反查", "记录实际命中的 delay",
                         "开启后，流程会结合捕获的精灵信息，在目标附近反查实际命中帧，并记录 delay 样本。动态 delay 策略可使用这些样本。\n"
                         "反查范围默认 ±500 帧；范围太小可能查不到，太大可能出现多个候选。使用此功能需要相应的反查脚本。", panel.reverse_field),
        "correction_strategy": ("2.7 · 校正策略", "确认过帧后的校正方式",
                                "校正用于重新确认当前帧数，减少过帧误差。\n"
                                "点击亮起的「设置」，确认校正上限、失败处理和过场预留帧数。", panel.strategy_settings_button),
        "save_config": ("2.8 · 保存配置", "保存这次的任务配置",
                        "点击亮起的「保存配置」，保存刚才确认的参数。\n"
                        "保存成功后，再点击下一步完成本步。右侧的脚本选择需要单独保存。", panel.save_config_button),
        "task_configured": ("第 2 步 · 已完成", "任务配置已保存",
                            "目标与任务参数已经准备好。当前引导到这里，可以收起提示。", panel.save_config_button),
    }
    caption, title, copy, target = entries[key]
    field = panel.delay_settings_field if key == "delay_strategy" else target
    highlights = (target,) if key in ("save_config", "task_configured") else _row(panel.strategy_form, field)
    return GuideStep(key, caption, title, copy, target, highlights)


def dialog_steps(panel, key: str) -> list[GuideStep]:
    if key == "delay_strategy":
        dialog = panel.delay_strategy_dialog
        caption = "2.2 · delay 策略"
        strategy = dialog.values().strategy
        entries = [
            ("strategy", "选择 delay 策略", dialog.strategy_combo,
             "固定策略使用你填写的 delay；动态策略会参考反查样本，调整下一轮的 delay。\n"
             "先选择要使用的策略，再点击下一步确认参数。"),
            ("baseline", "确认基准 delay", dialog.baseline_delay,
             "固定策略直接使用这个值；动态策略没有有效样本时也会回退到它。\n"
             "填写当前精灵和撞闪脚本对应的 delay，也可以保留现有值。"),
        ]
        if strategy not in (DelayStrategy.FIXED, DelayStrategy.LAST):
            entries.extend([
                ("window", "使用多少轮样本", dialog.window_size,
                 "统计窗口决定使用最近多少个有效轮次。窗口较小更跟随近期变化，较大则更平稳。\n"
                 "可以先保留默认 5 轮；调整窗口不会删除历史样本。"),
                ("multiple", "一轮出现多个候选时", dialog.multi_candidate_widget,
                 "「忽略该轮」让不明确的样本不参与统计；「按轮加权」将这一轮的权重分配给多个候选。\n"
                 "不确定时，可以先保留「忽略该轮」。"),
            ])
        if strategy is DelayStrategy.EWMA:
            entries.append(("weight", "最新样本占多大权重", dialog.ewma_weight_percent,
                            "权重越高，计算结果越跟随最近一轮；越低，变化越平缓。可以先保留默认 50%。"))
        if strategy is DelayStrategy.DENSE_INTERVAL:
            entries.append(("span", "多接近才算同一区间", dialog.dense_interval_width,
                            "软件会在这个跨度内寻找最集中的候选，再取其中位数。跨度越大，越可能把相距较远的候选放在一起。\n"
                            "可以先保留默认 2 帧，再参考实际样本的分布调整。"))
        result = [GuideStep(name, caption, title, copy, field, (dialog._form_rows[field],))
                  for name, title, field, copy in entries]
        result.append(GuideStep("confirm", caption, "确认 delay 设置",
                                "点击「保存设置」可保存当前精灵的策略。点击下一步也会确认当前设置并返回主界面。\n"
                                "动态策略还需要自动反查积累样本，后面会说明。",
                                dialog.ok_button, (dialog.ok_button,)))
        return result

    dialog = panel.strategy_dialog
    caption = "2.7 · 校正策略"
    entries = [
        ("limit", "校正上限最多 100 万帧", dialog.reseed_threshold_frames,
         "本次过帧量不超过所填上限时执行校正；超过上限，普通流程会重新测种，过场后则进入下一轮。\n"
         "最多填写 100 万帧，可以先保留默认 90 万帧。"),
        ("attempts", "普通校正尝试几次", dialog.reidentify_max_attempts,
         "普通校正连续失败达到这个次数后，执行下一项设置的失败处理。\n"
         "可以先保留默认 2 次；增加次数也会增加失败时的等待时间。"),
        ("failure", "连续失败后如何继续", dialog.reidentify_failure_policy,
         "「进入下一轮」会重新开始本轮流程；「先重测 Seed」会先尝试补救测种，成功后重新搜索目标。\n"
         "此设置只影响普通校正，过场校正失败后始终进入下一轮。"),
    ]
    if dialog.policy() == "recapture_seed":
        entries.append(("seed_attempts", "补救测种尝试几次", dialog.reidentify_seed_max_attempts,
                        "普通校正失败后，最多尝试这么多次补救测种。仅在选择「先重测 Seed」时生效。\n"
                        "可以先保留默认 1 次。"))
    entries.append(("reserve", "为过场和校正留出余量", dialog.reseeding_threshold,
                    "提前留出帧数，用于执行过场脚本和完成校正。预留太少，可能在过场或校正期间错过目标。\n"
                    "建议先保留默认 50 万帧，再根据实际消耗调整。仅配置了过场脚本时生效；设为 0 关闭过场策略。"))
    result = [GuideStep(name, caption, title, copy, field, _row(dialog.form, field))
              for name, title, field, copy in entries]
    result.append(GuideStep("confirm", caption, "确认校正策略",
                            "点击「确定」应用当前设置。点击下一步也会确认设置，返回主界面保存任务配置。",
                            dialog.ok_button, (dialog.ok_button,)))
    return result
