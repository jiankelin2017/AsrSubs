---
title: AsrSubs 文档总表
type: index
status: Active
version: 1.2.1
last_update: 2026-11-06
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: —
superseded_by: —
---

# INDEX · 文档总表

## 读法

- 本表是**唯一入口**。路径相对 `03_文档\`，`..\` 表示项目根。
- 目录下采用方案 A：物理目录仍叫 `03_文档\`（因为 `AsrSubs.iss`、`sync_payload.py`、`sync_version.py` 硬引用 `03_文档\使用说明.txt`），其内分 `design/ guides/ reference/ adr/ archive/`。
- **状态列同时标注文件是否真实存在**。`计划中（文件不存在）` 的行不是可用链接，不要点。
- 表头 `version: 1.2.0` 是**本表自身版本**（按三段式递增），与 `01_源码\version.txt` 的代码版本无关。
- 文中「开发文档 §X」「AI交棒 §6.2」等**历史章节引用**一律指 `archive\archive-dev-doc-20261105.md` 等归档件的对应章节，只作溯源，不作当前结论。
- 治理按主题分批推进。**第 1–7 批均已完成**：骨架 / 概览 / CHANGELOG / 模板 → 开发文档拆分 → 安装发布 + UI 图判定 → 归档与清理 → NOTICE 修订 + 菜单脚本部署 → engine 同步 + NOTICE 第 1 条 + 防腐化巡检脚本 → NOTICE API 复核 + 真机探针 + 旧产物清理。第三节「计划中」已清空，验收 18 项全绿、`sync_dev.py` 显示 engine 与源码完全一致。剩余待办：v1.2.0 三项改动真机验证（① 对照组、②-1 已通过；A / B 场景、② 的 2–4 步、③ 目标分支未执行）。旧构建产物与 5 个旧安装包均已于 2026-11-06 彻底删除。总表日期列为建档日期，各文档最后修订日以自身 front-matter 的 `last_update` 为准。

## 一、已生效（文件真实存在）

| 主题 | 路径 | 类型 | 状态 | 适用版本 | 维护人 | 最后核对 | 替代 / 归档 |
|---|---|---|---|---|---|---|---|
| 项目概览 | `..\README.md` | README | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 重写；改前快照 `archive\README-20261105.md`；第 3 批补文档导航与失效项 |
| 文档总表 | `INDEX.md` | index | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 第 1 批新建，第 2 / 3 批扩充 |
| 全局变更 | `CHANGELOG.md` | changelog | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 吸收旧 README §0 + 开发文档 §6.11–6.18 + 使用说明早期「更新记录」段（v1.2.4 及更早）；第 3 批修 ADR-0002 引用 |
| 用户手册 | `使用说明.txt` | guide | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | **原位不动**：`.iss:91`、`sync_payload.py:57`、`sync_version.py:59` 硬引用 |
| 文档模板 | `_TEMPLATE.md` | template | Active（已存在，辅助件） | 体系本身 | Jian | 2026-11-05 | 不计入分类份数 |
| 文档防腐化巡检脚本 | `..\check_docs.py` | 工具 | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 项目根（避开 verify_all 语法清单）；默认纯只读，`--strict --gates` 用于提交前；用法见 `guides\guide-doc-maintenance.md` |
| 系统架构与部署地图 | `design\design-architecture.md` | design | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §1–3 |
| Python 引擎设计 | `design\design-engine.md` | design | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §2 引擎部分 / §4 |
| 时间线就地操作 | `design\design-timeline-ops.md` | design | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §6.1 / 6.2 / 6.3 / 6.7 / 6.9 |
| Fusion 界面设计 | `design\design-ui.md` | design | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §6.8 / 6.10；第 3 批补两张 PNG 核实结论 |
| 安装器设计 | `design\design-installer.md` | design | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §3 打包部分 / 04 说明结构部分 |
| 开发工作流与漂移防护 | `guides\guide-dev-workflow.md` | guides | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §8 / 旧 README §3 / §8 |
| 安装 / 卸载 / 升级 | `guides\guide-install.md` | guides | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 使用说明「新电脑安装」段+ 04 说明「安装 / 卸载」段 |
| 出包与发布流程 | `guides\guide-release.md` | guides | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | `archive\archive-readme-merged-20261101.md`「发布流程（严格按序）」；纠正出包目录与验收项数 |
| 故障处置 | `guides\guide-troubleshoot.md` | guides | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 使用说明「出问题先看这里」段+ 安装器坑 #7 + 实测部署状态 |
| 文档维护与防腐化机制 | `guides\guide-doc-maintenance.md` | guides | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 第 6 批新建；五条机制 + 变更联动矩阵 + 定期巡检触发规则 |
| 模块清单 | `reference\reference-modules.md` | reference | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §4 |
| Resolve API 事实 | `reference\reference-resolve-api.md` | reference | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §6.6 / §6.8.4（最高价值事实库） |
| 测试体系与验收真值 | `reference\reference-tests.md` | reference | Active（已存在，第 5 批复测更新） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §7 / §9.5.5 / 旧 README §9；22:53 复跑 18 项全绿 |
| 已知限制与未完成项 | `reference\reference-limitations.md` | reference | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 旧 README §6 / 开发文档 §9 未完成项；第 3 批改 UI 图状态 |
| 安装器已知坑 | `reference\reference-installer-pitfalls.md` | reference | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §11；第 3 批吸收「卸载修复说明」三条历史根因 |
| 逐版发布说明 | `reference\reference-release-notes.md` | reference | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | `..\04_安装包\README_说明.txt` 版本参数段；逐版成品大小 / MD5 为实算值 |
| 原地落轨红线 | `adr\adr-0001-in-place-timeline.md` | adr | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §6.5；推翻 AI交棒 §6.2 的相反表述 |
| 放弃 R3 | `adr\adr-0002-abandon-r3-export.md` | adr | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §6.4 |
| 版本号重置 | `adr\adr-0003-version-reset-1.0.0.md` | adr | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §5 旧规则 / §6.14.1 |
| 版本唯一来源 | `adr\adr-0004-single-source-version.md` | adr | Active（已存在） | 1.2.1 | Jian | 2026-11-05 | 开发文档 §5 |
| 致谢 / 技术来源 | `..\NOTICE.md` | 法务声明 | Active（已存在，第 5 / 6 / 7 批修订） | 1.2.1 | Jian | 2026-11-06 | **第 1 批误判更正**：原「新建空时间线兜底」与 `adr-0001` 冲突；第 5 批改第 2 条为「对齐校验与失败处理」，第 6 批按 `land_subtitle.py:1004–1009` 改写第 1 条传参与落轨步骤。第 23–24 行 API 描述已于 2026-11-06 按官方 API 文档复核修订；开头「占位 cue」残留已清 |
| 安装包发布说明（随包分发） | `..\04_安装包\README_说明.txt` | 发布说明 | Active（已存在，第 3 批增量修 5 处） | 1.2.1 | Jian | 2026-11-06 | 文件名 / 已清理的 `_历史\` 路径 / 废弃引用已修；4.8.x 逐版段已标史料；版本参数已拆入 `reference-release-notes.md` |

## 二、已归档（Deprecated，仅供溯源）

| 主题 | 路径 | 类型 | 状态 | 适用版本 | 维护人 | 最后核对 | 替代 / 归档 |
|---|---|---|---|---|---|---|---|
| 归档区说明 | `archive\README.md` | archive | Active（已存在） | 体系本身 | Jian | 2026-11-05 | 归档规则、8 件清单与反向 mv 还原方法；本节的权威说明 |
| 开发者技术参考（原件） | `archive\archive-dev-doc-20261105.md` | 混合体 | Deprecated（已归档） | 头部 4.8.18 / 正文到 1.2.0 | Jian | 2026-11-05 | 有效内容已迁入第一节各篇；文首写「已废弃，见 INDEX.md」 |
| 整合包说明 / 发布流程（原件） | `archive\archive-readme-merged-20261101.md` | 说明 | Deprecated（已归档） | 4.8.18（已失效） | Jian | 2026-11-05 | 发布流程已并入 `guides\guide-release.md`；v4.8.18 安装包引用不存在 |
| 历史交接 | `archive\archive-carryover-20261031.md` | 交接 | Deprecated（已归档） | 3.2.6 | Jian | 2026-11-05 | 自标过期；§6 的 API 教训见 `reference\reference-resolve-api.md` |
| 一次性版本盘查 | `archive\archive-version-diff-20261031.md` | 分析 | Deprecated（已归档） | 3.0.5 / 3.2.6 | Jian | 2026-11-05 | 对比对象 `lua_src\` 全仓已不存在 |
| 卸载缺陷记录 | `archive\archive-uninstall-fix-20261030.md` | 缺陷说明 | Deprecated（已归档） | 1.28 | Jian | 2026-11-05 | 三条写法级坑已并入 `reference\reference-installer-pitfalls.md` |
| UI 设计预览 | `archive\ui-design-preview-20261031.png` | 素材 | Deprecated（已归档） | 图内标注 v4.7.2 | Jian | 2026-11-05 | 按钮写「开始生成字幕」，1.2.0 手册写「开始生成」；不能作为 1.2.0 界面依据 |
| UI 方案 B 草图 | `archive\ui-plan-b-sketch-20261031.png` | 素材 | Deprecated（已归档） | 图内标注 v4.8.0 | Jian | 2026-11-05 | 带红色编号 1–6 与「逐步核对」清单，属方案决策过程稿 |
| 改前快照 | `archive\README-20261105.md` | archive | Deprecated（已归档） | 1.2.0 写作前的 4.8.18 表述 | Jian | 2026-11-05 | 被 `..\README.md` 取代 |

> 归档执行于 **2026-11-05（第 4 批）**，**全部用 `mv`、零删除、可逆**；每个文本文件文首已写「已废弃，见 xxx」，两张 PNG 无法写文首，判定依据记在 `design\design-ui.md` 与 `archive\README.md`。全仓无脚本引用这些文件，归档不会断构建链。还原方法见 `archive\README.md`。

## 三、计划中（已清空）

第 1 批建骨架时列的计划文档（`guide-install` / `guide-release` / `guide-troubleshoot` / `reference-release-notes`）**已在第 3 批全部落地并登记在第一节**。当前没有「计划中但不存在」的文档，本表不放假链接。

## 四、构建产物（不纳入文档治理）

| 路径 | 说明 |
|---|---|
| `..\02_构建与安装器\payload\使用说明.txt` | `sync_payload.py` 生成的镜像，MD5 与 `使用说明.txt` 一致。**勿手改** |
| `..\02_构建与安装器\dist\...\payload\使用说明.txt` | 构建镜像（4,063 B），MD5 与 `03_文档\使用说明.txt` 一致，非文档、勿手改 |
| `..\02_构建与安装器\build\安装核心\` | PyInstaller 中间产物；本轮构建后已挪到项目外保留，交付包内不留含本机路径的中间文件 |
| `..\02_构建与安装器\dist\安装核心\`（v1.2.5 构建产物，本轮重建） | PyInstaller `--noconfirm` 原地重建，payload Lua 1.2.5、随包《使用说明》4,063 B；上一版 1.2.4 的 dist 与安装器已挪到项目外留存 |
| `..\04_安装包\AsrSubs_v1.2.5_安装器.exe` + v1.2.4 / v1.2.1 / v1.2.0 | 当前最新成品与三个上一版成品；5 个更旧的包已于 2026-11-06 彻底删除，大小 / MD5 保留在 `reference\reference-release-notes.md` |
| `..\_改前备份_部署v1.2.0_20261105\` | 第 5 批部署前备份：77 个源码文件 + 两处旧 `AsrSubs.lua`（各 378,114 B，MD5 `e7aa4eae…`）；第 6 批追加 `engine_改前\`（6 个待覆盖文件）。还原时按路径反向 copy |

## 五、核对规则

1. 每次代码发布，把 `applies_to` 不等于 `01_源码\version.txt` 的 Active 文档挑出来复核。
2. 每次新增文档，必须在本表登记；每次归档，必须把状态改成 `Deprecated` 并填 `替代 / 归档`。
3. 本表出现 `Active（已存在）` 但文件不存在的行，视为文档体系腐化，立即修。
4. 归档动作需先列清单、经确认后执行（第 4 批已按此执行）；归档文件文首必须写「已废弃，见 xxx」，清单与还原方法见 `archive\README.md`。
5. 维护人统一为 **Jian**；新增或修改文档时不得留 `待人工确认` 在维护人列（结论不确定处才用该标记）。
6. **调研 / 快照类文档必须带日期与适用范围**，且「本项目」能力描述不得当成当前版本结论；引用时指向 `design\` / `README.md`。
7. 每次出包必须同步 `..\04_安装包\README_说明.txt`、`CHANGELOG.md`、`reference\reference-release-notes.md` 与本表，见 `guides\guide-release.md` 第 6 步。
8. **每次部署后必须同步所有「部署状态 / 验收项数」表述**；且不得把「部署一致转绿」写成「引擎已升级」——该项只比 `.lua` 与 `paths.json`，engine 的 py 需另跑 `sync_dev.py` 只读巡检确认（第 5 批实测结论）。
9. **每次提交 / PR 前跑 `py -3.12 check_docs.py --strict --gates`**，退出码必须为 0；巡检项、变更联动矩阵与触发规则见 `guides\guide-doc-maintenance.md`。
10. **写回文档后检查 Windows 路径风格**：工具层会把盘符后的反斜杠转正；叙述用反斜杠、Git Bash 命令示例用 `/d/...`，`check_docs.py` 第十组会自动拦（第 5 / 6 批两次实测）。

## 六、当前实测状态（截至 2026-11-07）

- `py -3.12 tools\verify_all.py`（**2026-11-06 出 1.2.1 前复跑**）：**18 项全部通过（含 1 项跳过）**。「部署一致」输出「2 处部署副本与源码一致（2e1e5908；AsrSubs/paths.json ✓）」。
- `py -3.12 sync_dev.py`（**2026-11-05 第 6 批复测**）：「engine 与 01_源码 规范文件集完全一致」，版本号与 payload MD5 均一致；`D:\AsrSubs/engine/version.txt` = 1.2.0。改前 6 文件备份在 `..\_改前备份_部署v1.2.0_20261105\engine_改前\`。
- **部署动作（第 5 批）**：把 `01_源码\AsrSubs.lua`（386,357 B，11-05）手动 copy 到 `%ProgramData%\…\Utility\AsrSubs\` 与 `D:\AsrSubs\engine\` 两处，替换掉 378,114 B（11-03）的旧副本；四份 MD5 全为 `4a8ed9cc…`。改前快照在项目根 `..\_改前备份_部署v1.2.0_20261105\`（77 个源码文件 + 2 份旧 lua）。
- ⚠ **验收覆盖范围（仍成立的边界）**：「部署一致」只比 `.lua` 与 `paths.json`，**不比 engine 的 Python 文件**。第 6 批已按 `sync_dev.py` 差异清单把 `asr_engine.py` / `qwen_version.py` / `subs_io.py` / `worker.py` / `使用说明.txt` / `version.txt` 同步到 1.2.0，只读巡检显示「engine 与 01_源码 规范文件集完全一致」，`D:\AsrSubs/engine/version.txt` = 1.2.0。**但引擎是否同步仍须另跑 `sync_dev.py` 确认，不能由验收全绿推断**。
- v1.2.1 安装包已出：`..\04_安装包\AsrSubs_v1.2.1_安装器.exe`（12,504,407 字节，MD5 `13D61D1D35F80722A98319CB058EC25E`），ISCC 编译成功；上一版 1.2.0（12,501,856 字节，MD5 `78E5602F26FA0B51F891BFE5621E4F0E`）保留。**已做静默装 / 卸闭环验证**（2026-11-06 08:29，临时目录，装 2.6 秒 / 卸 1.6 秒，均退出码 0，`D:\AsrSubs` 与 Resolve 脚本目录前后一致）；**下载部署与覆盖升级未测**。详见 `reference\reference-tests.md` 第 6 轮。
- 各安装包的精确大小与 MD5 见 `reference\reference-release-notes.md`；其中 5 个旧包已于 2026-11-06 彻底删除，校验值仍保留在该页。
- 详见 `reference\reference-tests.md`。
- **2026-11-06 23:29（v1.2.4 出包）**：`verify_all.py` 18 项全通过（含 1 跳过），`check_docs.py --strict --gates` PASS 16 / FAIL 0 / WARN 0；`sync_dev.py` 显示 engine 与 01_源码 规范文件集完全一致（1.2.4）；三道出包闸门全绿；成品 `04_安装包\AsrSubs_v1.2.4_安装器.exe`（12,507,023 字节，MD5 `BCD85D9540D41630D26FE7E2DC14F4C0`）。静默装 / 卸已于 23:41 实测通过；下载部署、覆盖升级、真机渲染 / 落轨未测。
- **2026-11-06 23:41（v1.2.4 静默装 / 卸实测）**：隔离目录装 2.7 秒 / 卸 0.6 秒均退出码 0，包内 `安装核心.exe` 与 dist 同源、payload Lua 为 1.2.4，卸载后基线 0 处差异。详见 `reference\reference-tests.md` 第 7 轮与「v1.2.4 真机手动验证清单」。
- **2026-11-07（v1.2.5 用户手册重写 + 重新出包）**：`03_文档\使用说明.txt` 按新范文全文重写（535 行 → 51 行，UTF-8 无 BOM / 纯 CRLF），普通升级 `1.2.4 → 1.2.5`，5 处版本注入与 Active 文档 `applies_to` 全量联动；旧章节号引用已同步修正。05:36 完成 PyInstaller 重建（payload Lua 1.2.5、随包《使用说明》4,063 B）与 ISCC 编译（5.141 秒），成品 `04_安装包\AsrSubs_v1.2.5_安装器.exe`（12,495,260 字节，MD5 `C874086E160BD205F7F931CE4D4CFD53`）。**静默装 / 卸、下载部署、覆盖升级、真机渲染与落轨尚未实测**；注意本机当前已从 0 装回 v1.2.4 并在 Resolve 里实跑，故「部署一致」显示版本差（已装 1.2.4 / 源码 1.2.5），属预期，非回归。
