---
title: 参考 · 模块清单
type: reference
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §4「核心模块清单」
superseded_by: —
---

# 参考 · 模块清单

## 一句话摘要

按文件查「谁干什么、入口叫什么」，用于定位代码。

## 当前有效结论

- 规范源码根目录是 `01_源码\`；Lua 前端是**单文件** `AsrSubs.lua`，Python 引擎由 15 个 `.py` 组成。
- 2026-11-05 实测：`verify_all.py` 的「Python 语法」检查覆盖 **16 个文件**；`sync_payload.py --check` 比对 **18 个 payload 文件**，结果一致。
- `qwen_version.py` 的 `VERSION` 由 `sync_version.py` 生成，**勿手改**。

## 关键变更（仅影响使用的里程碑）

- v3.2.6 起改为 Lua 单文件制，`lua_src\` 13 分片已归档；`tools\build_lua.py` 从拼接器退化为只读体检器。
- v4.0.9 起测试与探针脚本统一放 `01_源码\tools\`，`engine\` 只留运行时文件。

## 操作 / 参考细节

### 引擎模块（来源：`archive\archive-dev-doc-20261105.md` §4）

| 文件 | 职责 | 关键入口 / 函数 |
|---|---|---|
| `AsrSubs.lua` | Fusion UI + 异步状态机；命令分发、Tick 轮询、落轨与写回 | `App:Tick`、`App:StartLand(mode,reason)`、`App:SaveSubs` |
| `worker.py` | 常驻后台执行器，消除 CMD 黑窗闪现 | 常驻文件邮箱接任务 |
| `resolve_bridge.py` | 识别入口；参数走文件（绕开 GBK 命令行） | `python resolve_bridge.py <cfg.json>` |
| `asr_server.py` | 常驻 ASR 服务，模型只加载一次 | `rt\asr_cmd.json` 任务邮箱 |
| `asr_engine.py` | 识别 + 强制对齐 + 切句 + 排时间 | Qwen3ASR 双模型 |
| `subs_io.py` | 字幕存取桥（SRT ↔ Lua 表 / JSON） | `mode=dump` / `mode=save` |
| `land_subtitle.py` | 挂字幕轨 + 导入媒体池；R2 闭环 | `land_inplace()`、`relang_subtitles()` |
| `cut_inplace.py` | 原地粗剪；R1 区间校准 | `do_cut_inplace()` |
| `cut_timeline.py` | 粗剪入口（按 `engine=inplace` 分发） | `python cut_timeline.py <cfg.json>` |
| `timeline_source.py` | 读时间线音频片段信息 | 输出 JSON 到 `rt/src.lua` |
| `corrections.py` | 权重词纠错（确定性替换，不靠模型） | 等号表解析 |
| `hotwords.py` | 全文高频热词统计（比例阈值） | 写 `rt/hotwords.lua` |
| `txt_out.py` | TXT 文稿输出 | 60 列重排 |
| `export_files.py` | 导出 srt/txt 到用户目录 | `python export_files.py <cfg.json>` |
| `diag.py` | 一键打包诊断信息 | 产出 `.zip` |
| `qwen_version.py` | 版本号常量（`sync_version.py` 生成） | `VERSION`、`banner()` |

### 配置与资源（2026-11-05 实读 `01_源码\`）

| 文件 | 内容 |
|---|---|
| `version.txt` | `1.2.0`，版本号唯一来源 |
| `models.txt` / `models.lua` | `Qwen3-ASR-0.6B-hf`，模型下拉清单 |
| `paths.json` / `paths.lua` | 路径配置 |
| `render_to_wav.xml` | 渲染预设 |
| `rt\` | 运行期文件邮箱（运行产物，非源码） |

### 构建 / 校验脚本（`01_源码\tools\`）

`build_lua.py`（只读体检）、`sync_version.py`、`sync_payload.py`、`verify_all.py`、`test_can_start.py`、`test_land_order.py`、`test_subtrack_info.py`、`test_close_page.py`、`cleanup_empty_sub_tracks.py` 等。完整测试清单见 `reference-tests.md`。

## 失效内容说明

- 旧 `archive\archive-carryover-20261031.md` §0 写「开发目录由 `lua_src\` 13 片经 `build_lua.py` 拼接」——**对象已不存在**，不可照做。
- 本表只登记职责与入口，**不登记行数、MD5、版本快照**；这类易漂移数据请以 `verify_all.py` 实时输出为准。
