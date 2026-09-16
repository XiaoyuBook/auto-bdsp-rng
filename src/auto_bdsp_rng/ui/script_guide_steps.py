"""Copy and anchors for choosing, trying and confirming each automation script."""
from __future__ import annotations

from html import escape

from auto_bdsp_rng.ui.guide_steps import GuideStep


SCRIPT_KINDS = ("seed", "advance", "hit", "exit", "reverse", "ocr", "escape", "save")
SCRIPT_NAMES = {"seed": "测种", "advance": "过帧", "hit": "撞闪", "exit": "过场", "reverse": "反查", "escape": "逃跑"}
PRACTICE_KINDS = ("seed", "advance", "hit", "reverse")
RED = '#C62828'


def red(text: str) -> str:
    return f'<span style="color:{RED}; font-weight:600">{text}</span>'


SEED_PLACES = (
    "玫瑰公园：交石板前。<br>"
    "三圣菇：洞穴门口，进入洞穴后能看到目标。<br>"
    "冥王龙：第三根柱子洞穴门口，进去后能直接看到冥王龙。<br>"
    "封面神、阿尔宙斯：洞穴门口（在洞穴里面）。<br>"
    "美梦神、谢米、噩梦神、火钢：秘密基地。保证进入游戏、进入地下洞穴后，按 UP 就能进入秘密基地。<br>"
    "马桶王：马桶王上一层楼梯口。"
)


def position(detail: str) -> tuple[str, str]:
    if not detail:
        return "seed", "select"
    kind, _, phase = detail.partition(":")
    if phase in ("follow", "locate", "wait", "confirm", "save") and kind in PRACTICE_KINDS:
        phase = "review"
    return (kind, phase or "select") if kind in SCRIPT_KINDS else ("seed", "select")


def script_run_target(panel):
    window = panel.window()
    return window.easycon_tab.run_button if window.easycon_tab._native_is_connected() else window.easycon_header_button


def script_step(panel, detail: str) -> GuideStep:
    kind, phase = position(detail)
    number = SCRIPT_KINDS.index(kind) + 1
    easycon = panel.window().easycon_tab
    caption = f"5.3.{number} · {SCRIPT_NAMES.get(kind, 'OCR 设置' if kind == 'ocr' else '保存配置')}" + ("脚本" if kind in SCRIPT_NAMES else "")

    def spec(title, copy, target, *others, separate=False, confirmation=False):
        return GuideStep("auto_script_config", caption, title, copy, target, (target, *others),
                         separate_highlights=separate, script_confirmation=confirmation)

    if kind == "ocr":
        return spec("检查自动反查使用的 OCR 区域",
                    "反查脚本结束后，先保持在精灵的笔记页。接下来演示如何框选、显示并测试 OCR 区域，再在自己的画面上逐项检查。<br>"
                    "性格、个性使用笔记页；六项能力值使用能力页。战斗文本和御三家战斗按钮用于判闪，需要切到对应画面检查。",
                    panel.capture_info_button)
    if kind == "save":
        return spec("保存自动流程的脚本选择",
                    "点击亮起的「保存」，保存本次各阶段的脚本选择。伊机控中修改过的脚本也需要保存到对应文件，自动流程才会使用修改后的内容。",
                    panel.save_scripts_button)

    combo = getattr(panel, f"{kind}_script_combo")
    picker = panel.script_picker_widgets[combo]
    copy = {
        "seed": red("测种脚本是每次 SL 后前往固定地点进行测种的脚本。测种时，人物不能与目标在同一个场景。") + "<br><br>" + SEED_PLACES +
                "<br><br>先在下拉框选择适合目标的脚本；BDSP测种.txt 可作为地上测种示例。选好后点击铅笔，到伊机控检查并试运行。",
        "advance": "过帧脚本通过图鉴等操作推进 RNG 帧数，结束后应回到测种地点，方便重新校正。<br>" +
                   red("过帧效率与图鉴完成度有关，请尽量完成更多图鉴。") +
                   "<br>选择适合自己的过帧脚本，再点击铅笔检查参数。bdsp过帧.txt 中需要特别检查第 7 行和第 127 行。",
        "hit": "撞闪脚本从测种地点出发，按目标所需路线触发战斗或交互。<br>选择与本次目标对应的脚本，点击铅笔，到伊机控试运行并确认能正常遇到目标。此次试运行用于检查操作路线，不保证出闪。",
        "exit": "过场脚本用于处理美梦神、噩梦神、火钢等目标的特殊过场。按目标需要选择；不需要时保持不使用。<br>" +
                red("启用过场脚本后，过场后的校正使用「校正配置」。它可能与捕捉 Seed 配置不同，请参考对应手动乱数教学。"),
        "reverse": "反查脚本负责捕捉目标并打开精灵信息，之后软件用 OCR 读取信息来反查实际命中帧。<br>" +
                   red("请先检查脚本中的选球操作，保证大师球位置正确。捕捉反查脚本.txt 第 4 行的循环次数需要按个人背包调整。") +
                   "<br>选择适合目标的反查脚本，点击铅笔，到伊机控运行一次。",
        "escape": "开启「未出闪时逃跑续搜」后，逃跑脚本负责退出当前战斗，让流程继续搜索和尝试。请按目标需要选择；不用逃跑续搜时保持关闭即可。",
    }[kind]
    if phase == "select":
        others = (panel.escape_continue_check,) if kind == "escape" else ()
        return spec(f"选择{SCRIPT_NAMES[kind]}脚本" if kind in PRACTICE_KINDS else f"了解{SCRIPT_NAMES[kind]}脚本", copy, picker, *others)

    selected = panel._selected_path(combo)
    filename = escape(selected.name) if selected else "所选脚本"
    if phase in ("edit", "edit_second"):
        notes = {
            "seed": red("BDSP测种.txt 第 24 行：快捷道具方向（如 RIGHT），请根据自己的快捷道具位置修改。"),
            "advance": red("bdsp过帧.txt 第 7 行：地下过帧开关。0 为不在地下过帧，1 为地下过帧；按实际场景设置。") if phase == "edit" else
                       red("bdsp过帧.txt 第 127 行：使用钓鱼竿的快捷道具方向（如 RIGHT），请按自己的位置修改。"),
            "hit": "请对照目标检查移动方向、按键顺序和 WAIT 时间。确认从当前测种地点出发可以正常触发目标。",
            "reverse": red("捕捉反查脚本.txt 第 4 行：FOR 次数决定向右移动几次选球。请对照背包确认大师球的位置。"),
        }[kind]
        return spec(f"检查 {filename}", notes + "<br><br>以上行号对应内置脚本；其他脚本请查找对应操作。修改后保存，再进入试运行。", easycon.editor)
    if phase == "run":
        target = script_run_target(panel)
        if target is not easycon.run_button:
            return spec("先连接伊机控",
                        "点击亮起的「伊机控」连接状态，选择端口并连接。连接成功后，再点击「运行脚本」试运行当前脚本。"
                        "<br>开发时可在连接窗口选择 Mock 串口。",
                        target)
        return spec(f"亲手运行{SCRIPT_NAMES[kind]}脚本",
                    f"当前脚本：{filename}。<br>确认游戏处于脚本要求的起点，点击亮起的「运行脚本」。运行后观察独立预览里的实际画面。<br>"
                    "按钮不可用时，请检查脚本内容和必填参数。",
                    target)
    places = "<br><br>" + red("推荐测种地点（人物不能与目标在同一场景）") + "<br>" + SEED_PLACES if kind == "seed" else ""
    if phase == "review":
        check = {"seed": "请对照下方推荐地点，确认人物已到达测种位置，且与目标不在同一个场景。",
                 "advance": "请确认已经完成过帧，并回到测种地点，可以继续校正。",
                 "hit": "请确认路线正确，能正常触发目标、进入战斗或相应交互。本次仅检查操作，不保证出闪。",
                 "reverse": "请确认正确使用大师球捕捉目标，并停在能读取性格、个性的笔记页。"}[kind]
        return spec("观察画面，确认脚本结果",
                    "请看绿色边框的「独立预览」，核对游戏中的实际结果。" + check +
                    "<br><br>脚本运行结束后，点击「脚本没问题」继续下一项；位置或操作不对，就点「脚本有问题」，修改后重新运行。" + places +
                    "<br><br>「跟随执行」自动滚动到当前语句；关闭后可自由查看代码，「定位执行行」可随时跳回。高亮是脚本执行位置，游戏画面可能有采集延迟。暂停脚本不会暂停游戏或 RNG。",
                    easycon.execution.bar, easycon.pause_button, easycon.stop_button, easycon.execution.stack,
                    separate=True, confirmation=True)
    if phase == "retry":
        return spec("修改后，再运行一次",
                    f"当前脚本：{filename}。<br>已回到可编辑的当前脚本，请结合刚才的游戏画面，修改操作或等待时间。"
                    "<br><br>请先将游戏恢复到脚本要求的起点。改好后点击「重新运行」，软件会先保存修改，再从头运行；结束后重新核对画面。"
                    "<br>若运行按钮不可用，请检查伊机控连接、脚本内容和必填参数。" + places,
                    easycon.editor, script_run_target(panel), separate=True)
    return spec("保存脚本的修改", "请保存当前脚本。自动流程从文件读取脚本，未保存的修改不会生效。", easycon.save_button)
