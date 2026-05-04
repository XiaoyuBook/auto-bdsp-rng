# auto_bdsp_rng

`auto_bdsp_rng` 目标是把 BDSP 眨眼识别、Seed 推算和 Gen 8 定点乱数生成整合到同一个工具里，减少当前在 Project_Xs 和 PokeFinder 之间手动复制 Seed 的操作。

## 项目来源

- Project_Xs_CHN: https://github.com/HaKu76/Project_Xs_CHN
  - 提供 BDSP 眨眼检测、窗口或摄像头画面捕获、Seed 推算、Advances 追踪等功能。
  - 当前重点使用其输出的 `Seed[0-3]` 和 `Seed[0-1]`。
- PokeFinder: https://github.com/Admiral-Fish/PokeFinder
  - 提供 Gen 8 BDSP 定点乱数生成逻辑。
  - 第一阶段重点参考 `Core/Gen8/Generators/StaticGenerator8.cpp` 和相关 RNG、Template、State、Filter 逻辑。

## 第一阶段目标

第一阶段先实现一个完整闭环：

1. 通过 Project_Xs 的眨眼检测流程生成 4 个 32-bit RNG state。
2. 自动转换成 PokeFinder Gen 8 定点使用的 2 个 64-bit seed。
3. 在本软件内直接调用 BDSP Gen 8 定点生成逻辑。
4. 输出候选结果列表，不再需要手动复制粘贴到 PokeFinder。

Seed 转换关系：

```text
seed0 = S0S1
seed1 = S2S3
```

例如：

```text
Seed[0-3]
S0 = 12345678
S1 = 9ABCDEF0
S2 = 11111111
S3 = 22222222

Seed[0-1]
seed0 = 123456789ABCDEF0
seed1 = 1111111122222222
```

## 建议模块划分

```text
auto_bdsp_rng/
  blink_detection/      Project_Xs 眨眼检测、画面捕获、Seed 推算
  rng_core/             Xorshift、XoroshiroBDSP、RNGList 等基础 RNG
  gen8_static/          BDSP Gen 8 定点乱数生成器
  profiles/             游戏版本、TID、SID、TSV、玩家配置
  data/                 定点宝可梦模板、种族数据、性别比例、版本数据
  ui/                   主界面、结果表格、筛选条件
  tests/                Seed 转换、RNG、生成器对照测试
```

## 核心流程

```text
画面捕获
  -> 眨眼检测
  -> Seed[0-3] 推算
  -> Seed[0-1] 转换
  -> Gen 8 定点生成
  -> 筛选
  -> 结果展示
```

## 功能范围

### 需要保留的 Project_Xs 功能

- 窗口捕获和摄像头捕获。
- 眼部模板选择和预览。
- Monitor Blinks。
- Reidentify。
- TID/SID。
- Timeline。
- Advances 追踪和手动增加。
- 配置保存和读取。

### 需要接入的 PokeFinder 功能

- BDSP Gen 8 Static 非游走定点生成。
- BDSP Gen 8 Static 游走定点生成。
- 初始 Advances、最大 Advances、Offset。
- 闪光判定。
- IV 固定项和随机项。
- 性格、特性、性别、身高、体重。
- 定点宝可梦模板。
- 结果筛选器。

## 第一阶段最小可交付版本

第一阶段完成后，用户应该可以：

1. 打开软件并完成眨眼检测配置。
2. 点击捕捉眨眼。
3. 软件自动识别 Seed。
4. 软件自动把 Seed 传给 BDSP 定点生成器。
5. 在同一界面看到定点候选结果。

## 后续功能预留

第三个新功能暂未定义，建议后续作为独立模块接入，避免和眨眼识别、Seed 转换、定点生成逻辑耦合。

预留入口：

```text
features/
  new_feature/
```

## 许可注意

Project_Xs_CHN 使用 MIT License。PokeFinder 使用 GPL-3.0 License。如果直接移植、修改或分发 PokeFinder 代码，需要按 GPL-3.0 的要求处理源代码开放和许可声明。
