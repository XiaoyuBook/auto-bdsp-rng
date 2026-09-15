# ROI 与眼睛模板演示

`index.html`、`demo.css`、`demo.js` 复用用户认可的循环动画，通过本地 Qt WebChannel 将“我已学会”交给原生引导。演示不调用真实框选或配置保存；真实操作由 `ui/eye_guide.py` 接续。

`screenshot.jpg` 来自用户提供并授权用于教学的软件截图。仅清除了已有的眼睛标注框，并交换两张按钮图块以匹配当前界面：左侧“框选眼睛区域”，右侧“截取眼睛”。图片原始尺寸为 1586×1280；动画锚点与框选位置均使用原图坐标，Canvas 随显示尺寸和 DPR 换算，不依赖桌面绝对坐标。

字体使用 Microsoft YaHei UI / Microsoft YaHei 系统回退。资源无远程依赖，随现有 `docs/assets` 发布复制。开发 Mock 视频源从此图裁取静态游戏画面，供真实右键框选操作测试；不代表实时采集或 Seed 捕捉能力。
