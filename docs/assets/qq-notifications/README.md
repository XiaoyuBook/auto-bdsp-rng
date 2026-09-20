# 软件内 QQ 通知界面

`index.html`、`style.css` 复用已确认的通知窗口设计，`app.js` 通过 Qt WebChannel 调用 `QQNotificationDialog` 和现有通知服务。所有字体、Logo 和教程原图均从打包资源加载，无需本地 HTTP 服务或联网加载网页。

- 凭据、接收方与图文测试在设置页和教程中共用同一组表单。
- 倒计时、换码与绑定结果由 Python QQ 客户端驱动；前端不生成绑定码。
- WebEngine 使用内存 profile，禁止页面导航、远程内容访问和 localStorage。AppSecret 只经本地通道初始化及编辑提交，常规状态推送不包含密钥；持久化仍由通知服务的 Windows DPAPI 选项负责。
- 图文测试页复用真实发送面板，记录以纯文本显示 QQ 返回的结果。
- `packaging/auto-bdsp-rng.spec` 已递归收集 PySide6 与 `docs/assets`，包括 WebEngine、WebChannel 和本目录。

验证：`python -m pytest tests/test_qq_notifications.py`；本地 HTTP/WebSocket 图文链路：`python -m unittest discover -s tools/qq_notify_test -p "test_*.py"`。
