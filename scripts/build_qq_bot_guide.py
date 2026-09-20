"""Generate the Markdown QQ setup guide from the walkthrough's step data."""
from __future__ import annotations

import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "docs/assets/guide-qq/steps.js"
    text = source.read_text(encoding="utf-8")
    steps = json.loads(text.removeprefix("const QQ_STEPS = ").strip().removesuffix(";"))
    lines = [
        "# QQ 机器人注册与通知配置", "",
        "[返回项目首页](../README.md) · [打开分步截图教程](assets/guide-qq/index.html) · [测试工具说明](../tools/qq_notify_test/README.md)", "",
        "主程序顶部点击「通知」即可打开 QQ 通知设置；「注册与绑定教程」使用原生 Qt 窗口，在软件内展示原图、点击提示和放大视图。教程的「连接与绑定」页面可以直接填写 AppID、AppSecret 并绑定私聊或群聊，与接入设置共用配置。", "",
        "跟随下面 12 步创建自己的 QQ 机器人，取得 AppID 和 AppSecret。截图依据 2026 年 9 月的平台界面，实际页面文案可能调整。", "",
        "开始前准备好用于登录的 QQ 账号，打开 [QQ 开放平台](https://q.qq.com/)。已有机器人时，可直接进入管理页并从第 9 步的「开发设置」继续；已有可用 AppSecret 时无需重置。", "",
        "## 步骤导航", "",
    ]
    lines.extend(f"{i + 1}. [步骤 {i + 1}：{step['title']}](#step-{i + 1})" for i, step in enumerate(steps))
    for i, step in enumerate(steps):
        image = root / "docs/assets/guide-qq" / step["image"]
        if not image.is_file():
            raise FileNotFoundError(image)
        lines.extend(["", f'<a id="step-{i + 1}"></a>', "", f"## {i + 1}. {step['title']}", ""])
        lines.extend(f"{j + 1}. {action}" for j, action in enumerate(step["actions"]))
        lines.extend(["", ("> " if step.get("caution") else "") + step["detail"], ""])
        if step.get("link"):
            lines.extend([f"[{step['link']['text']}](assets/app-icon.png)", ""])
        lines.extend([
            f"![第 {i + 1} 步：{step['title']}](assets/guide-qq/{step['image']})", "",
            f"**完成后：** {step['result']}",
        ])
    lines.extend([
        "", "## 验证凭据并绑定接收方", "",
        "1. 点击主程序顶部的「通知」，进入「接入设置」，或在软件内教程的「连接与绑定」页面继续。",
        "2. 填入 AppID 和 AppSecret，点击「验证凭据」。",
        "3. 私聊：点击「绑定私聊」，等待网关连接成功后显示 6 位绑定码，在 QQ 中向机器人发送当前绑定码。界面逐秒倒计时，每 60 秒自动更换绑定码并重新计时，旧码立即失效。",
        "4. 群聊：先把机器人加入目标群，再点击「绑定群聊」，在目标群内 @机器人并发送当前验证码。",
        "5. 成功后，接收方会自动保存。勾选私聊／群聊，点击「发送图文测试」，默认发送文字和软件 Logo 图片。到 QQ 确认文字、图片都已收到，再点击「我已收到文字和图片」。",
        "6. 打开通知窗口右上角开关，在「通知规则」选择任务完成、任务异常或手动停止等事件。", "",
        "绑定成功、取消绑定、离开绑定页面或关闭窗口时停止自动换码；网关断开或鉴权失败会明确报错，需要重新连接。可以同时选择私聊和群聊，OpenID 不是 QQ 号或群号。测试图片发送失败时不会算通过。", "",
        "## 自动通知与凭据保存", "",
        "默认在整项自动定点乱数／自动 TID 乱数任务完成或异常终止时通知，循环中的常规轮次不逐条推送。手动停止默认不通知，可自行勾选。自动通知默认附带结束时的最新视频画面，没有可用画面时使用软件 Logo。测试通知始终带图片。", "",
        "发送记录按私聊／群聊分别显示文字和图片的提交结果，网络操作异步执行，不影响任务运行。为避免重复通知，失败后不会自动重发；接口提交成功不等于对方已经看到，请在 QQ 中确认。", "",
        "配置位于 `%LOCALAPPDATA%\\auto_bdsp_rng\\settings\\qq-notifications.json`。AppSecret 默认只在内存中保留；勾选「记住密钥」后使用 Windows DPAPI 加密保存，仅当前 Windows 用户可解密。未记住密钥时，下次启动需要重新填写才能启用通知。访问令牌不会保存到文件。", "",
        "需要单独排查 QQ 接口时，仍可运行 `tools/qq_notify_test/run.bat`；独立工具与主程序共用通知客户端，配置各自独立。", "",
        "## 常见情况", "",
        "- **无法添加机器人或无法收发：** 检查平台的「服务范围」与「开发体验号码设置」，确认当前用户或群可以使用机器人；发送失败时查看测试工具日志里的 HTTP 状态码与平台错误信息。",
        "- **绑定码已更新：** 发送界面当前显示的新绑定码，旧码不再有效；群聊记得 @机器人。",
        "- **重置后原来的程序鉴权失败：** 更新那些程序中保存的 AppSecret，旧密钥已失效。",
        "- **别人只是接收通知：** 无需向他提供 AppSecret。共用机器人进行客户端发送需要另行设计服务端转发；本教程演示本人用自己的机器人直接连接。", "",
    ])
    output = root / "docs/QQ_BOT_SETUP.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Generated {output.name}: {len(steps)} steps")


if __name__ == "__main__":
    main()
