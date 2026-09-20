# QQ 机器人注册教程素材

`index.html` 是可离线打开的分步教程。使用 `steps.js` 中的内容，按 0～11 的原始编号顺序呈现 12 步；聚焦视图只改变显示区域，不修改图片。支持完整截图、可关闭的点击提示和原始尺寸放大。

`images/step-00.jpg` 到 `images/step-11.png` 来自用户提供的未标注原图，移动后逐一验证 SHA-256 未变化。第 11 号原图本身已有一个复制按钮红框，按原始素材保留。

原文件名中的「标注」图片仅用于识别点击位置，已归档到 Git 忽略目录 `private_assets/qq-bot-tutorial-reference/`，教程不加载这些文件。该目录的 `source-map.json` 记录原文件名、目标路径和校验值。

注意：第 8 号原图虽然名为「高级设置」，实际操作是点击「开发设置」。第 10 号标注图与原图尺寸不同，点击提示按原图按钮位置校正。

更新 `steps.js` 后，在项目根目录运行下面的命令同步 Markdown 图文版：

```powershell
.\.venv\Scripts\python.exe scripts\build_qq_bot_guide.py
```

主程序使用 `ui/qq_guide.py` 原生 Qt 教程读取同一份 `steps.js` 与原图，不加载网页。教程内的「连接与绑定」与接入设置共用凭据／接收方表单，「图文验证」共用图文测试面板。HTML 版本保留为可选离线阅读入口；图文版入口为 `docs/QQ_BOT_SETUP.md`。
