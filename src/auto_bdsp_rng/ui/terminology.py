"""Contextual descriptions of existing RNG controls and units."""
from PySide6.QtCore import Qt
from html import escape
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QTextBrowser, QVBoxLayout

TERMS = (
    ("Seed", "随机数生成器的内部状态。先捕获或输入 Seed，再生成对应结果；重新测种会得到新的定位依据。"),
    ("帧数 / Advances", "RNG 的推进次数，不一定等于视频帧数。自动 TID 由小卡比兽眨眼间隔推进，不能直接除以 60 换算秒数。"),
    ("delay", "从脚本触发到目标生成之间的推进差，单位为 RNG 帧。定点按轮解析策略，TID 使用启动时的固定值。"),
    ("NPC", "参与 RNG 消耗的非玩家角色数量，影响预测与校正；应按实际场景和所用 Project_Xs 配置设置。"),
    ("Timeline NPC", "Timeline 模型中的 NPC 数；与普通 NPC、宝可梦 NPC 分开配置，不能互相替代。"),
    ("TID / SID", "训练家 ID 与隐藏 ID，参与异色判定。它们与游戏界面显示的六位 ID 含义不同。"),
    ("Display TID", "游戏界面显示的六位训练家 ID。前导零属于显示格式，自动 TID 按该值匹配目标。"),
    ("TSV", "训练家异色值（Trainer Shiny Value），由 TID / SID 派生，用于 RNG 的异色匹配。"),
    ("IV / 个体值", "六项个体值依次为 HP、攻击、防御、特攻、特防、速度，各项范围 0–31。"),
    ("IV Count / 满个体数", "定点模板保证为 31 的个体值数量。其他项仍可能随机得到 31。"),
    ("Height / Weight", "身高与体重的内部体型参数，范围 0–255；这里的数值不是厘米或千克。"),
    ("EC / PID", "加密常量 / 性格值，以十六进制显示。复制和导出会保留原值。"),
    ("ROI / OCR", "ROI 是以像素表示的识别区域；OCR 从指定区域读取文字。先连接视频源，再框选并测试。"),
    ("校正 / Reidentify", "使用新的眨眼观测在已有 Seed 序列中重新定位推进数；与重新捕获 Seed 的用途不同。"),
    ("Seed 配置预设", "Seed 捕捉页可选择项目现有配置文件，编辑后用原保存入口保存。手动捕捉用左侧参数，自动流程用右侧所选配置。"),
    ("筛选方案", "定点数据区的“筛选方案”可保存自己的筛选条件。应用后点击生成；不会自动启动任务或替换 Seed。"),
)


def show_terminology(parent, query=""):
    dialog = QDialog(parent)
    dialog.setWindowTitle("术语与操作帮助")
    dialog.resize(720, 530)
    layout = QVBoxLayout(dialog)
    search = QLineEdit()
    search.setPlaceholderText("搜索术语、单位或操作")
    layout.addWidget(search)
    tree = QTextBrowser()
    def filter_terms(text):
        text = text.casefold().strip()
        matches = [(name, description) for name, description in TERMS if text in (name + description).casefold()]
        tree.setHtml("".join(f'<p style="font-size:16px; font-weight:500; color:#202A33">{escape(name)}</p><p style="font-size:14px; color:#52606D">{escape(description)}</p>' for name, description in matches) or "没有匹配的术语，请更换关键词。")
    search.textChanged.connect(filter_terms)
    search.setText(query)
    filter_terms(query)
    layout.addWidget(tree)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.close)
    layout.addWidget(buttons)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.show()
    return dialog
