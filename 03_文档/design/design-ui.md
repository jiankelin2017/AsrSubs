---
title: 设计 · Fusion 界面
type: design
status: Active
version: 1.0.0
last_update: 2026-11-05
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §6.8（字幕轨清单）/ §6.10（界面重构）
superseded_by: —
---

# 设计 · Fusion 界面

## 一句话摘要

界面跑在 Fusion UI 上下文里，参数是「配置 → `App.S.cfg` → 识别」单向链，弹窗只在点击时创建；字幕轨清单由插件自绘，不依赖 Resolve 轨道折叠能力。

## 当前有效结论

| 结论 | 适用条件 / 依据 |
|---|---|
| **参数真源是 `App.S.cfg`**；设置弹窗控件 → `syncSet()` 写回 `cfg` + `uiSnap` + 存盘；开始识别只读 `cfg` | 弹窗从没打开过也能正常识别（`archive\archive-dev-doc-20261105.md` §6.10.3） |
| 控制台 / 主窗口**不持有参数控件**，`App.S.items.LangCombo` 等一律为 `nil`；启动时不得往主窗口填下拉 | 否则报 `attempt to index field 'LangCombo'` |
| 设置弹窗**只在点击事件里 `AddWindow`**，且只创建一次，之后反复 Show/Hide | 启动阶段第二次 `AddWindow` 静默返回 `nil`、日志无报错 |
| 关闭事件必须按窗口 ID 绑：`w.On["AsrSubsSettings"].Close = ...` | `w.On.Close` **永不派发**；处理器里 `return false` 还会取消关闭 |
| 弹窗几何每秒随 Tick 存 `engine\rt\set_win.txt`（`x,y,w,h`），不依赖关闭事件 | 校验只挡 `<320×260`，过严会把 428×538 误判非法 |
| 字幕轨清单在**插件面板自绘**（`SubTrackInfo` Label +「刷新」按钮），每次刷新重新取 `GetCurrentTimeline()` | Resolve 21.1.2 官方 API **没有任何轨道高度 / 折叠能力**（四路交叉验证，见 §6.8.1） |
| 刷新时机：开窗 `GetItems()` 后、落轨完成（成功/失败都刷）、点「刷新」按钮 | `App.timeline` 是开窗快照，换集会过期 |
| `ui:Label` 宽度**按创建时的文本算死**，后续改字不自动加宽 | 初始文本必须写足（如按 3 条轨宽度 / `第 0000 行 00:00:00:00` 占位） |
| 日志 / 热词统一用 `TextEdit.PlainText` | `LineEdit` 单行不换行会溢出 |

> ⚠ **待人工确认（布局图）**：`archive\archive-dev-doc-20261105.md` §6.10.1 的布局图与 §6.10.2 控件清单是 **v4.8.14 快照**。此后 v1.0.1 有 18 处体验修复、v1.2.0 改了「开始生成」前置条件，**当前 1.2.0 界面是否仍与该图/清单逐项一致，本次治理未核实**，因此本页不把布局图当现状断言。
>
> **两张 PNG 已核实（2026-11-05）**：`archive\ui-design-preview-20261031.png` 图内标注 **AsrSubs v4.7.2**、按钮写「开始生成字幕」；`archive\ui-plan-b-sketch-20261031.png` 图内标注 **v4.8.0**，带红色编号 1–6 与「逐步核对」清单，是方案决策过程稿。1.2.0 用户手册的按钮文案是「开始生成」。两图**均不能作为 1.2.0 界面依据**，已归档（2026-11-05，不移入 `assets\`）；全仓无脚本引用它们，归档不会断构建链。已于 2026-11-05 归档。

## 关键变更（仅影响使用的里程碑）

- v4.8.0：参数真源改为 `App.S.cfg`；设置改为点击时创建弹窗。
- v4.0.9：新增面板内字幕轨清单（Resolve 无折叠 API 后的等价方案）。
- v1.1.2：修「去除标点符号 / 句末无标点勾了没反应」「每行字数关窗变回 24」「导出 TXT 后假死」，根因都是**仍在读主窗口那个不存在的控件**。
- v1.2.0：「开始生成」按 `App:CanStart()` 前置条件置灰。详见 `..\CHANGELOG.md`。

## 操作 / 参考细节

### 参数链（当前设计）

```
配置文件 ──readSettings──▶ App.S.cfg ──▶ 开始识别只读 cfg
                              ▲   │
                    syncSet() │   └──▶ App.S.uiSnap（控件读不到时的兜底）
                              └── 设置弹窗里的控件
```

控件读不到时的兜底顺序（v1.1.2 定下）：**弹窗控件 → 已保存配置 → 快照 → 默认**，空快照不得覆盖配置。

### 字幕轨清单

`App:ReadSubTracks()` 读 `GetTrackCount("subtitle")`，逐轨取条目数 / 启用态 / 轨名，全部用 `pcall` 包裹；`App:RefreshSubTrackInfo(showLog)` 拼成 `字幕轨 N 条：ST1(启用·53条)｜ST2(禁用·0条)`。

三条边界事实（`GetIsTrackEnabled` 对不存在的轨返回 `nil`，判定必须写 `en = (raw == true)`，写成 `raw ~= false` 会把 `nil` 误判成启用；`GetTrackName` 可能返回空串或抛异常，回退成 `ST编号`；`GetItemListInTrack` 异常时条数按 0）见 `..\reference\reference-resolve-api.md`。

### 已修的典型坑（结论，不重复过程）

| 现象 | 根因类型 |
|---|---|
| 设置 / 日志按钮报「窗口不可用」 | 启动阶段建第二个窗口返回 nil |
| 「删行即剪」永远提示没有视频轨 | 读了 `App.S.timeline`，实际缓存字段是 `App.timeline` |
| 弹窗「关闭」「X」都点不动 | `local function` 只对其之后的代码可见，处理器调用到全局 nil 而抛错 |
| 每行字数 / 两个标点开关改了不生效 | 读主窗口控件（已不存在），见 v1.1.2 |
| `SRT 有改动` 删了还显示 | 只改运行时赋值，标签**初始文本**仍是旧值 |
| 点表格行编辑框没内容 | 富文本渲染失败被 `pcall` 吞掉，改纯文本兜底 |

### 离线验证

`tools\test_subtrack_info.py`（7 用例，需 `pip install lupa`）从 `AsrSubs.lua` 源码正则抽取 `ReadSubTracks` / `RefreshSubTrackInfo` 真实文本塞进 lupa 跑。假对象必须返回 `lua.table_from([...])`——lupa 不把 Python list 的 `__len__` 暴露给 Lua 的 `#`，否则条数恒为 0。`tools\test_can_start.py`（9 用例）覆盖按钮前置条件。

## 失效内容说明

- 「把字幕轨道缩最小 / 折叠」是**已放弃的需求**：Resolve 21.1.2 无轨道高度 / 折叠 API，UI Automation 也只拿到 5 个顶层控件，5 个悬停点截图确认轨道头没有折叠箭头。取证过程见 `archive\archive-dev-doc-20261105.md` §6.8.1（已归档，2026-11-05）。
- §6.10.4 的「修复版本 4.8.x」编号是历史编号，**版本号体系已在 v1.0.0 重置**，见 `..\adr\adr-0003-version-reset-1.0.0.md`。
- 「面板常驻文字」清单（`已写回 SRT`、`SRT 有改动` 等）是当时的删除记录，不是当前控件清单。
