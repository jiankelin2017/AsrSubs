> 已废弃，见 ../adr/adr-0004-single-source-version.md。
> 归档日期：2026-11-05 | 状态：Deprecated | 维护人：Jian | 主目录不再保留。
> 废弃原因：对比对象 lua_src/ 全仓已不存在，差距结论无当前对应物。本文仅作历史史料，不得作为当前结论或操作依据。

# AsrSubs 版本差距盘查：lua_src(3.0.5) ↔ 装机版(3.2.6)

> 盘查时间：2026-10-30 18:30
> 目的：弄清开发版 `lua_src` 与实际在跑的 3.2.6 之间差了什么、差在哪、按什么顺序回灌。
> 方法：脚本化对比（difflib 逐行 + 函数边界归属 + 文件哈希），不做人工目测。

---

## 0. 摘要

| 项 | 开发版 A | 装机版 B | 差 |
|---|---|---|---|
| 文件 | `01_源码\AsrSubs.lua`（`build_lua.py` 产物） | `%ProgramData%\…\Utility\AsrSubs\AsrSubs.lua` | — |
| 版本 | **3.0.5**（`version.txt`） | **3.2.6**（`local VERSION`） | 6 个次版本 |
| 行数 | 4,965 | 6,144 | **+1,179** |
| 差异块 | — | — | **71 个 hunk** |
| 变更行 | — | — | 删 125 / 增 1,304＝**1,429 行** |
| 函数 | 119 个 | 152 个 | **新增 33 / 删除 0 / 改动 24** |
| UI 控件 ID | 56 个 | 73 个 | +18 / −1 |
| `App.S` 字段 | 57 个 | 75 个 | +18 / −0 |
| 顶层常量 | — | — | +6 |

一句话：**3.0.6 → 3.2.6 是一次功能性扩建，不是打补丁**——新增了一整套搜索高亮、多选管理、删除撤销、粗剪四条能力，代码全部只在装机版单文件里。

---

## 1. 三方版本位置（先厘清，避免再找错）

| 位置 | 角色 | 版本 | 大小 | 时间 |
|---|---|---|---|---|
| `D:\插件\AsrSubs\01_源码` | 开发版（`lua_src` 13 片） | 3.0.5 | 246,426 B | 10-30 03:45 |
| `D:\插件\AsrSubs\02_构建与安装器\payload` | 安装载荷 | 3.0.5 | 246,426 B | 10-30 03:45 |
| `D:\AsrSubs\engine` | 装机版运行目录 | 3.2.5 | 286,637 B | 10-30 16:52 |
| `%ProgramData%\…\Utility\AsrSubs` | Resolve 菜单实加载 | **3.2.6** | 301,498 B | 10-30 18:00 |

`engine` 与脚本菜单目录之间还差 316 行（v3.2.6 的粗剪），也就是说**装机版自身也有两个版本**。

---

## 2. 差距按功能分组（33 个新增函数全部在此）

行号均为装机版（B）行号，括号内为该函数体行数；"目标分片"是回灌时应落的 `lua_src` 文件。

### 组 A · 搜索与高亮（10 个）

| 函数 | B 行 | 行数 | 目标分片 | 作用 |
|---|---|---|---|---|
| `htmlEsc` | 1068 | 10 | `04_app.lua` | HTML 转义 |
| `htmlMark` | 1078 | 19 | `04_app.lua` | 给关键词套 `<span>` 黄底 |
| `stripHitMarks` | 1097 | 18 | `04_app.lua` | 剥离旧版【】残留（兼容兜底） |
| `App:PaintRow` | 4162 | 45 | `11_subs.lua` | 单行刷新：干净原文 + 命中底色三态 |
| `App:PaintAllRows` | 4207 | 5 | `11_subs.lua` | 整表上色 |
| `App:ReadFindKwRaw` | 4234 | 13 | `11_subs.lua` | 多路回退读查找框（含 nil 语义） |
| `App:ConfirmFind` | 4876 | 22 | `11_subs.lua` | 「确认」= 立即搜索并定位第一处 |
| `App:FindPrev` | 4963 | 38 | `11_subs.lua` | 查找上一个 |
| `App:HitCount` | 5070 | 10 | `11_subs.lua` | 命中"行"数统计 |
| `App:RenderSubEditRich` | 5578 | 49 | `11_subs.lua` | 编辑区富文本高亮（含回读校验） |

### 组 B · 多选与选中管理（9 个）

| 函数 | B 行 | 行数 | 目标分片 | 作用 |
|---|---|---|---|---|
| `App:CheckedCount` | 5080 | 11 | `11_subs.lua` | 已选行计数 |
| `App:CollectSelected` | 5091 | 41 | `11_subs.lua` | 从表格收集选中行号 |
| `App:SyncSelection` | 5132 | 25 | `11_subs.lua` | SelectionChanged → 选中集合 |
| `App:SyncCheckedFromTree` | 5157 | 6 | `11_subs.lua` | 选中集合落盘 |
| `App:RefreshSelBar` | 5163 | 11 | `11_subs.lua` | 刷新"已选 N 行"仪表盘 |
| `App:SyncSelAllChk` | 5174 | 9 | `11_subs.lua` | 同步全选框三态 |
| `App:ToggleSelectAll` | 5183 | 19 | `11_subs.lua` | 全选 / 取消全选 |
| `App:InvertSelection` | 5202 | 26 | `11_subs.lua` | 反选 |
| `App:RefreshRowInfo` | 5553 | 25 | `11_subs.lua` | 当前行时间码/时长显示 |

### 组 C · 删除与撤销（6 个）

| 函数 | B 行 | 行数 | 目标分片 | 作用 |
|---|---|---|---|---|
| `App:SnapshotUndo` | 5228 | 6 | `11_subs.lua` | 单级撤销快照 |
| `App:UndoDelete` | 5234 | 33 | `11_subs.lua` | 撤销（3.2.6 起按 `lastCut` 分流到"重建时间线"） |
| `App:DeleteSelectedRows` | 5267 | 23 | `11_subs.lua` | 删选中行（二次确认） |
| `App:DeleteHighlightTerm` | 5290 | 43 | `11_subs.lua` | 删命中字词（只删字不删行） |
| `App:DeleteSubsRows` | 5333 | 36 | `11_subs.lua` | 按行号删除 + 重建表格 |
| `App:RefreshDirty` | 5544 | 9 | `11_subs.lua` | 脏标记刷新 |

### 组 D · 粗剪（6 个，v3.2.6 独有）

| 函数 | B 行 | 行数 | 目标分片 | 作用 |
|---|---|---|---|---|
| `App:RecordCuts` | 5369 | 36 | `11_subs.lua` | 删行前记时间区间（不是行号） |
| `App:CutStats` | 5405 | 12 | `11_subs.lua` | 统计待切段数/秒数 |
| `App:RefreshCutBar` | 5417 | 19 | `11_subs.lua` | 刷新"将切除 N 段"与按钮可用态 |
| `App:CanCut` | 5436 | 21 | `11_subs.lua` | 是否可切 |
| `App:DoCut` | 5457 | 27 | `11_subs.lua` | 弹确认 → 启动 |
| `App:StartCut` | 5484 | 50 | `11_subs.lua` | 下发 `cut.json`，进入 `phase="cutting"` |

配套：`phase="cutting"` 分支在 `08_tick.lua` 区域（B 3429–3500，72 行新增）；Python 侧 `cut_timeline.py`（开发版缺失）。

### 组 E · 基础设施（4 个）

| 函数 / 标识 | B 行 | 目标分片 | 说明 |
|---|---|---|---|
| `itemToRow` | 1875 | `05_ui.lua` | 表格项 → 行号（`#` 列撤掉后的替代方案） |
| `App:Confirm` | 5534 | `11_subs.lua` | 自定义确认弹窗 |
| `CUT_SCRIPT` 常量 | ~296 | `02_env.lua` | 粗剪脚本路径 |
| `TXT_COL / HTML_BG / HTML_FG` | 分散 | `11_subs.lua` | 列号与配色常量 |
| `OPEN_MARK / CLOSE_MARK`（`【`/`】`） | 1052–1053 | `04_app.lua` | 旧版标记法的兼容常量，`stripHitMarks` 用 |

---

## 3. UI 层差距（18 增 / 1 删）

**新增**（全部落在 `05_ui.lua` 的 BuildUI 区域，B 1349–1440）：

`FindLbl`、`FindGoBtn`、`RepLbl`、`RowReplace`（替换行）、`SelBar`、`SelectAllChk`、`InvertBtn`、`HitLbl`、`SelCountLbl`、`SelSpacer`、`DelSelBtn`、`DelHiTermBtn`、`RowStatus2`、`DirtyLbl`、`RowInfoLbl`、`UndoBtn`、`CutLbl`、`CutBtn`

**移除**：`RowAct`（原"操作"按钮组，被 `SelBar` + `RowStatus2` 取代）

界面结构因此从"一栏到底"变成三段：查找行 → 选择工具条 → 状态条 + 主按钮行。

---

## 4. 状态与常量差距

`App.S` 新增 18 个字段：

- 搜索：`findKwCache`、`hitSet`、`tintState`、`tintWarned`
- 选择：`checked`、`selDirty`、`suppressSelSync`、`textFocus`
- 删除：`subsUndo`
- 粗剪：`cuts`、`cutFile`、`cutCfg`、`cutMode`、`cutSrt`、`cutStartedAt`、`lastCut`、`cutPad`、`cutMerge`

新增顶层常量 6 个：`CLOSE_MARK`、`OPEN_MARK`、`CUT_SCRIPT`、`TXT_COL`、`HTML_BG`、`HTML_FG`（前两个在 B 行 1052–1053，属 `04_app.lua` 区）。

---

## 5. Python 侧差距（比 Lua 侧小得多）

| 结论 | 明细 |
|---|---|
| 逐字节一致 | `asr_engine.py`、`asr_server.py`、`corrections.py`、`diag.py`、`export_files.py`、`hotwords.py`、`land_subtitle.py`、`qwen_version.py`、`resolve_bridge.py`、`subs_io.py`、`timeline_source.py`、`txt_out.py`、`worker.py`、`render_to_wav.xml`、`models.txt`（15 个） |
| 仅装机版有 | `cut_timeline.py`（19,190 B）、`probe_speaker_c.py`（15,194 B） |
| 仅装机版有（生成物） | `paths.json`、`paths.lua`（安装器生成，不必回灌） |

结论：**回灌的工作量 99% 在 Lua**；Python 只需补 2 个脚本，其中只有 `cut_timeline.py` 是 3.2.6 功能依赖项，`probe_speaker_c.py` 需另行确认是否被引用。

---

## 6. 改动量的分片归属（按 hunk 起点统计）

| 目标分片 | 增 | 删 | 合计 | 备注 |
|---|---|---|---|---|
| `11_subs.lua` | 821 | 78 | 899 | 四条新能力全在这里，分片将从 904 行涨到约 1,750 行 |
| `05_ui.lua` | 262 | 46 | 308 | 查找行 / 选择条 / 状态条重排 |
| `08_tick.lua` | 96 | 0 | 96 | 新增 `cutting` 相位 + JSON 轮询 |
| `04_app.lua` | 80 | 0 | 80 | `htmlEsc` / `htmlMark` / `stripHitMarks` |
| `02_env.lua` | 42 | 0 | 42 | `CUT_SCRIPT` 常量 |
| `12_tail.lua` | 2 | 0 | 2 | `EnableButtons` 增加 CutBtn |
| 合计 | 1,304 | 125 | 1,429 | — |

---

## 7. 回灌顺序（有依赖，不能乱）

1. **基础设施**：`04_app.lua`（htmlEsc/htmlMark/stripHitMarks）→ `02_env.lua`（CUT_SCRIPT）→ `05_ui.lua` 的 `itemToRow`。
2. **UI 骨架**：`05_ui.lua` 的 BuildUI 重排（18 个控件 + 移除 RowAct），此时按钮绑定的函数还不存在，需同步加空实现或一次做完第 3 步。
3. **搜索**：`11_subs.lua` 的 PaintRow/PaintAllRows/ReadFindKwRaw/ConfirmFind/FindPrev/HitCount/RenderSubEditRich + `RebuildMatches`/`ApplyFindHighlight` 改造。
4. **选择**：9 个选中管理函数 + SelectionChanged 绑定。
5. **删除撤销**：6 个函数 + `DeleteSubsRows` 里 `RecordCuts` 的调用点。
6. **粗剪**：6 个函数 + `08_tick.lua` 的 `cutting` 相位 + `cut_timeline.py` 进 `payload`。
7. **构建与验收**：`build_lua.py` → `check_lua.js` → `check_lua_load.js` → `verify_all.py` → `sync_payload.py`。

---

## 8. 风险与注意

1. **列结构变了**：3.0.5 是 4 列（含 `#` 行号列），3.2.6 是 3 列、行号藏进 `Text[3]`。`05_ui.lua` 里所有 `ColumnCount` / `ColumnWidth` / `Text[3]` 引用必须一次性改完，否则行号全错。
2. **`PaintRow` 语义变了三次**：3.0.5 → 加【】标记 → 撤标记 → 加底色。回灌时**只能取 3.2.6 的终态**，不要带 `markHits`。
3. **版本号必须同步**：`version.txt`（3.0.5）→ 回灌后改 3.2.6 → 跑 `sync_version.py` → `build_lua.py`，否则界面右下角与安装包属性对不上。
4. **payload 必须重同步**：`02_构建与安装器\payload\AsrSubs.lua` 现在是 3.0.5，不同步就打不出新版。
5. **统计口径**：第 6 节的"改动量"按 hunk 起点归属，整块插入会记到插入点的前一个函数上（例如 `ReplaceInSubs` 显示 +571 行，实际其中大部分是它后面新插入的 565 行代码）。数值只作量级参考，**以第 2 节的函数清单为准**。
6. **`probe_speaker_c.py` 未接线**：装机版里有这个 15 KB 脚本，但 Lua 侧没有任何引用，回灌时可暂不处理。

---

## 9. 一句话交代

差距集中在 **`11_subs.lua`（899 行）** 和 **`05_ui.lua`（308 行）**，成因是四块新功能；Python 侧几乎没动。回灌的难点不是代码量，而是**列结构、PaintRow 语义、UI 布局这三处是连锁改动**，必须整块搬，不能逐函数补。
