"""Teaching copy and real control anchors for the task configuration guide."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from auto_bdsp_rng.automation.auto_rng.delay_strategy import DelayStrategy


GUIDE_EMPHASIS = '#C2410C'


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


def workspace_step(panel, key: str, detail: str = "") -> GuideStep:
    if key == "easycon_intro":
        window = panel.window()
        easycon = window.easycon_tab
        return GuideStep(key, "5.1 · 伊机控简介", "用伊机控执行操作脚本",
                         "伊机控脚本会按顺序执行按键、摇杆和等待等操作，是自动流程与游戏之间的动作桥梁。\n"
                         "左侧可以浏览脚本和 lib，键盘控制用于录制或手动测试；接下来先看一遍脚本录制演示。",
                         easycon.script_sources, (easycon.script_sources, easycon.keyboard_control_group))
    if key == "easycon_recording":
        window = panel.window()
        easycon = window.easycon_tab
        if detail == "practice":
            return GuideStep(key, "5.2 · 脚本录制", "现在由你录制一小段操作",
                             "先把键盘控制切到「控制」，点击「开始录制」，按映射键完成操作，再点「停止录制」。\n"
                             "操作命令会实时追加到当前脚本末尾；松开按键时会补上保持时长和松开命令。停止录制后可以编辑，确认无误后点击下一步。",
                             easycon.record_btn, (easycon.controller_mode_buttons["active"], easycon.record_btn))
        return GuideStep(key, "5.2 · 脚本录制", "脚本录制动画准备中", "观看动画后，在真实伊机控页面完成一次录制。",
                         easycon.record_btn, (easycon.record_btn,))
    if key == "auto_script_config":
        return GuideStep(key, "5.3 · 自动流程脚本配置", "为自动流程选择脚本",
                         "自动流程中的每个脚本下拉框对应一个阶段：测种、过帧、撞闪、过场、反查和逃跑。\n"
                         "先从下拉框选择脚本；带有铅笔按钮的项目可以直接打开并编辑。过场目标的配置请结合手动乱数教学视频确认。",
                         panel.runtime_script_card, (panel.runtime_script_card,))
    if key == "easycon_script_config":
        easycon = panel.window().easycon_tab
        return GuideStep(key, "5.4 · 脚本配置", "在编辑器中检查并保存脚本",
                         "脚本会在中间编辑器中显示。可以直接修改命令，运行前请检查按键顺序与 WAIT 时间；保存后再回到自动流程选择它。\n"
                         "需要暂停或定位执行位置时，可使用顶部的暂停、停止和执行跟随工具。",
                         easycon.editor, (easycon.editor, easycon.save_button, easycon.run_button))
    if key == "seed_capture_tools" and detail in ("roi_button", "roi_drag", "eye_button", "eye_drag", "roi_adjust"):
        window = panel.window()
        actions = {
            "roi_button": ("现在由你框选 ROI", "点击左侧的「框选眼睛区域」，接着在自己的游戏画面中框出识别范围。", window.select_roi_button),
            "roi_drag": ("在画面中框选识别范围", '在右侧画面中<span style="color:#087C58; font-weight:600">按住鼠标右键拖动</span>，框住一只眼睛并留一点余量。松开后确认使用此区域。', window.preview_label),
            "eye_button": ("接下来截取眼睛模板", "点击右侧的「截取眼睛」，使用实际人物睁开的眼睛更新模板。", window.raw_screenshot_button),
            "eye_drag": ("贴着一只睁开的眼睛框选", '在刚才的 ROI 内<span style="color:#087C58; font-weight:600">按住鼠标右键拖动</span>，框选一只睁开的眼睛。模板要小于 ROI；松开后确认使用。', window.preview_label),
            "roi_adjust": ("让识别范围容纳新模板", "新截取的眼睛超出了原 ROI，请在右侧画面中按住鼠标右键重新框选识别范围，完整包含眼睛并留一点余量。", window.preview_label),
        }
        title, copy, target = actions[detail]
        return GuideStep(key, "4.3 · 动手框选", title, copy, target, (target,))
    if key == "target_selection":
        return GuideStep(key, "第 1 步 · 设置目标精灵", "先选好这次的目标",
                         "点击亮起区域里的「设置」，选择这次想乱的精灵，以及你希望得到的结果。\n"
                         "设置好后，点击下一步调整任务配置。",
                         panel.target_button, (panel.target_button.parentWidget(),))
    entries = {
        "search_range": ("2.1 · 搜索范围", "选一个搜索范围",
                         "范围越大，能查找更远的候选；推进到较远目标也需要更长时间。\n"
                         f'<span style="color:{GUIDE_EMPHASIS}; font-weight:600">御三家乱数时，搜索范围与 2.3 的「最大等待」应保持一致，例如都设为 5000 帧。</span>', panel.max_advances),
        "delay_strategy": ("2.2 · delay 策略", "设置这次的 delay",
                           "delay 用来补偿脚本操作到实际遇敌之间的帧数。数值越大，撞闪脚本启动得越早。\n"
                           "点击亮起的按钮，在设置窗口中选择策略并确认参数。", panel.delay_settings_button),
        "connect_devices": ("第 3 步 · 连接设备", "连接视频源和伊机控",
                             "先连接视频源，再连接伊机控。点击亮起的状态按钮打开对应设置，完成连接后点击下一步继续。",
                             panel.window().video_source_header_button if detail != "easycon" else panel.window().easycon_header_button),
        "max_wait": ("2.3 · 最大等待", "何时改为实时等待",
                     "距离撞闪脚本启动帧不超过这个值时，软件停止使用过帧脚本，改为实时等待。\n"
                     "数值越大，越早进入实时等待；越小，越依赖过帧脚本接近目标。\n"
                     "御三家乱数时，请与 2.1 的「搜索范围」保持一致，例如都设为 5000 帧。其他目标可以先保留默认 300 帧。", panel.max_wait_frames),
        "shiny_threshold": ("2.4 · 闪光阈值", "设置疑似闪光的判定时间",
                            "软件用 OCR 测量「出现了！」到「去吧／上吧」的文本间隔。达到阈值时，判为疑似闪光并停止自动流程。\n"
                            "请按实际战斗文本耗时设置；可在 Seed 页使用「校准闪光判定」。设为 0 关闭自动 OCR 判闪。", panel.shiny_threshold_seconds),
        "sync": ("2.5 · 同步", "让设置与队首精灵一致",
                 "需要利用同步特性筛选性格时，按当前队首选择「首位普通精灵」或「首位同步精灵」，并填写同步精灵的性格。\n"
                 f'<span style="color:{GUIDE_EMPHASIS}; font-weight:600">同步也会影响体型，对体型进行二次判定。想乱体型证时，建议配置同步，并与实际队首精灵保持一致。</span>\n'
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
                            "目标与任务参数已经准备好。点击下一步进入第 3 步，连接视频源和伊机控。", panel.save_config_button),
        "devices_connected": ("第 3 步 · 已完成", "设备已连接",
                              "视频源和伊机控都已连接，可以收起提示并开始后续操作。", panel.window().easycon_header_button),
        "seed_capture_page": ("第 4 步 · Seed 捕捉", "进入 Seed 捕捉页面",
                              "点击亮起的「Seed 捕捉」标签，进入接下来要使用的捕捉页面。", panel.window().project_xs_tab),
        "seed_capture_config": ("4.1 · Seed 捕捉配置", "先选好捕捉配置",
                                "Seed 捕捉页中的参数都来自上方所选的配置文件。请先选择配置，再按需要修改参数；如果不清楚如何调整，可以参考 B 站上的乱数教学配置。\n"
                                f'<span style="color:#087C58; font-weight:600">这里选择的捕捉配置用于手动乱数，也是右侧预览画面中识别框的来源。</span>\n'
                                f'<span style="color:#087C58; font-weight:600">使用自动乱数时，右侧画面仍显示这里选择的配置，但实际运行会使用“自动流程配置”中指定的配置。</span>', panel.window().config_combo),
        "seed_capture_actions": ("4.2 · Seed 捕捉操作", "认识三种捕捉操作",
                                 '「捕捉 Seed」用于获取当前 Seed，捕捉眨眼 <span style="color:#087C58; font-weight:600">40 次</span>。\n'
                                 '「校正」用于根据新的眨眼间隔校正当前帧，一般捕捉眨眼 <span style="color:#087C58; font-weight:600">7 次</span>。\n'
                                 '「TID/SID 测种」用于专门捕捉 TID/SID Seed，捕捉<span style="color:#087C58; font-weight:600">小卡比兽眨眼 64 次</span>。\n\n'
                                 "Seed（种子）记录游戏随机数生成器的状态。相同 Seed 按相同规则推进，会产生相同的随机数序列。软件通过眨眼反推出 Seed，再结合遇敌条件预测闪光、个体值等结果，帮助你在目标帧触发遇敌。\n"
                                 "这里的「帧数」指随机数推进次数，不是视频画面的帧数。", panel.window().capture_button),
        "seed_capture_tools": ("4.3 · 识别与眼睛设置", "设置识别所需的画面参数",
                               f'「框选 ROI」用于指定识别范围，<span style="color:#087C58; font-weight:600">请用鼠标右键在右侧画面中拖动框选</span>，软件只会在这个范围内识别眼睛；「截取眼睛」用于更新眼睛模板，请截取游戏画面中实际人物的眼睛。\n'
                               f'「识别阈值」是识别区域内画面与眼睛模板的匹配判定阈值，一般设置为 <span style="color:#087C58; font-weight:600">0.7</span> 比较合适。若测种或校正失败，可以观察人物眨眼时识别框是否同步变色；没有变色或识别不稳定时，优先检查 ROI、眼睛模板和阈值。\n'
                               f'NPC 数和下面的高级时序暂不展开，默认值为 0，先保持不动；如需使用相关功能，再参考手动乱数教学视频中的配置填写。预览中看到的眼睛框来自这里的设置，但它不一定就是自动流程实际使用的那份配置。\n'
                               f'<span style="color:{GUIDE_EMPHASIS}; font-weight:600">使用过场脚本的美梦神、噩梦神或火钢，建议先观看手动乱数教学视频；这类目标的「校正配置」与「捕捉 Seed 配置」不同，请分别参考教学配置填写。</span>', panel.window().select_roi_button),
        "seed_capture_save": ("4.4 · 保存配置", "保存 Seed 捕捉配置",
                              "前面的参数确认无误后，点击亮起的「保存配置」，保存本次 Seed 捕捉使用的配置。保存后再点击下一步，继续设置自动流程配置。", panel.window().save_config_button),
        "auto_flow_config": ("4.5 · 自动流程配置", "理解自动流程使用的配置",
                             "「Seed 配置」用于自动流程捕捉 Seed；通常不启用过场脚本时，校正也使用 Seed 配置，不会启用「校正配置」。只有启用过场脚本时，过场脚本运行后的校正才会改用校正配置。这里的两个下拉框分别选择对应配置文件。", panel.window().seed_config_combo),
    }
    caption, title, copy, target = entries[key]
    field = panel.delay_settings_field if key == "delay_strategy" else target
    if key == "seed_capture_actions":
        highlights = (panel.window().capture_button, panel.window().reidentify_button, panel.window().tidsid_button)
    elif key == "seed_capture_tools":
        highlights = (panel.window().select_roi_button, panel.window().raw_screenshot_button, panel.window().threshold, panel.window().npc_count)
    elif key == "auto_flow_config":
        highlights = (panel.window().seed_config_combo, panel.window().reidentify_config_combo)
    elif key == "connect_devices":
        highlights = (target,)
    else:
        highlights = (target,) if key in ("save_config", "task_configured", "seed_capture_save") else _row(panel.strategy_form, field)
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


def connection_dialog_steps(panel, device: str) -> list[GuideStep]:
    """Return the small, real-control sequence for the third-step dialogs."""
    window = panel.window()
    if device == "video_source":
        dialog = window.video_source_dialog
        return [
            GuideStep("device", "第 3 步 · 连接视频源", "选择视频设备",
                      "确认这里选择的是实际采集画面的设备。通常可以先保留当前选择。",
                      window.capture_device_combo, (window.capture_device_combo,)),
            GuideStep("api", "第 3 步 · 连接视频源", "选择采集方式",
                      "选择视频源的采集方式。一般优先使用“Media Foundation（推荐）”；如果画面无法正常获取，再尝试兼容方式。",
                      window.capture_api_combo, (window.capture_api_combo,)),
            GuideStep("connect", "第 3 步 · 连接视频源", "连接视频源",
                      "点击「连接」，等待状态变为已连接。连接成功后会自动进入伊机控连接。",
                      window.video_source_button, (window.video_source_button,)),
        ]
    dialog = window.easycon_tab.connection_dialog
    return [
        GuideStep("port", "第 3 步 · 连接伊机控", "选择连接端口",
                  "选择伊机控使用的串口或当前连接方式。没有特殊需求时可以保留现有选择。",
                  window.easycon_tab.port_combo, (window.easycon_tab.port_combo,)),
        GuideStep("connect", "第 3 步 · 连接伊机控", "连接伊机控",
                  "点击「连接」，等待顶部状态变为已连接。连接成功后完成第 3 步。",
                  window.easycon_tab.connect_button, (window.easycon_tab.connect_button,)),
    ]
