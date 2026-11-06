---
title: 设计 · 时间线就地操作（落轨 / 重挂 / 粗剪）
type: design
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §6.1 / §6.2 / §6.3 / §6.7 / §6.9
superseded_by: —
---

# 设计 · 时间线就地操作

## 一句话摘要

所有落轨、重挂、粗剪都在**当前已打开的时间线**上原地完成；本页是这套操作的决策依据与不可违反的顺序约束。

> 适用条件：本页结论来自 `archive\archive-dev-doc-20261105.md` 记录的 2026-10-31 ~ 11-01 真机实测（Resolve Studio 21.1.2.10）。本次治理**没有重跑真机**；若 Resolve 版本升级，须用 `..\reference\reference-resolve-api.md` 的方法重新取证。

## 当前有效结论

| # | 结论 | 违反后果 |
|---|---|---|
| 1 | **绝不新建时间线**；`land_inplace` 失败只报错、不静默新建 | 违反项目红线，见 `..\adr\adr-0001-in-place-timeline.md` |
| 2 | **导入必须先于任何时间线写操作**：`import_to_pool()` 在 `land_inplace()` 的 `SetTrackEnable` / `AddTrack` / `SetCurrentTimecode` 之前 | 先动时间线会触发 Resolve 自动保存工程，紧接着 `ImportMedia` 偶发返回空列表 → 表现为「落 0 条」 |
| 3 | 找轨顺序：**① 读边车定位插件专用轨 → ② 第一条空轨 → ③ 才 `AddTrack` 新建** | 旧逻辑「找空轨否则新建」会凭空造出空 ST2 |
| 4 | 边车 `<srt>.landed.json` **只在 `placed > 0` 时写**；失败时保留旧边车 | 失败的落轨会把边车覆写成 `{track:2, spans:[]}`，把后续 R2 彻底带偏 |
| 5 | 落轨失败或实际落点≠自建轨、且该轨为空 → 用 `DeleteTrack` 回收自建轨；**非空轨一律拒删** | 否则时间线上留下孤儿空字幕轨 |
| 6 | SRT 条数为 0 → **在动时间线之前早退**，连字幕轨都不碰 | — |
| 7 | 启用态定向只用 **`SetTrackEnable`（无 d）**，启用后必须 `GetIsTrackEnabled` 回读校验，最多 5 次 | 21.1.2 上 `SetTrackEnabled` / `SetTrackEnableState` 属性为 `None`，旧包装函数静默失败 → 多轨时落错轨并把整批字幕顺移到该轨末尾（实测首帧 30 → 420） |
| 8 | 字幕帧位置用 `GetStart()/GetEnd()`，**不能用 `GetStartFrame()/GetEndFrame()`** | 后者在字幕轨上返回 `None`，R1 校准会恒为 0（等于失效） |
| 9 | **21.1.2 没有任何原生 API 能修改字幕文本**；「导出 SRT → 重导入 → `AppendToTimeline`」是唯一受支持的更新路径 | 见 `..\reference\reference-resolve-api.md` |
| 10 | 每次刷新字幕轨清单都重新取 `GetCurrentTimeline()` | `App.timeline` 是开窗那一刻的快照，换集后会过期 |

## 关键变更（仅影响使用的里程碑）

- v4.0.6：R1 改用 `GetStart/GetEnd`；R2 从「stem 匹配」改为「边车 spans + 专用轨整轨清除」。
- v4.0.7：修掉「适配字幕」凭空造 ST2、失败落轨污染边车、界面误报删旧条数三项。
- v4.0.8：落轨失败回收自建空轨；空 SRT 早退。
- v4.1.0 / 4.1.1：确立顺序铁律与 `SetTrackEnable` 启用回读护栏。
- 逐版本细节见 `..\CHANGELOG.md`，本页只留当前规则。

## 操作 / 参考细节

### R1 · 粗剪区间校准

`cut_inplace.py` 的 `_subtitle_offset_frames(tl, cuts_sec, fps, …)` 用时间线**真实字幕轨位置**反推「SRT 内部秒数 → 时间线帧」的整体偏移：对每个切除区间，只在时长相近的真实字幕里取最接近的一条投一票（按帧偏移计票），取票数显著高于次高者作为校准值；容差 `max(2.0, 0.05*区间帧长)`；无共识返回 0（保持旧行为）。校准在 `C.merge(raw)` 之后执行。

### R2 · 字幕更新闭环重挂

`land_subtitle.py` 的 `relang_subtitles()` 顺序：

1. 记录当前各字幕轨启用状态。
2. 删掉**本插件旧字幕**（`clear_our_subtitle_items`），保留用户手工字幕轨。
3. 删到了旧字幕 → `land_inplace` 重挂 → 恢复其它轨启用态、确保落轨轨启用。
4. 时间线上本就没有本插件旧字幕 → 退化为「只入媒体池」，**不新建字幕轨**。

Lua 侧：时间线开着 → `StartLand("relang","sync")` 闭环重挂；否则 `StartLand("media","sync")` 只同步媒体池。

**边车字段**（键统一用原始 SRT 路径 `meta_key`，不用偏移后的临时副本路径）：`track` 专用轨索引、`spans` 上次落轨各条起止帧（最强证据）、`owned` 该轨是否全由插件产生、`tl` 时间线名（防跨时间线串扰）、`count` 上次落轨条数。`_dedicated_info()` 定位优先级：spans 命中 → 边车轨非空 → 唯一非空轨 → 条数吻合 → 认边车 → 跨时间线判定为「无」。`owned=False` 时只按 spans 精确删，**绝不误删用户手工字幕**。

### 文本更新：API 优先，达不到就整批退回

先试 `SetName()` 直改，四个前置条件缺一即退回 SRT 往返：轨 `owned=True`、轨上条目数 == SRT 条数、每条按起始帧（±2）都能配上、每条 `SetName` 后 `GetName` 回读一致。本会话一旦回读失败即置 `_SETNAME_CAPS["ok"]=False`，后续直接走往返。

### 导入与兜底

`import_to_pool()`：删同名旧项 → 第 1 轮原路径 → 空则第 2 轮换目录副本（**保文件名**）+ `RefreshFolders()` → 每轮回查 → 全败抛 `RuntimeError`。`AppendToTimeline` 后按 ID 差集判成败，没落上再试 `InsertIntoTimeline`（播放头已钉在时间线起点，语义等价）；判据不用返回值（21.1 返回 `None`）。

落轨结束打印单行摘要便于统计：`[land] result=ok|fail placed=.. total=.. track=.. first=.. srt_first=.. offset=.. aligned=.. reclaimed=..`

### 回归锁

`tools\test_land_order.py`：用例 1/6 锁顺序铁律、用例 4 锁直改不碰媒体池、用例 5 锁回读失败护栏、用例 7 锁「启不起来就不落、一条都不落」。`tools\test_r2_relang.py`（16 用例）、`test_r1_offset.py`（7 用例）、`verify_v407_live.py` / `verify_v408_live.py`（真机）。

## 失效内容说明

- 「新建空时间线先落字幕、再复制素材」是 v3.2.6 及更早的做法，**已被本页结论 1 推翻**（见 `archive\archive-carryover-20261031.md` §6.2，该文档自标过期）。
- 「按来源媒体项 stem 匹配识别本插件字幕」已废弃：`TimelineItem.GetMediaPoolItem()` 在字幕轨上返回 `None`。
- 「`SetName` 直改能改字幕文本」在 21.1.2 上实测 3/3 失败，只保留为**未来版本的前向尝试**，不是当前能力。
- §6.7.3 / §9.5 等整理过程与验收流水账不在此页，保留在归档件中。
