---
title: 设计 · 系统架构与部署地图
type: design
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §1 / §2 / §3
superseded_by: —
---

# 设计 · 系统架构与部署地图

## 一句话摘要

AsrSubs 由「Fusion Lua 单文件界面」和「常驻 Python 子进程」组成，两者靠磁盘文件邮箱通信；本页给出这套结构以及四份 `AsrSubs.lua` 的部署关系。

## 当前有效结论

| 结论 | 适用条件 / 依据 |
|---|---|
| 全程本机离线推理，音频不出机器 | `archive\archive-dev-doc-20261105.md` §1；模型 `Qwen3-ASR-0.6B-hf` + `Qwen3-ForcedAligner-0.6B` |
| 形态是 Fusion Lua UI + 常驻 Python 子进程，通过 Resolve 原生 `DaVinciResolveScript` API 操作时间线 | `archive\archive-dev-doc-20261105.md` §1 |
| **进程间不开任何网络端口**，一律走 `engine\rt\` 文件邮箱 | `.json` 收任务、`.lua` 回结果、`status.lua` 报进度、`hb` 心跳、`asr_cmd.json` 服务任务（`archive\archive-dev-doc-20261105.md` §2） |
| `ASCII_ROOT` 在 Lua 里指向 `D:\AsrSubs\engine`，即 Resolve 实际加载的 Python 引擎根 | `archive\archive-dev-doc-20261105.md` §2 |
| 规范源码是 `01_源码\AsrSubs.lua` **单文件**；`tools\build_lua.py` 只做只读体检，不生成文件 | `build_lua.py` 文件头；2026-11-05 实测校验输出「单文件制 v1.2.0，7521 行」 |
| 当前打包只走 `02_构建与安装器\build.bat` + `payload\`；`engine\packer\` 冗余副本已于 v4.0.9 归档 | `archive\archive-dev-doc-20261105.md` §3 / §9.5.1 |
| 时间线操作绝不新建时间线 | 见 `adr\adr-0001-in-place-timeline.md` |

> ⚠ **待人工确认**：`archive\archive-dev-doc-20261105.md` §3 记的「四份 Lua MD5 `6b9e249f…` / `71867272…`」是 2026-11-01 快照，本次治理**未重新实测** `D:\AsrSubs\engine` 与 `%ProgramData%` 两份的当前 MD5，故本页不写具体哈希值。

## 关键变更（仅影响使用的里程碑）

- v4.0.9（2026-10-31）：`engine\packer\` 2.x 旧打包区（55 MB）归档，打包路径收敛到 `02_构建与安装器`。
- v3.2.6 起（2026-10-30）：源码从 `lua_src\` 13 分片改为**单文件制**，避免「拼接出旧版本」事故。
- 逐版本演进见 `..\CHANGELOG.md`，本页不复述。

## 操作 / 参考细节

### 组件拓扑

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
      ├─ cut_timeline.py      粗剪入口（按 engine=inplace 分发）
      ├─ export_files.py      导出 srt/txt
      ├─ corrections.py       权重词纠错（确定性替换）
      ├─ hotwords.py          全文高频热词统计
      ├─ txt_out.py           TXT 文稿输出
      └─ diag.py              一键诊断包
```

模块职责查表见 `..\reference\reference-modules.md`。

### 四份 `AsrSubs.lua` 的部署关系

| 位置 | 角色 |
|---|---|
| `01_源码\AsrSubs.lua` | 规范源码（唯一改代码的地方） |
| `D:\AsrSubs\engine\AsrSubs.lua` | 实时引擎（被测试 / 被加载，`ASCII_ROOT`） |
| `02_构建与安装器\payload\AsrSubs.lua` | 安装包载荷（由 `sync_payload.py` 同步） |
| `%ProgramData%\…\Fusion\Scripts\Utility\AsrSubs\AsrSubs.lua` | Resolve 菜单实际加载 |

**四份必须 MD5 一致**。原第 5 份 `D:\AsrSubs\engine\packer\payload\AsrSubs.lua` 已随 packer 整体归档，理由：多一份就多一个漂移源。

`models.lua` / `models.txt` / `paths.json` / `paths.lua` **不进安装包 payload**（Lua 从 `ASCII_ROOT` 引擎目录读），但**必须**随 engine 一起部署。

### 目录地图

```
D:\插件\AsrSubs_整合_20261031\       开发目录（规范源码 + 文档）
├─ 01_源码\                        AsrSubs.lua + 16 个 .py + 配置 + tools\
├─ 02_构建与安装器\                AsrSubs.iss / installer.py / payload\ / build.bat
├─ 03_文档\                        文档体系（见 INDEX.md）
└─ 04_安装包\                      安装器成品

D:\AsrSubs\engine\                 实时引擎（ASCII_ROOT）
D:\AsrSubs\engine\rt\              运行期文件邮箱
```

同步与巡检的可执行步骤见 `..\guides\guide-dev-workflow.md`。

## 失效内容说明

| 原 §3 的说法 | 判定 |
|---|---|
| 「`AsrSubs.lua` 源码本体（v4.8.18，6935 行）」 | **已失效**，2026-11-05 实测为 1.2.0 / 7521 行 |
| 「四份 MD5 `6b9e249f…` / `71867272…`」 | **快照过期**，不得作为当前一致性判据；请重新计算 |
| 「`99_归档\` 在本包内」 | **已移出本包**，副本在 `D:\AsrSubs_整合_删除前备份_20261031\99_归档\` |
| §0 文档定位表（称 README 刷新到 v4.8.14、AI交棒文档偏旧） | **整节作废**，以 `..\INDEX.md` 为准 |
