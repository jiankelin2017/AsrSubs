> 已废弃，见 ../design/design-architecture.md、../reference/reference-resolve-api.md、../CHANGELOG.md。
> 归档日期：2026-11-05 | 状态：Deprecated | 维护人：Jian | 主目录不再保留。
> 废弃原因：自标过期（写于 v3.2.6），§6 的 API 教训已并入 Resolve API 参考。本文仅作历史史料，不得作为当前结论或操作依据。

# AsrSubs v3.2.6 · AI 交棒文档

> ⚠️ **本文档已过期（写于 v3.2.6）。架构、红线、API 事实请以 `../开发文档.md` 为准。**
> 已知冲突示例：本文 §6.2 说"字幕落轨必须先新建空时间线"，该结论在 v4.0.4 已被
> 「架构红线：只在当前时间线原地落轨」推翻。本文仅作历史沿革参考。

> 写于 2026-10-30 18:40，取代 v2.0.0 版本（旧版留在 `AI交棒文档.md.bak_20261030_183517`）。
> 本文以**装机版 v3.2.6 的实际代码**为准，不以 `lua_src`（仍为 3.0.5）为准。
> 开工前先读第 0 节和第 5 节，那里决定你第一件事该干什么。

---

## 0. 30 秒速览

| 项 | 值 |
|---|---|
| 线上版本 | **3.2.6**（`%ProgramData%\…\Utility\AsrSubs\AsrSubs.lua`，6,143 行） |
| 开发目录 | `D:\插件\AsrSubs\`（01_源码 / 02_构建与安装器 / 03_文档 / 04_安装包 / 09_归档） |
| 开发目录版本 | **3.0.5**（`01_源码\version.txt`），**落后线上 6 个次版本** |
| 装机引擎目录 | `D:\AsrSubs\`（engine / runtime / models / data / docs），其 `AsrSubs.lua` 为 3.2.5 |
| 构建产物 | `01_源码\AsrSubs.lua`，由 `lua_src\` 13 片经 `tools\build_lua.py` 拼接 |
| 差距清单 | `03_文档\版本差距盘查_v3.0.5_vs_v3.2.6.md`（33 新增函数 / 1,429 行） |
| Python 侧 | 两侧 15 个文件逐字节一致，装机版只多 `cut_timeline.py` |

**一句话**：3.0.6 → 3.2.6 的开发全在装机版单文件上做，`lua_src` 没跟上；现在打包会退回 3.0.5。

---

## 1. 项目是什么

DaVinci Resolve 的离线字幕插件：用本机显卡跑 Qwen3-ASR-0.6B + Qwen3-ForcedAligner-0.6B，
为当前时间线生成 SRT，挂到字幕轨并导入媒体池，音频全程不出机器。

3.2.6 在"生成"之外补齐了两件事：**在插件里改字幕**（查找/替换/删词/删行/多选）、
**按删掉的字幕切视频**（粗剪，可撤销）。

### 架构（全程文件邮箱，不开端口）

```
DaVinci Resolve
   └─ AsrSubs.lua（Fusion UI + 异步状态机，6,143 行）
        │  RunTask → worker.py（常驻执行器，CREATE_NO_WINDOW，不闪黑窗）
        ├─ timeline_source.py   读时间线音频片段 → rt\src.lua
        ├─ [多片段/裁剪过] Resolve 渲染 → qwen3_audio_*.wav
        ├─ resolve_bridge.py    识别入口：优先转发常驻服务，失败回退本地加载
        │     ├─ asr_server.py  常驻服务：模型只加载一次（省 6~7 秒/次）
        │     └─ asr_engine.py  识别 + 对齐 + 切句 + 排时间
        ├─ subs_io.py           SRT ↔ Lua 表（绕开 Lua 打不开中文路径）
        ├─ land_subtitle.py     挂字幕轨 + 导入媒体池
        ├─ cut_timeline.py      按删除区间原地重建时间线（3.2.6 新增）
        ├─ export_files.py      导出 srt/txt
        └─ diag.py              诊断信息
```

进程间靠 `engine\rt\` 下的文件通信：`*.json` 收任务、`*.lua` 回结果、
`status.lua` 报进度。**没有任何网络端口**——不依赖 Resolve 版本、不怕防火墙、不怕并发。

---

## 2. 目录地图

```
D:\插件\AsrSubs\
├─ 01_源码\                     ← 唯一源码目录（单文件制，2026-10-30 起）
│   ├─ version.txt              ★ 版本号唯一来源（当前 3.2.6）
│   ├─ AsrSubs.lua              ★ 源码本体（6,143 行），**直接改这份**
│   ├─ cut_timeline.py          粗剪脚本（3.2.6 新增，删行记账 + 重建时间线）
│   ├─ *.py                     其余引擎脚本（与装机版一致）
│   └─ tools\                   构建 / 校验 / 测试
├─ 02_构建与安装器\
│   ├─ AsrSubs.iss · installer.py · build.bat / build.ps1
│   └─ payload\                 安装载荷（17 个文件，3.2.6）
├─ 03_文档\                     使用说明 / 本文 / 资料对比 / 差距盘查
├─ 04_安装包\                   安装器成品
└─ 09_归档\
    └─ 08_旧版本清理_20261030\
        ├─ lua_src_v3.0.5\      原 13 个分片 + v1.28 基线快照（已弃用，仅留档）
        ├─ 部署目录旧备份\       16 个 .bak_v305…v323 + Qwen ASR.lua.old
        └─ 开发版旧文件\         v3.0.0 安装器、旧验收报告、engine 3.2.5、payload 3.0.5
```

> **为什么不再有 `lua_src\`**：3.0.6→3.2.6 的全部改动都直接做在装机版单文件上，
> 分片停在 3.0.5。只要跑一次拼接就会把搜索/高亮/多选删除/粗剪整体回退。
> 2026-10-30 决定改用单文件制：`build_lua.py` 退化为校验器，`sync_version.py`
> 成为唯一写版本号的地方，`build.bat` 增加三道前置闸。

---

## 3. 构建与校验

> ✅ **2026-10-30 起构建链已恢复可用**：源码是单文件，`build_lua.py` 只做校验，
> `build.bat` 打包前会跑三道闸（源码校验 / 版本一致 / payload 一致），
> 任一不过直接中止。以前「跑一次打包就把功能回退到 3.0.5」的风险已消除。

```bash
cd "D:/插件/AsrSubs/01_源码"

# 1) 校验单文件源码（文件存在 / 无 @VERSION@ 残留 / 代码版本 == version.txt）
python tools/build_lua.py

# 2) Lua 语法 + 编译（luaparse 静态 / fengari 真虚拟机，查 200 local 上限）
node tools/check_lua.js "AsrSubs.lua"
node tools/check_lua_load.js "AsrSubs.lua"

# 3) Python 语法
python -m py_compile *.py

# 4) 版本一致 / 载荷同步
python tools/sync_version.py --check
python tools/sync_payload.py --check

# 5) 一键验收（11 项）
python tools/verify_all.py

# 6) 打包（需 PyInstaller + Inno Setup 6）
cd ../02_构建与安装器 && powershell -ExecutionPolicy Bypass -File build.ps1
```

**部署到本机实测**（不打包的最快路径）：

```bash
cp "AsrSubs.lua" "D:/AsrSubs/engine/"                      # 引擎目录（先备份）
cp "AsrSubs.lua" "C:/ProgramData/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/AsrSubs/"
# 完全退出 Resolve 再开
```

**当前的野路子（3.0.6 之后实际怎么做）**：直接改 `%ProgramData%\…\AsrSubs.lua`，
改前备份成 `AsrSubs.lua.bak_v<版本>_<时间戳>`，用 `lua5.1.dll` 的 `luaL_loadfile`
做真编译校验，再 `diff` 确认开发版/部署版一致。这条路能跑，但没有分片、没有构建、没有验收。

---

## 4. 版本轨迹（3.0.5 → 3.2.6）

| 版本 | 内容 |
|---|---|
| 3.0.5 | 开发版当前停留点 |
| 3.1.1 | 表格改原生多选（`ExtendedSelection`）；新增选择工具条（全选/反选/命中与已选计数/删除）；`rowFromTree`→`itemToRow` |
| 3.1.2 | 新增「确认」按钮（`ConfirmFind`）：打完再搜；查找跳转改防抖 |
| 3.1.3 | 删掉「◀ 上一处」；关键词只染色、不再自动选中命中行（选中色会盖住黄）；新增 `selDirty` 下一拍重读选中集 |
| 3.1.5 | `ReadFindKwRaw` 多路回退（属性/方法/`String`），读不到返回 nil 而非 ""；新增 `findKwCache` |
| 3.1.6 | `FillSubTree` 三步各自 pcall（一处抛错不再中断整条刷新链）；命中集合 `hitSet` 改哈希表 |
| 3.1.7 | 命中计数单位改「行」；计数 Label 占位写「0000」（Fusion 按创建时文本定宽）；删除按钮右对齐并改名「删除」 |
| 3.1.8 | 单击一行即 `FocusSubEdit` 并全选（`SetFocus("OtherFocusReason")`） |
| 3.1.9 | 「#」列挂黄点图标（后被撤） |
| 3.2.0 | 撤「#」列：4 列 → 3 列，行号藏进不显示的 `Text[3]`；撤整行着色与黄点（Tree 只能整格染色，一染就整段变黄）；改【】标记 |
| 3.2.1 | 写回入口收敛：删除/删词/替换/撤销只标脏，唯一写入口是「更新字幕 · 写回 SRT」；`DeleteHighlightTerm` 的快照前移到改字之前 |
| 3.2.2 | 新增独立高亮预览 Label（`HitPreview`）+ `htmlEsc`/`htmlMark`（Label 方案后被撤） |
| 3.2.3 | 撤预览条：高亮直接写进编辑区 `TextEdit.HTML`，写完回读 `PlainText` 校验；新增 `editFocus`（与查找框的 `textFocus` 分开） |
| 3.2.4 | 关窗守卫 `App.S.closing`：`RenderSubEditRich` / `SubEdit.LostFocus` 在关闭中直接返回，避免在销毁的窗口上写控件 |
| 3.2.5 | 表格命中行恢复琥珀底色：只染字幕文本格，三态 `""/"hit"/"cur"` + `tintState` 缓存；点行让亮金跟随；编辑区改字即更新命中 |
| 3.2.6 | **粗剪**：`RecordCuts` 删行前记时间区间（相邻 <0.30s 合并）；`CutStats`/`RefreshCutBar` 显示「将切除 N 段 · X.Xs」；`CanCut` 三道门槛；`DoCut` 弹代价确认；`StartCut` 下发 `cut.json`；`08_tick` 新增 `cutting` 相位；`UndoDelete` 有 `lastCut` 时改为重建时间线；新增 `cut_timeline.py` |

---

## 5. 已验证 / 未验证

### 已验证（可以信）

- ✅ 3.2.0 → 3.2.5 每一版都做过 Lua 5.1 真编译（`luaL_loadfile`，非自制括号脚本），并 `diff` 确认开发版与部署版逐字节一致。
- ✅ 界面行为经用户真机截图验证：琥珀底色可显示、编辑区词级高亮生效、单击即进编辑、删除按钮右对齐。
- ✅ 表格 3 列 + `Text[3]` 存行号在 Resolve 21.1 上实测可读写。
- ✅ `TextEdit.HTML` 可写、`PlainText` 可无损回读（唯一失真：首尾空格被压缩，已有回退）。
- ✅ Python 侧 15 个文件在开发版与装机版之间逐字节一致（MD5 比对）。

### 未验证（下一棒必须做）

1. **粗剪（3.2.6）整体未在真机跑过**：`cut_timeline.py` 的原地重建、字幕重挂、
   SRT 改写、还原清单回滚，四步都只有代码级检查。
   - 验证方法：一段 1 分钟素材 → 搜「嗯」→ 删除高亮字词 → 写回 SRT → 切除视频 → 检查时长与音画同步 → 点撤销 → 检查是否回到切除前。
   - 失败表现：时间线被清空（Python 在动手前会写还原清单，可救）。
2. **`FindPrev` 无入口**：函数存在但 v3.1.3 删了「◀」按钮，目前是死代码。
3. **`probe_speaker_c.py` 未接线**：装机版有这个 15 KB 脚本，Lua 侧零引用。
4. **`ontbl` 报错**：控制台出现过 `attempt to index local 'ontbl' (a nil value)`，
   全盘 grep 无命中，判定为 Resolve 绑定层问题；3.2.4 加守卫后是否复现，未确认。

---

## 6. Resolve API 血泪教训（改代码前必读）

**v1.28 时代踩出来的（仍然有效）**

1. **字幕绝不能传 `startFrame/endFrame`** 给 `AppendToTimeline`——实测把 Resolve 当场打崩；传 `mediaType` 则字幕落不上。只传 `{mediaPoolItem, trackIndex, recordFrame}`。
2. **字幕落轨是纯追加**：`recordFrame` 和 `trackIndex` 都被忽略，落点恒等于时间线末尾。所以必须先落字幕、再放素材，且要换一条空时间线。
3. **Lua 的 `io.open` 打不开中文路径**（走 ANSI）。中文路径读写一律交给 Python。
4. **`JobStatus` 是本地化字符串**，不能和 `"Complete"` 比。
5. **交付页要 `OpenPage("deliver")` 且是异步的**：切完立刻设格式会静默失败。两段式（切页 → `render_cfg` 相位再配置）。
6. **`resolve_bridge.py` 顶层绝不能 import torch**：白占显存/页面文件。
7. **常驻服务心跳必须独立线程**：识别同步阻塞，心跳写主循环会停，客户端误判服务已死 → 回退本地再加载一份 torch → `WinError 1455`。
8. **`status.lua` 服务端绝不能删**：会和 bridge 的 100% 信号抢文件，界面永远停在 99%。
9. **`asr_cmd.json` 的基线必须是启动那一刻的签名**，否则会重放上一轮残留命令。
10. **`os.execute` 会闪黑窗**，一律走 `worker.py` 的 `CREATE_NO_WINDOW`。
11. **payload 必须与源码同步**，否则"本地测试是新的、装出来是旧的"。

**3.1–3.2 新踩的（同样别"顺手优化"掉）**

12. **TreeItem 没有 `ForegroundColor`**：官方属性集只有 `Text[] StatusTip[] ToolTip[] WhatsIt SizeHint[] TextAlignment[] CheckState[] BackgroundColor[] TextColor[] Icon[] Font[]`。v3.1.4~3.1.6 一直在写一个不存在的属性。
13. **Tree 单元格只能整格染色**，没有分段富文本；HTML 只在 `Label` / `Button` / `TextEdit` 生效。
14. **Qt 选中行用系统 HighlightedText 重绘**：选中时 item 的 `TextColor` / `BackgroundColor` 全被无视 —— 所以"选中盖住高亮"是必然，不是 bug。
15. **`TextEdit` 无局部选中**（只有 `SelectAll`）；**写 HTML 会重置光标**，所以打字过程中不能重排，只能失焦或换行时刷。
16. **`ColumnCount = 3` 时 `Text[3]` 仍可写可读**（不显示、不占宽度），被用来藏行号。
17. **Tree 事件默认开关不一样**：`CurrentItemChanged` / `ItemClicked` 默认开，`ItemDoubleClicked` / `ItemChanged` **默认关**，不显式打开永远不派发。
18. **Fusion 的 Label 宽度按创建时文本定死**：初始写「命中 0」，命中 47 时只显示「命中 4」。占位必须写宽。
19. **`ui:MessageBox` 不可用时**要能降级（`App:Confirm` 里 pcall 失败即视为确认），否则删除/切除流程会被卡住。

---

## 7. 待办（按优先级）

### P0 — 已全部完成（2026-10-30）

1. ~~**恢复构建链**~~ ✅ **已定为路线 ③ 单文件制**并落地：
   - `lua_src\` 13 个分片归档到 `09_归档\08_旧版本清理_20261030\lua_src_v3.0.5\`
   - 3.2.6 单文件直接作为 `01_源码\AsrSubs.lua`，不再拼接
   - `build_lua.py` 从拼接器改为校验器；`sync_version.py` 新增 `AsrSubs.lua` 目标（共 5 处）
   - `build.bat` 增加 `[0/3]` 三道前置闸，防误打包回退
   - 四处 `AsrSubs.lua` 已逐字节一致（MD5 `e31484f0…`），`verify_all.py` 11 项全绿
2. ~~**补 `cut_timeline.py` 进 `payload`**~~ ✅ 已进 `01_源码` 与 `payload`，并登记进 `sync_payload.py` 的 FILES

> 顺带修掉两个真 bug（都是去标识化时把旧名 `Qwen ASR` 也替换成 `AsrSubs` 造成的）：
> - `installer.py`：`LEGACY_MENU_DIR` 与 `MENU_DIR` 同为 `"AsrSubs"` → 安装器把刚写好
>   的菜单当老菜单 `rmtree` 掉，**装完 Resolve 菜单里根本没有 AsrSubs**。已改回 `"Qwen ASR"`。
> - `verify_all.py`：`MENU_DIRS = ["AsrSubs", "AsrSubs"]` → 同一目录数两遍，验收永远报
>   「菜单重复」。已改回 `["AsrSubs", "Qwen ASR"]`。

### P1 — 下一版该有

3. 粗剪真机回归（第 5 节第 1 条），把结果回填。
4. `FindPrev` 要么接快捷键（如 Shift+Enter），要么删掉。
5. 撤销从单级改多级（现在删两次只能撤最后一次）。
6. 粗剪保留效果：重建时把转场/特效按时间映射回去（Resolve 没有直接的"搬效果"API，需评估）。

### P2 — 想要但不急

7. 说话人分离：`probe_speaker_c.py` 已存在但未接线。
8. 双语字幕 / 翻译（与"全离线"定位冲突，先想清楚）。
9. 字幕样式预设（Resolve 字幕轨样式无 API，只能给模板文件）。

---

## 8. 回滚

- **Lua**：每次部署前在脚本目录留 `AsrSubs.lua.bak_v<版本>_<时间戳>`（已有 v3.0.5 起的完整链）
- **文档**：本轮改写前留 `.bak_20261030_183517`
- **历史快照**：`09_归档\05_旧开发分支_迁移前快照_20261027.zip`
- **v1.28 部署副本**：`09_归档\03_部署副本_Resolve脚本菜单\`
- **切除视频之后**：按 Python 写的还原清单（`rt\cut_pre_*.json`）点「撤销」重建

---

## 9. 环境事实（别猜，都实测过）

| 项 | 值 |
|---|---|
| 本机引擎 | `D:\AsrSubs`，venv 在 `D:\AsrSubs\runtime` |
| Python | 3.12（embeddable，安装器装的） |
| torch | 2.9.0+cu128 |
| transformers | 5.15.0.dev0（来源：`codeload.github.com/huggingface/transformers/tar.gz/c7f9c8815610d27e41a6b0b0cc9e2d3c49468d1d`，**唯一已知可用构建**） |
| 模型 | `models\Qwen3-ASR-0.6B-hf` + `Qwen3-ForcedAligner-0.6B-hf` |
| GPU | RTX 5060 Laptop（实测 RTF 0.30） |
| Resolve | Studio 21.1.1.10，非默认安装路径 |
| 安装器依赖 | PyInstaller 6.22.3、Inno Setup 6.7.3 |
| Lua 编译校验 | `D:\DaVinci Resolve\lua5.1.dll` + ctypes `luaL_loadfile` |

**注意**：`transformers` 是 dev 版，`pip install -U transformers` 会换成稳定版，
**换完大概率不能用**。别顺手升级。

---

## 10. 交接检查清单

- [ ] 读完第 0 节，知道**源码、payload、菜单目录、引擎目录四处都是 3.2.6**
- [ ] 读完第 5 节，知道粗剪一次都没在真机跑过
- [ ] 备份 `%ProgramData%\…\AsrSubs.lua` 与 `D:\AsrSubs` 再动手
- [ ] 知道源码是**单文件制**：改 `01_源码\AsrSubs.lua`，没有 `lua_src` 可改
- [ ] 改 Lua 后做过真编译校验（`luaL_loadfile`），不是只看括号
- [ ] 改完 `version.txt` 记得 `sync_version.py`（会覆盖代码里的 `local VERSION`）
- [ ] 打完包记得 `sync_payload.py --check`；`build.bat` 已内置同样三道闸
