---
title: 文档维护与防腐化机制
type: guides
status: Active
version: 1.0.0
last_update: 2026-11-06
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: —
superseded_by: —
---

# 文档维护与防腐化机制

## 一句话摘要

`check_docs.py` 把「文档状态与磁盘实况是否自洽」变成一条可重复执行的只读命令；本页给出五条防腐化机制、变更联动矩阵和巡检触发规则，让 21 篇规范文档不再随时间漂移。

## 当前有效结论

| 机制 | 落地位置 | 有效结论 |
|---|---|---|
| 唯一入口 | `..\INDEX.md` | 查资料一律从总表进入；总表的状态列同时标注文件是否真实存在，不放假链接 |
| 模板与元数据 | `..\_TEMPLATE.md` | 每篇必须含六字段 front-matter（`maintainer` 非空即可，巡检不校验收名字面值）与通用五段；ADR 不豁免 |
| 变更联动 | 本页「变更联动矩阵」+ `..\INDEX.md` 第五节 | 改代码 / 出包 / 部署 / 归档 / 新增文档，都有必须同步的文件清单 |
| 自动巡检 | 项目根 `..\check_docs.py` | 默认纯只读、零外部命令、退出码可判；`--gates` 才追加三道只读构建闸门 |
| 防复辟 | 主目录白名单 + 命名规则 + 过时措辞扫描 | 主目录出现同主题第二份、非规范命名、或未同步的过时状态即报警（明确标注为历史快照的行豁免） |

> ⚠ **脚本能力边界**：`check_docs.py` 只判断「状态、路径、结构、命名是否自洽」，**不判断技术结论是否正确**。结论对错仍须人工对照代码 / 配置 / 实测，脚本不臆造。

## 关键变更（仅影响使用的里程碑）

- v1.2.0（2026-11-05，第 6 批）：新建 `check_docs.py` 与本页；文档体系定型为 **21 篇 Active**（design 5 / guides 5 / reference 7 / adr 4）+ 10 件归档；engine 全套同步到 1.2.0；`NOTICE.md` 第 1、2 条按架构红线改写。
- 详细分批过程见 `..\CHANGELOG.md`，此处不复述。

## 操作 / 参考细节

### 1 · 巡检命令

在项目根 `D:\插件\AsrSubs_整合_20261031` 执行：

| 命令 | 用途 | 副作用 |
|---|---|---|
| `py -3.12 check_docs.py` | 文档体系九组检查 | 无，纯只读 |
| `py -3.12 check_docs.py --gates` | 追加 `build_lua.py` / `sync_version.py` / `sync_payload.py` 三道 `--check` | 三条只读校验 |
| `py -3.12 check_docs.py --strict` | WARN 也算失败，用于提交前 / CI | 无 |
| `py -3.12 check_docs.py --strict --gates` | **推荐用于 PR 与出包前** | 三条只读校验 |
| `py -3.12 check_docs.py --json` | 机器可读输出，便于接自动化 | 无 |
| `py -3.12 check_docs.py --quiet` | 只打印汇总与异常 | 无 |

退出码：`0` = 通过；`1` = 有 FAIL（`--strict` 下 WARN 也算）；`2` = 脚本自身异常。

**为什么放在项目根**：`verify_all.py` 的语法检查会扫 `01_源码\tools\*.py`，新增脚本放进该目录会改变「Python 语法（N 个文件）」的验收计数。放项目根与 `sync_dev.py` 同级，零构建影响。

### 2 · 十组检查项

| # | 检查组 | 判定标准 | 失败级别 |
|---|---|---|---|
| 1 | INDEX 实存 / 误标 | 状态列写「已存在 / 已归档」的文件必须存在；写「不存在」的必须不存在 | FAIL |
| 2 | 同主题唯一性 | INDEX 里同一主题不得出现两行 Active | FAIL |
| 3 | 主目录白名单 | `03_文档\` 根只允许 `INDEX.md` / `CHANGELOG.md` / `_TEMPLATE.md` / `使用说明.txt` + 五个规定目录 | FAIL |
| 4 | 统一命名 | `design-` / `guide-` / `reference-` / `adr-NNNN-` 前缀；禁 `final` / `副本` / `_vN` | FAIL |
| 5 | front-matter | 六字段齐全、维护人字段非空（不校验收名字面值） | FAIL |
| 6 | 五段结构 | 一句话摘要 / 当前有效结论 / 关键变更 / 操作·参考细节 / 失效内容说明，ADR 亦不豁免 | FAIL |
| 7 | 归档件标注 | `archive\` 文本文件文首有「已废弃，见」；PNG 登记在 `archive\README.md` | FAIL / WARN |
| 8 | 过时状态措辞 | 主目录不得出现 `待归档` / `原件仍在原位` / 旧验收数字 / 旧部署状态等事实性过时表述；带「历史 / 快照 / 已作废 / 早间」的行豁免 | FAIL |
| 9 | 版本一致性 + 资产登记 | `applies_to` 对应 `01_源码\version.txt`；`engine\version.txt` 与源码一致；机制文档与巡检脚本已在 INDEX 登记 | FAIL / WARN |
| 10 | Windows 路径风格 | 叙述里盘符后不得接正斜杠（工具层会把反斜杠转正）；命令行示例用 `/d/...` 这类 POSIX 写法；规则条文与坑记录豁免 | FAIL |

`--gates` 追加第十一组：三道构建闸门（Lua 单文件 / 版本号注入 / payload 一致性）。

### 3 · 变更联动矩阵

| 触发动作 | 必须同步的文件 | 依据 |
|---|---|---|
| 改代码（版本 / 架构 / 接口） | `INDEX.md`、`CHANGELOG.md`、对应 `design\` / `reference\` 篇、所有 Active 文档的 `applies_to` | `INDEX.md` 第五节第 1 / 5 / 6 条 |
| 出包 | `04_安装包\README_说明.txt`、`CHANGELOG.md`、`reference\reference-release-notes.md`、`INDEX.md` | `guide-release.md` 第 6 步 |
| 部署 / 状态变更 | **所有**「部署状态 / 验收项数」表述，并注明该检查的覆盖范围 | `_TEMPLATE.md` 第 9 条（第 5 批实测 10 处要一起改） |
| 归档 / 移动文件 | 全仓旧文件名与旧路径引用、主目录状态措辞、归档件文首、`archive\README.md` | `_TEMPLATE.md` 第 8 条（第 4 批实测 83 处路径 + 13 处状态） |
| 新增文档 | `INDEX.md` 登记一行 + 六字段 + 五段 + `类型-主题.md` 命名 | `_TEMPLATE.md` 第 1–4 条 |
| 改巡检规则 | 本页、`_TEMPLATE.md`、`check_docs.py` 三者同步 | 本页第 4 段 |

### 4 · 定期巡检触发规则

| 时点 | 建议命令 | 通过标准 |
|---|---|---|
| 每次提交 / PR 前 | `py -3.12 check_docs.py --strict --gates` | 退出码 0 |
| 每次出包前 | 同上 + `py -3.12 01_源码\tools\verify_all.py` | 两者都通过 |
| 每周一次 | `py -3.12 check_docs.py --strict` | 退出码 0；人工复核 WARN |
| 每月一次 | 全量巡检 + 人工看 `archive\` 是否有该归档未归档的旧文档 | 归档清单与实物一致 |
| 每次代码版本升级 | 全量复核所有 `applies_to`，再跑 `--strict` | 无版本错配 |

需要定时执行时，可自行注册 Windows 计划任务（**本次未创建任何计划任务或自动化**），示例：

```
schtasks /Create /TN "AsrSubs 文档周巡检" /SC WEEKLY /D MO /ST 09:00 ^
  /TR "py -3.12 D:\插件\AsrSubs_整合_20261031\check_docs.py --strict --json"
```

### 5 · 防复辟红线与告警处置

| 现象 | 脚本判定 | 处置 |
|---|---|---|
| 主目录出现同主题第二份、`final_v3` / `副本` 类命名 | FAIL | 归并到唯一当前版，旧版进 `archive\` 并写「已废弃，见」 |
| Active 行指向不存在的文件 | FAIL | 补文件，或把状态改成「计划中（不存在）」并说明 |
| 归档件缺文首标注 / 图片未登记 | FAIL / WARN | 补标注、补 `archive\README.md` 登记 |
| 出现 `待归档` / 旧验收数字 / 旧部署状态 | FAIL | 同步文档；若确属有意保留的历史快照，行内写明「历史 / 快照 / 已作废」以命中豁免 |
| 维护人为空、缺五段、缺 front-matter | FAIL | 按 `_TEMPLATE.md` 补齐 |

告警处置顺序：① 看 FAIL 明细定位「文件:行」→ ② 判断是文档没同步还是脚本误报（误报就改脚本或白名单，**不要删检查**）→ ③ 同步后复跑 `--strict` 直到退出码 0 → ④ 把新踩到的坑写进 `_TEMPLATE.md` 与本页，避免下次重犯。

### 6 · 已知豁免与边界

- 四类分类文档（design / guides / reference / adr）的 `applies_to` 必须包含当前代码版本；体系类文件（`INDEX.md` / `CHANGELOG.md` / `_TEMPLATE.md`）不参与该检查。
- `INDEX.md`、`CHANGELOG.md`、`_TEMPLATE.md` 不计入四类分类份数；`NOTICE.md` 是纯法务声明，按约定不带 front-matter，不纳入该检查。
- 两张归档 PNG 无法写文首标注，判定依据记在 `archive\README.md` 与 `design\design-ui.md`；脚本只检查它们是否被登记。
- `engine\version.txt` 在别的开发机上可能不存在，此时降级为 WARN 而非 FAIL。
- 巡检通过**不代表功能正确**：v1.2.0 三项改动仍需真机验证（① 对照组已实测，其余待办），见 `reference\reference-tests.md`。

## 失效内容说明

- **旧治理做法已失效**：靠人记路径、无总表、同主题多版并存、归档不标注、出包不同步发布说明、验收数字写死在正文——这些正是前 5 批清理掉的问题，不得恢复。
- **历史数字只在带日期的历史段落保留**：早间验收状态、旧部署状态等表述只能出现在明确标注「历史 / 已作废」的段落，不得当作当前结论；巡检脚本按此口径豁免。
- **第 5 批的引擎状态结论已被第 6 批取代**：`sync_dev.py` 只读巡检现显示「engine 与 01_源码 规范文件集完全一致」，`engine\version.txt` 与 `qwen_version.py` 均为 1.2.0。写「验收全绿」时仍须注明 `verify_all.py` 的「部署一致」只比 `.lua` 与 `paths.json`，不比 engine 的 `.py`——这是检查范围的事实，不随版本变化。
- 本页不替代 `guide-release.md`（出包）、`guide-dev-workflow.md`（代码同步）与 `reference-tests.md`（验收真值）；三者仍是各自场景的权威文档。
