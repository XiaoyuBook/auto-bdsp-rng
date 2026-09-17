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
