---
title: AsrSubs 项目概览
type: README
status: Active
version: 1.2.5
last_update: 2026-11-06
applies_to: AsrSubs 1.2.5（01_源码/version.txt）
maintainer: Jian
supersedes: 03_文档\archive\archive-readme-merged-20261101.md（发布流程部分，已迁入 03_文档\guides\guide-release.md）
superseded_by: —
---

# AsrSubs — DaVinci Resolve 离线字幕插件

## 一句话摘要

在 DaVinci Resolve 里把时间线音频识别成字幕并挂到**当前时间线**，全程本机离线推理，音频不出机器。

## 当前有效结论

| 项 | 结论 | 依据 |
|---|---|---|
| 当前版本 | **1.2.5** | `01_源码\version.txt`（唯一来源） |
| 版本注入点 | `AsrSubs.lua` / `AsrSubs.iss` / `installer.py` / `qwen_version.py` / `03_文档\使用说明.txt`，共 5 处，由 `sync_version.py` 注入 | `tools\sync_version.py`；本次 `--check` 实测五处一致 |
| 识别模型 | `Qwen3-ASR-0.6B-hf` + `Qwen3-ForcedAligner-0.6B`（词级强制对齐） | `01_源码\models.txt`、项目说明 |
| 程序形态 | Fusion **Lua 单文件 UI** + 常驻 **Python 子进程**；进程间靠 `engine\rt\` 下的文件通信，**不开任何网络端口** | `03_文档\archive\archive-dev-doc-20261105.md` §2、§5（归档件） |
| 架构红线 | **只在当前时间线原地落轨，绝不新建时间线**；R3（`timeline.Export` 导 SRT）已放弃 | `03_文档\adr\adr-0001-in-place-timeline.md` / `adr-0002-abandon-r3-export.md` |
| 源码形态 | 单文件制，`01_源码\AsrSubs.lua` 就是唯一源码；`tools\build_lua.py` 已退化为只读体检器，不生成文件 | `tools\build_lua.py` 文件头 |
| 文档唯一入口 | `03_文档\INDEX.md` | 本次治理新建 |

> **部署状态（2026-11-06，第 8 批复测）**：v1.2.1 的 `AsrSubs.lua` 已部署到两处（Resolve 菜单与 `D:\AsrSubs/engine`），四份 MD5 一致（`2e1e5908…`）；engine 的 Python 全套已同步到 1.2.1（`version.txt` / `qwen_version.py` 均为 1.2.1）。`verify_all.py` **18 项全部通过（含 1 项跳过）**，`sync_dev.py` 只读巡检显示「engine 与 01_源码 规范文件集完全一致」。详见 `03_文档\reference\reference-tests.md`。
>
> ⚠ **边界（仍成立）**：验收的「部署一致」只比 `.lua` 与 `paths.json`，**不比 engine 的 `.py`** —— engine 已同步这件事须另跑 `sync_dev.py` 只读巡检确认，不能仅凭验收全绿推断。安装包**已做静默装 / 卸闭环验证**（2026-11-06 08:29，临时目录，装 2.6 秒 / 卸 1.6 秒均退出码 0，`D:\AsrSubs` 与 Resolve 脚本目录零改动），但**下载部署与覆盖升级未测**；真机验证进展见 `\03_文档\reference\reference-tests.md`：①「有片段」、②-1、③ 目标分支、渲染后页面还原均已实测通过，① 的空时间线 / 仅字幕轨两场景、A / B 场景与 ② 的 2–4 步仍待实测。

> ✅ **NOTICE.md 已修（第 5 批改第 2 条，第 6 批改第 1 条）**：第 2 条「空时间线兜底」按 `land_subtitle.py:30–45` 与 `adr-0001` 改写为「对齐校验与失败处理（原地路径内）」；第 1 条按 `land_subtitle.py:1004–1009` / `:21–25` 改写为「禁用全部字幕轨 → 播放头钉到时间线起点 → `AppendToTimeline` 只传 `{mediaPoolItem}`」，并删掉与当前实现不符的「占位 cue 定位原点」「还原字幕轨」表述。标题括注「自研双策略」→「自研」。**2026-11-06 第 7 批复核**：按官方 `README.md:595–602` / `pyi:124–135` 改正第 23–24 行，把「不能传 `startFrame/endFrame/mediaType`」由「官方 API 禁令」纠正为「本项目对字幕媒体项的实测约束」，并清掉开头「占位 cue」残留

## 文档怎么找

`03_文档\INDEX.md` 是唯一总表，逐篇标注路径、状态与「文件是否已存在」。请按 INDEX 的实存标记找资料，不要凭旧文档名猜路径。

**治理进度**：第 1–8 批已完成（骨架 / 概览 / CHANGELOG → 开发文档拆分 → 安装发布资料迁移 → 归档与清理 → NOTICE 修订 + 菜单脚本部署 → engine 同步 + NOTICE 第 1 条 + 防腐化机制 → NOTICE API 复核 + 真机探针 + 旧产物清理 → 真机验证补齐 + 1.2.1 出包）。`INDEX.md` 第三节「计划中」已清空；`verify_all.py` 18 项全绿、`sync_dev.py` 显示 engine 与源码完全一致。剩余待办：**A / B 场景与 ② 的 2–4 步真机验证**（① 对照组、②-1、③ 目标分支、渲染后页面还原均已通过，见 `03_文档\reference\reference-tests.md`）；**安装包的下载部署与覆盖升级未跑**（静默装 / 卸闭环已于 2026-11-06 08:29 验证通过）。旧构建产物（1006 文件 / 33 MB）与 5 个旧安装包已于 2026-11-06 **彻底删除**（删前 MD5 逐条校验相符，合计释放约 92.6 MB，本机已无可回滚副本）。

| 需求 | 去哪里 |
|---|---|
| 用户操作 | `03_文档\使用说明.txt`（随安装包分发，`.iss` 直接引用，**不得改名或迁移**） |
| 变更历史 | `03_文档\CHANGELOG.md` |
| 架构 / 引擎 / 界面 / 时间线 / 安装器设计 | `03_文档\design\design-*.md` |
| 改代码、同步、防漂移 | `03_文档\guides\guide-dev-workflow.md` |
| 文档维护 / 防腐化机制 / 巡检规则 | `03_文档\guides\guide-doc-maintenance.md` |
| 提交前自动巡检（只读） | 项目根 `check_docs.py`，命令 `py -3.12 check_docs.py --strict --gates` |
| 安装 / 卸载 / 升级 | `03_文档\guides\guide-install.md` |
| 出包与发布（严格按序） | `03_文档\guides\guide-release.md` |
| 故障处置 | `03_文档\guides\guide-troubleshoot.md` |
| 模块职责、Resolve API、测试、已知限制、安装器坑 | `03_文档\reference\reference-*.md` |
| 逐版安装包参数（大小 / MD5 / 验证状态） | `03_文档\reference\reference-release-notes.md` |
| 架构决策（红线、放弃 R3、版本规则） | `03_文档\adr\adr-*.md` |
| 模板与规范 | `03_文档\_TEMPLATE.md` |
| 改前快照 | `03_文档\archive\README-20261105.md`（重写前原文 35,620 字节；文首 4 行为归档标注，去掉即可逐字节还原） |
| 归档清单与还原方法 | `03_文档\archive\README.md`（8 件归档件 + 反向 mv 命令） |

## 目录结构（2026-11-05 实测）

```
D:\插件\AsrSubs_整合_20261031\
├─ 01_源码\                 规范源码：AsrSubs.lua（7,521 行）+ 16 个 .py + 配置 + tools\
│   ├─ version.txt          版本号唯一来源（1.2.0）
│   └─ tools\               构建 / 校验 / 测试 / 同步脚本
├─ 02_构建与安装器\          AsrSubs.iss、installer.py、payload\、build.bat
│   └─ dist\                PyInstaller 与 Inno Setup 产物（可再生成）
├─ 03_文档\                 文档体系（见 INDEX.md）
│   ├─ 使用说明.txt          用户手册（原位，三处脚本引用）
│   ├─ INDEX.md  CHANGELOG.md  _TEMPLATE.md
│   ├─ design\  guides\  reference\  adr\     Active 文档 21 篇
│   └─ archive\                 8 件已废弃原件（含 2 张 PNG），见 INDEX.md 第二节
├─ 04_安装包\                v1.2.1 成品 + 上一版 v1.2.0 + README_说明.txt
├─ README.md                 本文件
├─ NOTICE.md                 致谢与技术来源（第 5 / 6 / 7 批修订）
├─ sync_dev.py               开发目录同步 / 巡检工具
├─ check_docs.py             文档防腐化巡检（只读，默认零外部命令）
└─ _旧构建_1.2.0_20261106\         1.2.0 的 dist 构建目录
```



## 常用命令（可执行）

构建类在 `01_源码\` 下执行；同步 / 巡检在项目根执行。本机 `D:\AsrSubs\runtime\Scripts\python.exe` **缺 tkinter**，打包须用 `py -3.12`（3.12.9 + PyInstaller 6.22.3 + tkinter 8.6）。

```bat
:: 一键验收
py -3.12 tools\verify_all.py
:: 版本号是否同步（只读）
py -3.12 tools\sync_version.py --check
:: payload 是否与源码一致（只读）
py -3.12 tools\sync_payload.py --check
:: 改完 version.txt 后注入 5 处
py -3.12 tools\sync_version.py
:: 源码同步进安装包载荷（打包前必做）
py -3.12 tools\sync_payload.py
```

```bat
:: 只读巡检：engine ↔ 01_源码 差异 + 版本一致 + payload MD5
py -3.12 sync_dev.py
:: 回灌已测代码（--deploy 会先回灌，可能覆盖 01_源码 的改动，慎跑）
py -3.12 sync_dev.py --sync
```

**改版本号只有一步链**：编辑 `version.txt` → `sync_version.py` → `sync_payload.py`（必要时 `sync_dev.py --deploy`）。

## 安装后目录与两条硬约束

安装器默认装到 `D:\AsrSubs`，向导可改任意本地固定盘 / 目录。两条约束缺一不可：

1. **路径必须纯 ASCII** —— Lua 的 `io.open` / `os.execute` 走 ANSI，中文根目录会导致「界面能开、一跑就超时」。
2. **必须是本地固定磁盘** —— U 盘或网络盘不可用。

| 位置 | 内容 |
|---|---|
| `<base>\_setup\` | 安装器本体（PyInstaller onedir） |
| `<base>\engine\` | 引擎 `.py` + `AsrSubs.lua` + `render_to_wav.xml` + 配置 |
| `<base>\engine\rt\` | 运行期文件邮箱（`args` / `status` / `hb` / `cmd` / `cut` / `env.lua`） |
| `<base>\runtime\Scripts\python.exe` | Python 3.12 + torch |
| `<base>\models\` | `Qwen3-ASR-0.6B-hf\` + `Qwen3-ForcedAligner-0.6B-hf\`（必须直接在一层） |
| `<base>\data\output\` | **字幕产物，卸载不删** |
| `<base>\data\logs\` / `data\cache\` | 日志 / 断点续跑缓存 |
| `<base>\docs\` | 使用说明 |
| `%ProgramData%\…\Scripts\Utility\AsrSubs\AsrSubs.lua` | Resolve 菜单实际加载的那份 |

**菜单只写一处**（`%ProgramData%\…\Utility\AsrSubs\`）：三个脚本根目录 Resolve 都会扫，多写会出现两个同名菜单项。

## 已知限制（摘要）

完整清单见 `03_文档\reference\reference-limitations.md`（含 v1.2.0 待真机验证三项）。高频几条：安装包本体约 12 MB，运行环境与模型装完在线下载（3.4~6 GB），断网机器须先备好 `engine` + `models`；跨窗上下文 `CTX_CHARS = 0`；撤销仅一级；粗剪重建的片段不带转场 / Fusion 特效 / 调色节点。

