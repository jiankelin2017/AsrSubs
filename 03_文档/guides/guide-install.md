---
title: 指南 · 安装 / 卸载 / 升级
type: guides
status: Active
version: 1.0.0
last_update: 2026-11-06
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: 03_文档\使用说明.txt「新电脑安装」/ 04_安装包\README_说明.txt「安装 / 卸载」段
superseded_by: —
---

# 指南 · 安装 / 卸载 / 升级

## 一句话摘要

当前可装成品是 **v1.2.4**（`04_安装包\AsrSubs_v1.2.4_安装器.exe`）；源码已到 v1.2.5 但**暂未出包**；双击 → 选位置 → 两次「下一步」即自动装完，**卸载默认保留字幕产物**。

## 当前有效结论

| 结论 | 依据 |
|---|---|
| 安装包本体约 12 MB，**只含安装核心**；运行环境 + 两个 ASR 模型（3.4~6 GB）装完在线下载 | `04_安装包\README_说明.txt`；`design-installer.md` |
| 两条硬约束：**安装路径必须纯 ASCII**（中文路径表现为「界面能开、一跑就超时」）、**必须是本地固定磁盘**（U 盘 / 网络盘不可用） | `03_文档\使用说明.txt`「新电脑安装」；`design-installer.md` |
| 默认装到 `D:\AsrSubs`；向导第一页预填「空间最大的本地固定盘」，可改任意本地盘的任意目录 | 同上；`reference-installer-pitfalls.md` #3 |
| 有 NVIDIA 卡默认选 **GPU 版**（多下约 2.5 GB，识别快数倍）；装完安装器会**亲测 CUDA 是否真的可用**并写明原因 | `03_文档\使用说明.txt`「新电脑安装」 |
| 装过 CPU 版想换 GPU 版：**重跑安装器选 GPU 版**即可，会真的替换 torch；模型与已生成字幕保留 | 同上 |
| 卸载四入口：开始菜单「AsrSubs → 卸载 AsrSubs」、Windows「设置 → 应用」、安装目录 `unins000.exe`、`unins000.exe /SILENT` | `04_安装包\README_说明.txt`；`03_文档\archive\archive-uninstall-fix-20261030.md` |
| `data\output\` 里的字幕产物**默认保留**，卸载前自动备份一份到「文档」，并额外问一句要不要连产物一起删（默认「否」） | `installer.py:1430–1433` 实读确认 |
| **卸载前先退出 DaVinci Resolve**，否则文件被占用会删不掉 | `03_文档\使用说明.txt`「新电脑安装」 |
| 覆盖升级这条路是通的：v1.1.0 实测对现有 `D:\AsrSubs` 跑 `安装核心.exe "D:\AsrSubs" --auto`，135.7 秒结束、退出码 0，torch 未被降级，`settings.lua` / `data\output` / 两个模型全保留 | `04_安装包\README_说明.txt` v1.1.0 段 |

> ⚠ **待人工确认**：① **v1.2.1 已完成编译、菜单 Lua 部署、engine 全套同步（1.2.1），并已通过静默装 / 卸闭环验证**（2026-11-06 08:29，临时目录）；但**静默模式跳过了 `[Run]`，下载部署主线与覆盖升级都没跑**；真机验证覆盖了「转渲染导出」链路与页面还原，A / B 场景与 ② 的 2–4 步仍未测；上表结论来自 1.2.0 用户手册与 1.1.0 时期的实测记录。② 覆盖升级的实测是 v1.1.0 做的，1.2.0 / 1.2.1 未复测。③ `D:\AsrSubs/engine` 与 `01_源码` 规范文件集已完全一致（`sync_dev.py` 只读巡检），但这是手动 copy 的结果，**不等于安装包覆盖安装路径已验证**。见 `..\reference\reference-tests.md` 第 6 轮。

## 关键变更（仅影响使用的里程碑）

- v1.1.0：一键安装 / 一键卸载；卸载确认框写清「删什么 / 留什么」。
- v1.0.1：选 GPU 版才真的装 GPU 版（旧逻辑 `pip` 见「already satisfied」就跳过）；按驱动挑 CUDA 通道；装完亲测 CUDA。
- v1.2.0：安装器逻辑**零改动**，只随引擎改动重出包。详见 `..\CHANGELOG.md`。
- v1.2.1：安装器逻辑仍**零改动**，随 Lua 四处修复重出包；装法与卸载行为与 1.2.0 完全一致。

## 操作 / 参考细节

### 新机器安装

1. 双击 `04_安装包\AsrSubs_v1.2.1_安装器.exe`。
2. 目录页已预填空间最大的本地固定盘 `\AsrSubs`，可改成任意本地盘的任意**英文/数字**目录（例如 `E:\Tools\AsrSubs`）。
3. 选 CPU 版或 GPU 版 → 两次「下一步」→ 文件铺设完成后安装核心自动启动，弹出下载向导（需联网，3.4~6 GB）。装完窗口 6 秒后自行关闭，不用再点第二层界面的「开始安装」。
4. 装完 DaVinci 菜单里就有 `工作区 → 脚本 → AsrSubs → AsrSubs`；没出现就完全退出 Resolve 再开一次。

断网机器：装完文件后手工补放 `models\` 与 `runtime\`；也可先备好 `engine` + `models` 再手工部署，见 `..\design\design-installer.md`。

### 安装后的目录

| 位置 | 内容 |
|---|---|
| `engine\` | 引擎 `.py` + `AsrSubs.lua` + `render_to_wav.xml` + 配置 |
| `runtime\` | Python 3.12 + torch（在线下载获得） |
| `models\` | `Qwen3-ASR-0.6B-hf\` + `Qwen3-ForcedAligner-0.6B-hf\`，必须直接在一层 |
| `data\output\` | 字幕产物，**卸载不删** |
| `data\logs\` | 日志（排查时看 `AsrSubs_error.log`） |
| `docs\` | 使用说明（`.iss:91` 直接引用 `..\03_文档\使用说明.txt`） |
| `_setup\` | 安装器本体 |

盘上只有安装目录这一个文件夹，没有别名 / 副本。菜单只写一处 `%ProgramData%\…\Scripts\Utility\AsrSubs\AsrSubs.lua`。

### 卸载

- 先退出 DaVinci Resolve。
- 开始菜单「AsrSubs → 卸载 AsrSubs」或 Windows「设置 → 应用」里卸载。
- 确认框会写明：运行环境、模型、引擎、文档、菜单项删除；`data\output` 的字幕产物默认保留 + 卸载前备份到「文档」。
- 额外一问「要不要连字幕产物一起删」，默认「否」。
- 卸载时会清理 `%TEMP%\qwen3asr_dl` 下载缓存。

### 升级

重跑同一安装包即可，向导按注册表卸载键判断为覆盖升级，**不会装出第二个目录**，也不会覆盖 Inno 自动填好的上次位置。

## 失效内容说明

- `04_安装包\README_说明.txt` 的「安装」段原写「双击 `AsrSubs_v4.8.23_安装器.exe`」，本批已改为 `AsrSubs_v1.2.0_安装器.exe`；原文另有 `` `历史\v4.8.23` `` 的目录名写法，实际目录是 **`_历史\`**（该目录连同 5 个旧包已于 2026-11-06 彻底删除），本批已改。
- v4.8.23 旧安装器（成品已于 2026-11-06 彻底删除）**不要用于装新机器**：旧逻辑选 GPU 版可能装成 CPU 版。
- `03_文档\archive\archive-uninstall-fix-20261030.md`（v1.28，2026-10-16）里的 `packer\AsrSubs.iss` / `packer\installer.py` 路径已不存在；其结论已落进当前代码，该文档已归档（2026-11-05）。历史根因见 `..\reference\reference-installer-pitfalls.md`。
