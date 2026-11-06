---
title: 归档区说明（archive）
type: archive
status: Active
version: 1.0.0
last_update: 2026-11-06
applies_to: AsrSubs 1.2.0 · 文档治理基线
maintainer: Jian
supersedes: —
superseded_by: —
---

# 归档区说明（archive）

## 一句话摘要

本目录只放**已废弃、仅供溯源**的原件；主目录 `03_文档\` 每类只留当前版，归档件一律不得作为操作或结论依据。

## 当前有效结论

| 规则 | 内容 |
|---|---|
| 命名 | `主题-YYYYMMDD.md`；本批沿用第 3 批已获批准的 `archive-主题-YYYYMMDD.md` |
| 文首 | 每个文本文件首行写「已废弃，见 xxx」+ 归档日期 + 状态 + 废弃原因 |
| 隔离 | 主目录不再保留同主题第二份；旧版一律进本目录 |
| 图片 | PNG 无法写文首标注，判定依据记在 `..\design\design-ui.md` 与下方清单 |
| 可逆 | 本批全部用 `mv`，**零删除**；还原方法见「操作 / 参考细节」 |

## 关键变更（仅影响使用的里程碑）

- 2026-11-05 第 1 批：重写 `README.md` 前，把当时的 35,620 字节原文存为 `README-20261105.md`；第 4 批补了 4 行文首归档标注，去掉即得逐字节原文。
- 2026-11-05 第 4 批：一次性归档 7 类原件；3 个旧安装包移入 `04_安装包\_历史\`，旧构建产物移入 `02_构建与安装器\_构建产物归档\`。旧构建产物已于 2026-11-06 删除；5 个旧包已于 2026-11-06 彻底删除，本条只作历史记录。
- 2026-11-05 第 6 批：engine 同步前，把 6 个待覆盖文件备份到 `..\..\_改前备份_部署v1.2.0_20261105\engine_改前\`（不是归档件，是改前快照）。

## 操作 / 参考细节

### 归档清单

| 归档件 | 归档前路径 | 替代文档 |
|---|---|---|
| `README-20261105.md` | `..\..\README.md`（改写前快照） | `..\..\README.md` |
| `archive-dev-doc-20261105.md` | `..\开发文档.md` | `..\INDEX.md` 第一节（design / guides / reference / adr 共 21 篇） |
| `archive-carryover-20261031.md` | `..\AI交棒文档.md` | `..\design\design-architecture.md`、`..\reference\reference-resolve-api.md`、`..\CHANGELOG.md` |
| `archive-version-diff-20261031.md` | `..\版本差距盘查_v3.0.5_vs_v3.2.6.md` | `..\adr\adr-0004-single-source-version.md`（对比对象已不存在，无当前对应物） |
| `archive-uninstall-fix-20261030.md` | `..\卸载修复说明.txt` | `..\reference\reference-installer-pitfalls.md` |
| `archive-readme-merged-20261101.md` | `..\..\README_整合说明.md` | `..\guides\guide-release.md`、`..\reference\reference-release-notes.md` |
| `ui-design-preview-20261031.png` | `..\UI设计预览.png` | `..\design\design-ui.md`（图内 v4.7.2，历史预览稿） |
| `ui-plan-b-sketch-20261031.png` | `..\UI方案B对照草图.png` | `..\design\design-ui.md`（图内 v4.8.0，方案决策过程稿） |

### 两张 PNG 的判定依据

`UI设计预览.png` 图内标注 **AsrSubs v4.7.2**、按钮写「开始生成字幕」；`UI方案B对照草图.png` 图内标注 **v4.8.0**，带红色编号 1–6 与「逐步核对」清单。1.2.0 用户手册的按钮文案是「开始生成」。两图均不能作为 1.2.0 界面依据；全仓无脚本引用它们，归档不会断构建链。

### 还原方法（如需撤回归档）

本项目根为 `D:\插件\AsrSubs_整合_20261031`，归档全部是移动，因此直接反向 `mv` 即可，例如：

```
mv "03_文档/archive/archive-dev-doc-20261105.md" "03_文档/开发文档.md"
mv "03_文档/archive/archive-readme-merged-20261101.md" "README_整合说明.md"
（第 4 批的旧包还原命令已**永久失效**：5 个旧包于 2026-11-06 移出项目包后已永久删除，暂存目录 `D:\插件\_AsrSubs_旧包隔离_20261106\` 也于同日移除。本机已无副本可还原，只保留大小 / MD5 留档于 `..\reference\reference-release-notes.md`。）
```

如需撤回第 6 批的 engine 同步，把 `engine_改前\` 里的 6 个文件反向 copy 回 `D:\AsrSubs\engine\`（命令用 Git Bash 的 POSIX 路径写法）：

```
cp -p "_改前备份_部署v1.2.0_20261105/engine_改前/"* /d/AsrSubs/engine/
```

其余按上表逐项反向执行。**注意**：还原后 `..\INDEX.md` 的归档状态需同步改回，否则总表会变成假链接。

## 失效内容说明

- 本目录内除本说明文件外，全部 `.md` / `.txt` 状态为 **Deprecated**；当前结论以 `..\INDEX.md` 第一节的 Active 文档为准。
- 归档件中的版本号（v4.8.18 / v3.2.6 / v1.28 等）是历史快照，不得「换算」成当前 1.2.0 的结论，也不得把过时内容润色成看起来正确。
- 本说明只描述归档机制与清单，不记录任何产品功能结论；清单状态以 `..\INDEX.md` 为准。
