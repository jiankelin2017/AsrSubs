---
title: ADR-0002 · 放弃 R3（用 timeline.Export 导出 SRT）
type: adr
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §6.4
superseded_by: —
---

# ADR-0002 · 放弃 R3（用 `timeline.Export` 导出 SRT）

| 项 | 值 |
|---|---|
| 状态 | **已放弃**（技术不可行） |
| 决策时间 | 2026-10-31（v4.0.x 期间） |
| 影响范围 | 字幕导出路径；保留「文件复制式」导出 |

## 一句话摘要

R3（用 `timeline.Export` 导 SRT）在 DaVinci 21.1 上技术不可行，已放弃，字幕导出继续走文件复制 / SRT 往返。

## 当前有效结论

- **决策**：放弃 R3，保留现有「文件复制式」导出。
- **状态**：已放弃（最终结论），2026-10-31 确立，此后未变。
- 「导出 SRT → 重导入 → `AppendToTimeline`」是 21.1.2 上唯一受支持的字幕更新路径。

## 关键变更（仅影响使用的里程碑）

- 2026-10-31（v4.0.x 期间）：R3 真机验证不可行，决定放弃，保留「文件复制式」导出。
- 2026-11-05：固化为 ADR-0002；`..\CHANGELOG.md` 中原先的「待建」引用已改为指向本文件。此后未变。

## 背景

R1（粗剪区间校准）、R2（字幕更新闭环重挂）之后曾规划 R3：用 `timeline.Export` 直接把时间线字幕导成 SRT，以取代「读 SRT 文件 → 复制 / 重导入」的路径。

## 决策

**放弃 R3，保留现有「文件复制式」导出。**

## 理由（真机探针 `probe_r15.py` 结论）

1. 当前 DaVinci 21.1 的 `TimelineExportType` 枚举**没有 SRT / 字幕类导出类型**（只有 EDL、FCPXML、AAF、CSV、ALE、OTIO 等），也无 `EXPORT_SRT` 常量。
2. `TimelineItem` **没有任何 API 能回读字幕文本**（`SetName` / `SetProperty` 6 键全失败，时间线级 `ImportSubtitleFromFile` 等属性均为 `None`），见 `..\reference\reference-resolve-api.md`。

两点叠加，即使能调 `Export` 也拿不到可用的字幕文本，R3 技术上不可行。

## 后果

| 正面 | 负面 |
|---|---|
| 不必维护一条依赖特定 Resolve 版本的导出分支 | 字幕更新仍走「导出 SRT → 重导入 → `AppendToTimeline`」，这是 21.1.2 上**唯一受支持的路径** |
| 导出行为在各版本 Resolve 上一致 | 需要持续维护 SRT ↔ Lua 表桥（`subs_io.py`） |

## 操作 / 参考细节

本决策不提供操作步骤，只列落地位置与查阅入口：

- 字幕更新走「导出 SRT → 重导入 → `AppendToTimeline`」；SRT ↔ Lua 表桥在 `subs_io.py`。
- 导出类型枚举与 `TimelineItem` 回读能力的实测结论见 `..\reference\reference-resolve-api.md`。
- 若 Resolve 新增字幕导出 API，须另开 ADR 重新评估，不得直接改本页结论。

## 失效内容说明

- 「R3 待做 / R3 是下一步」这类表述（散见于旧版待办）**不成立**，本 ADR 是最终结论。
- 若将来 Resolve 新增字幕导出 API，本 ADR 需重新评估并另开一篇，不得直接改结论。
