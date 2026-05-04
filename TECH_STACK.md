# 技术栈决策

## 最终选择

本项目第一阶段采用 **Python 桌面应用 + Python 移植版 RNG 核心 + 可插拔 NS 自动化适配器**。

具体组合：

- 主语言：Python 3.12。
- UI：PySide6。
- 眨眼识别与画面捕获：复用并整理 Project_Xs_CHN 的 Python/OpenCV/pywin32 逻辑。
- RNG 与 BDSP 定点生成：在本仓库用 Python 移植 PokeFinder 的 Gen 8 Static 相关逻辑，并用固定样例做对照测试。
- 数据：JSON 或 TOML 保存定点模板、种族信息、Profile、用户配置。
- 测试：pytest。
- 打包：PyInstaller，目标平台先固定为 Windows 64-bit。
- NS 自动化：定义独立 `automation` 适配层。当前确认伊机控没有可调用接口，后续采用 OpenCV 识图定位伊机控界面元素，再通过 pyautogui 或 pywin32 模拟鼠标/键盘操作。第一阶段暂不接入真实伊机控自动点击。

## 为什么不选 C++/Qt 作为第一阶段主栈

PokeFinder 是 C++/Qt，直接复用核心代码有一致性优势，但 Project_Xs_CHN 的窗口捕获、摄像头捕获、模板识别和眨眼流程已经是 Python 生态。第一阶段最重要的是打通闭环，而不是提前承担 C++/Qt、Python 绑定、OpenCV 捕获和打包的双栈复杂度。

因此第一阶段不直接把 PokeFinder 作为库嵌入，也不把整套软件改成 C++/Qt。PokeFinder 作为算法参考和对照测试来源保留在 `third_party/PokeFinder`。

## 许可策略

Project_Xs_CHN 使用 MIT License。PokeFinder 使用 GPL-3.0 License。

如果移植、修改或分发 PokeFinder 的实现，本项目需要按 GPL-3.0 要求处理源代码开放和许可证声明。第一阶段默认按开源发布路线设计，避免后续打包分发时出现许可风险。

## 发布策略

项目按开源发布方向开发，并接受 GPL-3.0 许可约束。第一阶段主要服务作者自己的 Windows 环境，优先打通完整功能闭环；等后续功能彻底完成并验证稳定后，再打包给其他用户开箱即用。

## 伊机控自动化策略

当前确认伊机控没有命令行、HTTP、Socket 或脚本调用接口，因此不能按 API 集成。自动控制 NS 需要通过 OpenCV 识别伊机控窗口里的按钮、状态区域或截图，再模拟鼠标/键盘操作来完成。

推荐分层：

1. `automation.controller`：统一暴露高层动作，例如连接、按键、等待、截图、状态检查。
2. `automation.adapters.ijikong_gui`：通过窗口查找、截图、模板匹配、坐标换算和鼠标/键盘模拟来操作伊机控。
3. `automation.vision`：OpenCV 模板匹配或 OCR，用于定位伊机控按钮、确认按钮状态、确认游戏画面状态等。
4. `automation.flows`：组合动作流程，例如等待目标 advances、提前量倒计时、按 A 触发遇敌或交互。

为了降低纯识图自动化的不稳定性，后续实现时需要固定伊机控窗口缩放和布局，优先使用窗口相对坐标，不直接写死屏幕绝对坐标。所有关键点击前后都应做一次状态确认，失败时给出明确错误提示，而不是继续盲点。

## 第一阶段闭环

第一阶段实现目标：

1. Project_Xs 眨眼检测得到 `Seed[0-3]`。
2. 转换为 `Seed[0-1]`。
3. 根据 Profile、定点模板、Advances、Offset 和筛选条件生成候选结果。
4. UI 展示候选结果。
5. 暂不接入伊机控自动点击，只保留后续 `automation` 模块边界。

## 版本锁定

- Project_Xs_CHN：`b6cfaaeca8aa6a95e2f07ccaef606e301fa8ad7a`，当前 submodule 描述为 `v1.1-1-gb6cfaae`。
- PokeFinder：`2d5c6afed9240f2bdb98634b5b8b1fab352aefa5`，当前 submodule 描述为 `v4.3.2`。

后续更新上游时，应先更新 submodule 指针，再运行对照测试确认眨眼输出、Seed 转换和 Gen 8 Static 结果没有回归。
