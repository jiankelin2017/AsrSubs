---
title: ADR-0004 · version.txt 是版本号的唯一来源
type: adr
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §5 / 旧 README §8
superseded_by: —
---

# ADR-0004 · `version.txt` 是版本号的唯一来源

| 项 | 值 |
|---|---|
| 状态 | **已接受，现行规则** |
| 决策时间 | v3.2.6 事故后确立（2026-10-30 起）；2026-11-05 复核 |
| 影响范围 | 全部代码、构建脚本、文档、安装包命名 |

## 一句话摘要

`01_源码\version.txt` 是版本号唯一来源，由 `sync_version.py` 注入 5 处，其余地方一律不得手写版本号。

## 当前有效结论

- **决策**：改版本号只改 `version.txt`，然后 `sync_version.py` → `sync_payload.py`。
- **状态**：已接受的现行规则；2026-11-05 实测 `--check` 报「版本号 1.2.0 已同步完毕」，5 处一致。
- `qwen_version.py` 由脚本生成，**勿手改**。

## 关键变更（仅影响使用的里程碑）

- 2026-10-30（v3.2.6 事故后）：确立 `version.txt` 唯一来源与 `sync_version.py` 五处注入，防止打出「旧程序 + 新说明书」。
- 2026-11-05：实测 `--check` 报「版本号 1.2.0 已同步完毕」，五处一致；固化为 ADR-0004。

## 背景

3.0.6 → 3.2.6 期间的改动只做在装机版单文件上，`lua_src` 分片与 `version.txt` 都停在 3.0.5。那时打包会打出「3.0.5 的程序 + 3.2.6 的说明书」，搜索 / 高亮 / 多选删除 / 粗剪全部丢失。根因是版本号与代码状态分散在多处、无单一真相源。

## 决策

**`01_源码\version.txt` 是版本号的唯一来源**。`tools\sync_version.py` 把它注入 5 处：

1. `01_源码\AsrSubs.lua` 的 `local VERSION = "x.y.z"`
2. `02_构建与安装器\AsrSubs.iss` 的 `#define MyAppVersion`
3. `02_构建与安装器\installer.py` 的 `APP_VERSION`
4. `01_源码\qwen_version.py` 的 `VERSION`
5. `03_文档\使用说明.txt` 第 2 行抬头

**改版本号只改 `version.txt` 一处**，然后跑 `sync_version.py` → `sync_payload.py`。`qwen_version.py` 由脚本生成，**勿手改**。

## 理由

- 一处写、五处同步，杜绝「代码版本 ≠ 文档版本 ≠ 安装包版本」。
- 打包前的 `sync_version.py --check` 能把不同步直接拦下（三道闸之一）。

## 后果

| 正面 | 负面 / 需遵守 |
|---|---|
| 版本号可机器校验 | 任何文档里的 `applies_to` 必须等于 `version.txt`；`..\INDEX.md` 的核对规则以此为准 |
| 打不出「旧程序 + 新说明书」 | `03_文档\使用说明.txt` 被脚本改写第 2 行，该文件同时被 `.iss` 引用，**不得改名或迁移** |
| — | `build_lua.py` 已退化为只读体检器，只校验 `AsrSubs.lua` 与 `version.txt` 一致，**不生成文件** |

## 验证方式

```bat
py -3.12 tools\sync_version.py --check
```

2026-11-05 实测输出：「版本号 1.2.0 已同步完毕」，5 处全部一致。

## 操作 / 参考细节

- 五个注入点的完整清单见上文决策段；`qwen_version.py` 由脚本生成，勿手改。
- 一致性校验命令：`py -3.12 01_源码\tools\sync_version.py --check`（只读）。
- 出包流程里「升版本」这一步的严格顺序见 `..\guides\guide-release.md` 第 1 步；三道闸门见 `..\design\design-installer.md`。

## 失效内容说明

- `archive\archive-carryover-20261031.md` §0「开发目录版本 3.0.5、装机版 3.2.6、落后 6 个次版本」是**事故当时的快照**，对比对象 `lua_src\` 已不存在，不可作为当前版本依据。
- 旧 README §8 与开发文档 §5 里「改版本号只有一步链」的表述仍成立，但**必须补上三段式规则**（见 `adr-0003-version-reset-1.0.0.md`）。
