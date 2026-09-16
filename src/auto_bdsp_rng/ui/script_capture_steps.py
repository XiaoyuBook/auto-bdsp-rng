"""Seed capture and reidentification checks between script practice stages."""
from auto_bdsp_rng.ui.guide_steps import GuideStep
from auto_bdsp_rng.ui.script_guide_steps import SEED_PLACES, red


CAPTURE_PHASES = {"capture_config", "capture_roi", "capture_save", "capture_start", "capture_wait", "capture_result", "capture_failed"}


def capture_step(window, kind, phase):
    correcting = kind == "advance"
    action = "校正" if correcting else "捕捉 Seed"
    caption = "5.3.2 · 过帧后校正" if correcting else "5.3.1 · 验证测种配置"
    places = "<br><br>" + red("人物必须与目标不在同一个场景。") + "<br>" + SEED_PLACES
    spec = lambda title, copy, target, *others: GuideStep(
        "auto_script_config", caption, title, copy, target, (target, *others), separate_highlights=True)
    if phase == "capture_config":
        return spec("核对当前画面的捕捉配置",
                    "先选择上方配置文件，再核对 ROI、眼睛模板与识别阈值。人物眨眼时，识别框应同步变色；阈值一般可先用 0.7。"
                    "<br>需要重画时，点击「框选眼睛区域」或「截取眼睛」，再在右侧画面按住鼠标右键框选。高级时序参考对应手动乱数教学。"
                    "<br><br>这里手动测试使用当前页面的配置。请确认它与自动流程的「Seed 配置」一致；过场后的校正配置在后续单独设置。" + places,
                    window.config_combo, window.select_roi_button, window.raw_screenshot_button, window.threshold,
                    window.capture_advanced_button, window.capture_advanced_fields, window.npc_count)
    if phase == "capture_roi":
        eye = window._selection_mode == "eye"
        return spec("右键框选眼睛模板" if eye else "右键框选识别区域",
                    "在亮起的游戏画面按住鼠标右键拖动，松开后确认。" +
                    ("贴着一只睁开的眼睛截取模板。" if eye else "范围要完整包含眼睛，并为眼睛模板留出余量。"), window.preview_label)
    if phase == "capture_save":
        return spec("保存刚确认的配置", "点击「保存配置」。保存成功后，再点击下一步，亲手运行「" + action + "」。",
                    window.save_config_button)
    if phase == "capture_start":
        return spec("点击「" + action + "」",
                    ("保持过帧后已经复原的测种画面，点击「校正」。软件会根据新的眨眼间隔重新确认当前帧，一般捕捉 7 次；特殊模式按实际设置。"
                     if correcting else "保持人物在推荐测种地点，点击「捕捉 Seed」。本次需要捕捉人物眨眼 40 次，请保持画面和人物状态稳定。") +
                    "<br><br>等待实际计算结果后再确认；仅收集完眨眼次数，还不代表计算成功。",
                    window.reidentify_button if correcting else window.capture_button)
    if phase == "capture_wait":
        from auto_bdsp_rng.ui.mock_capture import is_mock_video
        if is_mock_video(window):
            return spec("Mock 进度已完成",
                        "在 Mock 结果窗口选择「模拟成功」或「模拟失败」，分别测试后续引导与失败后的调整流程。关闭结果窗口会按停止捕捉处理。",
                        window.capture_button)
        return spec("正在" + action,
                    "请观察绿色边框的独立预览与下方眨眼进度。收集完成后还会计算结果，请耐心等待。<br>需要中止时，点击亮起的停止捕捉按钮；中止不会记作成功。",
                    window.capture_button)
    if phase == "capture_result":
        return spec(action + "成功，请确认",
                    ("软件已完成本次校正，并更新当前帧。确认画面仍处于测种状态后，进入撞帧脚本的配置和试运行。"
                     if correcting else "软件已根据 40 次眨眼成功计算 Seed。确认人物仍在推荐测种地点后，进入过帧脚本试运行。") +
                    ("" if correcting else places), window.advances_value if correcting else window.seed64_outputs[0],
                    *(window.seed64_outputs[1:] if not correcting else ()))
    return spec(action + "未成功，选择调整方向",
                "没有得到可用结果，暂不进入下一项。<br><br>「修改配置」：检查识别框是否跟随眨眼变色，调整 ROI、眼睛模板或阈值，再保存并重试。"
                "<br><br>「修改" + ("过帧" if correcting else "测种") + "脚本」：如果位置或人物状态不对，先调整路线或操作，再重新验证。" +
                ("<br>过帧异常时，先用虚拟手柄回到原位，再修改脚本。" if correcting else places),
                window.config_combo)
