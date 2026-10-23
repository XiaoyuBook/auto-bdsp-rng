# 开发指南

[返回项目首页](../README.md) · [使用指南](USAGE.md) · [Windows 打包](../BUILD.md) · [发布流程](../RELEASE.md)

## 环境要求

- Windows x64、Git。
- Python **3.12 x64**；当前项目要求 `>=3.12,<3.13`。
- Visual Studio Build Tools，安装「使用 C++ 的桌面开发」工作负载（MSVC 与 Windows SDK）。安装项目时会编译 C++17 / pybind11 原生扩展。

## 从源码运行

在 PowerShell 中克隆仓库并安装依赖。以下命令直接使用虚拟环境的解释器，无需修改 PowerShell 执行策略：

```powershell
git clone --recurse-submodules https://github.com/XiaoyuBook/auto-bdsp-rng.git
cd auto-bdsp-rng
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ocr]"
.\.venv\Scripts\python.exe -m auto_bdsp_rng gui
```

已有仓库可运行 `git submodule update --init --recursive` 补齐子模块。不使用 OCR 时，可将安装项改为 `".[dev]"`；自动判闪与精灵信息识别需要 OCR 依赖。

安装时原生扩展编译失败会导致安装失败。安装成功后，运行时原生模块不可用、输入不受支持或校正未命中时，相关计算路径保留 Python 回退。

## 无硬件界面调试

运行 `run_dev_gui.bat`，或在源码入口使用以下命令，可显示 Mock 视频源与 Mock 串口：

```powershell
.\.venv\Scripts\python.exe -m auto_bdsp_rng gui --mock-devices
```

在相应连接窗口中选择并连接 Mock 设备后，可测试键盘控制、录制和脚本执行跟随。手动捕捉 Seed 或校正时可选择模拟成功/失败，检查对应分支。Mock 视频为静态演示画面，不能据此验证真实眨眼、实机时序或乱数结果；正式包的普通启动入口不启用这些设备。

## 原生界面一致性

- 主程序通过 `ui/workspace_style.py` 在创建页面前统一安装基于 Fusion 的浅色 Qt 风格。箭头使用固定逻辑尺寸的线条绘制，不依赖 Windows 系统风格插件；保留 `ui_scale.py` 的 DPI 和界面倍率处理。
- 页面仍复用 `ui/workspace_theme.py` 的颜色、尺寸及随包 MiSans 字体。主导航的 QSS 只作用于直接子标签栏，避免把样式传到内嵌页面。
- v3.4.0 将轮次记录留在主导航，完整日志由共享 `RunLogBuffer` 驱动的独立非模态窗口展示，右栏保留实时日志。任务、历史轮次和实时区入口分别传入运行/轮次或来源上下文；窗口关闭后继续收集消息，警告与错误的未读提示由共享状态更新。
- 延迟更新布局时给 `QTimer.singleShot` 传入控件上下文，确保控件销毁后取消回调。
- 涉及控件绘制或布局的改动，至少检查 100%、125%、150% 缩放及 860×600、1150×900 窗口；相关回归测试为 `test_workspace_style.py`、`test_run_records_ui.py`、`test_log_window_ui.py`、`test_responsive_layout.py` 和 `test_ui_font_loading.py`。

## 搜索、结果与历史记录

定点与 TID 查询使用后台工作线程和请求代次，取消或收到新 Seed 后不再应用旧结果。原生生成和校正释放 GIL，并通过取消令牌终止；Python 回退保留同一取消语义。更新原生接口后应重新执行 editable install，避免本机残留的旧 `_native.pyd` 掩盖接口变化。

`ui/result_paging.py` 将定点与 TID 表限制为每页 200 行，排序、筛选和前缀查找基于完整数据；选中项以原始结果身份恢复。`ui/result_transfer.py` 在点击时固定结果、语言、能力值和日期基准，在后台准备完整复制或导出；准备阶段取消或失败时保留原剪贴板和已有文件，进入写入提交阶段后等待终态，以原子替换完成保存。

`ui/history_archive.py` 为会话历史提供临时归档，轮次、候选与处理详情每页最多 100 条。自动流程整理快照后交由后台归档，不等待磁盘队列容量；慢盘期间保留完整的待写入快照，提交后释放，因此积压会暂时增加内存占用。清空、关闭或失效的历史发布只丢弃对应记录，只有自动流程自身的取消信号才能抛出任务取消。归档失败回滚并保留可用的内存记录。全量搜索、复制和导出独立于当前可见页，退出时释放临时存储。

相关回归覆盖 `test_search_performance.py`、`test_result_transfer.py`、`test_reidentify_cancellation.py`、`test_history_performance.py` 及 `test_history_producer_*.py`。大量结果的布局可用 `scripts/render_search_results_review.py --dpi 100`（另验 125、150）检查；`scripts/benchmark_history_ui.py` 用于历史归档与界面响应基准，实测结果应连同机器条件记录。

## 视频、关闭与日志响应性

视频预览的读帧、眼图匹配和标注在持久工作线程中串行执行，预览请求与结果各只保留最新一份，Qt 定时器负责取结果和绘制。原始帧由捕捉源持有独立像素，标注只修改显示副本；OCR、截图和区域选择可复用最新原始帧。自动流程的显示帧通过最新帧邮箱合并唤醒，过期视频代次的回调会被丢弃；这个限制只作用于界面显示，眨眼检测仍处理其捕捉流程收到的帧。

预览请求、结果、原始帧缓存和首帧消费者共享稳定来源标识：Broker 会话、摄像头序号，或窗口名称与裁剪范围；识别参数和每次创建的工厂函数不参与来源比较。来源改变时旧读帧结果及缓存失效，新的框选、范围显示、OCR 和捕捉等待新来源画面。设备列表枚举使用单个后台 COM 线程，快速刷新或切换采集方式只保留最新待处理请求；完成时读取当前设备选择，关闭时忽略晚到列表并保留线程所有权直到结束。

Broker 停止与 OBS/摄像头预览释放都在后台执行。`disconnect_video_source()` 返回 `False` 可以表示清理尚在进行；测试和调用方应等待停止线程完成，再检查最终状态。取消连接会在启动结束后再次确认停止，防止首次停止后才创建的子进程泄漏。停止未获确认时保留“重试断开”状态，清理结束前禁止重新连接。首次捕捉通过 Qt 定时器等待画面，5 秒内没有首帧则取消准备；非共享预览完成句柄释放后才继续捕捉，共享视频源保持预览。

首帧就绪后的排队回调在执行时再次检查请求令牌、来源和准备状态；Stop、读帧失败、超时或关闭会撤销令牌并恢复尚未启动的自动任务控件。原生伊机控连接若在工作线程完成后、Qt 交付前才收到取消，串行追加后台断开，完成清理后才通知连接回调；清理失败仍保留后端与 transport 以便重试。自动任务在断开、关闭准备和 shutdown 期间禁止开始，同串口连接中的请求仍可合并等待。

无缓存时的眼图、ROI、OCR 区域框选与范围显示复用预览工作线程等待首帧，并显示可取消状态。状态栏取消按钮、Esc 或再次点击框选/显示按钮会撤销等待；关闭、切换视频源或修改来源配置会拒绝晚到结果。TID 设置页的识别按钮使用受管理的 OCR 工作线程，取消后保留线程所有权直到退出。OBS 代理只把无等待的 `try_read()` 调用投递到 Qt 主线程，工作线程每 10 ms 串行轮询，以首帧 1 秒、后续帧 250 ms 为等待期限；单次 Qt 调度仍有 10 秒超时，取消通过 QThread 中断或释放事件结束。Qt Multimedia 的创建、取帧与释放仍在应用线程，旧同步 `QtWindowCapture.read()` 接口保留兼容。

关闭窗口先冻结键盘输入和录制，在后台释放钩子与按键，再显示未保存脚本等确认。确认后发出各任务的停止请求，由正常 Qt 事件循环轮询完成情况，保留工作线程所需的界面清理回调。普通任务超过 2 秒仍未退出会拒绝本次关闭；Broker、预览清理和设备枚举完成前继续等待，不强制终止 QThread。界面缩放的重启意图只在关闭被真正接受后消费一次，取消确认或清理失败会撤销意图。阻塞驱动可能延长清理时间，模拟延迟测试只能验证界面继续响应，实机仍需检查相应驱动的读帧与释放行为。

原生脚本输出在执行线程直接流式写入运行日志，完成时只发布一次界面结果，避免每行产生 Qt 事件和重复写盘。伊机控本地显示与共享会话消息保留有限尾部：每条/本地文档最多约 256 Ki 字符，单行最多 4096 字符，并显示截断提示；本地显示还遵循用户设置的行数上限。完整输出依赖启用磁盘日志，界面复制、导出获取的是保留的显示内容。

日志文案在事件产生处通过 `log_events.LogMessage` 指定摘要、事件类型、阶段、诊断标记和可选级别；它保留原始字符串值，兼容现有双参数日志 sink、历史回调和磁盘保存。`RunLogEntry.user_message` 和 `log_summary` 是用户展示边界：只有明确编写、非诊断且非 DEBUG 的摘要可进入右栏实时区和用户日志，禁止以原始消息兜底。未分类的普通字符串只进入诊断；没有用户摘要的警告、错误和严重错误使用固定问题提示，异常原文、诊断摘要均不能因级别升高而直接显示。摘要不得拼接异常、路径、OCR 原文、脚本输出或内部状态；需要展示的结果数值由事件产生处明确选择。用户列表、提示、复制和导出共用安全摘要，完整原文只能主动进入“全部诊断”或展开详情查看。显式指定 `coalesce_key` 的相邻同类事件，仅在来源、运行、轮次、阶段和级别均相同时合并；更新信号刷新既有行，保留首次及最近原文，磁盘继续保存每条原文。每次进度快照仍正常交付，计时采样仅从用户日志中隐藏，不改变等待、取消或脚本执行逻辑。相关回归为 `test_log_readability.py`。

`ui_stall_diagnostics.py` 使用默认 250 ms 的 Qt 心跳和独立采样线程，在心跳延迟超过 2 秒时记录一次 Python 线程堆栈、时间及版本，不读取画面或局部变量；只有运行日志保存开启时工作，诊断文件保留七个自然日。Python 采样线程无法在原生扩展持续持有 GIL 时运行，因此正式 `run()` 入口启用单次 `faulthandler.dump_traceback_later` 回退，并独占该进程级超时器。嵌入与测试入口默认不启用回退；已有超时器的宿主不可直接开启，因为 Python 不提供读取或恢复他人超时器的接口。诊断不替换异常钩子、信号处理或崩溃处理，关闭确认被取消时仍保持运行，退出路径必须先停止诊断再关闭日志管理器。

对应回归测试为 `tests/test_video_lifecycle_performance.py`、`tests/test_ui_stall_diagnostics.py`，以及 Capture Broker、OBS、预览、关闭和日志相关界面测试。压力测试显示主窗口、伊机控和完整日志窗口，并同时检查短行、超长行输出的完整文件保存、单次发布与 Qt 心跳。

## 命令行

下列命令均在仓库根目录执行：

```powershell
# 查看版本
.\.venv\Scripts\python.exe -m auto_bdsp_rng --version

# 读取 Project_Xs 配置
.\.venv\Scripts\python.exe -m auto_bdsp_rng blink-config --project-xs-config config_camera.json

# 转换 Seed[0-3] / Seed[0-1]
.\.venv\Scripts\python.exe -m auto_bdsp_rng convert-seed --seed 12345678 9ABCDEF0 11111111 22222222

# 捕捉眨眼并恢复 Seed
.\.venv\Scripts\python.exe -m auto_bdsp_rng capture-blinks --project-xs-config config_camera.json --blink-count 40

# 对已有 Seed 做校正
.\.venv\Scripts\python.exe -m auto_bdsp_rng reidentify --project-xs-config config_camera.json --seed 12345678 9ABCDEF0 11111111 22222222
```

## 目录结构

```text
auto-bdsp-rng/
  bridge/EasyConBridge/             保留的旧 Bridge 兼容源码
  docs/                             设计、协议和验证文档
  packaging/                        Windows 绿色包和升级器配置
  script/                           唯一的内置/用户脚本目录
  scripts/                          发布说明、更新包和构建辅助脚本
  src/auto_bdsp_rng/
    automation/auto_rng/            自动定点状态机、搜索、OCR 和脚本处理
    automation/auto_tid_rng.py      自动 TID 状态机与 Display TID 搜索
    automation/easycon/             Python 原生 EasyCon 和兼容后端
    blink_detection/                Project_Xs 捕捉、Seed 恢复与校正适配
    capture_broker*.py              共享视频帧与独立 Broker 进程管理
    gen8_id/                         Gen 8 ID 数据生成与筛选
    gen8_static/                     Gen 8 BDSP 定点生成与筛选
    rng_core/                        RNG 基础和 C++ 原生扩展
    ui/                              PySide6 主窗口、页面与对话框
    run_log.py                       每日运行日志
    update_core.py                   更新事务、文件保留与回滚
    update_service.py                Release 检查、下载与升级启动
  tests/                             pytest 测试
  third_party/                       固定版本的上游参考实现
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试覆盖 Seed/RNG、PokeFinder 对齐、Project_Xs、自动定点、自动 TID、OCR、原生 EasyCon、Capture Broker、更新回滚、每日运行日志、打包资源和 PySide6 界面。发布流程还会检查版本号、CHANGELOG、升级包协议、绿色包目录和 SHA-256 清单。

## 打包与发布

完整 Windows 绿色包、OCR 模型与升级器的构建步骤见 [BUILD.md](../BUILD.md)。版本号、更新日志、Release 和增量更新包的发布约定见 [RELEASE.md](../RELEASE.md)。

## 上游参考

- [Project_Xs_CHN](https://github.com/HaKu76/Project_Xs_CHN)：子模块 `b6cfaaeca8aa6a95e2f07ccaef606e301fa8ad7a`，MIT License。
- [PokeFinder](https://github.com/Admiral-Fish/PokeFinder)：子模块 `2d5c6afed9240f2bdb98634b5b8b1fab352aefa5`（v4.3.2），GPL-3.0 License。

`third_party` 用于固定版本的上游参考。调整适配时应保持 Project_Xs、PokeFinder 与 EasyCon 的兼容行为，优先在项目自身的适配层修改。
