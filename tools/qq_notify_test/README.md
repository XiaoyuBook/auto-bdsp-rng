# QQ 通知独立测试工具

使用 Python + PySide6，独立于 BDSP 主程序。仅依赖 PySide6，不连接游戏、采集卡或手柄。

首次创建机器人，可点击工具顶部的「注册教程」，或打开 [分步截图教程](../../docs/assets/guide-qq/index.html) / [完整图文版](../../docs/QQ_BOT_SETUP.md)。教程图片使用用户提供的原图，按步骤提示点击位置。

## 启动

在当前项目中双击 `run.bat`，自动使用项目 `.venv`。

也可以在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe tools\qq_notify_test\app.py
```

工具窗口可独立运行，不启动游戏功能。客户端现与主程序共用，请保留仓库的 `src/auto_bdsp_rng` 目录；不再支持仅复制此工具目录。使用 Python 3.12 时也可执行：

```powershell
python -m pip install -r requirements.txt
python app.py
```

主程序已有原生 Qt 教程，可从顶部「通知 → 注册与绑定教程」进入。此独立工具保留原有离线图文教程入口。

## 测试步骤

1. 打开 [QQ 开放平台机器人管理](https://q.qq.com/#/apps)，创建、配置机器人，准备 AppID 和 AppSecret，以及对应的私聊／群聊权限。
2. 填入凭据，点击“验证凭据”。成功表示已获得访问令牌，不代表已验证消息发送权限。
3. 点击“绑定私聊”，在 QQ 中向机器人发送界面显示的验证码；或点击“绑定群聊”，在目标群 @机器人并发送验证码。READY 后开始 60 秒有效期，到期自动换码并更新提示，旧码失效。主程序界面还提供逐秒倒计时。
4. 选择私聊、群聊或同时发送。也可手动填写已有 OpenID，OpenID 不是 QQ 号或群号。
5. 点击“发送测试通知”，默认会发送测试文字和软件 Logo 图片。到 QQ 中确认文字与图片均已收到，验证绑定对象及图片发送能力。

每次测试都会带图片，无需手动选图。可以“更换图片”，也可以“恢复默认”使用软件 Logo；只发送图片时可清空文字。图片会转为 JPEG，按 QQ API 分片上传；先发送文字，再发送图片。图片上传或发送失败时，测试会报告失败，不能仅凭收到文字就认为图文测试通过。私聊和群聊分别处理，某个目标失败不阻止尝试另一个目标。

默认图片随工具放在 `assets/test-notification.png`，由本项目 `docs/assets/app-icon.png` 缩小为 512 × 512。单独复制工具时保留整个 `assets` 目录；图片丢失时会报错，不会退化为纯文字测试。

绑定时必须匹配当前验证码，不会仅凭陌生好友添加或机器人入群事件自动绑定。WebSocket 只在绑定期间存在，日常发送使用 HTTP API。网络操作在 Qt 事件循环中异步执行，支持取消；取消不能撤回已发出的消息。测试工具不自动重试发送，避免因响应丢失而重复通知。

## 配置与日志

AppID、用户／群 OpenID、发送目标保存在 `%LOCALAPPDATA%\AutoBdspRng\QQNotifyTest\config.json`。绑定成功和关闭窗口时自动保存，也可点击“保存配置”。AppSecret、Token 不保存到文件，也不显示在日志中。

HTTP 失败会显示状态码及平台返回的错误。消息权限、可发送对象、主动消息额度等以机器人在 QQ 开放平台的实际配置为准。当前客户端使用正式环境端点。

## 离线验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tools\qq_notify_test -p "test_*.py" -v
```

测试通过本机 HTTP / WebSocket 服务模拟平台，不向 QQ 发送消息。实际账号的绑定和推送需填写自己的凭据后操作 GUI 验证。

## 参考实现

协议流程参考 [BetterGI QqNotifier.cs](https://github.com/babalae/better-genshin-impact/blob/f29966868c6e2d5b8798bb6a4f3df201ec4a5f95/BetterGenshinImpact/Service/Notifier/QqNotifier.cs) 和同目录的 `QqWebSocketHelper.cs`，版本 `f29966868c6e2d5b8798bb6a4f3df201ec4a5f95`。遵循本仓库 GPL-3.0-or-later 许可。
