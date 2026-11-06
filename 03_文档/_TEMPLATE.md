---
title: 本文档模板（辅助件，已在 INDEX 第一节登记）
type: template
status: Active
version: 1.0.5
last_update: 2026-11-05
applies_to: 文档体系本身
maintainer: Jian
---

# 文档模板 · 用法

1. 复制本文件到目标分类目录，按 `类型-主题.md` 命名（全小写、连字符，不带版本号 / 日期 / `final` / `副本`）。
2. 补齐 front-matter 六个必填字段；`status: Deprecated` 时 `superseded_by` **必填**，`supersedes` 填被替代的旧路径。
3. 正文按下面五段写，**结论先行**。写不出「一句话摘要」说明这篇文档不该存在。
4. 在 `INDEX.md` 登记一行，并在 `替代 / 归档` 列写出被替代的旧文档。
5. 不确定的结论写 `⚠ 待人工确认：……`，**不要猜、不要把过时内容润色成看起来正确**。
6. **ADR 结构**：`adr\` 用标准 ADR 结构（状态表 / 背景 / 决策 / 理由 / 后果），但**必须同时含通用五段**（一句话摘要 / 当前有效结论 / 关键变更 / 操作·参考细节 / 失效内容说明）。ADR 的「操作 / 参考细节」段写**落地位置与查阅入口**，不写操作步骤。**第 3 批起 4 篇 ADR 已全部补齐，不再有豁免**；新建 ADR 一律按此办。
7. 维护人统一填 `Jian`；只有**结论不确定**处才允许出现「待人工确认」。
8. **归档 / 移动文件后，必须全仓检索旧文件名与旧路径并同步更新所有引用**；同时把主目录里「待归档 / 原件仍在原位」这类状态改成「已归档」，否则等于亲手制造新的过时文档。归档清单与反向 mv 还原方法登记在 `archive\README.md`。
9. **部署 / 状态变更后，必须全仓同步「部署状态」「验收项数」表述**（第 5 批实测：10 处文档要一起改）。写「验收全绿」时必须写明该检查的覆盖范围：`verify_all.py` 的「部署一致」只比 `.lua` 与 `paths.json`，**不比 engine 的 `.py`**；engine 是否同步须另跑 `sync_dev.py` 只读巡检。写回文档用 `newline=""` 并先归一 `\r\n`，否则跨行替换会因 CRLF 静默失配。
10. **每次提交 / PR 前跑 `py -3.12 check_docs.py --strict --gates`**（项目根），退出码必须为 0；巡检脚本检查索引实存、命名、元数据、五段、归档标注、过时措辞与版本一致性。规则变更须同步 `_TEMPLATE.md`、`guides\guide-doc-maintenance.md` 与 `check_docs.py` 三处。
11. **写回文档后必须 grep 盘符路径**：工具层传递 Bash / heredoc 命令时会把 Windows 反斜杠路径转成「盘符 + 正斜杠」形态（第 5、6 批两次踩到，分别 16 处 / 9 处）。叙述统一用反斜杠，Git Bash 命令示例统一用 `/d/...`；`check_docs.py` 第十组会自动拦。

---

---
title: （文档标题）
type: design | guides | reference | adr | changelog | README
status: Active | Deprecated
version: （文档自身版本，1.0.0 起）
last_update: YYYY-MM-DD
applies_to: AsrSubs X.Y.Z（对应 01_源码/version.txt）
maintainer: （姓名或角色）
supersedes: —
superseded_by: —
---

# （文档标题）

## 一句话摘要

（一句话说清这篇解决什么问题、读者读完能做什么。）

## 当前有效结论

（可直接据以操作或决策的结论；每条标注适用条件。用表格或短列表，不写过程。）

## 关键变更（仅影响使用的里程碑）

- vX.Y.Z（YYYY-MM-DD）：（一行说清改了什么、对用户意味着什么）
- 详细历史见 `CHANGELOG.md`，此处不复述。

## 操作 / 参考细节

（正文。命令要能直接复制执行；路径写全；API 事实注明验证方式。）

## 失效内容说明

（哪些旧结论不再成立、被哪篇文档取代；历史过程放附录或 `archive/`。）

---

## 归档文件专用头

移入 `archive/` 的文件保留原正文，只在**文首**加：

```markdown
> 已废弃，见 `../design/xxx.md`（或 `../CHANGELOG.md`）。
> 废弃原因：（一句话）。归档日期：YYYY-MM-DD。适用代码版本：（当时的版本）。
> 本文结论不得用于当前操作。
```

归档命名：`主题-YYYYMMDD.md`，例如 `archive-carryover-20261031.md`。

> **坑（2026-11-05 实测）**：给归档件加文首标注时，Windows 上 Python 文本模式 `io.open(...,"w")` 会把全文 LF 统一写成 CRLF，使「逐字节快照」失真（本次 35,620 → 36,443 字节）。加标注一律用二进制 `open(...,"rb")` 读、`open(...,"wb")` 写，并实测去掉归档头后的正文长度。

## 防腐化自检（提交前逐项看）

| 检查 | 通过标准 |
|---|---|
| 版本号 | front-matter 的 `applies_to` == `01_源码\version.txt` |
| 状态一致 | front-matter 的 `status` 与 `INDEX.md` 同一行一致 |
| 唯一性 | 同一主题在主目录只有一篇 Active 文档 |
| 无死链 | `INDEX.md` 里每个 Active 路径都真实存在 |
| 无编造 | 所有「当前结论」都能指向代码 / 配置 / 实测；否则标 `待人工确认` |
| 结论先行 | 「一句话摘要」+「当前有效结论」在正文最前，过程叙事不在主文档 |
| 五段齐全 | 一句话摘要 / 当前有效结论 / 关键变更 / 操作·参考细节 / 失效内容说明，ADR 亦不豁免 |
| 快照标注 | 调研或快照类文档必须带**日期 + 适用范围**，「本项目」能力描述不得当作当前版本结论 |
| 出包联动 | 出包后必须同步 `04_安装包\README_说明.txt`、`CHANGELOG.md`、`reference\reference-release-notes.md`、`INDEX.md` |
| 部署联动 | 部署后同步所有「部署状态 / 验收项数」表述，并注明验收不覆盖 engine 的 py；不得把「菜单 Lua 已部署」写成「引擎已升级」 |
| 自动巡检 | 提交前 `check_docs.py --strict --gates` 退出码 0；规则与触发时点见 `guides\guide-doc-maintenance.md` |
| 路径风格 | 叙述里盘符后接反斜杠；命令示例用 `/d/...`；`check_docs.py` 第十组拦截被转正的路径 |
| 命名 | `类型-主题.md`，无 `final_v3_副本` 类字样 |
| 归档同步 | 归档后主目录无「待归档 / 原件仍在原位」类过时状态；所有引用指向 `archive\`；归档件文首有「已废弃，见 xxx」 |
