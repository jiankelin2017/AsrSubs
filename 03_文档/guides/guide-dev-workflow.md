---
title: 指南 · 开发工作流与漂移防护
type: guides
status: Active
version: 1.0.0
last_update: 2026-11-06
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §8 / 旧 README §3 / §8
superseded_by: —
---

# 指南 · 开发工作流与漂移防护

## 一句话摘要

在 `D:\AsrSubs\engine` 改代码、用 `sync_dev.py --sync` 回灌、再升版本与验收；**在 `01_源码` 直接改完就跑 `--deploy` 会被旧代码覆盖**。

## 当前有效结论

| 结论 | 依据 |
|---|---|
| 真正「被测试 / 被加载」的引擎在 `D:\AsrSubs\engine`（`ASCII_ROOT` 指向它）；`01_源码` 是规范源码 | `archive\archive-dev-doc-20261105.md` §8.1 |
| `sync_dev.py` 无参 = **只读检查**（engine↔01_源码 差异、版本号一致、01_源码↔payload MD5）；`--sync` = 回灌；`--deploy` = 回灌 + 推 payload / packer / 菜单 Lua | `archive\archive-dev-doc-20261105.md` §8.3 |
| **`--deploy` 的第一步是 engine → 01_源码 回灌**，会把你在 `01_源码` 的未同步改动覆盖掉 | `archive\archive-dev-doc-20261105.md` §8.5，2026-10-31 连中两次、2026-11-01 又中一次 |
| 版本唯一来源是 `01_源码\version.txt`，`sync_version.py` 注入 5 处；三段式规则见 `..\adr\adr-0003-version-reset-1.0.0.md` | `..\adr\adr-0004-single-source-version.md` |
| 本机打包**必须用 `py -3.12`**（3.12.9 + PyInstaller 6.22.3 + tkinter）；`D:\AsrSubs\runtime\Scripts\python.exe` 缺 tkinter | 2026-11-05 实测 |
| 改完代码必须同步更新文档（至少 `..\INDEX.md`、`..\CHANGELOG.md`、相关设计/参考页） | 项目文档同步规则 |

## 关键变更（仅影响使用的里程碑）

- v4.0.9：测试与探针脚本统一迁到 `01_源码\tools\`；Lua 编译校验改用 lupa。
- v1.0.0：版本号规则改为三段式。
- 详见 `..\CHANGELOG.md`。

## 操作 / 参考细节

### 标准动作（每次改完代码后）

1. 在 `D:\AsrSubs\engine` 改代码、自测（Resolve 重新运行脚本加载）。
2. 跑离线单测 + `py_compile`。
3. **回灌**：`py -3.12 sync_dev.py --sync`（项目根执行）。
4. **升版本**（如适用）：改 `01_源码\version.txt` → `py -3.12 01_源码\tools\sync_version.py` → `py -3.12 01_源码\tools\sync_payload.py`。
5. **部署**：`py -3.12 sync_dev.py --deploy`。
6. **验收**：`py -3.12 01_源码\tools\verify_all.py`；必要时开 DaVinci 跑真机探针（清单见 `..\reference\reference-tests.md`）。

### 常用命令速查

| 目的 | 命令（工作目录见括号） |
|---|---|
| 一键验收 | `py -3.12 tools\verify_all.py`（`01_源码`） |
| 只读漂移巡检 | `py -3.12 sync_dev.py`（项目根） |
| 回灌已测代码 | `py -3.12 sync_dev.py --sync`（项目根） |
| 版本号一致性校验 | `py -3.12 tools\sync_version.py --check`（`01_源码`） |
| payload 一致性校验 | `py -3.12 tools\sync_payload.py --check`（`01_源码`） |
| Lua 单文件体检 | `py -3.12 tools\build_lua.py`（`01_源码`，只读） |
| 出包（分步，避开 `build.bat` 的批量删除 / pause） | 见 `..\design\design-installer.md`「出包链」 |

### ⚠ `--deploy` 的三次真实事故与正确姿势

| 时间 | 后果 |
|---|---|
| 2026-10-31（两次） | `使用说明.txt` 新写的更新记录被冲掉；`land_subtitle.py` 三处修复被回退，还被当成 4.1.1 发了出去 |
| 2026-11-01（v4.8.24） | 回灌把 7 个 py/lua 文件的改动全部覆盖回 4.8.23；更隐蔽的是把 `01_源码\AsrSubs.lua` 里的 `--<<INSTALL_ROOT>>` **占位锚点吃掉**（engine 版是替换后的真实路径），若这样打包，装到 `D:\` 以外的机器会沿用写死的 `D:\AsrSubs\engine`，表现为「界面能开、一跑就超时」 |

**正确姿势（二选一）**：

1. 在 `D:\AsrSubs\engine` 改，跑 `--sync` 回灌；或
2. 在 `01_源码` 改完后，**先把改动文件 `copy` 到 engine**，再 `sync_payload` → `--deploy`。

**三条配套纪律**：

1. 动手前把 `01_源码` 备份成 `_改前备份_<主题>_<日期>`；**改完立刻再存一份** `_改动后_<主题>_<日期>`。
2. 推完立刻抽查：`grep -c "_word_char_cuts" D:\AsrSubs\engine\asr_engine.py` 必须 ≥1；`grep -c "INSTALL_ROOT>>" 01_源码\AsrSubs.lua` 必须 ≥1。
3. 部署时**不要有识别 / 落轨任务在跑**：engine 里的 py 正被 Python 读入时被 `copy2` 覆盖，实测出现「进度卡在 99%、进程存活、内存 7 GB、日志停在最后一行」的假死。

deploy 后必须确认 engine 与 `01_源码` 关键文件 MD5 一致，并抽查新代码标记仍在，**别只看「汇总：一致」**。

### 每日巡检

已建自动化，每日只读跑 `py -3.12 sync_dev.py`，发现漂移即提示补回灌。它不改任何文件。

### 第 5 批部署记录（2026-11-05 22:53）

**已完成**：按「备份 → 手动 copy → 复跑验收」把 v1.2.0 的 `AsrSubs.lua` 推到**两处**部署副本（`%ProgramData%\…\Utility\AsrSubs\` 与 `D:\AsrSubs\engine\`），替换掉各 378,114 B（11-03）的旧版；四份 MD5 全为 `4a8ed9cc…`。`verify_all.py` **18 项全部通过（含 1 跳过）**。改前备份在项目根 `_改前备份_部署v1.2.0_20261105\`（77 个源码文件 + 2 份旧 lua）。

**为什么手动 copy 能过验收**：`verify_all.py:183–197` 的 `lua_fingerprint` 会把 `INSTALLED_ROOT` 锚点行归一成占位符再比 MD5；本机安装目录恰为默认值 `D:\AsrSubs\engine`，源码里保留的 `--<<INSTALL_ROOT>>` 锚点可直接用，不必像 `installer.deploy()` 那样替换成真实路径。**锚点必须仍在**：`grep -c "INSTALL_ROOT>>" 01_源码\AsrSubs.lua` 必须 ≥ 1。另需确认 `D:\AsrSubs\engine\paths.json` 存在且 `engine` 字段指向自己（`check_deploy()` 会查）。

⚠ **覆盖范围（仍成立的边界）**：`check_deploy()` 只比 `.lua` 与 `paths.json`，**不比 engine 的 `.py`**。第 5 批复跑时 `sync_dev.py` 报 5 处漂移、engine 版本为 1.1.3；**第 6 批已按差异清单逐个 copy 同步 6 个文件到 1.2.0**，`sync_dev.py` 现显示「engine 与 01_源码 规范文件集完全一致」、`D:\AsrSubs/engine/version.txt` = 1.2.0。结论：engine 是否同步**必须另跑 `sync_dev.py` 只读巡检**确认，不能由 `verify_all.py` 的「部署一致」推断。

### 第 6 批 engine 同步记录（2026-11-05）

按 `sync_dev.py` 只读巡检的 5 项差异清单（外加不在 CANON、巡检不报的 `version.txt`），把源码逐个 copy 到 `D:\AsrSubs/engine/`：`asr_engine.py` / `qwen_version.py` / `subs_io.py` / `worker.py` 取自 `01_源码\`，`使用说明.txt` 取自 `03_文档\`，`version.txt` 取自 `01_源码\`。差异方向先做了取证——源码是 11-05 的 v1.2.0（行尾标点收口、subs_err 空值不写、worker.pid 竞态修复），engine 是 11-02 的 1.1.3，`>` 行只有旧版本号与旧写法片段，没有 engine 独有的新功能。

**没有跑 `--deploy`**：它第一步是 engine→源码回灌，本文件上面记了三次覆盖事故；也**没有碰 `paths.json`**（两边 MD5 本就一致，且它是运行期真实路径文件，全量 copy 可能把模板盖上去）。改前 6 文件备份在 `_改前备份_部署v1.2.0_20261105\engine_改前\`。同步后复跑 `sync_dev.py` 显示完全一致、`verify_all.py` 18 项全绿。

**剩余待办**：A / B 场景（空时间线 / 只有字幕轨）与 ② 的 2–4 步真机验证 —— ① 对照组、②-1、③ 目标分支与渲染后页面还原已于 2026-11-06 五轮实跑通过；清单与回填表见 `..\reference\reference-tests.md`。**安装包已做静默装 / 卸闭环验证（第 6 轮），下载部署与覆盖升级仍未跑。**

## 失效内容说明

- 旧 README §3 里「`python`（无版本前缀）」的命令在本机可能落到缺 tkinter / 缺依赖的解释器；本次治理统一写成 `py -3.12`。
- §8.2 第 5 步写的「推到 payload / **packer** / 菜单 Lua」中 `packer` 已于 v4.0.9 归档，当前载荷只有 `02_构建与安装器\payload\`。
- §8.5.1 提到的 `push_engine.py` 是本轮临时脚本，项目根目录**未发现该文件**，不要当作现成工具调用。
