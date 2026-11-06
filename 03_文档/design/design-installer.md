---
title: 设计 · 安装器
type: design
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §3 打包部分 / 04_安装包\README_说明.txt（结构部分）
superseded_by: —
---

# 设计 · 安装器

## 一句话摘要

安装包是「Inno Setup 外壳 + PyInstaller onedir 安装核心」两层结构：外壳负责选位置与引导，安装核心负责下载运行环境/模型并部署脚本菜单。

## 当前有效结论

| 结论 | 依据 |
|---|---|
| 外壳 `AsrSubs.iss`（Inno Setup 6），核心 `installer.py`（PyInstaller `--onedir --noconsole`，产物 `安装核心.exe`） | `02_构建与安装器\build.bat`、`安装核心.spec` |
| 载荷是 `02_构建与安装器\payload\`，由 `tools\sync_payload.py` 从 `01_源码` 同步；2026-11-05 实测 `--check` 报「18 个文件一致」 | `sync_payload.py` |
| 打包前有三道只读闸门：`build_lua.py`、`sync_version.py --check`、`sync_payload.py --check`，任一不过即中止 | `build.bat` |
| 全新安装自动预填「空间最大的本地固定盘」（够 7 GB 里挑最大，都不够退取最大盘并让 `validate_base()` 报差额）；**升级安装必须保持 Inno 自动填好的上次位置** | `archive\archive-dev-doc-20261105.md` §11 第 3 条 |
| 双击 → 选位置 → 两次「下一步」→ 安装包带 `--auto` 拉起安装核心，界面一出现就按检测结果开跑（有 NVIDIA 卡默认 GPU 版），装完窗口 6 秒后自行关闭 | `04_安装包\README_说明.txt` v1.1.0 段 |
| 安装包本体约 12 MB；运行环境与模型（3.4~6 GB）装完**在线下载**，断网机器须先备好 `engine` + `models` 再手工部署 | 旧 README §6 已知限制 |
| 卸载有 4 个标准入口（安装目录 `unins000.exe` / 开始菜单 / 控制面板 / `unins000.exe /SILENT`）；**`data\output` 里的字幕产物默认保留**，卸载前自动备份一份到「文档」，并额外询问是否连产物一起删（默认「否」） | `04_安装包\README_说明.txt`；`installer.py:1430–1433` 实读确认 |
| 安装位置两条硬约束：**路径纯 ASCII**、**本地固定磁盘** | Lua 的 `io.open`/`os.execute` 走 ANSI；网络盘读几千个小文件会超时 |
| 菜单只写一处：`%ProgramData%\…\Scripts\Utility\AsrSubs\AsrSubs.lua` | 三个脚本根目录 Resolve 都会扫，多写会出现两个同名菜单项 |
| 安装器写 `_setup\_deployed.json` 记录真正写了菜单的那一处，供升级 / 卸载判断 | `archive\archive-dev-doc-20261105.md` §10 |

> ⚠ **待人工确认**：`installer.py` 中下载源的完整清单、GPU/CPU 版判定细节及「不可达源黑名单」的具体阈值，本次治理未逐行核对代码，只沿用 `archive\archive-dev-doc-20261105.md` §11 与用户手册的既有结论。

## 关键变更（仅影响使用的里程碑）

- v1.1.0：一键安装 / 一键卸载（含卸载前备份字幕产物）。
- v4.8.16–4.8.18：Unicode 盘符校验、`InitializeWizard` 不展开 `{app}`、修「下不动」、build.bat 加 tkinter 闸门。
- v1.2.0：本版只改引擎三处（见 `..\CHANGELOG.md`），**安装器逻辑未改**。2026-11-05 已重出 `AsrSubs_v1.2.0_安装器.exe`（12,501,856 字节，MD5 `78E5602F26FA0B51F891BFE5621E4F0E`）。
- v1.2.1：安装器逻辑依旧**未改**，随 Lua 四处修复（渲染链路三处 + 页面还原顺序）重出 `AsrSubs_v1.2.1_安装器.exe`（12,504,407 字节，MD5 `13D61D1D35F80722A98319CB058EC25E`）。

## 操作 / 参考细节

### 出包链（本次实测可用）

```bat
:: 打包环境：py -3.12（3.12.9 + PyInstaller 6.22.3 + tkinter）
:: 注意 D:\AsrSubs\runtime\Scripts\python.exe 缺 tkinter，不能用来打包
py -3.12 -m PyInstaller --clean --noconfirm "安装核心.spec"
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "AsrSubs.iss"
```

产物落在 `02_构建与安装器\dist\AsrSubs安装器.exe`（`.iss` 里 `OutputDir=dist`、`OutputBaseFilename=AsrSubs安装器`），再复制到 `04_安装包\AsrSubs_vX.Y.Z_安装器.exe`。

`build.bat` 是官方一键入口，但它会先 `rmdir /s /q build dist` 且结尾有 `pause`；在受限环境里按上面两步手动跑等价，且不会触发批量删除。

### 安装后目录

| 位置 | 内容 |
|---|---|
| `<base>\_setup\` | 安装器本体（onedir，装完可删） |
| `<base>\engine\` | 引擎 `.py` + `AsrSubs.lua` + `render_to_wav.xml` + 配置 |
| `<base>\engine\rt\` | 运行期文件邮箱 |
| `<base>\runtime\Scripts\python.exe` | Python 3.12 + torch |
| `<base>\models\` | `Qwen3-ASR-0.6B-hf\` + `Qwen3-ForcedAligner-0.6B-hf\`（必须直接在一层） |
| `<base>\data\output\` | 字幕产物，**卸载不删** |
| `<base>\docs\` | 使用说明（`.iss:91` 直接引用 `..\03_文档\使用说明.txt`） |

### 七条已知坑

见 `..\reference\reference-installer-pitfalls.md`（改 `.iss` 前必读）。

## 失效内容说明

- `03_文档\archive\archive-uninstall-fix-20261030.md` 写的是 v1.28（2026-10-16）时代 `packer\AsrSubs.iss` / `packer\installer.py`，**路径已不存在**；其「卸载不得整棵删、字幕产物必须保留」结论已落进当前代码，该文档已归档（2026-11-05）。
- `engine\packer\payload\`（2.x 旧打包区）已于 v4.0.9 归档，不是当前载荷来源。
- 旧 README §4 的「3.0 布局」标题是历史命名，目录内容仍适用。
