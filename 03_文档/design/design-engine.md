---
title: 设计 · Python 引擎
type: design
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §2 引擎部分 / §4
superseded_by: —
---

# 设计 · Python 引擎

## 一句话摘要

引擎侧用「常驻服务 + 文件邮箱」把识别、对齐、切句、落轨串成一条无需网络端口的离线流水线；本页说明几个关键设计取舍。

## 当前有效结论

| 设计 | 结论 | 为什么 |
|---|---|---|
| 常驻执行器 | `worker.py` 常驻，用 `pythonw` + `CREATE_NO_WINDOW` 启动 | 消除每次 RunTask 闪现 CMD 黑窗（`archive\archive-dev-doc-20261105.md` §2） |
| 常驻 ASR 服务 | `asr_server.py` 让模型只加载一次；`resolve_bridge.py` 优先转发常驻服务，失败才回退本地加载 | 模型加载耗 10~30 秒，反复加载不可接受 |
| 通信方式 | 全部走 `engine\rt\` 文件：`.json` 收任务、`.lua` 回结果、`status.lua` 报进度、`hb` 心跳、`asr_cmd.json` 服务任务 | **不开任何端口**；参数走文件也绕开 GBK 命令行编码问题 |
| 字幕存取桥 | `subs_io.py` 负责 SRT ↔ Lua 表 / JSON（`mode=dump` / `mode=save`） | Lua 的 `io.open` 打不开中文路径 |
| 参数真源 | 识别参数从 `App.S.cfg` 读，控件读不到时用 `App.S.uiSnap` 兜底 | 设置弹窗未打开过也能正常识别（`archive\archive-dev-doc-20261105.md` §6.10.3） |
| 唯一写文件入口 | 只有「更新字幕 · 写回 SRT」写 SRT + 同步媒体池 / 闭环重挂；删除、替换、撤销只改内存并标脏 | 防止静默写盘（旧 README §1） |
| 识别链路 | `asr_engine.py` 完成识别 + 强制对齐 + 切句 + 排时间；写盘前统一做一次行尾标点收口 | v1.2.0 起，收口放在 `refold_overlong_text` 之后，见 `..\CHANGELOG.md` |

> ⚠ **待人工确认**：`asr_server.py` 常驻服务在 1.2.0 下的实际启停条件、以及「失败回退本地加载」的触发阈值，本次治理未逐行核对代码，只沿用开发文档的既有结论。

## 关键变更（仅影响使用的里程碑）

- v1.2.0：出口收口行尾标点；`audio_trim` 每轮归零（防字幕整体错位）。详见 `..\CHANGELOG.md`。
- v1.1.3：单片段 + 速度 1 时，掐头 / 中间取用 / 移位都直接抽用到的那一段音频，不再渲染整条；按**源 fps** 换算。
- v4.0.9 起：测试与探针脚本迁到 `01_源码\tools\`，`engine\` 只留运行时必需文件。

## 操作 / 参考细节

### 模块速查

每个文件的职责与入口函数见 `..\reference\reference-modules.md`；本页不复述表格。

### 识别入口与裁剪（1.2.0 现状）

| 时间线形态 | 处理方式 |
|---|---|
| 单片段、速度 1（含掐头 / 从素材中间取用 / 移到时间线后面） | 直接抽「用到的这一段」音频；字幕时间从 0 起算，落轨时按片段起始帧整体偏移 |
| 多个片段 / 片段被变速 / 多轨同时出声 | 先渲染导出音频再识别，慢一些但声画一定对齐 |

来源：`03_文档\使用说明.txt`「三步上手」段（旧版行号 217–229 对应 v1.2.4 及更早）。

### 验证方式

- 离线：`py -3.12 tools\verify_all.py`（当前实测 18 项，见 `..\reference\reference-tests.md`）。
- 语法：`py -3.12 -m py_compile <file>`。
- 真机：需在 Resolve 里开启「外部脚本使用」，脚本清单见 `..\reference\reference-tests.md`。

## 失效内容说明

- `archive\archive-dev-doc-20261105.md` §5 的版本号规则（「次版本号 +1 / 主版本号 +1」）**已被 v1.0.0 的三段式规则取代**，见 `..\adr\adr-0003-version-reset-1.0.0.md`。
- §4 模块清单里写的「`txt_out.py` TXT 文稿输出（全项目唯一实现）」等表述属于当时快照；文件是否仍唯一未复核，故本页不做该断言。
- §6.11–6.18 的逐版本实现细节是过程记录，已抽取影响使用的部分进 `..\CHANGELOG.md`，其余保留在归档件中。
