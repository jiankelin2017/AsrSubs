---
title: 参考 · 测试体系与当前验收结果
type: reference
status: Active
version: 1.1.0
last_update: 2026-11-06
applies_to: AsrSubs 1.2.5（含 3 处渲染链路缺陷修复）
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §7 / §9.5.5 / 旧 README §9
superseded_by: —
---

# 参考 · 测试体系与当前验收结果

## 一句话摘要

离线验收用 `tools\verify_all.py` 一键跑；真机探针需 Resolve 在运行且开启外部脚本。本页给出脚本清单和 **2026-11-05 22:53 复跑**的验收真值。

## 当前有效结论（2026-11-06 出 1.2.1 前复跑）

命令：在 `01_源码\` 下执行 `py -3.12 tools\verify_all.py`。

| 结果 | 数量 | 明细 |
|---|---|---|
| **通过** | **17 项** | Python 语法（16 文件）、版本号一致（5 处）、Lua 构建（v1.2.1 / 7600 行）、Lua 编译（lupa）、Lua 语法（luaparse）、纠错表单测（12 组）、R1 粗剪校准、R2 重挂 + 落轨回收、字幕轨清单、落轨顺序 / API 优先、开始按钮前置条件、诊断文本、关窗不切页、粗剪门槛 / 撤销、迁移逻辑、payload 一致（18 文件）、**部署一致（2 处副本）** |
| **跳过** | 1 项 | 热词单测 —— 找不到样片 SRT |
| **失败** | **0 项** | 第 5 批部署后已无失败项 |
| 合计 | **18 项** | 脚本末尾输出「验收通过：18 项全部通过（含 1 项跳过）」；部署一致项输出「2 处部署副本与源码一致（2e1e5908；AsrSubs/paths.json ✓）」 |

> **结论**：2026-11-05 第 5 批已把 v1.2.0 的 `AsrSubs.lua` 手动 copy 到**两处**部署副本（`%ProgramData%\…\Utility\AsrSubs\`、`D:\AsrSubs\engine\`），四份 MD5 一致，验收 18 项全绿。
>
> ⚠ **该项的覆盖范围（必须知道）**：`check_deploy()`（`verify_all.py:385–431`）只对 `.lua` 做「归一化 `INSTALLED_ROOT` 锚点后比 MD5」，再检查 `paths.json` 字段，**不比 engine 里的任何 `.py`**。第 5 批时 `sync_dev.py` 报 5 处漂移、engine 版本 1.1.3；**第 6 批已把 6 个文件同步到 1.2.0，只读巡检现显示「engine 与 01_源码 规范文件集完全一致」**。所以「部署一致转绿」= 菜单脚本已是 1.2.0，**不等于 engine 已同步**；后者须另跑 `sync_dev.py` 确认。当时未运行安装程序、未做真机验证（安装包的静默装 / 卸已于 2026-11-06 08:29 补做，见第 6 轮）。

> ⚠ **待人工确认**：`archive\archive-dev-doc-20261105.md` §7 写「verify_all 18 项」、§9 写「16/16」、旧 README §9 写「17 项通过 + 1 项跳过」——三处互相矛盾。本页以 2026-11-05 实跑输出为准；旧数字均已作废。

## 关键变更（仅影响使用的里程碑）

- v4.0.9：Lua 编译校验从 `fengari`/`luaparse`（依赖 node_modules，长期必红）改用 **lupa** 真编译，同样能查「200 local 上限」；依赖缺失的 JS 测试改为跳过并说明原因。
- v1.1.1：新增 `test_close_page.py`（关窗不切页）。
- v1.2.0：新增 `test_can_start.py`（9 用例，按钮前置条件）。
- 2026-11-05（第 5 批）：手动 copy 部署两处 `.lua` 后复跑，18 项全部通过（含 1 跳过），失败项归零；当时 engine 的 Python 引擎版本落后一版，该差异已于第 6 批消除。
- 2026-11-05（第 6 批）：按 `sync_dev.py` 差异清单同步 engine 六个文件到 1.2.0 后复跑，`sync_dev.py` 显示完全一致、`verify_all.py` 仍 18 项全绿。检查覆盖范围不变。
- 2026-11-06（第 7 批）：新增 `tools\probe_v120_canstart_live.py`（只读真机探针，含 `--lua` 片段）；用 `fuscript.exe` 取得 ① 的对照组证据；见下文「v1.2.0 三项真机验证清单」。
- 2026-11-06（第 8 批，v1.2.1）：真机五轮把「探测超时 → 转渲染导出」跑通，沿路修掉三处历史缺陷并修正页面还原顺序；出 1.2.1 前复跑，仍 18 项全通过（含 1 项跳过），部署一致 MD5 `2e1e5908`。
- 详见 `..\CHANGELOG.md`。

## 操作 / 参考细节

### 离线测试（无需 DaVinci）

| 脚本 | 覆盖 | 运行 |
|---|---|---|
| `tools\verify_all.py` | 一键验收（18 项，见上） | `py -3.12 tools\verify_all.py` |
| `sync_dev.py`（项目根，无参） | **只读**巡检 engine ↔ 源码的 py/txt 漂移；`verify_all.py` 不覆盖这部分 | `py -3.12 sync_dev.py` |
| `tools\test_r1_offset.py` | R1 偏移校准（7 用例） | `py -3.12 tools\test_r1_offset.py` |
| `tools\test_r2_relang.py` | R2 relang 闭环 + v4.0.7 三项 + v4.0.8 缺陷 4（16 用例） | `py -3.12 tools\test_r2_relang.py` |
| `tools\test_land_order.py` | 落轨顺序铁律 + SetName 直改护栏（7 用例） | `py -3.12 tools\test_land_order.py` |
| `tools\test_subtrack_info.py` | 字幕轨清单（7 用例，需 `pip install lupa`） | `py -3.12 tools\test_subtrack_info.py` |
| `tools\test_can_start.py` | 开始按钮前置条件（9 用例，需 lupa） | `py -3.12 tools\test_can_start.py` |
| `tools\test_close_page.py` | 关窗不切页（5 断言） | `py -3.12 tools\test_close_page.py` |
| `tools\cleanup_empty_sub_tracks.py` | 空字幕轨体检 / 清理（默认只读） | `py -3.12 tools\cleanup_empty_sub_tracks.py` |
| `python -m py_compile <file>` | 语法编译 | 逐文件 |

`cleanup_empty_sub_tracks.py` 三种用法：无参 = **只读体检**（列出每条字幕轨条目数 / 名称 / 启用态）；`--delete N` = 删除第 N 条**空**轨（非空轨一律拒绝）；`--delete-empty` = 删除所有空轨（从大到小删，避免索引位移）。

### Lua 编译校验（推荐 lupa，无需 node 生态）

```bat
py -3.12 -c "import io,lupa; lupa.LuaRuntime().compile(io.open('AsrSubs.lua',encoding='utf-8').read()); print('OK')"
```

`check_lua.js` / `check_lua_load.js` / `test_diag_text.js` 需 `node_modules`；本次实跑中 luaparse 项通过，说明当前环境可用。

### 真机探针（需 DaVinci 在运行 + 开启外部脚本）

| 脚本 | 用途 |
|---|---|
| `tools\e2e_r2_live.py` | 端到端：落轨 → 改 SRT → relang → 断言删旧重挂无重影 → 清理 |
| `tools\verify_v407_live.py` | 首次落轨 → **重复落轨不得新增轨** → 失败落轨不污染边车 → relang 定位 |
| `tools\verify_v408_live.py` | 失败落轨 → **自建空轨必须被回收**（`failed_land_growth==0`）→ 空 SRT 早退 |
| `tools\probe_subtrack_api.py` | 字幕轨 API 取证 |
| `tools\probe_r15.py` / `probe_r2_verify.py` | 只读探测 API 面 / 验证 R1·R2 依赖的字幕轨读取 |
| `tools\check_live_readonly.py` | 只读巡检：连接 / 工程 / 时间线 / 帧率 / `SetTrackEnable` 可调用性 / 部署 MD5，**不碰时间线** |
| `tools\probe_v120_canstart_live.py` | v1.2.0 ① 专用：Python 侧逐轨记录 `GetItemListInTrack` 返回类型；`--lua` 输出贴进 Resolve 控制台 / 交给 `fuscript.exe` 执行的 Lua 片段，取 Lua 侧 `type()` / `#` 真值。全程只读 |

**测试卫生**：`verify_v407_live` / `verify_v408_live` 原本「只增不减」（不调 `DeleteTrack`），连跑会堆空轨；现改为「结束时还原到进入时的轨数」——只删本次新增且为空的轨，进入时就存在的轨一律不动。

真机连不上时 `scriptapp("Resolve")` 返回 `None`（不是 bug，是 DaVinci 没开 / 外部脚本未启用）。需在 Resolve：工作区 → 控制台 → 勾选「外部脚本使用」= 本地 / 网络。

### v1.2.0 三项真机验证清单（2026-11-06 第 7 批）

**红线**：全程只在**当前已打开的时间线**上原地操作，**绝不新建时间线**。工程里若没有现成的 A / B 场景，就照实填「未测」，不要为了验证去造时间线。

#### ① `GetItemListInTrack` 返回类型 —— 对照组 C 已实测

证据取自 2026-11-06 Resolve 21.1 正在运行（外部脚本可用、`fuscript.exe` 可连）时的两次**只读**探测，输出原文：

```text
$ py -3.12 tools\probe_v120_canstart_live.py
连接 Resolve: OK
当前时间线: Timeline 1
  video    [1] type=list len=1
  audio    [1] type=list len=1
场景判定: C 正常有片段（对照组，按钮应可点）
按 CanStart 判据，按钮预期: 可点
※ Python API 面只作旁证；① 的直接证据请用 --lua 取 Lua 侧 type()/#

$ py -3.12 tools\probe_v120_canstart_live.py --lua > %TEMP%\_asrsubs_canstart_probe.lua
$ "D:\DaVinci Resolve\fuscript.exe" %TEMP%\_asrsubs_canstart_probe.lua
[probe] timeline=Timeline 1
[probe] video[1] type=table len_ok=true len=1
[probe] audio[1] type=table len_ok=true len=1
[probe] DONE
```

**可验证事实**：在**有片段**的对照组，Lua 侧 `GetItemListInTrack` 返回 `table`、`#` 可取（未抛 POBJECT），`App:CanStart()` 的「读不出来就不拦」分支不触发，按钮预期可点；Python 侧返回 `list`，只作旁证。
**未覆盖**：A 空时间线、B 只有字幕轨。这两场景是否返回空表、还是 `nil` / 拿不到 `#` 的代理对象，本次**没有实测**；`AsrSubs.lua:4891–4895` 注释里「代理对象取 `#` 抛 POBJECT」是历史记录，不是本次证据。

| 场景 | 前置（必须已有，不得新建） | 操作 | 预期 | 实测结果（回填） |
|---|---|---|---|---|
| A 空时间线 | 工程里现成的、音视频轨都无片段的时间线 | 打开插件窗口看「开始生成」 | 变灰；`App.S.startWhy` =「时间线上没有片段」 | 未测 |
| B 只有字幕轨 | 工程里现成的、仅字幕轨有条目的时间线 | 同上 | 变灰（`CanStart` 只看 `audio` / `video`） | 未测 |
| C 有片段（对照组） | 当前 `Timeline 1` | 同上 + 探针 | 可点；Lua `type=table len_ok=true len=1` | **已实测，见上** |

#### ② 换时间线后 3 秒内按钮变灰 / 变亮

代码依据：`AsrSubs.lua:3434–3441` 非忙碌相位每 3 秒 `RefreshStartBtn()`；`:4858–4898` `App:CanStart()`；`:4901–4908` 把原因写进 `App.S.startWhy`。

| 步骤 | 操作 | 预期 | 回填 |
|---|---|---|---|
| 1 | 在**已有的**有片段时间线上打开插件 | 「开始生成」可点 | **通过**（2026-11-06 07:26 目视；插件不记录按钮状态，无日志佐证） |
| 2 | 切到**已有的**空时间线（不新建） | ≤ 3 秒内变灰 | ☐ 通过 ☐ 不通过 |
| 3 | 切回有片段时间线 | ≤ 3 秒内变亮 | ☐ 通过 ☐ 不通过 |
| 4 | 在当前时间线上把片段删光（原地，不新建） | ≤ 3 秒内变灰 | ☐ 通过 ☐ 不通过 |

日志关键字：`App.S.startWhy` 的四种取值「任务进行中」「环境自检未通过」「没有打开时间线」「时间线上没有片段」；错误日志为 `LOG_DIR\AsrSubs_error.log`（`:546`），窗口日志走 `self:Log(...)`。按钮不变化时先查 `BUSY_PHASES[App.S.phase]` —— 忙碌相位下看门狗不跑（`:3438`），这是设计行为，不是缺陷。

#### ③ `audio_trim` 复位后组合路径不再错位

代码依据：`:2898` 每轮开始 `App.S.trimFrom, App.S.trimDur = 0, 0`；`:3086–3099` 探测成功才回填；`:3832–3838` 探测超时转渲染；`:4669–4672` 仅当 `trimDur > 0.2` 才把 `audio_trim` 写进 `args.json`。

| 步骤 | 操作 | 预期 | 回填 |
|---|---|---|---|
| 1 | 在**已有的**剪过片段的时间线上跑一轮识别（产生 `trimDur > 0`） | 字幕与本轮视频对齐 | **通过，但走的是快速路径**：2026-11-06 07:26 轮写入 `audio_trim: [0.000, 202.933]`，与素材 202.93 秒一致，172 条字幕全部上轨 |
| 2 | 同一时间线，让本轮探测超时（20 秒）走渲染导出 | `args.json` 不出现上一轮的 `audio_trim` 残留 | **通过**：2026-11-06 07:53 第 4 轮真机，日志 `探测超时（20 秒），转渲染`，`args.json` 全文无 `audio_trim`（见下节） |
| 3 | 检查生成 SRT 的起始时间 | 不与上轮掐头偏移错位 | **未测**：该轮为取证在 `args.json` 落盘后立即停引擎，字幕未落轨，无 SRT 可比对 |

日志关键字：「探测超时（20 秒），转渲染」「读取时间线超时，走渲染导出」（`:3837–3838`）、「快速路径：直接使用源文件，跳过渲染」（`:3108`）。离线可跑 `py -3.12 tools\test_audio_trim.py` 覆盖纯逻辑，**但它不是真机验证**。

#### 2026-11-06 07:26 首次真机轮次（记录）

工程 `新建项目 1` / `Timeline 1`（单片段、未裁剪、202.93 秒），插件实跑一轮，日志原文要点：

```text
2026-11-06 07:26:11  探测已下发（异步）
2026-11-06 07:26:12  [log] 单片段且未裁剪 → 可直接识别，无需渲染
2026-11-06 07:26:12  [log] 只取素材第 0.00 秒起的 202.93 秒直接识别（跳过渲染）
2026-11-06 07:26:12  [log] 快速路径：直接使用源文件，跳过渲染
2026-11-06 07:27:27  [log] 识别完成：172 条字幕
2026-11-06 07:28:04  [log] 正在把字幕挂到时间线字幕轨…
2026-11-06 07:28:07  Close ExitLoop ok=true err=nil
```

回读 `D:\AsrSubs\engine\rt\args.json`（07:26:12 写入）得 `"audio_trim": [0.000, 202.933]`；时间线复核为 `subtitle 1 轨 / 172 条`，与识别条数一致。

**这次能证明什么、不能证明什么**

- 能证明：快速路径下 `audio_trim` 与本轮素材时长一致（202.93 秒），不是上一轮遗留值；落轨链路正常，172 条全部上轨。
- **不能证明**：本轮走的是 `OnProbe` 成功分支，`:3086` 会再清一次零，因此 `audio_trim` 正确与 `:2898` 是否生效**无关**。③ 真正要覆盖的是「探测超时」分支——`:3832–3838` 直接 `StartRender()` / `StartPython()`，**绕过 `:3086`**，那段路只有 `:2898` 兜底。

**代码级证据（非真机）**：`:2898` 的 `App.S.trimFrom, App.S.trimDur = 0, 0` 位于 `Tick` 每轮入口、在任何分支判断之前无条件执行；`:3086` 是 `OnProbe` 内的第二次兜底零点。因此「上轮掐头 → 本轮超时转渲染」的残留通道在静态上已被 `:2898` 封堵。**但 20 秒探测超时在不动代码或配置的前提下无法稳定构造，该分支至今没有真机复现证据。**

#### 2026-11-06 07:53 第 4 轮：③ 目标分支真机通过

前三次都没走到目标分支：第 1 轮进了快速路径；第 2、3 轮虽然进了「探测超时转渲染」，却接连撞上两处历史缺陷（见下一节）。第 4 轮全部打通，日志原文要点：

```text
2026-11-06 07:52:46  探测已下发（异步）
2026-11-06 07:53:07  探测超时（20 秒），转渲染
2026-11-06 07:53:07  ImportRenderPreset ok=true 返回=true
2026-11-06 07:53:07  作业属性：qwen3_audio_20261105-235307.wav | V=false A=true | 视频=Wave/ | 音频=lpcm
2026-11-06 07:53:08  StartRendering = true
2026-11-06 07:53:11    [log] 音频导出完成
```

回读 `args.json`（07:53:12 写入）：**全文没有 `audio_trim` 字段**，`audio` 指向 `qwen3_audio_20261105-235307.wav`，该文件 38,963,812 字节，等于 202.93 秒 × 48kHz × 16bit × 2 声道。

**判定：③ 通过。** 该轮从「探测超时」出口直接进 `StartRender()`，绕过了 `OnProbe` 内的第二次清零；`args.json` 里没有出现上一轮快速路径留下的 `[0.000, 202.933]`，说明每轮入口的无条件归零在这条路径上确实生效——这正是 ③ 要覆盖的那段路。

**时间线零改动**：取证监视器在 `args.json` 落盘后立即停掉引擎 worker，落轨未发生。复核时间线仍为 `video 1 轨 1 条 / audio 1 轨 1 条 / subtitle 0 轨`，帧范围 0–6088，与渲染前一致。

**构造方法（可还原，非改代码）**：把 `D:\AsrSubs\engine\timeline_source.py` 临时替换成「只睡眠 40 秒、不产出任何文件」的桩，插件等 20 秒必然超时转渲染；原文件存 `.verifybak`（MD5 `ed4f2117…5bb5`），取证后立即覆盖还原并核对 MD5。`StartProbe` 每轮开头本来就会删 `src.lua` / `src_path.txt`，所以不存在旧文件顶替。

#### 本轮修掉的三处历史缺陷（均非 v1.2.0 引入）

| # | 现象 | 真因 | 影响面 |
|---|---|---|---|
| 1 | `找不到 render_to_wav.xml（）`（括号里是空串） | 候选表首元素写成 `_scriptDir() ~= "" and (...) or nil`，实际求值为 `nil`；Lua 的 `ipairs` 遇到 `[1]=nil` 立即停止，后两个**真实存在**的候选从未被检查 | 所有走渲染导出的场景在这台机器上必失败 |
| 2 | `无法设置音频导出格式（当前：unknown / ）` | `ExportVideo=false` 的音频-only 模式下，`GetCurrentRenderFormatAndCodec()` 恒返回 `unknown`，`SetCurrentRenderFormatAndCodec()` 恒返回 `false`——这两个接口读写的是**视频**格式 | 校验永远不可能通过，即使建出的作业属性完全正确（实测 `Wave / lpcm / V=false`） |
| 3 | `未生成音频文件（时间线可能没有音频轨）` | `bmd.readdir(dir)` 少尾部 `\*`：裸目录名被当成 pattern 在**父目录**里匹配，只返回目录自身；条目本身是 table（文件名在 `.Name`），旧代码 `tostring(条目)` 得到 `table: 0x…` | 渲染成功产出 WAV 也判失败；`rt` 目录里的旧音频同样从不清理（同一函数被清理逻辑复用） |

三处都已在 `01_源码\AsrSubs.lua` 修正：缺陷 1 改候选表逐条追加（不留 nil 空洞）；缺陷 2 把判据挪到建作业**之后**读作业属性（校验输出以 `.wav` 结尾且 `IsExportVideo=false`），交付页格式探测只记日志；缺陷 3 改 `bmd.readdir(base .. "\\*")` 并读 `ent.Name`、跳过 `ent.IsDir`。修完部署到两处运行副本 + payload，四方 MD5 一致（`188bf428…`）。

#### 第 5 轮（2026-11-06 07:58）：页面还原与全链路跑通

目的：验证「调换 `CleanupRender` / `ReturnToPrevPage` 顺序」后，渲染结束界面能否自己回到原页面。手段与第 3 / 4 轮相同（把 `timeline_source.py` 临时换成只睡眠的桩，制造 20 秒探测超时），跑完立即还原（MD5 `ed4f2117…`）。

| 时间 | 日志 | 判定 |
|---|---|---|
| 07:57:37 | `点击开始` → `任务已下发 id=2` | 用户点击 |
| 07:57:58 | `探测超时（20 秒），转渲染` | 目标分支 |
| 07:57:59 | `ImportRenderPreset ok=true` / `作业属性：…235759.wav \| V=false A=true \| 视频=Wave/ \| 音频=lpcm` | 预设与作业属性均正确 |
| 07:58:03 | `CleanupRender：快照=预设 AsrSubsBackup-20261105-235759-7289；还原前格式=unknown /` | 快照走**预设**分支；还原前读数 `unknown` 是音频-only 模式的正常表现 |
| 07:58:03 | `CleanupRender：还原后格式=mov /` | 格式已切回本轮的原始读数（与本轮开始前一致） |
| 07:58:53 | `识别完成：170 条字幕` / `字幕文件：…\Timeline 1.srt` | 引擎跑通 |
| 07:58:55 | `已导入媒体池：Timeline 1` / `要让字幕上时间线，点「适配字幕」` | 落轨为**手动**触发，未自动改时间线 |
| 结束态 | `GetCurrentPage() = edit` | ✅ **页面已回到编辑页**（第 4 轮同流程结束在 `deliver`） |

**结论**：顺序调换生效，页面还原通过。本轮结束后时间线仍是 `Timeline 1` 0–6088 帧、`subtitle` 0 轨，`qwen3_audio_*.wav` 测试产物与临时桩均已清理。

**本轮顺带确认的两件事**：

- `args.json` 仍无 `audio_trim` 字段 → ③ 的结论在本轮复现（不是一次性偶然）。
- 「清理旧音频」随 `listFiles` 修复一起生效：起跑前 `rt\` 里的旧 WAV 被清掉，目录内只剩本轮产物。

**仍待人工确认**：交付页格式读数为 `mov /`（codec 为空）。音频-only 模式下该接口读数不可靠，是否与用户原设置一致无法从 API 判断。

**已知遗留（本轮未修，需单独确认）**：

- ~~渲染结束后界面停在交付页，`ReturnToPrevPage()` 未生效~~ → **已修并验证**（2026-11-06 07:58，见下文「第 5 轮」）：9 处调用原本是「先 `ReturnToPrevPage()` 再 `CleanupRender()`」，而 `CleanupRender` 内的 `DeleteRenderPreset` / `LoadRenderPreset` 会把界面踢回交付页；调换顺序后本轮结束页为 `edit`。
- 交付页格式读数为 `mov /`（codec 为空）。音频模式下这个读数本就不可靠，是否与用户原设置一致**待人工确认**。
- ② 的 2–4 步、A / B 场景仍未验证：当前工程只有 `Timeline 1` 一条时间线，无现成空时间线，红线禁止新建。

#### 第 6 轮（2026-11-06 08:29）：安装包静默装 / 卸闭环

目的：验证 **1.2.1 安装包的打包链**（PyInstaller → ISCC → 解包落地 → 卸载回收），不是验证插件功能。做法：把包静默装到临时目录 `D:\_asrsubs_it_1.2.1\AsrSubs`，校验落地物，再静默卸载，全过程不碰 `D:\AsrSubs`。

命令（进程未提权，安装器 `PrivilegesRequired=admin`，故走 `runas` 拉起；本机 `ConsentPromptBehaviorAdmin=0`，提权不弹窗）：

```
AsrSubs_v1.2.1_安装器.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART ^
  /DIR="D:\_asrsubs_it_1.2.1\AsrSubs" /LOG="…\install.log"
unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="…\uninstall.log"
```

| 环节 | 实测 | 判定 |
|---|---|---|
| 安装退出码 | `0`，耗时 2.6 秒 | ✅ |
| 落地文件 | `_setup\` 1006 个 / 31,751,046 字节、`docs\使用说明.txt` 43,666 字节、`unins000.exe` + `.dat` | ✅ |
| 打包同源性 | 包内 `_setup\安装核心.exe` MD5 `edd64d00a2426207f27f6c4891277652`，与本地 `dist\安装核心\安装核心.exe` **完全一致** | ✅ 打包链无损 |
| `[Run]` 自动部署 | 日志无 `Executing` 行 → `skipifsilent` 生效，**未**调起安装核心、**未**下载 3.4~6 GB | ✅ 预期行为 |
| 目录完整性 | `engine\` / `runtime\` / `models\` / `data\` 均未生成 | ✅ 静默安装只铺外壳 |
| 快捷方式 | 开始菜单 3 项（使用说明 / 重新运行安装向导 / 卸载）、公共桌面 1 项 | ✅ |
| 卸载退出码 | `0`；日志 `Uninstallation process succeeded` / `Removed all? Yes`；目录 1.6 秒后消失 | ✅ |
| 卸载后回收 | 4 个快捷方式全清、注册表键全清、`%TEMP%\qwen3asr_dl` 未误删（本就不存在） | ✅ |
| **未误伤 Resolve** | `uninstall.log` 里 `Fusion` 出现 **0 次** → `[UninstallDelete]` 的三个 `NeedDeleteResolveDir*` 判为假；`Utility\AsrSubs\` 前后逐文件 MD5 一致（`AsrSubs.lua` `2e1e5908…`、`render_to_wav.xml` `91b20a41…`） | ✅ 最关键项 |
| **未误伤现有部署** | `D:\AsrSubs` 前后均为 31,465 个文件 / 7,421,519,676 字节；119 个关键文件 MD5 差异 **0** | ✅ |

**顺带发现的缺陷（已改注释，AppId 未动；不影响装 / 卸）**：注册表卸载键实际是
`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{7A3F9C4E-2B8D-4E6A-9F1C-5D8A2B7E3C46}}_is1`
—— **末尾多一个 `}`**。原因是 `AsrSubs.iss` 的 `#define MyAppId "{{…}}"`：ISPP 只把 `{{` 当转义（展开为一个 `{`），`}}` 不是转义序列、原样保留两个右花括号。历史版本写法相同，**非本次引入，也不影响升级识别**（同一字符串恒定，Inno 仍能认出同产品的旧安装）。**AppId 保持不动**（改它会让已在机器上的安装失去升级对象）；已把 `AsrSubs.iss` 那段注释改成事实描述（连带说明「按带两个 `}` 的键名去查」），并修掉 `reference-installer-pitfalls.md` 里 `{#MyAppIdGuid}_is1` 这个 `#define` 根本不存在、且少一个 `}` 的旧写法。**改注释不动二进制，1.2.1 成品 MD5 不变。**

**本轮未覆盖（必须知道）**：① **没有跑完整向导** —— 静默模式跳过 `[Run]`，所以「下载 Python + torch + 两个模型 → 铺 engine / runtime / models → 写 Resolve 脚本目录」这一段一次都没走；② **覆盖升级未测**（需要先有一份同 AppId 的旧安装，会动到真实目录）；③ 安装核心的 GUI 界面未打开。

**注释改动的回归验证（2026-11-06 08:35）**：把 `AsrSubs.iss` 里那段 AppId 注释改成事实描述后，用 ISCC 6.7.3 重新编译到临时目录（`/O` 参数，不覆盖 `dist`），产物 **12,504,407 字节、MD5 `13D61D1D35F80722A98319CB058EC25E`** —— 与 `04_安装包\AsrSubs_v1.2.1_安装器.exe` **逐字节相同**。两个结论：① 注释不进入二进制，**已发布包不必替换**；② 「当前源码树 + `dist\安装核心` + `03_文档\使用说明.txt`」能原样重建出 1.2.1 包，构建**可复现**。同时编译器报出一条 `UsedUserAreasWarning`（`PrivilegesRequired=admin` 与 `{userappdata}` 并存），已记入 `reference-installer-pitfalls.md`，评估结论是「只会漏删、不会误删」，不改。

> ⚠ 截至 2026-11-06 08:30：① 对照组 C、② 步骤 1、③ 步骤 1–2 与目标分支、渲染后页面还原 **均已通过真机验证**；第 6 轮又验证了**安装包的静默装 / 卸闭环**（见上）。仍未完成的：A / B 场景与 ② 的 2–4 步（工程内无现成空时间线，红线禁止新建，不得为了凑数据去建一条）；**安装核心的下载部署与覆盖升级**（静默模式绕过了这一段）。`..\CHANGELOG.md` 1.2.0 节的「待人工确认」可据此收窄，但**不得**整体改成「三项全部通过」；`..\..\README.md` / `..\INDEX.md` 里「未运行安装程序」的字样可以改成「已做静默装 / 卸验证，未跑下载部署与覆盖升级」，但**不得**写成「安装已验证」。

#### 第 7 轮（2026-11-06 23:41）：v1.2.4 安装包静默装 / 卸闭环

目的：复验 **1.2.4 重新出包后的打包链**（PyInstaller → ISCC → 解包落地 → 卸载回收），确认 1.2.2 / 1.2.3 / 1.2.4 三版累积改动没破坏安装器。做法沿用第 6 轮：静默装到隔离目录 `D:\_asrsubs_it_1.2.4\AsrSubs`，校验落地物后静默卸载，全过程不碰 `D:\AsrSubs`。

命令（用 Python 原生 `subprocess` 调用，绕开 Git Bash 的 MSYS 路径转换；当前进程未提权，安装器 `PrivilegesRequired=admin` 自行提权，本机 `ConsentPromptBehaviorAdmin=0` 不弹窗）：

```
AsrSubs_v1.2.4_安装器.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART ^
  /DIR="D:\_asrsubs_it_1.2.4\AsrSubs" /LOG="…\install.log"
unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="…\uninstall.log"
```

| 环节 | 实测 | 判定 |
|---|---|---|
| 安装 / 卸载退出码 | `0` / `0`，耗时 2.7 秒 / 0.6 秒 | ✅ |
| 落地文件 | `_setup\` 1006 个 / 31,758,855 字节、`docs\使用说明.txt` 43,666 字节、`unins000.exe` + `.dat` | ✅ |
| 打包同源性 | 包内 `_setup\安装核心.exe` MD5 `37214d6b6ef1828dedc6f97099310923`，与本地 `dist\安装核心\安装核心.exe` 完全一致；`_internal\payload\AsrSubs.lua` 为 1.2.4 且与 `01_源码` 同源（`e4e529a1…`） | ✅ |
| `[Run]` 自动部署 | 安装日志 `Executing` / `--auto` 行数 0 → `skipifsilent` 生效，**未**下载 3.4~6 GB | ✅ 预期行为 |
| 目录完整性 | `engine\` / `runtime\` / `models\` / `data\` 均未生成 | ✅ |
| 快捷方式 / 注册表 | 开始菜单 3 项 + 公共桌面 1 项；`DisplayVersion=1.2.4`，`InstallLocation` 指向隔离目录 | ✅ |
| 卸载回收 | 日志 `Uninstallation process succeeded` / `Removed all? Yes`；`AsrSubs\` 子目录消失，隔离目录与日志已清理（日志留证在系统 Temp） | ✅ |
| **未误伤 Resolve** | `uninstall.log` 里 `Fusion` 出现 **0 次**；`Utility\AsrSubs\` 两个文件前后 MD5 一致 | ✅ 最关键项 |
| **未误伤现有部署** | 装前 / 装后全量基线比对（engine 110 文件、顶层文件、三个菜单目录、开始菜单、注册表卸载键、Documents 备份目录）**0 处差异** | ✅ |
| 落地物脱敏 | 明文扫本机账户名、外部项目名、业务字幕名与真实录制路径，命中 0 处 | ✅ |

**本轮未覆盖**：① 完整向导（下载 Python + torch + 两个模型 → 铺 `engine` / `runtime` / `models` → 写 Resolve 脚本目录）仍未走；② 覆盖升级未测；③ **DaVinci Resolve 真机渲染与落轨未测** —— 当前 Resolve 未运行，注册表仅有 `DaVinci Resolve 21.1.20010` 的 MSI 卸载项，`C:\Program Files\Blackmagic Design\DaVinci Resolve\` 只剩空的 `Support` 目录、无 `Resolve.exe` 主程序，GUI 交互无法由自动化代跑，见下节手动清单。

#### v1.2.4 真机手动验证清单（待人工执行）

在已装好引擎的 `D:\AsrSubs` 上启动 DaVinci Resolve，从「脚本 → AsrSubs」打开插件（版本应显示 1.2.4），按下列顺序跑并回填本表：

| # | 步骤 | 预期 | 实测 |
|---|---|---|---|
| 1 | 打开插件看版本，什么都不做直接关窗 | 显示 `1.2.4`；关窗不切到「交付」页 | 待填 |
| 2 | 单片段时间线，直接识别并生成字幕 | 字幕落到**当前（原）时间线**，不新建时间线 | 待填 |
| 3 | 多片段 / 探测超时场景走「转渲染导出」 | 生成 WAV、识别完成，界面回到原页面，不残留交付页 | 待填 |
| 4 | 含中文与空格的 output 路径下 SRT 落轨 | 字幕正常挂上，无路径解析失败 | 待填 |
| 5 | 导出诊断包后人工查看内容 | 无本机账户路径、无业务字幕文本、无自定义路径 | 待填 |

红线沿用：全程只在**当前已打开的时间线**上原地操作，**绝不新建时间线**；工程里没有现成场景就照实填「未测」，不要为了凑数据造时间线。第 6 轮已实测的 ① 对照组、②-1、③ 目标分支与页面还原是 **1.2.1** 的结论，不能直接当作 1.2.4 的真机结论。

## 失效内容说明

- §9.5.5「整理后验收 15 项全部通过」、§9「verify_all 16/16」、旧 README §9「17 项通过 + 1 跳过」都是**各自时点的快照**，不得作为当前项数依据。
- 「Lua 编译永远失败 / JS 测试永远失败」是 v4.0.9 之前的状态，本次实跑两项均通过，该结论已失效。
- 「18 项 = 16 通过 + 1 跳过 + **1 失败（部署一致）**」是 2026-11-05 早间部署前的快照；22:53 复跑后失败项归零，旧数字已作废。
