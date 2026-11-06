# AsrSubs 开发文档（内部技术参考）

> **已废弃，见 `INDEX.md`。** 本文件全部有效内容已于 2026-11-05 按主题拆分迁入
> `design\` / `guides\` / `reference\` / `adr\`，版本叙事已并入 `CHANGELOG.md`。
> 废弃原因：头部版本号停在 v4.8.18、正文却写到 v1.2.0，且单篇混装 7 类内容；
> 本文结论**不得用于当前操作**。归档日期：2026-11-05（已移入 `03_文档\archive\`，主目录不再保留）。适用代码版本：1.2.0 写作前的历史稿。
>
> 对照表：`INDEX.md` 第二节记录了每篇原件被哪篇新文档取代。

> 以下内容为历史原文，保留仅作溯源：
>
> 当前版本：**v4.8.18** · 按 2026-11-01 的实际代码编写（界面重构已完成，见 §6.10；
> v4.8.16–v4.8.18 为安装器缺陷修复 + 一键体验 + 使用说明重排 + 下载修复，引擎功能零改动）。
> 本文档是**开发者技术参考**，与现有文档的分工见末尾「文档体系与待刷新项」。

---

## 0. 文档定位

| 文档 | 读者 | 内容 |
|---|---|---|
| `README.md` | 入门 / 概览 | 功能清单、目录结构、安装后布局。**2026-10-31 整合时已刷新到 v4.8.14**（结构/版本/部署地图与实包一致） |
| `03_文档/使用说明.txt` | 最终用户 | 操作手册（随安装包分发） |
| `03_文档/AI交棒文档.md` | 接手开发者 | 架构、坑、待办（**同样偏旧，需以本文件为准**） |
| **`开发文档.md`（本文件）** | 维护者 | 架构、模块、版本管理、v4.0.5→v4.0.9 重构、v4.2→v4.8.14 界面重构、测试、同步工作流 |

---

## 1. 项目概述

**AsrSubs** 是一个 DaVinci Resolve 插件：在 Resolve 里把时间线音频识别成字幕，**全程本地离线推理，音频不出机器**。

- 识别模型：`Qwen3-ASR-0.6B` + `Qwen3-ForcedAligner-0.6B`（词级强制对齐）。
- 运行环境：本机 NVIDIA GPU，本地离线；`D:\AsrSubs\runtime\Scripts\python.exe`（Python 3.12 + torch）。
- 形态：Fusion **Lua 前端 UI** + 常驻 **Python 子进程引擎**，通过 Resolve 原生 `DaVinciResolveScript` API 操作时间线。
- 平台红线约束见 §7。

---

## 2. 系统架构

```
DaVinci Resolve
 └─ AsrSubs.lua              ← Fusion UI + 异步状态机（Tick 轮询 rt/ 文件）
      │  RunTask → worker.py  ← 常驻执行器（pythonw，CREATE_NO_WINDOW，不闪黑窗）
      ├─ timeline_source.py   读时间线音频片段 → rt/src.lua
      ├─ resolve_bridge.py    识别入口（优先转常驻服务，失败回退本地加载）
      │     ├─ asr_server.py  常驻服务：模型只加载一次
      │     └─ asr_engine.py  识别 + 对齐 + 切句 + 排时间
      ├─ subs_io.py           SRT ↔ Lua 表（绕开 Lua 打不开中文路径）
      ├─ land_subtitle.py     挂字幕轨 + 导入媒体池（含 R2 relang 闭环）
      ├─ cut_inplace.py       原地粗剪（R1 区间校准）
      ├─ cut_timeline.py      粗剪入口（按 engine=inplace 分发到 cut_inplace）
      ├─ export_files.py      导出 srt/txt
      ├─ corrections.py       权重词纠错（确定性替换）
      ├─ hotwords.py          全文高频热词统计
      ├─ txt_out.py           TXT 文稿输出
      └─ diag.py              一键诊断包
```

- **进程间通信一律走 `engine\rt\` 文件邮箱**（`.json` 收任务、`.lua` 回结果、`status.lua` 报进度、`hb` 心跳、`asr_cmd.json` 服务任务）。**不开任何网络端口**。
- **`ASCII_ROOT`**：Lua 里写死指向 `D:\AsrSubs\engine`，即 Resolve 实际加载的 Python 引擎根。开发目录 `D:\插件\AsrSubs_整合_20261031\01_源码` 是规范源码，必须镜像 engine。

---

## 3. 目录结构与部署地图

```
D:\插件\AsrSubs_整合_20261031\       ← 开发目录（本仓库根，整合包）
├─ 01_源码\                       ← 【规范源码】单文件 Lua + 全部 .py + 配置文件
│   ├─ AsrSubs.lua                源码本体（v4.8.18，6935 行）
│   ├─ *.py                       引擎（与 engine 逐字节一致）
│   ├─ models.lua / models.txt    模型清单（Lua 下拉框读）
│   ├─ paths.json / paths.lua     路径配置
│   ├─ version.txt                版本号唯一来源
│   └─ tools\                     构建/校验/测试脚本
├─ 02_构建与安装器\
│   ├─ AsrSubs.iss                Inno Setup 安装脚本
│   ├─ installer.py               安装核心（PyInstaller onedir）
│   ├─ payload\                   打进安装包的载荷（由 sync_payload.py 同步）
│   └─ build.bat / build.ps1      三道闸：build_lua + sync_version --check + sync_payload --check
├─ 03_文档\                       使用说明.txt / AI交棒文档.md / 各类分析
├─ 04_安装包\                     安装器成品
└─ （历史归档 `99_归档\` 已移出本包 → 副本见 D:\AsrSubs_整合_删除前备份_20261031）

D:\AsrSubs\engine\                ← 【实时引擎】Resolve 实际加载（ASCII_ROOT）
D:\AsrSubs\engine\rt\             ← 运行期文件邮箱
D:\AsrSubs\engine\packer\payload\ ← 打包载荷（engine 自带）★ v4.0.9 已归档
%ProgramData%\…\Fusion\Scripts\Utility\AsrSubs\AsrSubs.lua  ← Resolve 菜单实际加载
```

**四份 `AsrSubs.lua` 必须 MD5 完全一致**（2026-11-01 实际：仓库两份 `6b9e249f…` v4.8.18，
装机两份 `71867272…` v4.8.16——装机版比仓库旧两个版本，引擎仅版本号字符串变化）：

| 位置 | 角色 |
|---|---|
| `01_源码\AsrSubs.lua` | 规范源码（唯一改代码的地方） |
| `D:\AsrSubs\engine\AsrSubs.lua` | 实时引擎（被测试 / 被加载） |
| `02_构建与安装器\payload\AsrSubs.lua` | 安装包载荷 |
| `%ProgramData%\…\Utility\AsrSubs\AsrSubs.lua` | Resolve 菜单 |

> ★ **v4.0.9 整理**：原第 5 份 `D:\AsrSubs\engine\packer\payload\AsrSubs.lua` 已随
> 整个 `engine\packer\`（2.x 旧打包区，55MB）归档到
> `删除前备份\99_归档\11_v4.0.9整理_20261031\engine\packer\`。
> 理由：当前打包走 `02_构建与安装器\build.bat` + `02_构建与安装器\payload`，
> packer 是**冗余副本**，多一份就多一个易漂移的漂移源。
> 详见 §9「v4.0.9 整理归档」。

> `models.lua/models.txt/paths.json/paths.lua` **不进安装包 payload**（Lua 从 `ASCII_ROOT` 引擎目录读），但**必须**随 engine 一起部署。

---

## 4. 核心模块清单

| 文件 | 职责 | 关键入口 / 函数 |
|---|---|---|
| `AsrSubs.lua` | Fusion UI + 异步状态机；命令分发、Tick 轮询、`StartLand`/`SaveSubs`/`pendingSync` | `App:Tick`、`App:StartLand(mode,reason)`、`App:SaveSubs` |
| `worker.py` | 常驻后台执行器，消除 CMD 黑窗闪现 | 常驻文件邮箱接任务 |
| `resolve_bridge.py` | 识别入口；参数走文件（绕开 GBK 命令行） | `python resolve_bridge.py <cfg.json>` |
| `asr_server.py` | 常驻 ASR 服务，模型只加载一次 | `rt\asr_cmd.json` 任务邮箱 |
| `asr_engine.py` | 识别 + 强制对齐 + 切句 + 排时间 | Qwen3ASR 双模型 |
| `subs_io.py` | 字幕存取桥（SRT ↔ Lua 表 / JSON） | `mode=dump` / `mode=save` |
| `land_subtitle.py` | 挂字幕轨 + 导入媒体池；**R2 `relang_subtitles`** | `land_inplace()`、`relang_subtitles()` |
| `cut_inplace.py` | 原地粗剪；**R1 `_subtitle_offset_frames`** | `do_cut_inplace()` |
| `cut_timeline.py` | 粗剪入口（按 `engine=inplace` 分发） | `python cut_timeline.py <cfg.json>` |
| `timeline_source.py` | 读时间线音频片段信息 | 输出 JSON 到 `rt/src.lua` |
| `corrections.py` | 权重词纠错（确定性替换，不靠模型） | 等号表解析 |
| `hotwords.py` | 全文高频热词统计（比例阈值） | 写 `rt/hotwords.lua` |
| `txt_out.py` | TXT 文稿输出（全项目唯一实现） | 60 列重排 |
| `export_files.py` | 导出 srt/txt 到用户目录 | `python export_files.py <cfg.json>` |
| `diag.py` | 一键打包诊断信息 | 产出 `.zip` |
| `qwen_version.py` | 版本号常量（由 `sync_version.py` 生成，勿手改） | `VERSION`、`banner()` |

---

## 5. 版本管理

- **唯一来源**：`01_源码\version.txt`。改版本号只改这一处。
- **同步脚本**：`tools\sync_version.py` 把版本注入 **5 处**——
  `AsrSubs.lua` 的 `local VERSION`、`AsrSubs.iss` 的 `#define MyAppVersion`、`installer.py` 的 `APP_VERSION`、`qwen_version.py` 的 `VERSION`、`03_文档\使用说明.txt` 头部。
- **打包载荷同步**：`tools\sync_payload.py` 把 `01_源码` 推到 `02_构建与安装器\payload`（`--check` 只比对不写）。
- **版本号规则**（用户约定）：
  - 常规升级（功能 / 修复 / 改动）：次版本号 +1（如 `4.0.4 → 4.0.5`）。
  - 大版本（架构调整 / 不兼容变更 / 重大重构）：主版本号 +1（`4.0.5 → 5.0.0`）。
- **改版本标准动作**：编辑 `version.txt` → `python tools\sync_version.py` → `python tools\sync_payload.py`。
- **界面迭代期（v4.2~v4.8）做过的事**：每修一处就用一个补丁号发一版（4.8.4 / 4.8.5 / 4.8.13 / 4.8.14 …），
  好处是"哪一版修了什么"一目了然、出问题能精确回退；代价是版本号密度高。
  **功能稳定后建议并回次版本**（如把 4.8.x 归并为 4.9.0），`使用说明.txt` 里保留分条记录即可。

---

## 6. v4.0.5 → v4.0.9 重构：R1 / R2 / R3

### 6.1 R1 — 粗剪区间校准（修复"偏移场景误切 / 空切"）

- **问题（bug-A 类）**：粗剪切除区间此前由 Lua 内存里的 SRT 时间码推算，当时间线真实字幕轨位置与 SRT 内部时间存在整体偏移（例如素材入点不在 0 帧）时，会切错区间甚至空切。
- **方案**：`cut_inplace.py` 新增 `_subtitle_offset_frames(tl, cuts_sec, fps, …)`，直接用时间线**真实字幕轨位置**（`GetTrackCount("subtitle")` → `GetItemListInTrack` → `GetStart()/GetEnd()`）来反推"SRT 内部秒数 → 时间线帧"的整体偏移。
- **算法**：对每个切除区间，只在"时长相近（span-matching）"的真实字幕里取**最接近的一条**投一票（按帧偏移计票）；取票数显著高于次高的偏移作为校准值。 tolerant 容差 = `max(2.0, 0.05*区间帧长)`。无共识则返回 0（不校准，保持旧行为）。
- **文件**：`cut_inplace.py`（集成于 `do_cut_inplace()`，R1 校准在 `C.merge(raw)` 之后执行）。
- **★ v4.0.6 真机修正**：真实字幕 `TimelineItem` 的 `GetStartFrame()/GetEndFrame()` 返回 **None**（真机实测），必须改用 `GetStart()/GetEnd()` 才能读到正确帧。v4.0.5 用错了接口，导致 R1 在真机上永远读不到字幕位置、偏移恒为 0（等于失效）；v4.0.6 已修正并经端到端验证（读回真实 span `0/60/75/120/150/216`、`detected_offset=0` 正确）。

### 6.2 R2 — 字幕更新闭环重挂

- **问题**：「更新字幕 · 写回 SRT」(`SaveSubs`) 原本只写 SRT + 同步媒体池，**不会把新字幕重新挂到时间线**，用户还得手动再落一次轨。
- **方案**：Python 侧 `land_subtitle.py` 新增 `relang_subtitles(mp, tl, land_srt, media, fps, work, expected_offset=0)`，形成闭环：
  1. 记录当前各字幕轨的**启用状态**（`_get_track_enabled`）。
  2. 删掉**本插件旧字幕**（`clear_our_subtitle_items`），保留用户手工字幕轨。
  3. 若删到了旧字幕 → `land_inplace` 重挂 → 恢复其它轨启用态、确保落轨轨启用。
  4. 若时间线上本就没有本插件旧字幕 → 退化为"只入媒体池"，**不新建字幕轨**。
- **Lua 接线**：`App:Tick` 的 `pendingSync` 处理器改为——**时间线开着 → `StartLand("relang","sync")` 闭环重挂；否则 `StartLand("media","sync")` 只同步媒体池**（避免无时间线时报错）。`StartLand` 新增 `relang` 模式（复用 `landing` 阶段），`landing` 处理器补"已删除旧插件字幕 N 条并重挂"日志。
- **★ v4.0.6 真机修正（识别策略重构）**：v4.0.5 靠"来源媒体项 stem 匹配"识别本插件字幕，但真机实测字幕 `TimelineItem.GetMediaPoolItem()` 返回 **None**，stem 匹配永远失败 → 删不掉旧字幕 → 字幕更新时新旧叠加（重影）。按 DaVinci **字幕轨唯一性规则**（多轨有字幕时只显示一条）重构为**专用轨整轨清除**：
  - `land_inplace` 落轨后，把本插件专用字幕轨索引 + 各条起止帧写入边车 `<srt>.landed.json`（跨进程持久化）。
  - `clear_our_subtitle_items` 主路径读边车拿专用轨索引，**整轨清除该轨**旧条目；无边车（老版本/首落）时退回原 stem 匹配兜底。
  - 实测：删旧 3 条 + 重挂 4 条，末尾 4 条（无重影），`cleanup_killed=4` 时间线还原。

### 6.3 v4.0.7 — 真机实战暴露的三项落轨缺陷（已修）

> 触发场景：用户真实素材（53 条字幕）+ 批量删行 + 字幕更新 + 粗剪的连续操作。
> 用户反馈「**ST2（字幕轨道2）是直接出现的**」——凭空多出一条字幕轨。

#### 6.3.1 缺陷 1 — 「适配字幕」凭空新建 ST2

- **根因**：`land_inplace` 的找轨逻辑是「**找第一条空轨，找不到就 `AddTrack` 新建**」。
  R2 成功后专用轨 1 上已有 31 条（非空）→ 再点「适配字幕」→ 新建 track 2 → 字幕却没落进去（报错"实际首帧 0，SRT 首条 124"）→ **空 ST2 留在时间线上**。
- **修复**：`land_inplace` 找轨顺序改为 **① 读边车定位插件专用轨 → ② 第一条空轨 → ③ 新建**。
  定位函数 `_dedicated_info(tl, meta)` 采用多证据（见 6.3.4）。复用专用轨前先 `_remove_our_items()` 精确删掉本插件旧条目 → **不再新建 ST2，也顺带解决"重复落轨重影"**。

#### 6.3.2 缺陷 2 — 失败的落轨污染边车，把后续 R2 彻底带偏

- **根因**：v4.0.6 的 `_write_landed_meta` 在**校验之前**无条件执行。那次失败的落轨把边车覆写成 `{"track": 2, "spans": []}`（track=2 是新建的空轨、spans 为空）→ 后续 R2 读到空轨 → 清 0 条 → 走"时间线无旧字幕"分支 → **旧字幕没删、新字幕也没落**（日志实证：`已挂到时间线字幕轨：0 条`）。
- **修复**：边车**只在 `placed > 0`（真落上轨）时才写**；失败时打印"落轨 0 条，保留原边车不动"并保留旧边车。

#### 6.3.3 缺陷 3 — `write_lua` 漏掉 `relang_cleared`，导致 Lua 侧永远误报

- **根因**：`write_lua()` 没有输出 `relang_cleared` 字段 → Lua `tonumber(d.relang_cleared)` 恒为 `nil` → `or 0` → **无论实际删了多少条，界面一律显示"时间线此前无本插件字幕"**。真机日志明明写着「R2：整轨清除专用字幕轨 1 旧条目 53 条」，界面却说没删——**误导排查方向**。
- **修复**：`write_lua` 补 `relang_cleared`；Lua 侧文案分三档：`cleared>0` → "已删除旧插件字幕 N 条并重挂"；`cleared==0 且 n>0` → "本次仅上轨"；`cleared==0 且 n==0` → "仅同步媒体池（未改动时间线）"。

#### 6.3.4 配套重构 — 边车 v2 与多证据定位

| 边车字段 | 含义 | 用途 |
|---|---|---|
| `track` | 专用轨索引 | 基础定位 |
| `spans` | 上次落轨各条的 `(start,end)` 帧区间 | **最强证据**——帧区间是落轨产物，不会撞上别的轨 |
| `owned` | 该轨条目是否全由插件产生 | `True`→可整轨清；`False`→只按 spans 精确删，**绝不误删用户手工字幕** |
| `tl` | 时间线名 | 同一条 SRT 跨时间线使用时**不串扰** |
| `count` | 上次落轨条数 | 边车被污染时靠"条数吻合"交叉验证真正的插件轨 |

`_dedicated_info()` 的定位优先级：**spans 命中 → 边车轨非空 → 唯一非空轨（救回被污染的边车）→ 条数吻合 → 认边车 → 跨时间线则判定为"无"**。

另外 `land_inplace` 落轨后改为**按各轨条目 ID 差集探测字幕实际落到哪条轨**（不再假设落在我们启用的那条），落点以实际为准并写进边车。

**边车键统一用【原始 SRT 路径】**（`meta_key`），不再用偏移后的临时副本路径 —— 否则偏移量一变边车就"失联"。

### 6.4 R3 — 用 `timeline.Export` 导出 SRT（**已放弃**）

- **结论**：经真机探针（`probe_r15.py`）验证，当前 DaVinci 21.1 的 `TimelineExportType` 枚举**没有 SRT / 字幕类导出类型**（只有 EDL、FCPXML、AAF、CSV、ALE、OTIO…），也无 `EXPORT_SRT` 常量；且 `TimelineItem` **没有任何 API 能回读字幕文本**。因此 R3 技术上不可行，保留现有"文件复制式"导出。

### 6.5 架构红线（永久约束）

> **所有落轨 / 粗剪操作都必须在"当前已打开的时间线"上原地完成，绝不允许新建时间线。**
> 历史"新建独立时间线"分支已彻底删除；`land_inplace` 失败只报错、不静默新建。

### 6.6 Resolve 字幕轨 API 事实（真机 + 官方文档交叉确认）

| 事实 | 说明 | 影响 |
|---|---|---|
| `GetTrackCount("subtitle")` | 可用 | 字幕轨数量 |
| `GetItemListInTrack("subtitle", i)` | 可用 | 读轨上条目 |
| `TimelineItem.GetStart()/GetEnd()` | **可用**（字幕轨） | 字幕帧位置 |
| `TimelineItem.GetStartFrame()/GetEndFrame()` | **返回 None**（字幕轨） | 不可用，v4.0.6 已改用 `GetStart/GetEnd` |
| `TimelineItem.GetMediaPoolItem()` | **返回 None**（字幕轨） | 无法按来源 stem 匹配字幕 → 改用**边车 spans** |
| `AppendToTimeline([{mediaPoolItem}])` | 可用；字幕轨**忽略 `recordFrame`**，只认**播放头位置** | 落轨前须 `SetCurrentTimecode(GetStartTimecode())` 把播放头钉到起点 |
| `AddTrack("subtitle")` | 可用，但**曾在已落字幕的时间线上凭空造出空 ST2** | v4.0.7 改为优先复用专用轨；v4.0.8 补上「失败后回收自建轨」 |
| `DeleteTrack("subtitle", idx)` | **可用**（官方 API 文档有载，v20.2 / v21.0.4 一致） | 清理历史遗留空字幕轨的依据 |
| `SetTrackEnable`（**无 d**） | 可用；调用后 `GetIsTrackEnabled` 回读一致 | 落轨定向的**唯一**手段（v4.1.1 修正） |
| `SetTrackEnabled` / `SetTrackEnableState` | 属性为 **None**（21.1.2） | 旧包装函数因此**静默失败**，多轨时落错轨 —— v4.1.1 已修 |

**v4.1.1 真机补充（多字幕轨场景，2026-10-31）**

| 事实 | 实测 | 影响 |
|---|---|---|
| 只启用目标轨后 Append | 正确落入该轨（目标 ST1→ST1，目标 ST2→ST2） | 启用态定向**本身是有效的** |
| 启用失败（方法名错 / 新轨未就绪） | Append 落到上一条启用轨 | 落到**非空轨**会把整批字幕**顺移到该轨末尾**（实测首帧 30→420） |
| `AddTrack` 后立刻启用 | 有时不可用（需回读确认） | 落轨前必须 `_enable_and_verify`，否则赌运气 |

**v4.1.0 真机穷举补充（Resolve Studio 21.1.2.10，2026-10-31）**

| 尝试 | 实测返回 | 结论 |
|---|---|---|
| `TimelineItem.SetName("x")` | **False**（3/3 条全部失败，`GetName` 不变） | 不能改字幕文本 |
| `TimelineItem.SetProperty("Name"/"Text"/"Subtitle"/"Title"/"String"/"Content", "x")` | **False**（6 个键全失败） | 没有可写的文本属性 |
| `TimelineItem.SetClipProperty` | 方法不存在 | — |
| `Timeline.ImportSubtitleFromFile` / `ImportSubtitle` / `AddSubtitle` / `ImportSRT` | 属性均为 **None** | 时间线级字幕导入不存在 |
| `MediaStorage.AddItemListToMediaPool([srt])` | **可用**，返回 `list[1]` | ImportMedia 全挂时的第三兜底候选（本轮未接线） |

> **结论**：21.1.2 上**没有任何原生 API 能修改字幕文本**。
> 所以"导出 SRT → 重导入 → `AppendToTimeline`"**不是绕路，而是唯一受支持的路径**。
> `land_inplace` 里的 `SetName` 直改只作为**未来版本的前向尝试**：回读失败即把
> `_SETNAME_CAPS["ok"]` 置 False，本会话后续直接走 SRT 往返，不再逐条试错。

> 官方文档来源：DaVinci Resolve Scripting API Doc v21.0.4 —— `AddTrack(trackType, optionalSubTrackType)` / `DeleteTrack(trackType, trackIndex)` / `SetTrackEnable(trackType, trackIndex, Bool)` / `GetIsTrackEnabled(trackType, trackIndex)`。

**Fusion UI（UIDispatcher）事实（v4.7 / v4.8 重构期间实测，2026-10-31）**

| 事实 | 实测 | 影响 |
|---|---|---|
| 启动阶段（主窗口 `Show()` 之前）第二次 `dispatcher:AddWindow` | **静默返回 nil**，日志无报错 | 设置/日志不能做成"启动就建好、用时 Show" |
| 点击事件里 `AddWindow` | 正常 | 与「关于」「选择字幕」「诊断」同一机制，是唯一可行时机 |
| 窗口关闭事件绑定 | 必须 `w.On["窗口ID"].Close = function() ... end` | `w.On.Close` **永不派发**；处理器里 `return false` 还会取消关闭 |
| 弹窗几何 | `w.Geometry` 可读可写（`{x,y,w,h}`） | 记住大小靠它；**校验不能过严**（曾用 ≥560×380 把 428×538 判非法） |
| `Label` 宽度 | **按创建时的文本算死**，后续改字不会自动加宽 | 要显示的标签必须用"最长的初始文本"占位（如 `第 0000 行 00:00:00:00`） |
| `local function` 可见性 | 只对其**之后**的代码可见 | 事件处理器里调用"后面才定义"的函数会解析成全局 nil → 处理器抛错、后续语句不执行（曾导致弹窗关不掉） |
| `TextEdit` 文本 | `PlainText` 可读写，`Text` 亦可 | 日志/热词统一写 `PlainText` |
### 6.7 v4.0.8 — 真机复验暴露的第四项落轨缺陷（已修）

> 触发场景：v4.0.7 部署后跑 `verify_v407_live.py` 真机复验。**前三项断言全部通过**（重复落轨零新增轨、失败不污染边车、relang 精确删旧重挂），但第 3 步（失败落轨）暴露了新缺陷。

#### 6.7.1 缺陷 4 — 落轨失败时留下孤儿空字幕轨

- **真机日志**：
  ```
  [land] 对齐校验失败：字幕轨上没取到任何条目
  [land] 落轨 0 条，保留原边车不动
  → result.track = 2              ← 失败落轨竟然新建了字幕轨 2！
  → 脚本清理后 sub_tracks_left = 2, nonempty_left = []   ← 空 ST2 留在时间线上
  ```
- **根因**：`land_inplace` 的「没有可用空轨 → `AddTrack` 新建」分支，**落轨失败后不回收自建轨**。这与 v4.0.6 真机日志「整轨清除专用字幕轨 1 旧条目 53 条 → 已挂到 0 条 → 边车写成 track=2」是**同一条路径** —— v4.0.7 的修复 1/2 只堵住了「有边车时复用专用轨」，没堵住「落轨失败后自建轨变孤儿」。
- **修复（方案 A，经用户拍板）**：
  | 子项 | 做法 |
  |---|---|
  | **修复 4** | 记录本次是否自建轨（`self_created`）；落轨失败、或实际落点≠自建轨、且该轨为空 → `DeleteTrack("subtitle", idx)` 回收 |
  | **修复 4a** | SRT 条数为 0 → **在动时间线之前就早退**，连字幕轨都不碰（比"落失败再回收"更彻底）|
  | 配套 | `write_lua` 导出 `reclaimed_track`；`relang_subtitles` 恢复启用态时跳过已回收的轨 |
- **安全约定**：只删**本次自建**且**当前为空**的轨；`_delete_track()` 内部再校验一次「非空轨一律拒删」；删除的总是最后一条 → 无索引位移问题。

#### 6.7.2 真机验证（v4.0.8 部署后）

`engine\verify_v408_live.py` 专项探针，实测输出：
```
[land] 新建字幕轨 2（此前没有可用的空轨）
[land] 对齐校验失败：字幕轨上没取到任何条目
[land] 已回收本次自建的空字幕轨 2        ← ★ 孤儿空轨被回收
→ sub_tracks_before: 1, sub_tracks_after: 1   ← 轨道数没变
→ reclaimed_track: 2
```
| 断言 | 结果 |
|---|---|
| 失败落轨轨道增量 | `failed_land_growth = 0` ✅ |
| 确实回收了自建轨 | `reclaimed_track = 2` ✅ |
| 空 SRT 早退 | `empty_srt_growth = 0`，诊断"SRT 无有效字幕条目（0 条），未落轨" ✅ |

#### 6.7.3 v4.0.8 顺带修好的两个 `sync_dev.py` 缺陷

| 缺陷 | 现象 | 修复 |
|---|---|---|
| engine 部署漏文档类文件 | `使用说明.txt` 住在 `03_文档\` 而非 `01_源码\`，engine 部署循环只复制 `.py` / `AsrSubs.lua` → engine 里永远停在旧版本 → 漂移检查长期报差异 | engine 部署改为遍历完整 `CANON` 列表，文档类文件从 `03_文档\` 取源 |
| 汇总复用部署前的检查结果 | 部署明明成功，末尾汇总却报"不一致" | 部署后**重新跑一遍**三项校验再汇总 |

---

### 6.8 v4.0.9 — 字幕轨清单（Fusion UI 路线）

#### 6.8.1 起因与结论：「缩最小」这条路走不通

用户需求（2026-10-31）：*"动 st1/st2 等字幕轨道的时候，把字幕轨道默认缩最小，这样第一时间知道有几条轨道，不被遮挡。"*

**排查结论：Resolve 21.1.2 官方 API 没有任何轨道高度 / 折叠能力。** 证据链（四路交叉验证）：

| 验证手段 | 结果 |
|---|---|
| 本地权威 `DaVinciResolveScript.pyi` 全文 | `Timeline` 类 **72 个成员**全查过，**无** `SetTrackHeight` / `Collapse` / `Expand` / `Fold` / `Compact` 之类方法 |
| 真机 `dir(timeline)` 内省（21.1.2 实跑） | 同样 72 个成员，**无**任何高度/折叠方法。`hasattr` 对任何名字都返回 True —— **SWIG 万能属性壳，`hasattr` 不可信**，必须用 `dir()` 或 `getattr() is None` 判定 |
| 工程设置键扫描 | `timeline:GetSetting()` / `project:GetSetting()` 共 **159 项**，只有 `limitSubtitleCPL`（每行字数）这类内容限制，**无轨道高度键** |
| UI Automation 扫描控件树 | Resolve 是纯 Qt 渲染，控件树**不对外暴露** —— `uiautomation` 只拿到 **5 个顶层控件**，无法定位任何折叠控件 |
| 5 个悬停点截图实证 | 悬停 ST1 徽标 / 左侧竖条 / 字幕气泡图标 / 时间线左上角 / V1 徽标 —— **字幕轨道头上根本不存在三角形折叠箭头**，5 张图完全一致 |

> **结论**：字幕轨道头**没有**折叠控件，官方 API **也调不出**折叠/高度。
> 于是"缩最小"改为等价目标：**在插件面板里自绘一份字幕轨清单**。

#### 6.8.2 路线选型：Fusion UI


| 线路 | 用途 | 关键调用 |
|---|---|---|
| **A · Resolve Scripting API** | 读时间线字幕 / 写字幕到时间线 | `resolve:GetProjectManager()` → `GetCurrentTimeline()`；`GetTrackCount("subtitle")`、`GetItemListInTrack`、`GetIsTrackEnabled`、`SetTrackEnable`、`AddTrack`；`ImportMedia` → `AppendToTimeline` |
| **B · Fusion UI 自绘窗口** | **它的"多字幕展示"** | `resolve:Fusion()` → `fusion.UIManager` → `bmd.UIDispatcher(ui)` → `disp:AddWindow{...}` → `ui:Tree{ ColumnCount=3 }` |

**关键发现：它的"多字幕展示"≠ 时间线多轨并显**，而是把字幕**抽出来**在自绘窗口里平铺成三列表格（序号 / 起止时间码 / 文本）。

而且它**自己也是单轨逻辑** —— 读取时 `L2093` 有 `break`，只读第一条「已启用且非空」的轨；全文件 0 处 `trackIndex` / `sourceTrack`，**没有多轨并存的数据结构**。它也没有任何折叠/隐藏实现（全文件 0 处 `collapse` / `height` / `compact`）。

**本项目采纳线路 B**，理由：**AsrSubs.lua 本来就跑在 Fusion UI 上下文里**（`L1014` 取 `resolve:Fusion()`、`L12-13` 注释已写明 `resolve:Fusion().UIManager → bmd.UIDispatcher(ui)`、`L1344+` 已在用 `ui:ComboBox` / `ui:CheckBox` / `ui:Button`），**零新增依赖**。

#### 6.8.3 实现

| 位置 | 内容 |
|---|---|
| UI（顶部第二行） | `ui:HGroup{ ID="RowTrackInfo" }` = `ui:Label{ ID="SubTrackInfo", Font=11px }` + `ui:Button{ ID="RefreshTrackBtn", Text="刷新" }` |
| `App:ReadSubTracks()` | 读 `GetTrackCount("subtitle")` → 逐轨取「条目数 / 启用态 / 轨名」，全部 `pcall` 包裹 |
| `App:RefreshSubTrackInfo(showLog)` | 拼 `字幕轨 N 条：ST1(启用·53条)｜ST2(禁用·0条)`，写 Label；`showLog=true` 时同时追加到界面日志（Label 被裁时可复制查） |
| 刷新时机 | ① 开窗（`GetItems()` 成功后）② 落轨完成（landing 阶段成功/失败后）③ 点「刷新」按钮 |
| 当前时间线 | 每次刷新都**重新取** `GetCurrentTimeline()` —— `App.timeline` 是开窗那一刻的快照，Resolve 里换集后会过期 |

#### 6.8.4 API 事实（本次新踩）

| API | 真实行为 |
|---|---|
| `GetIsTrackEnabled("subtitle", i)` | 对**不存在**的轨返回 `nil`。Lua 侧判定必须写成 `en = (raw == true)` —— 写成 `raw ~= false` 会把 `nil` **误判成"启用"**（虚报） |
| `GetTrackName("subtitle", i)` | `.pyi` 签名支持 `'subtitle'`，但真机可能返回**空串**或**抛异常** → 必须 `pcall` + 空串回退成 `ST编号` |
| `GetItemListInTrack("subtitle", i)` | 抛异常时条数按 0 处理，不让单条轨的异常拖垮整份清单 |
| Fusion `ui:Label` | **宽度按【创建时】的文本算死**，不随后续改字自动加宽 → 初始文本必须写足（这里按 3 条轨的宽度写），否则刷新后末尾被裁掉 |

#### 6.8.5 离线单测方法（可复用）

`engine\test_subtrack_info.py`（**7 用例**）：不抄副本，而是**从 `AsrSubs.lua` 源码里正则抽取** `ReadSubTracks` / `RefreshSubTrackInfo` 两个函数的**真实文本**，塞进 `lupa` 的 Lua 运行时里跑，假 timeline 对象喂边界情况。

```bash
pip install lupa
python test_subtrack_info.py
```

覆盖：三轨混合 / `nil` 启用态 / 轨名异常回退 / 真实轨名 / 0 条轨 / 条目读取异常 / 计数与数组不一致。

> **踩坑**：lupa **不把 Python list 的 `__len__` 暴露给 Lua 的 `#` 运算符** —— 假对象必须返回 `lua.table_from([...])`，否则条数恒为 0（第一版单测就栽在这）。

### 6.9 v4.1.0 — 落轨顺序铁律 + API 优先直改

> 起因：落轨历史上反复出现「落 0 条 / 错位」。对照同类工具（剪包助手）的真机取证后，
> 定位到两个可复现的结构性原因，并按「API 优先、达不到再退回」的原则改造。

#### 6.9.1 顺序铁律（导入必须先于任何时间线写操作）

- **现象**：`ImportMedia` 失败时**返回空列表而不抛异常** → 表现为「落 0 条」。
- **根因**：脚本先动时间线（`SetTrackEnable` / `AddTrack` / `SetCurrentTimecode`）会触发
  Resolve 自动保存工程，紧接着调 `ImportMedia` 就偶发返回 `list/0`。
- **约束**：`import_to_pool()` 必须在 `land_inplace()` 的任何时间线写操作之前调用；
  `relang_subtitles()` 同理（先导入、再删旧、再重挂）。
- **实现**：`land_inplace(..., preloaded=(item, base))` 接收已导入的媒体项，
  避免二次导入把刚导入的同名项当「旧项」删掉。
- **回归锁**：`tools/test_land_order.py` 用例 1 / 6 —— 断言 `ImportMedia` 调用那一刻
  时间线的写操作日志必须为空。

#### 6.9.2 API 优先：`SetName` 直改文本（更新路径）

- Resolve 的字幕条目是 `TimelineItem`，`SetName()` 是唯一的原生文本写入口。
- **策略**：先试直改，回读校验不过就整批放弃、退回 SRT 往返。既吃到 API 的性能
  （不导媒体池、不动轨道），又不承担静默失败风险。
- **四个前置条件**（缺一即退回，见 `try_apply_texts_direct()`）：

  | 条件 | 原因 |
  |---|---|
  | 轨道 `owned=True`（整轨都是本插件字幕） | 万一中途失败，可由兜底整轨清掉重挂 |
  | 轨上条目数 == SRT 条数 | 直改无法新增/删除条目（删行必须走往返） |
  | 每条按起始帧（±2）都能配上 | 时间码变过就不是"纯文本更新" |
  | 每条 `SetName` 后 `GetName` 回读一致 | 部分版本返回 True 但不生效 |

- **失败记忆**：本会话一旦回读失败，`_SETNAME_CAPS["ok"]=False`，后续直接走往返，
  不再逐条试错。
- **回归锁**：`tools/test_land_order.py` 用例 4（直改不碰媒体池）/ 用例 5（回读失败护栏）。

#### 6.9.3 导入两轮回退

`import_to_pool()` 改为：删同名旧项 → 第 1 轮原路径 → 空则第 2 轮换目录副本
（**保文件名**）+ `RefreshFolders()` → 每轮回查 → 全败抛 `RuntimeError`。
副本只换目录不换名，是为了让「按名字 + 字幕类型」的旧项清理继续有效
（随机名会让池里堆清不掉的项）。

#### 6.9.4 `InsertIntoTimeline` 兜底

`AppendToTimeline` 后按 ID 差集判成败；没落上再试 `InsertIntoTimeline`（此时播放头
已被钉在时间线起点，语义等价）。判据始终不用返回值（21.1 会返回 None）。

#### 6.9.5 观测

`land_inplace` 结束打印单行摘要，便于统计失败率：

```
[land] result=ok|fail placed=.. total=.. track=.. first=.. srt_first=.. offset=.. aligned=.. reclaimed=..
```

#### 6.9.6 v4.1.1 — 多字幕轨落错轨的根因与加固

**根因**：`_set_track_enabled()` 只尝试 `SetTrackEnabled` / `SetTrackEnableState`，
而 21.1.2 上这两个属性都是 `None`。函数静默返回 `False`，调用方又忽略返回值，
于是「禁用其它轨、只启用目标轨」**从未真正执行**。单字幕轨时间线看不出差别，
一旦时间线上有用户手工字幕轨，`AppendToTimeline` 就会落到那条**仍处于启用态**
的轨上，并被顺移到该轨已有内容之后（实测首帧 30 → 420）。

**修复（三层）**：

| 层 | 做法 |
|---|---|
| 方法名 | `_set_track_enabled` 优先试 **`SetTrackEnable`**（无 d） |
| 启用校验 | 新增 `_enable_and_verify()`：启用后回读 `GetIsTrackEnabled`，最多 5 次，失败即放弃 |
| 落点校验 | 目标轨启用失败 / 非空 → `_abort()` 直接放弃；若仍落错轨，按 UID 删掉误落条目再报错 |

**测试**：`test_land_order.py` 用例 7（启用护栏）锁死「启不起来就不落、一条都不落」。

---

### 6.10 v4.2 → v4.8.14 — 界面重构（单页 + 设置弹窗）

> 时间：2026-10-31。从"左右两栏平铺"演进到"单页 + 独立设置弹窗"，
> 期间发了 4.2.0 ~ 4.8.14 共 20 个小版本，全部围绕**界面结构**与**参数读写链路**。

#### 6.10.1 最终布局

```
┌ v4.8.14 · Timeline 1 ───────────────────────────────┐
│ 字幕轨 N 条                        [刷新][清理空轨] │
│ 从音频创建字幕 [开始生成] [停止]   [媒体池字幕][时间线字幕] │
├──────────────────────┬──────────────────────────────┤
│ 权重词纠错            │ 字幕编辑                     │
│ ┌──────────────────┐ │ 查找[__][▶] 替换[__][替换][全部]│
│ │ 人参=人生,人叁     │ │ ☐全选 [反选] 命中N 已选x/y [删除]│
│ └──────────────────┘ │ ┌ 表格 ────────────────────┐ │
│ 热词                 │ └──────────────────────────┘ │
│ ┌──────────────────┐ │ ┌ 编辑框 ──────────────────┐ │
│ └──────────────────┘ │ └──────────────────────────┘ │
│                      │ ☐删行即剪            [撤销]  │
│                      │ [字幕更新][适配][粗剪][导出] │
├──────────────────────┴──────────────────────────────┤
│ 第 N 行 起→止 (时长)   就绪   时间码        [设置]   │
└─────────────────────────────────────────────────────┘
         ↓ 点「设置」（点击时才创建窗口）
┌ 设置 ───────────────────────────────────┐
│ 语言 [▾]        识别模型 [▾]            │
│ 模型路径 [_______][浏览…]                │
│ 每行字数 [24]                           │
│ ☐去除标点符号   ☐句末无标点              │
│ 运行日志                     [清空]     │
│ ┌─────────────────────────────────────┐ │
│ └─────────────────────────────────────┘ │
│ 改完直接关闭                     [关闭] │
└─────────────────────────────────────────┘
```

#### 6.10.2 控件清单（主窗口）

| 区域 | 控件 ID | 说明 |
|---|---|---|
| 顶行 | `TlName` | `AsrSubs vX.Y.Z · 时间线名`，换时间线时随 `RefreshSubTrackInfo` 刷新 |
| 轨道行 | `SubTrackInfo` / `RefreshTrackBtn` / `CleanTrackBtn` | 轨道清单、刷新、清理空轨 |
| 生成行 | `StartBtn` / `StopBtn` | 「开始生成」「停止」 |
| 载入行 | `LoadPoolBtn` / `LoadTrackBtn` | 「媒体池字幕」「时间线字幕」 |
| 左栏 | `CorrEdit` / `HotEdit` | 权重词（可编辑）/ 热词（只读多行，自动换行） |
| 右栏 | `FindInput` `FindNextBtn` `ReplaceInput` `ReplaceOneBtn` `ReplaceAllBtn` | 查找替换 |
| 右栏 | `SelectAllChk` `InvertBtn` `HitLbl` `SelCountLbl` `DelSelBtn` | 选择工具条（条数只在这里显示） |
| 右栏 | `SubTree` / `SubEdit` | 表格 + 编辑框 |
| 右栏 | `AutoCutChk` / `UndoBtn` | 删行即剪（最左）/ 撤销（最右） |
| 右栏 | `UpdateSubsBtn` `LandBtn` `CutBtn` `TxtBtn` | 四个动作，等宽右对齐 |
| 底行 | `RowInfoLbl` / `Status` / `StatusTC` / `SetupBtn` | 行详情（左）/ 状态 / 时间码 / 设置（右） |

> **刻意删掉的常驻文字**（信息已在别处或改为悬停）：
> `已写回 SRT`（改悬停「字幕更新」+ 日志）、`SRT 有改动`（同）、
> `字幕编辑器已载入 N 条`（表格上方已有「已选 x / y 行」）、
> `将切除 N 段` 与切除被拦原因（改悬停「视频粗剪」）、
> 括号注释 `(当前时间线没有字幕轨)` `(正确词=错误写法)` `(识别后自动统计，可复制)`。

#### 6.10.3 设置弹窗（关键实现）

```lua
App.EnsureSettings = function()           -- 定义在 BuildUI 内，闭包持有 ui/dispatcher
    if App.S.winSet then return App.S.winSet end   -- 只创建一次，之后反复 Show/Hide
    -- 1) 读几何 rt\set_win.txt（容错：仅挡 <320×260）
    -- 2) dispatcher:AddWindow{ ID = "AsrSubsSettings", Geometry = geo, ... }
    -- 3) GetItems() → App.S.setItems（参数控件都在这里）
    -- 4) 用 App.S.cfg 回填控件；改动 → syncSet() 写回 cfg + uiSnap + 存盘
    -- 5) w.On["AsrSubsSettings"].Close = ...  （必须按窗口 ID 绑）
    -- 6) Hide()，等待用户点「设置」
end
```

**参数真源**（v4.8.0 起的核心设计）：

```
配置文件 ──readSettings──▶ App.S.cfg ──▶ 开始识别只读 cfg
                              ▲   │
                    syncSet() │   └──▶ App.S.uiSnap（控件读不到时的兜底）
                              └── 设置弹窗里的控件
```

- 弹窗**没打开过**也能正常识别（读 `cfg` / `uiSnap`）
- 控制台/主窗口不再持有参数控件，`App.S.items.LangCombo` 等一律为 nil
- 启动时**禁止**再往主窗口填下拉（会报 `attempt to index field 'LangCombo'`）

**几何持久化**：`App.SaveSetGeo` 每秒随 Tick 存一次（不依赖关闭事件），
文件 `engine\rt\set_win.txt`，内容 `x,y,w,h`。

#### 6.10.4 期间踩过的坑（都已修，留作前车之鉴）

| # | 现象 | 根因 | 修复版本 |
|---|---|---|---|
| 1 | 设置/日志按钮报「窗口不可用」 | 启动阶段建第二个窗口返回 nil | 4.7.1 改同窗口折叠 → 4.8.0 改点击时创建 |
| 2 | 「删行即剪」永远提示"没有视频轨" | 读 `App.S.timeline`，而实际缓存字段是 `App.timeline` | 4.4.1 |
| 3 | 弹窗「关闭」「X」都点不动 | `saveGeo` 定义在事件之后 → 处理器调用到全局 nil → 抛错，`Hide()` 不执行 | 4.8.4（提前定义）/ 4.8.5（改按 ID 绑 Close） |
| 4 | 每行字数改了不生效 | 弹窗控件读不到 + 开始识别读的是主窗口控件 | 4.8.4（Text/PlainText 双读 + cfg 优先） |
| 5 | 弹窗大小记不住 | 恢复时代码要求 ≥560×380，把 428×538 判成非法 | 4.8.14（放宽到 ≥320×260） |
| 6 | `SRT 有改动` 删了还显示 | 只改了运行时赋值，标签的**初始文本**仍是旧值，启动不调 `RefreshDirty` | 4.8.13（整控件删除） |
| 7 | 热词文字溢出左栏 | `LineEdit` 单行不换行 | 4.8.2（改只读 `TextEdit` + `PlainText`） |
| 8 | 点表格行编辑框没内容 | 富文本渲染失败被 `pcall` 吞掉 | 4.8.3（纯文本兜底） |

### 6.11 v4.8.24 — 全流程体验优化 11 项（2026-11-01）

起因：用户卸载重装后，从「开始生成」到「字幕上轨」完整跑了一遍，逐环取证。
识别算法（模型 / VAD / 对齐）**零改动**，改的全是「用户能感知的那一层」。

#### 6.11.1 环境选型：GPU 版成为默认

`installer.py` 原来把「运行版本」写死默认 CPU 版，且全文件没有任何显卡探测。
同一台 RTX 5060 机器两次安装都拿到了 `torch 2.15.0+cpu`，实测实时系数 0.44~0.51
（116 秒素材识别 50~63 秒）。现加 `detect_nvidia()`（只读 `nvidia-smi`，失败按无卡处理），
有卡则 `var_gpu=True` 并在选项文案里写出型号。

> 提速倍数**未实测**：需要真的装一次 GPU 版再用同一条素材对比。安装器文案写的是"快数倍"，
> 具体数字以实测为准，不要在文档里编。

#### 6.11.2 热词：没有 jieba 就明说，不要造伪词

`hotwords.py` 的降级分支原来是"连续汉字二字滑窗"，缺 jieba 时实测产出
「位朋 6」「里最」这类伪词（5 个热词里 2 个看不懂）。现在：

- `PY_DEPS` 补 `jieba`（纯 Python 包，无编译）；
- 降级分支直接返回空，`write_lua` 多写一个 `no_jieba = true`；
- Lua 侧 `RefreshHotwords` 见到该标记把左栏标题写成「热词（需 jieba，未安装）」。

#### 6.11.3 字幕排版四刀（`asr_engine.py`）

| 毛病（实测） | 改法 | 落点 |
|---|---|---|
| 整条只有一个「。」，0.64s，还污染导出文稿（"皓月当空，。"） | `split_sentences` 第 5 步：无字母/数字/汉字的片段并回前一条；跨片段的由 `merge_punct_only_blocks` 兜 | `split_sentences` / `merge_punct_only_blocks` |
| 断词：「我代 / 表云岭」「中秋祝 / 福。」 | 折行断点优先用强制对齐的词边界：`_word_char_cuts` 把词序列换算成字符下标，喂给 `_wrap_line` | `_wrap_line(…, word_cuts)` |
| 单条占 6.20 秒，下一句只剩 0.80 秒 | `resplit_overlong_blocks`：>4 秒且字数够多就按标点/词边界再切一刀，切点时间吸附到最近词边界 | `_timed` |
| 9 条 <0.79 秒，最短 0.12 秒（"心"） | `merge_tiny_blocks`：<0.4 秒的碎片并进前一条（`enforce_min_duration` 只能延长到撞下一条，撞上就只能退让） | `_timed` |
| 长停顿断行切出 18 列的行（「时值中秋佳节，皓」+「月当空，万家团圆。」），连带把「皓月」拆开 | `split_at_long_pauses` 增加宽度闸：任一半超宽就放弃这次切分 | `split_at_long_pauses(…, max_chars)` |
| 上游三条路都判断了宽度，8 字档仍漏 2 条 18 列的行 | 出口统一兜一次：写 SRT 前对超宽块重新折行（时间按字符数摊） | `refold_overlong_text`（run 末尾） |
| 并条把行拼回 9~12 字（8 字档实测 8 条超字数） | `merge_tiny_blocks` / `merge_punct_only_blocks` 加 `max_width`，放不下就不合并 | 同上两个函数 |

两条路径都接上了：新识别在 `_process_segment` 拿到词边界后**重切一次**句子；
缓存命中走 `_blocks_from_text`，用缓存里的词表算边界。算不出词表时行为与 4.8.23 完全一致。

实测（同一素材 116.5 秒，24 字档）：纯「。」条目 1→0、最长条 6.20→3.96 秒、
超 4 秒只剩 1 条 4.52 秒、<0.4 秒只剩 1 条（末尾降级段）；日志会打
「碎片并条：N 条」「长条再切：N 条」。

#### 6.11.4 交互可达性

| 问题 | 改法 |
|---|---|
| 落轨 / 导入 / 粗剪 / 导出四个阶段「停止」是灰的（粗剪最长干等 300 秒） | `StartLand` / `StartCut` / `StartExport` 各补一句 `StopBtn.Enabled = true` |
| 导入媒体池超时后提示"完成 N 条"但表格空白、「适配字幕」点不动 | `importing` 超时分支、`StartLand` 的两个失败分支各补 `LoadSubs(true)` |
| 识别时长时间停在「识别中… 3%」 | `status.lua` 增 `msg` 字段（抽音频 / 加载模型 / 第 N 段），Lua 优先显示文案 |
| 导出静默覆盖同名文件 | `export_files.py` 覆盖前先存 `<名>.<时间戳>.bak`（与 `subs_io.save` 对齐） |
| 全新安装后模型下拉为空 | 安装器 `write_model_manifest()` 部署时直接写 `models.lua` / `models.txt` |
| 每开一次窗口就写一条「下拉项填充 ok=false」 | 删掉 `BuildUI` 里启动阶段必然 `error()` 的死代码填充块（控件填充本来就在 `EnsureSettings`） |
| 诊断看不出跑的是 CPU 还是 GPU | `diag.py` 读 `torch/version.py`（不 import torch）拿 `+cpu`/`+cu` 变体，环境信息增「推理设备」一行 |

### 6.12 v4.8.25 — 第三轮复测后补的 5 项（文件容错 + 长任务可预期性）

起因：v4.8.24 落地后按「从识别到上轨」再跑一轮，专门打之前没测的环节
（停止按钮、断点续跑、10 分钟长素材、2000 条字幕、编码/坏文件、只读文件）。
本轮不碰识别算法，全部是输入输出与交互层面的修补。

#### 6.12.1 编码嗅探：UTF-16 不再静默变空

实测：同一份 SRT 存成 UTF-8 / UTF-8 BOM / GBK / UTF-16，前三种各读出 2 条，
**UTF-16 读出 0 条且退出码 0**。原因是 `read_text_any` 的探测顺序
`utf-8-sig → utf-8 → gbk → latin-1`：UTF-16LE 的字节流（每字符后跟 `\x00`）在 GBK 下
"恰好合法"，被解成乱码后时间码正则全部失配。记事本「另存为 → Unicode」正是这个格式。

现在 `read_text_any`（`subs_io.py:231`）：

1. **先看 BOM**：`\xff\xfe` / `\xfe\xff` → UTF-16（LE/BE/带 BOM 三种都试）；
   `\xef\xbb\xbf` → UTF-8-sig；
2. **无 BOM 的 UTF-16**：前 4 字节里出现 ≥2 个 `\x00` 就试 utf-16-le / utf-16-be
   （用「解析出的文本含 `-->`」当判据）；
3. 其余按 utf-8 → gbk 依次试，**每种都要过「含 `-->` 或文件本身为空」自检**，
   不再让 latin-1 那种"永远成功"的编码混进来；
4. 全都不像就返回最后一次结果，交给上层报"格式无法识别"。

实测：UTF-8 / GBK / UTF-16（带 BOM）/ UTF-16BE（无 BOM）均正确读出 2 条。

#### 6.12.2 解析失败不再无声

`dump` 分支原来只打印 `OK dump 0 blocks`，界面就是个空表格 ——
用户分不清「文件本来就是空的」和「编码/时间码坏了」。
现在文件有内容却解析出 0 块时写 `subs_err.lua`（`write_err_mark`），
Lua 侧 `LoadSubs` 读到就 `Log("⚠ …")` + 状态栏「字幕文件解析失败，详见日志」。
`LoadSubs` 发起前会先删这个标记，避免把上一轮的报错当成本轮结果。

实测：时间码写成 `00:00:0X,abc` 的文件 → 0 条 + 标记写入；正常文件 → 标记为空。

#### 6.12.3 写回失败说人话

SRT 正被记事本 / PR / 播放器打开时（Windows 常见），`os.replace` 抛
`PermissionError: [WinError 5] 拒绝访问`，原来界面直接吃一屏英文堆栈。
现在 `save` 分支包 try/except，输出：

```
ERR 字幕文件被占用或为只读：<路径>
    原文件未改动，可放心；请关闭正在打开它的程序后重试。
```

「原文件未改动」放在**最后一行**——Lua 取的是日志最后一行。
实测只读文件：退出码 4、stderr 为空、原内容完好、`.bak` 已生成。

#### 6.12.4 停止不再连预热服务一起杀

`worker` 的 `kill` 原来一律 `kill_all()`，里面包含 `_kill_resident_server()`。
用户点「停止」本意只是停当前识别，结果把预热好的常驻 ASR 服务也杀了，
下次识别又要重新加载模型（实测多等约 12 秒）。

改成两条命令：

| 命令 | 谁发 | 行为 |
|---|---|---|
| `kill` | `App:KillTasks()`（停止按钮 → `Cleanup(true)`） | 只停子任务，**保留**常驻服务 |
| `killall` | `App:KillTasks(true)`（关窗口 / 退出 → `Cleanup()`） | 连常驻服务一起收 |

`Children.kill_all(include_server=True)` 用参数控制，`quit` 分支仍全杀。
状态栏文案也随之改了：「已停止（识别模型保持加载，下次更快）」。

实测（干净环境、假常驻服务）：`kill` → `killed=0` 且服务存活；
`killall` → `killed=1` 且服务被收。

> ★ 测试时踩到的坑：如果机器上有**残留的旧 worker 进程**，它会和新 worker
> 抢同一份 `cmd.json`，表现为"kill 把服务杀了、killall 报命令缺字段"。
> 排查办法：先给旧 worker 发一条 `quit` 再启动新的，确认只有一个 worker 在跑。

#### 6.12.5 跨片段碎片合并 + 剩余时间预估

**碎片**：`merge_tiny_blocks` 只在 `_timed`（片段内部）调用，而实测那批
0.30 秒的「好，可以。」正好是每个 VAD 片段末尾的降级段，自成一个片段 →
片段内没有"前一条"可并，10 分钟素材里重复出现 5 次。
现在 `run()` 出口在 `merge_punct_only_blocks` 之后再调一次
`merge_tiny_blocks(blocks, max_width=max_chars*2)`。
实测 116 秒素材：<0.79 秒条目 1 → **0**。

**ETA**：状态栏按「已用时间 / 当前百分比」推算剩余，跑过 15 秒且进度过 15% 才显示
（前期含模型加载，估不准），取整到 10 秒避免数字乱跳。文案形如
「正在识别第 3/14 段…（58%，约剩 1 分 10 秒）」。纯 Lua 计算，不动引擎。


### 6.13 v4.8.26 — worker 单实例保护 + 真机端到端复验（2026-11-01）

#### 6.13.1 为什么必须只允许一个 worker

`rt\cmd.json` 是**单槽**通道：谁写谁覆盖，谁读到谁执行。两个 worker 同时活着时，
**同一条命令会被其中一个抢走**，另一个只看到"命令消失"。

这不是理论风险。v4.8.25 验证 `kill` / `killall` 语义时实测撞到：

| 现象 | 真因 |
|---|---|
| `kill` 把常驻 ASR 服务也杀了（应为保留） | 抢到命令的是**上一轮残留的旧 worker**，跑的是老逻辑 |
| `killall` 回执报「命令缺少 script 字段」 | 新 worker 按 `killall` 解析前，命令已被旧 worker 消费掉 |

两小时排查方向全在代码上，最后是靠「先给旧 worker 发 quit 清场」才复现出正确行为。
真正该修的不是命令语义，而是**进程唯一性**。

#### 6.13.2 实现：pid 文件 + 映像名双重校验

`worker.py` 新增 `acquire_single()` / `release_single()` / `_read_pid_file()` /
`_is_worker_pid()` / `_pid_alive()` / `_proc_image()`，锁文件 `rt\worker.pid`：

1. `os.open(PID_FILE, O_CREAT|O_EXCL|O_WRONLY)` 原子创建 —— 只有一个进程能成功；
2. 创建失败先读文件：`pid` **还活着且映像名含 python** → 本进程让路，
   `errlog("已有 worker 在跑（pid=…），本进程直接退出")`，**退出码 0**；
3. 否则视为残锁（上次被强杀/崩溃），删掉重抢，并记一行「清理残留的 worker.pid」；
4. 退出（含 `quit`、空闲自退、异常）走 `finally: release_single()`，
   只删自己写的那个 pid。

三个容易写错的点，都踩过：

| 坑 | 说明 |
|---|---|
| 不能用 `os.kill(pid, 0)` 判活 | Windows 上 `os.kill` 走 `TerminateProcess`，`sig=0` 会**直接把那个进程杀掉**。改用 `OpenProcess(SYNCHRONIZE)` + `WaitForSingleObject(h,0)==WAIT_TIMEOUT` |
| 只判"活着"不够 | PID 会被系统复用，指到一个无关的活进程上就永远误判"有人在跑"，worker 再也起不来。必须 `QueryFullProcessImageNameW` 确认映像名是 python |
| 闸门不能放在清理之后 | `_main_loop` 开头会删 `res.lua` 和上一轮 `rc_*.lua`。重复拉起的进程若执行这些删除，会**打断正在干活的 worker**，表现为"命令刚发出去就没了"。所以闸门在 `main()` 里、任何清理之前 |

结构上把原 `main()` 主体改名为 `_main_loop()`，新 `main()` 只做三件事：
抢锁 → 跑 `_main_loop()` → `finally` 释放。

#### 6.13.3 验证（`ux_probe\test_single_instance.py`）

| # | 断言 | 实测 |
|---|---|---|
| ① | 第一个 worker 正常起、pid 写进锁 | pid=23508，心跳就绪 |
| ② | 第二个 worker **0.1 秒**内退出、退出码 **0**、进程不在、不碰锁 | 退出码 0，0.1s，锁仍指向 A |
| ② | 第一个不受影响（还能收命令） | `ping` → `{id=101, ok=true, ping=true}` |
| ③ | 强杀 A 留残锁 → 新 worker 能接管并记日志 | 就绪，日志含「清理残留」，锁改指新 pid |
| ④ | 正常 `quit` 后锁被删除 | 进程退出、`worker.pid` 不存在 |

#### 6.13.4 真机端到端复验（v4.8.26 装机版，Resolve 21.1）

素材 116.5 秒、单片段、24 字档；工程「新建项目 1」，新建时间线「AsrSubs完整流程验证」。

| 环节 | 实测 | 判定 |
|---|---|---|
| 识别 | 62.2 秒、40 条、纯标点 0、重叠 0、短于 0.4 秒 0、最长 4.52 秒 | 正常（这条素材前几轮 47.9~55.3 秒，本轮与 Resolve 抢 CPU 偏慢） |
| 落轨 | `placed=40 track=1 replaced=0`，1.8 秒；时间线**未新建**、视频片段未动、字幕轨 1 条 | 通过 |
| 重复落轨 | `placed=40 replaced=40`，1.6 秒；轨道仍 40 条、重复区间 0 | 通过（不叠字） |
| 粗剪（删第 10 条 31.02~31.82） | 视频 1 段 → **2 段**，字幕清 40 挂 **39**、重复区间 0、被删条起点已清除，1.9 秒 | 通过 |
| 字幕更新（relang） | `placed=39 replaced=0 cleared=39`，1.6 秒；轨道首条文本已变成改后内容，时间线未新建 | 通过 |

#### 6.13.5 ETA 实测：文案没问题，早期估值偏高

验证方法：把 `AsrSubs.lua` 里 ETA 那 18 行**原样抽出来**，用 lupa 在每个真实采样点
（`status.lua` 的 pct + 实际已用秒数）重放，拿到"界面会显示的那串字"，再与实际剩余比较。

| 已用 | pct | 状态栏显示 | 实际还需 |
|---|---|---|---|
| 1s | 3 | 正在加载识别模型（首次较慢，之后会复用常驻服务）…（3%） | 61s |
| 18s | 19 | 正在识别第 2/14 段…（19%，约剩 1 分 20 秒） | 44s |
| 32s | 52 | 正在识别第 7/14 段…（52%，约剩 30 秒） | 30s |
| 45s | 72 | 正在识别第 10/14 段…（72%，约剩 20 秒） | 17s |
| 49s | 79 | 正在识别第 11/14 段…（79%，约剩 10 秒） | 13s |

- 「约剩」出现 11/16 个进度采样点；首次出现于 **18 秒 / 19%**，符合 15 秒 + 15% 的门槛；
- 后半段误差中位数 **3 秒**、最大 8 秒 —— 可用；
- **前段误差最大 36 秒**：18 秒时给「约剩 1 分 20 秒」，实际只剩 44 秒。
  原因是"已用时间"里含模型加载（约 7 秒），而百分比不含 —— 分母被算大了。
  已知偏差，尚未修（见 §9 待办）。

---

### 6.14 v1.0.0 — 版本号重置 + ETA 重算 + 粗剪清轨改判据（2026-11-01）

#### 6.14.1 版本号规则变更

`4.8.26` 直接改名为 `1.0.0`，此后三段式：

| 变更类型 | 动哪一位 | 例子 |
|---|---|---|
| 小更新（修补、文案、单点修复） | 最右 | 1.0.0 → 1.0.1 |
| 重要更新（成组功能、较大修复） | 中间，最右归零 | 1.0.1 → 1.1.0 |
| 超大型更新（架构调整、不兼容） | 最左，后两位归零 | 1.1.0 → 2.0.0 |

历史 4.x 记录保留原样不改。`sync_version.py` 不校验格式，`1.0.0` 一路写入 5 处均正常
（Inno Setup 的 `AppVersion` / `VersionInfoVersion` 也吃三段式）。

#### 6.14.2 剩余时间（ETA）：从"已用/百分比"改成加权速率

**旧实现的偏差**：`left = el*100/pct - el`，而 `el` 含模型加载（实测 7~17 秒）、`pct` 不含。
116 秒素材 19% 时给「约剩 80 秒」，实际只剩 44 秒。

**试过三条路，只有第三条两头都站得住**（同一批采样点回放，误差按界面实际显示的字算）：

| 方案 | 116 秒素材 中位/最大 | 10 分钟素材 中位/最大 |
|---|---|---|
| 旧：已用/百分比 | 7s / 73s | 13s / 61s |
| ① 锚点速率（越 25% 才开始算） | 3s / 15s | 13s / **788s**（某段几乎没进度，速率趋 0） |
| ② 扣掉加载后全程均速 | 4s / 13s | 7s / 146s（前 25% 偏快，外推过低） |
| ③ **滑窗 60s×0.7 + 全程均速×0.3** | **3s / 11s** | **15s / 61s** |

选 ③。实现要点（`AsrSubs.lua` 的 `App:EtaText`）：

- 历史必须**每拍都记**（同一秒只记一次）。原来写的是"pct 变了才记"，结果某个片段卡住
  十几秒时，窗口里找不到"60 秒前的参照点"，会一路退到很早以前，算出完全过时的速率。
- 门槛：`pct >= 15` 且「扣掉加载后 ≥20 秒」才显示；扣掉的加载段 = 第一次看到 `pct>=6`
  的时刻（bridge 在 `load()` 之后才报 6）。
- 显示：`>=60s` 取整到分钟、`10~59s` 取整到 10 秒、`<10s` 不显示。
- 抖动抑制：新值偏离上次估值超过 50% 就取两者中值。
- 每轮开始（`StartPython`）清空 `etaHist / etaLoadAt / etaLast`。

**验证方法**（`ux_probe\eta_lua_replay.py`）：把要发布的 `App:EtaText` 源码整段抽出来，
用 lupa 逐拍喂入真实采集的 `status.lua` 序列，拿"界面会显示的那串字"和事后已知的实际剩余比。

#### 6.14.3 粗剪清旧字幕：名字判据其实从没生效过

**真相**：`TimelineItem.GetMediaPoolItem()` 在字幕轨条目上**返回 None**（Resolve 21.1 实测）。
而 `_is_our_subtitle` 第一条就是「拿不到媒体项 → 返回 False」，所以"按来源媒体项同名"这条
判据一直只是偶尔走运；SRT 一改名，100% 清不掉。

**后果链**：清不掉 → `land_inplace` 发现字幕轨上有东西 → 新开一条字幕轨 →
真机复现 **39 条（轨1）+ 38 条（轨2）= 77 条，重复区间 38 处**。

**新判据**（`cut_inplace.clear_sub_track`，两级 + 比例门槛）：

| 情形 | 动作 |
|---|---|
| 名字对得上 | 直接清（老路径） |
| 名字对不上，时间码吻合 **≥90%** | 认定整轨是我们的，**整轨清**（连"本次被删掉的那条"幽灵也清） |
| 吻合 **60%~90%** | 只清吻合的那些，剩下的留着，日志说明 |
| 吻合 **<60%** | 判断不了归属，**跳过这条轨** |

时间码容差 0.35 秒（覆盖 pad 与帧取整）。`_looks_like_our_sub` **不再要求媒体池项**。

**为什么必须保守**：字幕清理发生在 `EndUndo` **之后**（视频剪辑的撤销组已经关了），
清错了 Ctrl+Z 也救不回来。所以宁可留旧字幕，也不误删用户内容。

**单测**（假对象，`ux_probe` 里四种情形）：整轨我们的 → 清 39 留 0；用户另一条轨 → 清 38
留 6；混合 7/10 → 清 7 留 3；2/6 → 跳过不动。

**真机验证**（删第 10 条 + 另存为 `ux_renamed.srt` 再粗剪）：

| | 修复前 | 修复后 |
|---|---|---|
| 字幕条数 | 77（两条轨） | **38（一条轨）** |
| 重复区间 | 38 | **0** |
| 日志 | 无认领记录 | 「清掉 39 条，其中 38 条靠时间码认领」 |

同名路径回归：落轨 39 → 重复落轨 replaced=39 不叠字 → 粗剪拆 2 段清 39 挂 38 无重复 →
relang 文本更新且不新建时间线，四段全过。

#### 6.14.4 本轮踩到的两个测试坑

- `MediaPool.CreateTimelineFromClips` **不保证把新时间线切成当前**，脚本里必须
  `project.SetCurrentTimeline(tl)` 并断言，否则后续操作全落在上一条时间线上（本轮因此
  得出过一次假的"叠字"结论）。注意 `ProjectManager.SetCurrentTimeline` 在 API 里是 `None`，
  要用 `Project` 上的那个。
- `Timeline.DeleteClips(items)` 有可能改动传进去的列表，`n += len(mine)` 会数出 0。
  先取长度、传 `list(mine)` 副本。

### 6.15 v1.0.1 — 逐环实测后的 18 处体验修复（2026-11-01）

本轮方法：真机（Resolve 在跑 / 99.2 秒素材 / RTX 5060 但 torch 是 +cpu）按
`timeline_source → resolve_bridge → land_subtitle(timeline/relang) → subs_io(save)`
逐环跑通，再对每个"卡住/忽悠/静默"的点做代码定位。识别算法（模型、切句策略）
一行未改，改的全是**状态机、提示语、还原路径**。

#### 6.15.1 两个 P0

1. **停止/关窗会把用户的交付页永久改成"仅音频 WAV"**
   `App:Cleanup` 从不调 `App:CleanupRender`，而还原用户预设、删 `render_to_wav`、
   恢复 `ExportVideo/ExportAudio` 全在那里面。渲染中途按「停止」→ 设置留在坏态；
   下一轮 `StartRender` 又从【当前】设置快照（`renderSnap`），坏态被当成"用户原样"，
   原始设置不可恢复。修法：`Cleanup` 第一行 `pcall(CleanupRender)`。
2. **诊断窗口失去入口**
   4.8 界面重构把 `VerBtn` 删了、版本号并进顶栏 `TlName` Label，`ShowDiagWin` 只剩
   一个指向死按钮的 handler → 用户出问题时打不开诊断；`使用说明` 却仍写"点右下角版本号"。
   修法：顶栏改回 `ui:Button{ ID="VerBtn" }`（`QPushButton#VerBtn` 的透明小字样式还在），
   沿用原 `win.On.VerBtn.Clicked`；`RefreshSubTrackInfo` 里同步改 `it.VerBtn`。

#### 6.15.2 状态机 / 还原 / 提示

| 症状 | 根因 | 修法 |
|---|---|---|
| 子进程早期崩溃，界面挂「识别中」5 分钟 | rc 看门狗只 `Log/Status` 不改 phase；无心跳时 `stale = elapsed > 300` | rc≠0 直接 `Fail`；渲染相位先 `ReturnToPrevPage + CleanupRender` |
| 失败后按钮文字停在「识别中 47%」 | `Fail/EnableButtons` 只动 `Enabled` 不动 `Text` | `EnableButtons` 里复位 `StartBtn.Text` |
| 停止提示恒为「模型保持加载」 | 文案写在回调里，没看 phase | 按 phase 分支给文案 |
| 停「落轨」不杀旧 python，重试会叠两条轨 | 超时只置 `done` + `EnableButtons`（把停止也关了） | 超时先 `KillTasks(false)`，提示改为"已尝试终止" |
| 渲染被别的渲染堵住，0% 干等 15 分钟后报超时 | `IsRenderingInProgress` 是工程级，`(not rendering)` 的卡住判定永不成立 | 新增「排队等待」分支：`rendering and pct<=0 and elapsed>20` → 明写"等待渲染队列"，30 分钟才失败 |
| 切交付页 3 秒就判失败 | `OpenPage` 异步，千片段工程读预设慢 | 放宽到 10 秒 + 状态栏写已等秒数 |
| 半截 WAV 当"导出完成" | 只判"文件 1.5 秒没长大" | 作业百分比 0<pct<50 且文件停了 → 判"导出被中断" |
| 媒体池导入没起来，一个字都不说 | `StartLand` 的 `RunTask` 失败分支只有 `LoadSubs` | 补 Log + `Status("字幕已生成，媒体池未导入")` |
| 导入超时后晚到的结果被丢弃 | phase 一变 `done` 就再没人读 `import.lua` | `lateImportUntil = now+120`，非忙碌阶段补读一次 |
| 新一轮识别时旧字幕表还在、「字幕更新」可点 | `Start` 只关 4 个按钮，`App.S.subs` 不清 | `Start` 里 `FillSubTree({})` + 锁 `UpdateSubsBtn/CutBtn` |
| 「停止」顺手清掉 `audioPath`，随后的适配字幕少了素材旁副本 | `Cleanup` 里无条件 `audioPath = nil` | 删掉这行（每轮 Start 都会重写） |

#### 6.15.3 折行（唯一动到文本产出的改动）

`_wrap_line` 原来**先取词边界、标点只作兜底**，而词边界来自强制对齐器的分词：
对齐器把「大家晚上」当一个词时，断点就落在词中间，实测切出
「…大家晚 / 上好。」——正是 v4.8.23 注释里声称已经修掉的那个例子（注释说
"宁可提前几格也要落在标点后"，代码顺序却相反）。改为：**标点优先 → 词边界 →
原子边界**；另加一条：断点落在 `CLOSERS`（`”’」』）】》`）上时往后挪一格，
避免行首出现「”」（实测切出过「…扣一波“一」+「”，好不好？」）。
24 字档实测对比：`大家晚 / 上好。` → `…小伙伴们， / 大家晚上好。`；
`…“一 / ”，好不好？` → `…“一”， / 好不好？`。4000 条随机串 × 6 档宽度的
fuzz 无丢字、无空行。

#### 6.15.4 环境可见性与可维护性

- **有显卡却静默跑 CPU**：`asr_engine` 只按 `torch.cuda.is_available()` 落 `cpu`，
  面板/状态栏从不显示设备；实测这台 RTX 5060 装的是 `torch 2.15.0+cpu`，实时系数 0.77。
  现在 `resolve_bridge` 在本地加载后判「device==cpu 且存在 nvidia-smi」→ 写
  `status.lua` 的 `warn` 字段（"CPU 模式"），Lua 把它常驻显示在状态栏尾巴并往日志
  写一次完整解释。常驻服务路径暂不带这个字段（服务端尚未上报设备）。
- **ETA 宁可多报一档**：旧公式 `0.7*窗口 + 0.3*全程` 在本次实测 75% 时给 10~15 秒，
  实际还差 38 秒（尾段含对齐重试/并条/长条再切）。改为 `max(窗口, 全程) * 1.25`。
- **素材旁 srt 开关**：`settings.lua` 增 `sidecar`（缺省 true），设置页
  「在素材旁生成同名 SRT」，`land.json` 透传，`land_subtitle` 里
  `if cfg.get("sidecar", True) is not False` 才写。
- **验收脚本 GBK 误报**：`verify_all.run()` 现在强制子进程
  `PYTHONIOENCODING=utf-8 / PYTHONUTF8=1`。此前中文控制台里测试脚本的 `✓`
  会抛 `UnicodeEncodeError`，把"通过"判成"失败"（R2 就因此红过一次）。

#### 6.15.5 粗剪链路（本轮补的真机实测）

用 `MediaPool.CreateTimelineFromClips` 造一条**独立测试时间线**（不碰用户原时间线），
在上面落 33 条字幕后跑 `cut_timeline.py`，测完 `DeleteTimelines` 删掉：

| 用例 | 输入 | 结果 |
|---|---|---|
| 中段切 2 段 | 30.4–31.6 / 44.0–45.0 | 视频/音频各拆 2 次 → 3 段 + 2 个空洞（909–951、1317–1353 帧）；时长仍 2976 帧；字幕 33 条按原时间码重挂 |
| 尾部切 1 段 | 96.5–99.2 | 裁掉尾段，时长 2976 → 2892 帧（−2.8s），`gap_s=0`，字幕仍在 |

**测出的 P0 级缺陷：R1 校准会切错地方。**
`_subtitle_offset_frames` 用"时长一致"给每段切除区间找字幕条目投票。实测在
残留字幕里恰有一条 2.68 秒的条目（79.91–82.59s），而这次要切的是尾部静音
2.70 秒（96.5–99.2s）——两者时长吻合，**单票就生效**，偏移被算成 −498 帧
（−16.6 秒），于是实际切在 79.9s：画面被切走 16 秒，而返回结果里
`ok=true`、`dur/saved/gap` 全都自洽，日志也看不出异常。

修法（v1.0.1）：R1 只在"按原区间根本切不到素材"时才有意义，所以在
`do_cut_inplace` 里先算素材覆盖率，**平移后覆盖率必须严格变高**才采用；
否则按原区间切，并打印"校准出偏移 X 帧但覆盖没有变多（100% → 100%）"。
真偏移场景（素材从第 N 帧起）原区间覆盖率≈0，校准照旧生效；
`tools/test_r1_offset.py` 7 项全过。修复后同一操作：R1 报 −591 帧被拒
（100% → 100%），切口正确落在 30.4–31.6s。

#### 6.15.6 安装器：CPU / GPU 自动选择（本轮审查 + 修复）

起因：这台机器是 RTX 5060 + 驱动 610.62，装出来的却是 `torch 2.15.0+cpu`。
逐段查了 `02_构建与安装器\installer.py`，发现**三个叠加的缺陷**，任何一个都能让
"选 GPU 版"变成"还是 CPU"：

1. **`pip install torch` 对已装的 torch 是空操作。**
   `pip_install` 只写 `pip install torch --index-url <源>`，没有 `--upgrade` /
   `--force-reinstall`。实测（本机 runtime）：
   ```
   pip install --dry-run --no-deps torch --index-url .../cu128/
   → Requirement already satisfied: torch ... (2.15.0)
   ```
   所以"装过 CPU 版 → 重跑安装器选 GPU 版"根本不会替换 torch，
   而 `使用说明` 里恰好写着"重跑安装器即可"。

2. **`--extra-index-url PyPI` 让 CPU 版反杀 CUDA 版。**
   原命令同时给 PyPI 作 extra index，而镜像上的版本现状是：
   | 通道 | 最高版本 |
   |---|---|
   | cpu | 2.15.0+cpu |
   | cu128（安装器写死的） | **2.11.0+cu128** |
   | cu126 | 2.14.1+cu126 |
   | cu130 | 2.14.1+cu130 |

   pip 按"版本最高"选候选，于是即使加了 `--force-reinstall`，
   `Would install torch-2.15.0`（来自 PyPI，CPU 版）——CUDA 通道等于白设。

3. **冒烟测试不看设备。**
   `smoke()` 只认 `SMOKE_OK`，装的是 GPU 版、实际 `device=cpu` 也照样打
   "✓ 冒烟测试通过"。另外 `detect_nvidia()` 只按名字调 `nvidia-smi`，
   老驱动把它放在 `Program Files\NVIDIA Corporation\NVSMI\` 的机器会被判成"没卡"。

修法（v1.0.1）：

- torch 安装命令改为 **只给一个源 + `--no-deps`**，切换版本时再加
  `--force-reinstall`；torch 的运行依赖（filelock/fsspec/jinja2/networkx/
  setuptools/sympy/typing-extensions）写进 `TORCH_DEPS`，由下一步从 PyPI 装。
  Windows 的 torch 轮子自带 CUDA DLL，所以 `--no-deps` 是安全的。
- **按驱动选通道** `pick_cuda_channel()`：驱动 ≥580 → cu130；Blackwell
  （RTX 50 系）在 570~579 上只能 cu128；其余 525+ → cu126。实测三个通道在
  win_amd64/cp312 上都能解析出 2.14.1（cu126/cu130）。
- 装完用 `probe_torch()` **亲测** `torch.version.cuda` 与 `cuda.is_available()`：
  选了 GPU 却不可用时明确写"会跑 CPU，慢 5~10 倍 + 去更新驱动/改选 CPU 版"；
  反之装了 CPU 版但 CUDA 可用时也提示一句（正是本机的情况）。
- `smoke()` 增加 `device=cuda` 检查；`detect_nvidia()` 增加 NVSMI 路径回退，
  并返回驱动版号，GUI 上直接把驱动写出来，低于 570 时给一行橙色提醒。

已重打并发布：`04_安装包\AsrSubs_v1.0.1_安装器.exe`
（12,492,782 字节，MD5 `36A2B99693C4017D3498894E06C3C97E`，ProductVersion 1.0.1），
由 `02_构建与安装器\build.bat` + PyInstaller 6.22.3（`py -3.12`）+ Inno Setup 6 编译，
包内 payload 的 `AsrSubs.lua` 版本 = 1.0.1。旧成品 `AsrSubs_v4.8.23_安装器.exe` 保留备查，
但**不要再用它装新机器**（它的 CPU/GPU 逻辑是旧的）。

#### 6.15.7 本轮实测数据（留档）
- 99.2 秒音频 / 12 段 / 33 条字幕 / 引擎 81.9 秒（RTF 0.83）/ 总 114.1 秒 / 设备 CPU
- 落轨 33/33、1.6 秒、首帧 124 对齐；relang 清旧 33 → 落新 32、单轨无重影
- 粗剪（测试时间线）：中段 2 段切 → 3 片段 2 空洞、时长不变；尾部切 → 99.2s → 96.4s
- 安装器探针：`detect_nvidia() → (True, 'RTX 5060 Laptop GPU', '610.62')`；
  `probe_torch(runtime) → {'VER': '2.15.0+cpu', 'CUDA': 'None', 'AVAIL': 'False'}`
- `verify_all.py` 16 项全绿（1 项跳过：没有样片 SRT 时热词单测跳过）

---

### 6.16 v1.1.0 — 一键安装 / 一键卸载（2026-11-01）

只改安装包，插件代码零改动（`AsrSubs.lua` 仅版本号）。

#### 6.16.1 一键安装：`--auto`

以前的链路是两层：Inno 装完文件 → `[Run]` 拉起 `安装核心.exe "{app}"` →
**用户还得再点一次「开始安装」**。现在 `[Run]` 传 `--auto`：

- `installer.py` 的 `App.__init__(..., auto=True)` 在 UI 建好后
  `root.after(700, self._auto_go)`，直接按界面上的默认值开始（位置由安装包给定、
  CPU/GPU 用 `detect_nvidia()` 的检测结果，有卡默认 GPU）。
- 成功时不再弹「完成」messagebox（那又是一次点击），改为
  `root.after(6000, root.destroy)` —— 窗口自己关。
- 失败仍弹错误框并停住，不会一闪而过。

#### 6.16.2 一键卸载：确认框说清楚 + 可选清产物

- `[Messages] ConfirmUninstall` 重写：一次写明"删运行环境/模型/引擎/文档/菜单项"、
  "留 data\output"、"先退出 Resolve"。
- `[Code] CurUninstallStepChanged(usUninstall)` 在 `BackupSubtitleOutput` 之后新增
  一问：要不要连 `data\output` 一起删？用 `SuppressibleMsgBox(..., IDNO)` ——
  **默认「否」**，`/SILENT` 卸载同样取默认值，绝不会静默删用户字幕；
  选「是」才 `DelTree`。备份在任何情况下都先做。

#### 6.16.3 真机验证（本轮实做）

| 用例 | 结果 |
|---|---|
| 静默安装到沙箱 `/VERYSILENT /DIR=…` | 退出码 0；`_setup`、`docs` 就位；注册表 `DisplayVersion=1.1.0`；开始菜单 3 项 |
| 静默卸载 | 退出码 0；目录 / 菜单 / 注册表全清，Documents 无残留 |
| **一键安装（覆盖升级）** | 对现有 `D:\AsrSubs` 执行 `安装核心.exe "D:\AsrSubs" --auto`：**135.7 秒、退出码 0**；引擎与菜单 Lua 升到 1.1.0；torch 仍 `2.14.1+cu130`（CUDA 可用，未被降级 —— 证明"已是目标版本就跳过"的逻辑在真实安装里生效）；`settings.lua`、`data\output`、两个模型全部保留 |
| 包内载荷 | `AsrSubs.lua` = 1.1.0；`使用说明.txt` 与 `03_文档` 逐字节一致 |

成品：`04_安装包\AsrSubs_v1.1.0_安装器.exe`（12,496,392 字节，
MD5 `1D65DCC3308E6E8A30A7880FA7280401`）；旧版在 `04_安装包\_历史\`。

---

### 6.17 v1.1.1 — 关窗不再跳到「交付」页（2026-11-01）

**现象**（用户实测）：在 DaVinci 里打开 AsrSubs，什么都不做，直接关掉窗口 →
DaVinci 自己切到交付页。

**根因是两层叠加，缺一不可：**

1. v1.0.1 为修"停止改坏交付页"，把 `App:Cleanup` 改成也调 `App:CleanupRender()`
   （见 §6.15.1）。而 `CleanupRender` 无条件执行：

   ```lua
   pcall(function() App.project:DeleteRenderPreset("render_to_wav") end)
   ```

   **Resolve 删渲染预设时会把界面切到交付页** —— 没渲染过也会执行这一句。

2. `App:ReturnToPrevPage` 只判 `App.S.prevPage` 有没有值。那个值是
   `StartRender` 留下的；没渲染过就是 `nil` → 什么都不做，于是停在 ① 造成的
   交付页上。（若上一轮渲染留下过 prevPage，则表现为"被拽回上一轮那页"。）

**修法：**

- `CleanupRender` 开头加闸：`if not App.S.renderSnap and not App.S.jobId then return end`
  —— 这次根本没配置过渲染时，一个交付页 API 都不碰。
- `StartRender` 切页时置 `App.S.pageMoved = true`；
  `ReturnToPrevPage` 只消费这个一次性标志：没切过就绝不切回，
  目标页已经有值且就是当前页时也不重复 `OpenPage`（多余一次切页会闪）。
- `OnRun` 里记下 `App.S.openPage`（开窗时所在页），作为 prevPage 缺失时的兜底。

**回归测试**：新增 `tools\test_close_page.py`（lupa 载入真 Lua，假 resolve/project），
5 条断言，已进 `verify_all.py`（现在 17 项）：

| 用例 | 断言 |
|---|---|
| 没渲染就关窗 | 页面/渲染 API 调用次数 = **0**，页面不变 |
| 无状态调 CleanupRender | 调用次数 = 0 |
| 渲染过再关窗 | `OpenPage('edit')` 恰好一次 |
| 已在目标页 | 不重复 `OpenPage` |
| 有快照 | 作业删除 + 预设还原 + 切回原页，且不走兜底 `SetRenderSettings` |

成品：`04_安装包\AsrSubs_v1.1.1_安装器.exe`（12,496,693 字节，
MD5 `1FC679AEC877D20EF22806BE3FCDB5C1`）；`verify_all.py` 现 **17 项**全绿。

⚠ 本轮验证方式是离线单测 + 代码路径确认（当时 DaVinci 未运行）；"删渲染预设会
切到交付页"这一条是按现象与调用链推断的，没有当场用 API 复测。但修复的做法是
**彻底不调用**那些交付页 API，所以无论具体是哪一个切换的页面，症状都不会再出现。

---

### 6.18 v1.2.0 — 句末标点收口 + 「开始生成」前置条件 + 裁剪区间复位（2026-11-05）

本轮起因是用户报的两个问题（`1. 去除句末标点后仍有"，"；2. 条件不满足时按钮应灰掉`），
外加一次针对 v1.1.0 → v1.1.3 全部变更的代码审查。识别算法、落轨、粗剪流程均未改动。

#### 6.18.1 「句末无标点」失效：后处理顺序问题（用户问题 1）

**现象**：设置里勾了「句末无标点」，「去除标点符号」没勾，导出的 SRT 里仍有整行以
「，」结尾（不是句号、不是行内，就是行尾）。

**根因**：`strip_tail_punct()` 在 `asr_engine.py` 里只被调用三次
（`_blocks_from_text` 2028、`_process_segment` 2109 / 2128），全部发生在 `_timed()`
**之前**。而它后面还有三条会**重新切分文本**的路径，且三条都优先切在标点之后：

| 路径 | 切点逻辑 | 复现输出（8 字档） |
|---|---|---|
| `resplit_overlong_blocks` → `_split_text_evenly`（1288-1327） | 候选切点 = `END_PUNCT` + `PAUSE_PUNCT` 之后 | `['我们今天在这里欢聚一堂，', '共同庆祝这个美好的节日']` |
| `split_at_long_pauses` → `_snap_to_punct`（1459-1533） | 断点吸附到最近的标点 | `['我们今天在这里欢聚一堂，', '共同庆祝这个节日']` |
| `refold_overlong_text` → `_wrap_line`（1338-1375，调用点 2277） | 标点优先（v1.0.1 改的顺序） | `['我们今天在这里欢', '聚一堂，', …]` |

第三条在**写盘前**执行，所以它造出来的行尾标点必然进 SRT。
「去除标点符号」（`strip_all_punct`）不受影响 —— 它在切句阶段就把逗号删光了，
后面没标点可切，这也是为什么只有 `end_punct` 一条开关会暴露这个 bug。

**修法**：`run()` 在 `refold_overlong_text` 之后、空块过滤之前加一道**出口收口**
（仅当 `trim_punct or strip_all_punct` 生效）：

```python
if trim_punct or strip_all_punct:
    swept, n_tail = [], 0
    for st, en, tx in blocks:
        t = _flatten_ws(str(tx or ""))
        t2 = t.rstrip(TRAIL_PUNCT).strip()
        if t2 != t.strip():
            n_tail += 1
        if t2:
            swept.append((st, en, t2))
        else:
            self.log("  丢弃 1 个只剩标点的块（%.2f~%.2fs）" % (st, en))
    blocks = swept
```

选择"出口统一收口"而不是"在上游逐个堵"的理由：上游是三条独立路径，各自都有宽度、
时间、词边界的判断，往里塞标点逻辑会互相干扰；出口只有一处，且只做字符串裁尾，
宽度只会变窄不会变宽，不会破坏已有的宽度约束。

⚠ 这条只处理**行尾**。如果哪天要"行内标点也去"，必须走 `strip_all_punct`，
不要在这里加 `translate` —— 那会让断句信息在中途丢失，后续任何重切都会失效。

#### 6.18.2 「开始生成」按前置条件置灰（用户问题 2）

**改前**：`StartBtn` 在开窗（BuildUI）和 `EnableButtons()` 里都被**无条件**置为可点；
相位忙时只靠 `App:Start` 内部一句 `return` 拦一下，用户点了才知道不行。

**改后**：新增 `App:CanStart()`，三条判据：

| 判据 | 取值方式 | 不满足时的提示 |
|---|---|---|
| 非忙碌相位 | `BUSY_PHASES[App.S.phase]` | 任务进行中 |
| 环境自检通过 | `App.S.pathBad`（引擎脚本 / Python 缺失） | 环境自检未通过（第一条原因） |
| 时间线有片段 | `GetTrackCount` + `GetItemListInTrack`，**数到第一个就返回** | 没有打开时间线 / 时间线上没有片段 |

刷新点（五处）：

1. `BuildUI` 末尾 —— 开窗即正确，不再是"先亮后判"；
2. `EnableButtons()` —— 替换原来那句无条件的 `= true`（先复位文案再判，顺序不能反）；
3. `Tick` 非忙相位**每 3 秒** —— 换时间线、把片段删光、关掉工程都不会发通知，
   这是唯一能兜住"环境在背后变化"的地方；不放进 0.5 秒主循环是为了不在大工程上白烧帧；
4. 点击处理器开头再判一次 —— 按钮状态最多滞后 3 秒，避免"还亮着但点下去白跑"；
5. 点击异常分支 —— 相位先落 `error` 再刷，反了会把刚失败的界面又点亮。

⚠ 判据刻意**不用** `timeline_source.py`（异步 Python 探测）：那样每次刷新都要起进程，
而这里只需要"有没有片段"这一个布尔值。

#### 6.18.3 审查发现：`audio_trim` 跨轮残留（字幕整体错位）

`App.S.trimFrom / trimDur`（v1.1.3 的"掐头/从中间取用直接识别"）只在 `App:OnProbe`
里赋值与归零。而 `Tick` 的 `probing` 分支有两条**绕过 `OnProbe`** 的出口：

- 探测超时 20 秒但拿到 `src_path.txt` → 直接 `StartPython(src)`；
- 探测超时且没有兜底文件 → 直接 `StartRender()`（渲染完成后同样走 `StartPython`）。

上一轮的裁剪区间会留在这两个字段里，被 `StartPython` 原样写进 `args.json` 的
`audio_trim`：引擎只抽本轮音频的一小段，SRT 从 0 起算而画面没动 —— 表现为
「字幕整体前移 + 只覆盖开头一小段」。渲染路径更隐蔽：喂进去的是整条时间线的 WAV，
却按上一轮的区间裁。

修法：`App:Start` 里每轮归零（`App.S.trimFrom, App.S.trimDur = 0, 0`）。
**位置必须在 `Start()` 而不是 `OnProbe`**，因为要盖住的正是"OnProbe 根本没跑"的那两条路。

#### 6.18.4 审查发现：其余四处

| 问题 | 位置 | 说明与修法 |
|---|---|---|
| `KillTasks(all)` 兜底分支忽略 `all` | `AsrSubs.lua` 4575-4600 | `pids.lua` 只含 worker 拉起的子进程（`sync_pids` 只遍历自己的 `procs`），常驻 `asr_server` 不在里面。worker 已死时关窗走的正是这条兜底路 → 服务收不掉，继续占 2~3GB 显存。现按 `asr_hb.txt` 第一行的 `"pid"` 补杀（兼容老的空格分隔格式） |
| 单实例锁竞态 | `worker.py` `acquire_single` | 对方刚 `O_EXCL` 建文件、JSON 还没落盘时读到空内容 → 被当残留删掉 → 两个 worker 并存。改为：读不出内容且文件年龄 < 60 秒 → 让路；超时才清 |
| 裸 fd 泄漏 | `worker.py` `acquire_single` | `os.fdopen` 失败时 `fd` 无人接管。改为先单独 `fdopen`，失败则 `os.close(fd)`；成功才交给 `with`（避免重复 close） |
| `lateImportUntil` 未清 | `AsrSubs.lua` `App:Start` | importing 超时设的 +120 秒窗口在新一轮不清，本轮正常导入完成后会被当作"（迟到）"再报一遍并覆盖状态栏 |

**未发现**：Lua 侧的 nil 解引用（`ctlGet` / `ctlSet` / `ctlChecked` / `setChecked` 对
`nil` 都有 guard）；命令注入（`subprocess` 全为列表参数、无 `shell=True`，`taskkill`
只拼数字 PID）。唯一的结构性风险是 `loadLuaTable` 用 `loadfile` 执行 `rt` 目录下的
`.lua` —— 该目录可写即等于可执行代码，属设计取舍（与插件同权限），已在
§9 待办里记为"可选加固项"。

**验证**：`py_compile` 8 个 Python 文件通过；`tools\check_lua.js`（luaparse，5.3 语法）
通过；§6.18.1 的三条复现与出口收口在同一套函数上跑过前后对照；
新增 `tools\test_can_start.py`（lupa + 假 resolve/timeline，**9 用例**）已进
`verify_all.py`（现 **18 项**，17 项通过 + 1 项跳过）。

⚠ 仍待真机确认的两件事：① `GetItemListInTrack` 在 Resolve 里返回的确实是 Lua 表
（若返回代理对象，`CanStart` 会走 fail-open 分支不拦，按钮行为退化为旧版但不会锁死）；
② 换时间线后 3 秒内按钮能如期变灰/变亮。这两条离线覆盖不到，本轮未做真机回归。

---

## 7. 测试体系

### 离线（无需 DaVinci）
| 脚本 | 覆盖 | 运行 |
|---|---|---|
| `tools\test_r1_offset.py` | R1 偏移校准（7 用例） | `python tools\test_r1_offset.py` |
| `tools\test_r2_relang.py` | R2 relang 闭环 + v4.0.7 三项修复 + v4.0.8 缺陷4（**16 用例**） | `python tools\test_r2_relang.py` |
| `tools\e2e_r2_live.py` | **真机端到端**（需 DaVinci 打开）：落轨→改SRT→relang→断言删旧重挂无重影→清理 | `python e2e_r2_live.py` |
| `tools\verify_v407_live.py` | **v4.0.7 三项修复真机复验**（需 DaVinci 打开）：首次落轨→**重复落轨不得新增轨**→失败落轨不得污染边车→relang 定位 | `python verify_v407_live.py` |
| ⚠ 测试卫生 | `verify_v407_live` / `verify_v408_live` 原本"只增不减"（不调 `DeleteTrack`），**连跑会堆空轨**；v4.8.15 改为"结束时还原到进入时的轨数"——**只删本次新增且为空的轨**，进入时就存在的轨一律不动 | 见 §7 说明 |
| `tools\verify_v408_live.py` | **v4.0.8 缺陷4 真机复验**（需 DaVinci 打开）：失败落轨→**自建空轨必须被回收**（`failed_land_growth==0`）→空 SRT 早退 | `python verify_v408_live.py` |
| `tools\cleanup_empty_sub_tracks.py` | 清理**历史遗留空字幕轨**（默认只读体检；`--delete N` / `--delete-empty` 才动手，拒绝删非空轨） | `python cleanup_empty_sub_tracks.py` |
| `tools\test_land_order.py` | **v4.1.0 落轨顺序铁律 + SetName 直改护栏**（**7 用例**，纯假对象，不连 Resolve） | `python test_land_order.py` |
| `tools\test_subtrack_info.py` | **v4.0.9 字幕轨清单**（**7 用例**，需 `pip install lupa`）：从源码抽取真实函数跑，喂假 timeline | `python test_subtrack_info.py` |
| `tools\test_can_start.py` | **v1.2.0「开始生成」前置条件**（**9 用例**，需 `pip install lupa`）：空时间线 / 有轨无片段 / 有片段 / 忙相位 / 环境自检未过 / 无时间线 / **返回非表对象时 fail-open** / Enable 真值写入 / done 相位恢复 | `python test_can_start.py` |
| `tools\probe_subtrack_api.py` | **v4.0.9 真机 API 取证**（需 DaVinci 打开）：`GetTrackCount` / `GetIsTrackEnabled` / `GetTrackName` 对字幕轨的真实返回 | `python probe_subtrack_api.py` |
| `tools\check_live_readonly.py` | **只读巡检**（需 DaVinci 打开）：连接 / 工程 / 时间线 / 帧率 / `SetTrackEnable` 可调用性 / 部署 MD5，**不碰时间线** | `python tools\check_live_readonly.py` |
| `python -m py_compile <file>` | 语法编译 | 逐文件 |
| `tools\check_lua.js` / `check_lua_load.js` / `test_diag_text.js` | Lua 静态语法 / fengari 编译 / 诊断文本（**需 node_modules**，缺了会被 verify_all 跳过） | `node tools\check_lua.js "AsrSubs.lua"` |
| **lupa（Python 扩展，替代 fengari）** | **Lua 真编译**（查 200 local 上限）—— v4.0.9 起为 verify_all 的主 Lua 校验路径，不依赖 node 生态 | `pip install lupa` |
| `tools\verify_all.py` | 一键验收（**18 项**：v4.0.9 起含 R1/R2/字幕轨清单，v4.1.0 起含落轨顺序，v1.2.0 起含开始按钮前置条件） | `python tools\verify_all.py` |

### 真机（需 DaVinci 在运行，且开启外部脚本）
| 脚本 | 用途 |
|---|---|
| `tools\probe_r15.py` | 只读探测 API 面（字幕轨读取、`EXPORT_SRT` 不存在等） |
| `tools\probe_r2_verify.py` | 只读验证 R1/R2 依赖的真实字幕轨位置读取 + 来源 stem 识别 |

> 真机连不上时 `scriptapp("Resolve")` 返回 `None`（不是 bug，是 DaVinci 没开 / 外部脚本未启用）。需在 Resolve：工作区 → 控制台 → 勾选"外部脚本使用"= 本地/网络。

---

## 8. 开发工作流 & 如何保持本目录最新（及时更新）

### 8.1 漂移风险
真正的"被测试 / 被加载"的引擎在 `D:\AsrSubs\engine`（`ASCII_ROOT` 指向它）。开发目录 `D:\插件\AsrSubs_整合_20261031\01_源码` 是规范源码。**在 engine 改完测试、忘了回灌**就会导致两边漂移——**2026-10-31 真实发生过**：`01_源码` 停在 `4.0.4`，engine 已是 `4.0.5`，靠 `sync_dev.py` 检查发现并修复。

### 8.2 标准动作（每次改完代码后）
1. 在 `D:\AsrSubs\engine` 改代码、自测（Resolve 重新运行脚本加载）。
2. 跑离线单测 + `py_compile`。
3. **回灌**：`python sync_dev.py --sync` —— 把 engine 的已测代码写回 `01_源码`（只同步规范文件集，忽略 `.bak`/测试/探针/`rt`）。
4. **升版本**（如适用）：改 `version.txt` → `sync_version.py` → `sync_payload.py`。
5. **部署**：`python sync_dev.py --deploy` —— 在回灌基础上推到 `payload` / `packer` / 菜单 Lua。
6. **验收**：`python tools\verify_all.py`；必要时后台开 DaVinci 跑 `probe_r2_verify.py`。

### 8.3 `sync_dev.py`（本目录自带）
- 默认（无参）= **只读检查**：engine↔01_源码 差异、版本号一致性、01_源码↔payload MD5。
- `--sync` = 回灌 engine → 01_源码。
- `--deploy` = 回灌 + 推到安装包 payload / packer payload / 菜单 Lua（菜单为实时文件，Resolve 需重新运行脚本才生效）。
- 任何模式都会打印三项的汇总，便于一眼确认"开发目录是否最新"。

### 8.4 推荐：每日漂移巡检（可选自动化）
可设一个**每日只读**运行 `sync_dev.py`（无 `--sync`，不改文件、只报警）的自动化，作为"及时更新"的安全网，发现 engine↔01_源码 漂移立即提示补回灌。

### 8.5 ⚠️ `--deploy` 会先回灌，能覆盖你在 01_源码 的改动（2026-10-31 连中两次）

`sync_dev.py --deploy` 的第一步是 **engine → 01_源码 回灌**，然后才推 payload/菜单。
所以**只在 `01_源码` 改、不先同步到 engine，就会在 deploy 时被 engine 的旧版本覆盖**。
本次两个文件都中招：

| 文件 | 现象 |
|---|---|
| `03_文档\使用说明.txt` | 新写的 v4.1.0 更新记录被冲掉（只剩版本号抬头） |
| `land_subtitle.py` | v4.1.1 的三处修复被回退，还被当成 4.1.1 发了出去 |

**正确姿势（二选一）**：
1. 在 `D:\AsrSubs\engine` 改，跑 `--sync` 回灌；或
2. 在 `01_源码` 改完后，**先把改动文件 `copy` 到 engine**，再 `sync_payload` → `--deploy`。

**验证**：deploy 后必须确认 `engine` 与 `01_源码` 的关键文件 MD5 一致，并抽查新代码标记
（如 `_enable_and_verify`）仍在，别只看"汇总：一致"。

#### 8.5.1 2026-11-01 又中第三次（v4.8.24，代价更大）

在 `01_源码` 改完 11 项体验优化（7 个 py/lua 文件）后直接跑 `sync_dev.py --deploy`：

- 回灌把 7 个文件的改动**全部**覆盖回 4.8.23，只有 `02_构建与安装器\installer.py`
  幸存（它不在同步集里）；
- 更隐蔽的一层：回灌还把 `01_源码\AsrSubs.lua` 里的 `--<<INSTALL_ROOT>>` **占位锚点吃掉了**
  （engine 版是替换后的真实路径）。若这样打包，装到 `D:\` 以外的机器会沿用写死的
  `D:\AsrSubs\engine`，表现为"界面能开、一跑就超时"。

**恢复与规避（本次定下的做法）**：

1. 动手前把 `01_源码` 备份成 `_改前备份_<主题>_<日期>`；
2. **改完立刻再存一份** `_改动后_<主题>_<日期>` —— 这次能快速回滚全靠它；
3. 在 `01_源码` 改代码时，**不要用 `--deploy`**。用单向推送：
   `copy` 规范 py → `engine`，再把 lua 的占位锚点替换成真实路径后写到
   `engine` 与菜单目录（本次的 `push_engine.py` 就是这个脚本，可复用）；
4. 推完立刻抽查：`grep -c "_word_char_cuts" D:\AsrSubs\engine\asr_engine.py` 必须 ≥1，
   `grep -c "INSTALL_ROOT>>" 01_源码\AsrSubs.lua` 必须 ≥1；
5. 部署时**不要有识别/落轨任务在跑**：engine 里的 py 正被 Python 读入时被 `copy2` 覆盖，
   实测出现"进度卡在 99%、进程存活、内存 7 GB、日志停在最后一行"的假死，很难判断。


---

## 9. 待办与已知问题

> **最新归档（2026-10-31，截至 v4.8.14）**
>
> | 版本段 | 内容 | 状态 |
> |---|---|---|
> | 4.2.0 / 4.2.1 | 「读取时间线字幕」只读载入、`dump_track` 模式、清理空轨按钮 | ✅ 真机验证 |
> | 4.2.2 | **修复 4**：适配字幕前"已有字幕且无落轨记录"先确认 | ✅ |
> | 4.3.0 / 4.3.1 | 多级撤销（50 步、深拷贝）、MediaStorage 第三兜底 | ✅ 用例覆盖 |
> | 4.4.0 / 4.4.1 | **删行即剪**（可选）、`App.timeline` 字段修复 | ✅ 真机 |
> | 4.5.0 ~ 4.5.2 | 编辑版另存 `_edit.srt`、日志级别化 | ✅ |
> | 4.6 ~ 4.8.14 | 界面重构：单页 + 设置弹窗、参数真源 `App.S.cfg`（详见 §6.10） | ✅ |
> | 4.8.12 ~ 4.8.14 | 提示改悬停、关闭事件按 ID 绑定、几何校验放宽 | ✅ |
>
> 验收：`verify_all` **16/16**；真机只读巡检（连接 / 工程 / 部署 MD5）通过。

- **v4.1.0 / v4.1.1 已完成并真机验证（2026-10-31）**：
  - v4.1.0：顺序铁律（导入先于时间线写操作）、`SetName` 直改优先、导入两轮回退、
    `InsertIntoTimeline` 兜底、播放头还原、`result=` 单行指标日志。
  - v4.1.1：**修复多字幕轨落错轨**（`SetTrackEnable` 方法名 + 启用回读校验 +
    落错轨自动回滚），离线用例扩到 7 个。
  - 真机（4.1.1 代码，时间线上有用户手工 41 条字幕）：`e2e_r2_live` pass=true、
    `verify_v407_live` 全部断言通过、`verify_v408_live` 全部通过、
    多轨共存测试 ST1 条目数与 UID 集合完全不变。`verify_all` 16/16。
- **修复 4（重复落轨交互提示）**：✅ **已完成**（v4.2.2）。点「适配字幕」时，若时间线已有带字幕的轨
  且当前 SRT **没有落轨边车**，先弹确认列出轨号与条数；有边车（= 本插件自己的轨）则不打扰。
- **R4（删行直接 `DeleteClips`）**：✅ **已实现**（v4.4.0「删行即剪」，可选开关）。
  勾选后：删行 → 自动写回 SRT → 自动粗剪；剪切仍走 `cut_inplace`（`DeleteClips(ripple=False)`），
  **保留空隙、字幕按原时间码重挂**（删 b/d 后 a/c/e 各自留在原位，不会挤成 ace）。
- **真机端到端验证**：✅ 已通过（v4.0.6，2026-10-31）。落轨→改 SRT→relang 删旧重挂无重影，`e2e_r2_live.py` `pass:true`，清理还原时间线。R1/R2 真机缺陷（`GetStartFrame` 返回 None、`GetMediaPoolItem` 返回 None）均已修复。
- **★ v4.0.7 三项修复的真机复验**：✅ **已通过**（2026-10-31，`verify_v407_live.py`）：
  | 断言 | 实测 |
  |---|---|
  | 首次落轨 | `land1_tracks_growth = 0`（复用 ST1）✅ |
  | **重复点「适配字幕」** | **`repeat_land_growth = 0`、`repeat_land_same_track = true`** ✅ ← v4.0.6 凭空造 ST2 的直接回归锁，已锁死 |
  | 落轨失败不污染边车 | `failed_land_no_sidecar = true`、`main_sidecar_intact = true` ✅ |
  | relang | `relang_no_new_track = true`、`relang_cleared = 3`（精确删旧 3 条、重挂 4 条、末态 4 条无重影）✅ |
- **★ v4.0.8 缺陷 4 的真机复验**：✅ **已通过**（2026-10-31，`verify_v408_live.py`）：失败落轨 `failed_land_growth = 0`、`reclaimed_track = 2`（自建空轨被回收）、空 SRT `empty_srt_growth = 0`。
- **历史遗留的空字幕轨**：✅ **已清理**（2026-10-31）。测试工程「新建项目 1 / Timeline 1」上的 ST1 + ST2 均已用 `cleanup_empty_sub_tracks.py --delete N` 删除，当前该时间线 **0 条字幕轨**。用户真实工程若还有空轨，用同一工具清理：
  - `python cleanup_empty_sub_tracks.py` → **只读体检**，列出每条字幕轨的条目数/名称/启用态，指出哪些是空轨（不动任何东西）。
  - `python cleanup_empty_sub_tracks.py --delete 2` → 删除第 2 条**空**字幕轨；**非空轨一律拒绝删除**（防止误删用户手工字幕）。
  - `python cleanup_empty_sub_tracks.py --delete-empty` → 删除所有空字幕轨（从大到小删，避免索引位移）。
  - 底层用 `timeline.DeleteTrack("subtitle", idx)`（真机已验证可用，见 §6.7）。
- ~~修复 4（重复落轨交互提示）未做~~ —— 已由 v4.2.2 实现，见上文。
- **文档滞后**：`README.md` 已在 2026-10-31 整合时刷新到 **v4.8.14**（结构 / 版本 / 部署地图与实包一致；
  §1 功能清单仍以 4.0.9 为基线，4.2–4.8.14 的界面重构见 §6.10）。`03_文档/AI交棒文档.md` 等可继续对齐。
- **★ v1.2.0 待真机验证（2026-11-05）**：三件事没有在 Resolve 里点过 ——
  ①「开始生成」的三条判据（`GetItemListInTrack` 在空时间线 / 只有字幕轨的时间线上
  是否真返回空表）；② 换时间线后 3 秒内按钮是否如期变灰/变亮；③ `audio_trim` 复位后，
  "上一轮掐头识别 → 本轮探测超时转渲染"这条组合路径是否不再错位。
  离线只能覆盖语法与纯函数（§6.18）。
- **可选加固**：`loadLuaTable` 用 `loadfile` 执行 `rt` 目录下的 `.lua` 数据文件。
  这些文件与插件同权限，任何能写该目录的程序都等于能执行代码。若要收紧，可改成
  只解析受限子集（`k = v,` / 数组），代价是丢掉"直接 loadfile Lua 表"的便利。
  当前定位为单机单人使用，暂不处理。
- **粗剪不保留特效**：重建出的片段不带转场 / Fusion 特效 / 调色节点（架构约束，见 §6.5 红线）。
- **撤销仅一级**：且粗剪撤销走"重建时间线"，不是回滚字幕。

---

## 9.5 v4.0.9 整理归档（2026-10-31）

### 9.5.1 归档清单

全部**只归档不删除**，总说明见 `删除前备份\99_归档\11_v4.0.9整理_20261031\_总说明.md`：

| 归档位置 | 内容 | 体积 | 归档理由 |
|---|---|---|---|
| `…\engine\packer\` | 2.x 旧打包区（dist/build/_旧版备份/旧 installer.py/冗余 payload） | 55M | 当前打包走 `02_构建与安装器\build.bat` + `02_构建与安装器\payload`，packer 是冗余副本 |
| `…\engine_旧备份\` | engine 根目录的 `.bak` / `.old`（9 个） | 1.6M | 其中 6 个在 `删除前备份\99_归档` 已有同版本同名副本 |
| `…\engine_运行期产物\` | `Qwen ASR 诊断` zip | 52K | 2026-10-27 的诊断包快照 |
| `…\probes_一次性取证\` | 10 个「字幕轨折叠可行性」取证探针 | 60K | 问题已关闭，结论固化在 §6.6 |
| `…\tools\` | `sync_local.py` / `split_lua.py` / `_patch_txt.py` | 32K | 零引用，功能已被取代 |
| `…\死代码删除前快照\` | `AsrSubs.lua` + `land_subtitle.py` 原件 | 380K | 回滚用 |

**engine 根目录从 60+ 项降到 28 项**，只剩运行时必需文件。

### 9.5.2 清除的死代码（5 个，零调用已实证）

| 文件 | 函数 |
|---|---|
| `land_subtitle.py` | `ensure_tracks`（仅 2.x 旧备份里被调用）、`isolate_subtitle_tracks`、`restore_subtitle_tracks`、`make_temp_srt` |
| `AsrSubs.lua` | `ensureDir` |

保留 `_get_track_enabled` / `_set_track_enabled`（前者仍在 `relang_subtitles` 里用，后者 11 处在用）。

### 9.5.3 ★ 误判后纠正（重要）

**`test_migrate.py` 差点被归档**。初判依据是「零引用」，实查发现
`02_构建与安装器\installer.py` 的 `migrate_legacy()` **仍在用迁移逻辑**
（`installer.py:951`），且该测试实跑「全部通过」——**是活跃测试，保留**。
教训：**零引用不等于死代码**，必须同时确认「被测对象是否还活着」。

同理保留：`cut_timeline.py`（入口）与 `cut_inplace.py`（引擎）是有意分工；
`lua_str`/`write_lua`/`fmt_ts` 等同名函数逐个比对后转义强度与字段集都不同，
强行合并风险大于收益。

### 9.5.4 顺带修好的四个工程缺陷

| 缺陷 | 修复 |
|---|---|
| `verify_all.py` **跑不到最关键的落轨测试**（只跑纠错表/热词两个老单测） | 补入 `test_r1_offset` / `test_r2_relang` / `test_subtrack_info` |
| **Lua 编译校验永远失败**（fengari 依赖未安装的 `node_modules`） | 改用已装的 **`lupa`** 做真编译（同样能查「200 local 上限」，正是 fengari 的检查点） |
| **JS 测试永远失败**（luaparse 同因） | 依赖缺失时**跳过**并说明原因，验收链不再「必红」 |
| 三个测试搬到 `tools\` 后路径假设失效 | 改为自定位回退一级（`SRC_ROOT`） |

### 9.5.5 整理后验收

`verify_all.py` **15 项全部通过**（整理前只有 12 项且 Lua 编译项必红）。
4 份 `AsrSubs.lua` MD5 全一致 = `1d3400bda98b11c7e0e657fb535b37ef`。

---

## 10. 回滚 / 备份

- Lua 每次部署前在脚本目录留 `AsrSubs.lua.bak_v<版本>_<时间戳>`。
- `删除前备份\99_归档\` 存历史快照；迁移前完整快照见 `删除前备份\99_归档\05_旧开发分支_迁移前快照_20261027.zip`。
- 安装器写入 `_setup\_deployed.json`（记录真正写了菜单的那一处），供升级/卸载判断。
- 引擎与模型不在开发目录留备份，活的就在 `D:\AsrSubs\`。

---

## 11. 安装器（Inno Setup）踩坑记录（2026-11-01 实测）

> 两条都是用户截图复现后定位的 P0，改 `AsrSubs.iss` 前必读。

1. **盘符校验必须用 Unicode API**。
   `external 'GetDriveTypeA@kernel32.dll stdcall'` 收 Inno 的 Unicode 字符串时，
   字节被按 ANSI 解读：`"D:\"` 被读成无反斜杠的 `"D"`，对**任何盘符**都返回
   1（DRIVE_NO_ROOT_DIR）→ 向导报「找不到这个盘：D:\」，而资源管理器里 D: 明明在。
   实测对照（DrvTest）：C:/D: 的 `GetDriveTypeA` 均返回 1，`GetDriveTypeW` 均返回 3。
   `GetDiskFreeSpaceExW` 同理要用 W 版。结论：**Inno 6 里调 kernel32 一律用 W 版**。

2. **`InitializeWizard` 阶段不能展开 `{app}`**。
   该常量到向导初始化之后才可用；提前 `ExpandConstant('{app}')` 抛
   「内部错误: An attempt was made to expand the "app" constant before it was
   initialized」——静默模式直接退出码 1（连目录页都进不去），界面模式弹运行时错误框。
   判断「是否覆盖升级」改用注册表卸载键：
   `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppIdGuid}_is1`
   的 `InstallLocation` 是否存在（GUID 用不带花括号的 `#define`，ISPP 转义只服务于段参数）。

3. **预填逻辑只在全新安装时生效**：`PickBestDrive()` 枚举本地固定盘
   （`GetDriveTypeW == DRIVE_FIXED`），够 7 GB 的盘里挑剩余空间最大者；
   一块都不够就退取最大盘，让安装核心的 `validate_base()` 报具体差额。
   升级安装必须保持 Inno 自动填好的上次位置，否则会装出第二个目录。

4. **PyInstaller 的 Python 必须有 tkinter**：`build.bat` 已加闸门
   （无 tkinter 直接报错拦住）。没有闸门时 PyInstaller 不报错，但打出的
   安装核心 exe 启动即退，且只有跑到用户机器上才发现。

5. **`.iss` 的 const 段不支持类型注解常量**：`MinFreeBytes: Int64 = 7516192768;`
   编译不过，写无类型形式 `MinFreeBytes = 7516192768;`。

6. **多源下载必须做「不可达源黑名单」**（2026-11-01 用户实测复现）：
   模型仓库有 5 个小文件（`tokenizer.json` 等）在国内镜像站上不存在，而另外两个源
   （ModelScope / 官方 HF）直接连不上（WinError 10060）。旧逻辑对每个缺失文件都把
   三个源各试两遍、每个不可达源干等 60 秒 × 2 次——5 文件 × 2 仓库白等 20~40 分钟，
   而权重早已下完，用户看到的就是"下不动"（安装其实早已完成，进程最后自行退出）。
   现 `download()` 接受 `timeout/tag/dead`：超时与连接类失败把 tag 记入调用方的
   会话级 `dead` 集合，`_fetch_repo()` 后续文件直接跳过该源；小文件超时 60→20 秒；
   403/404 **不拉黑**（源可达只是没这个文件，换个文件同一源可能就有）。
   隔离测试（本地 HTTP 服务模拟正常/404/挂起）10 项断言全过。

7. **判断"下载卡住"先看文件系统**：本次用户报"下不动"时，`models\` 里两个权重
   （1.8 GB × 2）和 8 个文件都已就位、`_deployed.json` 已写入——进程在做无谓的
   可选文件重试。排查顺序：先 `ls models\` 看权重是否完整、看 `_setup\_deployed.json`
   是否已部署，再决定是等、是杀、还是修代码。
