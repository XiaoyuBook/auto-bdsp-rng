"""Shared original screenshot steps for the embedded QQ tutorial."""
from __future__ import annotations

import json

from auto_bdsp_rng.resources import resource_path


def load_qq_steps():
    path = resource_path("docs", "assets", "guide-qq", "steps.js")
    text = path.read_text(encoding="utf-8")
    steps = json.loads(text[text.index("["):text.rindex("]") + 1])
    for step in steps:
        step["path"] = path.parent / step["image"]
        for key in ("title", "detail", "result"):
            step[key] = step[key].replace("QQ 通知测试工具", "QQ 通知设置").replace("测试工具", "软件的 QQ 通知设置")
        step["actions"] = [action.replace("QQ 通知测试工具", "QQ 通知设置").replace("测试工具", "软件的 QQ 通知设置") for action in step["actions"]]
    steps[-1]["detail"] = "AppSecret 是机器人鉴权密钥。接收通知的人不需要拿到这份密钥。继续下一阶段，在教程内填写凭据并绑定接收方。"
    return steps
