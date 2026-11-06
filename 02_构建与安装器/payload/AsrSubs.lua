--[[
    AsrSubs — DaVinci Resolve 集成脚本
    ==========================================
    基于 Qwen3-ASR-0.6B 的本地 AI 字幕生成

    全异步架构（不阻塞界面）：
      1. AddRenderJob + StartRendering 导出时间线音频
      2. 轮询渲染状态，完成后启动 Python 引擎
      3. 轮询进度文件，完成后导入媒体池

    UI API 模式：
      resolve:Fusion().UIManager
        → bmd.UIDispatcher(ui)
        → dispatcher:AddWindow{...}
        → dispatcher.On.Timeout + RunLoop
]]

-- ══════════════════════════════════════════════════════════════════
-- 版本号 —— 全项目唯一来源是仓库根目录的 version.txt
--   · 构建时由 tools/build_lua.py 把 3.0.5 替换成实际版本
--   · 界面右下角、关于弹窗、安装包属性全部读这一处
--   ★ 不要再往代码里写死 "v1.28" 这种字面量：v1.28 时代版本号散落在
--     Lua 的关于文本、Inno 的 .iss、打包记录三处，改版本必漏一处，
--     结果界面显示 v1.28、安装包属性却是 1.26（备份记录里真有这事故）。
-- ══════════════════════════════════════════════════════════════════
local VERSION = "1.2.5"

-- ══════════════════════════════════════════════════════════════════
-- 路径解析
-- ----------------------------------------------------------------
-- ★ 本工具跟 DaVinci 装在哪个盘【没有关系】：
--   它自己找的是引擎目录（engine）、运行环境（runtime）、模型目录，
--   这三处都是独立于 DaVinci 安装位置的。
--   唯一要求：把本脚本文件放进 DaVinci 的 Scripts\Utility\ 里，
--   菜单里才会出现。
--
-- ★ 工具【自己】可能被装到别的盘（默认 D:\AsrSubs）。
--   所以下面按盘符顺序探测，第一个找到引擎的盘就用它；
--   全都找不到才退回默认盘（不会因为探测失败就跑不起来）。
--   探测用 resolve_bridge.py 当路标 —— 它是 ASCII 路径，Lua 读得动；
--   中文目录（模型目录）Lua 在 Windows 上打不开，探不了，只能按
--   同级的兄弟目录推导。
-- ══════════════════════════════════════════════════════════════════

-- 文件存在性探测（此处不能复用下面的 exists()，它定义在后面）
--   bmd.fileexists 优先，io.open 兜底 —— 顶层执行时 bmd 有可能还没注入。
local function _fileOk(p)
    if type(bmd) == "table" and type(bmd.fileexists) == "function" then
        local ok, r = pcall(bmd.fileexists, p)
        if ok and r == true then return true end
    end
    if io ~= nil then
        local f = io.open(p, "r")
        if f then f:close() return true end
    end
    return false
end

-- 纯 ASCII 路径判定：含任何非 ASCII 字节（中文/日文/emoji…）返回 false。
--   ★ 为什么必须有它：Lua 的 io.open / os.execute 在 Windows 上走 ANSI 接口，
--     中文路径会变成乱码（"AsrSubs瀛楀箷宸ュ叿"）而打不开。而安装器允许用户
--     自选安装位置，完全可能选到 D:\软件\AsrSubs 这种目录 —— 那样整个插件
--     会以"任务超时"这种和真因无关的方式静默坏掉，极难排查。
--     所以：中文路径一律弃用，退回去按盘符探测（候选 \AsrSubs\engine 全是 ASCII）。
local function _isAsciiPath(s)
    if type(s) ~= "string" or s == "" then return false end
    return s:find("[\128-\255]") == nil
end

-- 盘符表：C: ~ Z:
-- ★ 2.x 只探到 H:，装在 I: 之后的机器一旦目录被搬走就再也找不回来。
--   现在全量遍历，代价只是最多 24 次文件存在性检查（只在锚点失效时才跑）。
local function _drives()
    local out = {}
    for i = 67, 90 do                      -- 'C'..'Z'
        out[#out + 1] = string.char(i) .. ":"
    end
    return out
end

-- 按盘符找带路标的目录，返回第一个命中的完整路径；都没命中返回 nil
local function _probeRoot(tail, marker)
    for _, d in ipairs(_drives()) do
        local root = d .. tail
        if _fileOk(root .. "\\" .. marker) then return root end
    end
    return nil
end

-- ★★ 布局：<根>\engine（唯一布局）
local NEW_TAIL = "\\AsrSubs\\engine"

-- 引擎目录（纯 ASCII，可探测）
-- ★ 安装器会把下面这行的路径替换成真实安装位置
--   （默认 D:\AsrSubs\engine），所以装到别的盘也能直接用。
--   若这个目录后来被删掉/搬走了，会自动回退到按盘符探测。
-- ★ 中文安装路径直接弃用（见 _isAsciiPath 的说明），不让它带着乱码往下跑。
-- ★★ 这一行的 `--<<INSTALL_ROOT>>` 标记是安装器的锚点，务必保留：
--     installer.py 靠它做正则替换，标记一旦丢失，装到 D:\ 以外就会
--     沿用这里写死的路径，插件表现为"界面能开、一跑就报超时"。
--     （2026-11-01 用 sync_dev --deploy 回灌时丢过一次，别再删。）
local INSTALLED_ROOT = "D:\\AsrSubs\\engine"  --<<INSTALL_ROOT>>
local ASCII_ROOT = INSTALLED_ROOT
if not _isAsciiPath(ASCII_ROOT)
   or not _fileOk(ASCII_ROOT .. "\\resolve_bridge.py") then
    ASCII_ROOT = _probeRoot(NEW_TAIL, "resolve_bridge.py")
                 or "D:\\AsrSubs\\engine"
end

-- 布局：当前唯一布局为 engine（历史布局已不再兼容）。
-- ★ 这个变量决定 data / models / runtime 怎么拼，下面两个分片都要用。
local LAYOUT = "new"

-- 共同的父目录（默认 D:\AsrSubs）—— 用来推导其它目录
local BASE_DIR = ASCII_ROOT:match("^(.*)\\[^\\]+$") or "D:\\AsrSubs"

-- ── 安装器生成的 paths.lua（可选，存在即权威）──────────────────────
--   ★ 有它就以它为准：以后改目录名只改安装器一处，Lua 不再靠猜。
--   ★ 没有它（开发机 / 老部署）就按 LAYOUT 推导，行为与 2.x 一致。
--   ★ 用 loadfile 读，不用 io.open —— 后者在 Windows 上打不开中文路径。
local PATHS = nil
do
    local f = ASCII_ROOT .. "\\paths.lua"
    if type(loadfile) == "function" and _fileOk(f) then
        local pok, t = pcall(loadfile, f)
        if pok and type(t) == "table" then PATHS = t end
    end
end
if PATHS and type(PATHS.root) == "string" and PATHS.root ~= "" then
    BASE_DIR = PATHS.root
end

-- ── 自带 WAV 渲染预设（render_to_wav.xml）──────────────────────
-- ★ 为什么需要它：Resolve 内置的 'Audio Only' 预设在非英文版上名字是
--   本地化的（中文版叫"仅音频"），LoadRenderPreset 按显示名找 → 必失败；
--   而格式设不上时渲染作业会"成功"结束却零产出（实测：报一句
--   "未生成音频文件"，和真因八竿子打不着）。
--   官方 DaVinci ASR 的做法是 ImportRenderPreset 导入自带 XML，
--   格式写死在文件里，不依赖任何本地化名字 —— 照做。
-- 按顺序找：脚本所在目录（安装目录）→ 引擎目录 → 安装目录的常见位置。
local function _scriptDir()
    local ok, info = pcall(debug.getinfo, 1, "S")
    if ok and type(info) == "table" and type(info.source) == "string" then
        local s = info.source
        if s:sub(1, 1) == "@" then s = s:sub(2) end
        local d = s:match("^(.*)[\\/][^\\/]*$")
        if d then return d end
    end
    return ""
end

local RENDER_PRESET_XML = ""
do
    -- ★★ 候选表不能留 nil 空洞！ipairs 遇到第一个 nil 就停止遍历。
    --   原来写成 `_scriptDir() ~= "" and (...) or nil`，而菜单加载的脚本里
    --   _scriptDir() 通常返回空串 → cands[1] = nil → 后面两个【真实存在】的
    --   候选路径一个都不会被检查，render_to_wav.xml 明明在安装目录里却永远
    --   "找不到"，渲染路径必然卡在"无法设置音频导出格式"。改为逐条追加。
    --   （2026-11-06 真机验证 ③-2 时实测复现，属历史遗留缺陷）
    local cands = {}
    local sd = _scriptDir()
    if sd ~= "" then cands[#cands + 1] = sd .. "\\render_to_wav.xml" end
    cands[#cands + 1] = ASCII_ROOT .. "\\render_to_wav.xml"
    cands[#cands + 1] = BASE_DIR .. "\\AsrSubs\\render_to_wav.xml"   -- 历史布局兼容
    for _, p in ipairs(cands) do
        if _fileOk(p) then RENDER_PRESET_XML = p break end
    end
    -- 注意：此刻 ELog 还没定义（在 300 多行），这里不能调它
end

-- ══════════════════════════════════════════════════════════════
-- 运行环境（Python 解释器）解析
--
-- ★★ 独立性：绝不能被别的插件的安装/卸载带着一起坏。
--    早期版本曾共享过 DaVinci ASR 插件的 venv —— 对方一卸，
--    本插件所有按钮都失效，而且报的是"任务超时"这种误导信息。
--    现在运行环境完全自持：就在引擎目录下（runtime）。
--
-- 解析顺序（第一个存在的就用）：
--   1) rt\settings.lua 里用户手动指定的（最高优先，永久记住）
--   2) 本插件自带的 venv：引擎目录 runtime（当前默认）；
--      全新安装则由安装器装在 安装位置\AsrSubs\.venv
--   3) 系统 Python 3.8~3.13（最后保障）
-- 全都不存在时 PYTHON = ""，各处给出明确错误而不是静默失败。
-- worker.py 里有一份同顺序的探测，两边保持一致。
-- ══════════════════════════════════════════════════════════════
local SETTINGS_FILE = ASCII_ROOT .. "\\rt\\settings.lua"

-- 从 settings.lua 里读 python 路径。
-- ★ 这里【不能】用后面的 loadLuaTable（定义在 300 行之后，此刻还是 nil），
--   所以用最笨的正则抠一行 —— 文件格式是我们自己写的，够用了。
-- ★ 反斜杠要归一化：我们自己写文件时用单反斜杠，但用户手改时很可能会
--   按 Lua 字符串的规矩写成 "\\legacy"（双反斜杠）。两种都要认，
--   否则用户明明填对了路径却"不生效"，极难排查。
local function readPySetting()
    if io == nil then return "" end
    local f = io.open(SETTINGS_FILE, "r")
    if not f then return "" end
    local d = f:read("*all") or ""
    f:close()
    local p = d:match('python%s*=%s*"([^"]+)"')
    if not p then p = d:match("python%s*=%s*'([^']+)'") end
    if not p then return "" end
    if not _fileOk(p) then
        local flat = p:gsub("\\\\", "\\")
        if flat ~= p and _fileOk(flat) then return flat end
    end
    return p
end

local function pyCandidates()
    local out, seen = {}, {}
    local function add(p)
        if p and p ~= "" and not seen[p] then
            seen[p] = true
            out[#out + 1] = p
        end
    end

    -- 1) 用户手动指定（直接编辑 settings.lua 里的 python = "..."）
    add(readPySetting())

    -- 2) 本插件自带的运行环境（当前默认）：
    --    3.0：<根>\runtime\Scripts\python.exe
    --    2.x：<根>\AsrSubs\.venv\Scripts\python.exe
    --    开发机：引擎目录下的 .venv
    add(ASCII_ROOT .. "\\.venv\\Scripts\\python.exe")
    if PATHS and type(PATHS.runtime) == "string" and PATHS.runtime ~= "" then
        add(PATHS.runtime .. "\\Scripts\\python.exe")
    end
    add(BASE_DIR .. "\\runtime\\Scripts\\python.exe")
    add(BASE_DIR .. "\\AsrSubs\\.venv\\Scripts\\python.exe")

    -- 3) 系统 Python：按版本从新到旧逐个试。全是 _fileOk 探测，不闪窗
    local roots = {}
    local okEnv, la = pcall(os.getenv, "LOCALAPPDATA")
    if okEnv and type(la) == "string" and la ~= "" then
        roots[#roots + 1] = la .. "\\Programs\\Python"
    end
    roots[#roots + 1] = "C:\\"
    for _, r in ipairs(roots) do
        for _, v in ipairs({ "313", "312", "311", "310", "39", "38" }) do
            add(r .. "\\Python" .. v .. "\\python.exe")
        end
    end
    return out
end

local PYTHON = ""
for _, p in ipairs(pyCandidates()) do
    if _fileOk(p) then PYTHON = p break end
end
-- pythonw 优先（启动 worker 不闪窗）。名字不是预期形状时留空，
-- 各处用 exists(PYTHONW) and PYTHONW or PYTHON 兜底
local PYTHONW = ""
if PYTHON ~= "" then
    local w = PYTHON:gsub("[pP]ython%.exe$", "pythonw.exe")
    if w ~= PYTHON then PYTHONW = w end
end

-- 数据目录 / 模型目录 / 输出目录
-- ★ 3.0 新布局：<根>\data（含 output\）、<根>\models
--   2.x 老布局：<根>\AsrSubs（含 字幕输出\ 与 models\）
-- ★ paths.lua 存在时以它为准；否则按 01_paths.lua 判定的 LAYOUT 推导。
-- ★ 这几个目录【只有 Python 会真正打开】，Lua 只做字符串拼接后传给 Python，
--   所以 data 允许带中文（比如用户把数据目录设到「文档」下）。
local TOOL_ROOT, MODELS_DIR, OUTPUT_DIR
if LAYOUT == "legacy" then
    TOOL_ROOT = BASE_DIR .. "\\AsrSubs"
    MODELS_DIR = TOOL_ROOT .. "\\models"
    OUTPUT_DIR = TOOL_ROOT .. "\\字幕输出"
else
    TOOL_ROOT = BASE_DIR .. "\\data"
    MODELS_DIR = BASE_DIR .. "\\models"
    OUTPUT_DIR = TOOL_ROOT .. "\\output"
end
if PATHS then
    if type(PATHS.data) == "string" and PATHS.data ~= "" then TOOL_ROOT = PATHS.data end
    if type(PATHS.models) == "string" and PATHS.models ~= "" then MODELS_DIR = PATHS.models end
    if type(PATHS.output) == "string" and PATHS.output ~= "" then OUTPUT_DIR = PATHS.output end
end

-- ★★ 凡是 Lua 会碰到的路径，必须全是 ASCII。
--    两条独立的坑，都会让中文路径变成 "AsrSubs瀛楀箷宸ュ叿"：
--      1) 命令行（os.execute / io.popen）按 GBK 解读 UTF-8 参数
--      2) Lua 的 io.open / exists() 在 Windows 上走 ANSI 接口
--    → 所以中间产物（源路径、状态、日志、导出的 WAV）全放 ASCII 目录。
--    中文素材路径本身没问题：它由 Python 写出、Python 读取，全程 Unicode。
--
-- ★★★ 这里【绝对不能】再写 `local ASCII_ROOT = "C:\\AsrSubs\\engine"`！
--    文件顶部（第 75 行）已经声明过一次 ASCII_ROOT 并做了盘符探测：
--        优先用安装器替换过的 INSTALLED_ROOT，失败再 _probeRoot() 按盘符找。
--    Lua 的 local 在同一 block 里可以重复声明，后一个会【遮蔽】前一个 ——
--    一旦在这里再写死一次，从这行往下所有路径变量（PROBE_SCRIPT /
--    PROBE_WORK / BRIDGE / LAND_SCRIPT / SUBS_IO / EXPORT_SCRIPT /
--    ASR_SERVER / WORKER / MODEL_MANIFEST*）就全部指回 C 盘，
--    顶部的探测彻底白做。症状：插件装到 D 盘后模型目录还能找到
--    （TOOL_ROOT 走的是探测出来的 BASE_DIR），引擎脚本却一个都找不到，
--    报的错还会是"任务超时"这种和真因无关的话。
--    曾有此 bug，已删。改动本文件时注意别再"顺手补一行"。
local PROBE_SCRIPT = ASCII_ROOT .. "\\timeline_source.py"
local PROBE_WORK = ASCII_ROOT .. "\\rt"
local BRIDGE = ASCII_ROOT .. "\\resolve_bridge.py"
-- 落轨脚本：把 SRT 挂到时间线字幕轨 + 导入媒体池（逻辑全在 Python 侧）
local LAND_SCRIPT = ASCII_ROOT .. "\\land_subtitle.py"
-- ★ v3.3.0：粗剪脚本。按「删除的字幕区间」新建一条「原名 · 粗剪」时间线，
--   原时间线一个片段都不动（随时可切回去）。字幕按新时间落到新时间线，
--   改写后的字幕另存为「原名 · 粗剪.srt」，不覆盖用户原 SRT。
--   逻辑全在 Python 侧：Lua 拿不到帧级 API，也没法安全地做跨上千片段的追加。
local CUT_SCRIPT = ASCII_ROOT .. "\\cut_timeline.py"
-- 字幕存取桥：SRT ↔ Lua 表 / JSON。
--   SRT 放在中文目录里，Lua 的 io.open 打不开中文路径，所以读写都交给它。
local SUBS_IO = ASCII_ROOT .. "\\subs_io.py"
-- 导出脚本：把 srt / txt 复制到用户指定的文件夹
local EXPORT_SCRIPT = ASCII_ROOT .. "\\export_files.py"
-- ★ v2.0.0：诊断包脚本。把版本/环境/日志/设置打成一个 zip，出问题一键交上来。
local DIAG_SCRIPT = ASCII_ROOT .. "\\diag.py"
-- ★ 常驻 ASR 服务：模型只加载一次，之后每次识别省掉 6~7 秒
--   它只是加速器，不在/崩了 resolve_bridge.py 会自动退回本地加载
local ASR_SERVER = ASCII_ROOT .. "\\asr_server.py"
-- 给常驻服务的"预热模型"文件（写本次用的模型路径，服务起来就装这个）
local SRV_PRELOAD = PROBE_WORK .. "\\asr_preload.txt"

-- ★★ 常驻后台执行器 worker.py
--   为什么需要它：见 worker.py 顶部说明。
--   一句话：os.execute 一定要经 cmd.exe，而 cmd.exe 被 Resolve 拉起时
--   会闪一个黑窗。worker 只在会话里启动一次（且用 pythonw，本身无窗），
--   之后它通过 CREATE_NO_WINDOW 启动任务，全程零窗口。
local WORKER = ASCII_ROOT .. "\\worker.py"
local CMD_FILE = PROBE_WORK .. "\\cmd.json"
local RES_FILE = PROBE_WORK .. "\\res.lua"
local WHB_FILE = PROBE_WORK .. "\\whb.lua"
-- worker 写下的"还活着的子进程 PID"。worker 不在时用它精确 taskkill，
-- 绝不退化成 taskkill /IM python.exe（那会把机器上所有 python 一起杀掉）
local PIDS_FILE = PROBE_WORK .. "\\pids.lua"

-- ★ 工作目录 = ASCII 目录。原来在中文目录里，导致 FindAudio 的
--   io.popen('dir /b ...') 和 io.open 全部失效（同一个编码坑的第三处）。
local WORK_DIR = PROBE_WORK
-- ★ PYTHON / PYTHONW 已在文件顶部「路径解析」里按盘符探测好了（可能不在 C 盘）

local LANGUAGES = {
    { "中文", "Chinese" },
    { "English", "English" },
    { "粤语", "Cantonese" },
    { "日语", "Japanese" },
    { "韩语", "Korean" },
    { "自动识别", "Auto" },
}
local MAX_CHARS_OPTS = { "16", "24", "32", "40", "50" }

-- 这些阶段表示"后台还有活儿在跑"。
-- ★ 导出按钮以前没有这道检查：识别途中点导出会把 phase 改成 exporting，
--   于是识别进度再也没人轮询，界面永远停在"识别中"。
local BUSY_PHASES = {
    probing = true, rendering = true, recognizing = true,
    landing = true, importing = true, exporting = true,
    -- 交付页就绪等待 + 格式配置（渲染前的准备相位，见 App:ConfigRender）
    render_cfg = true,
    -- 「更新字幕」之后的媒体池同步（见 App:StartLand 的 reason="sync"）
    syncing = true,
    -- ★ v3.2.6：粗剪（原地重建时间线）。这一步会动视频/音频，
    --   期间必须锁住其它按钮，否则同时在写时间线会互相踩。
    cutting = true,
}

local App = {}
App.S = {
    phase = "idle",          -- idle | rendering | recognizing | done | error
    jobId = nil,
    renderStartedAt = 0,
    logLines = {},
    win = nil,
    dispatcher = nil,
    langCode = "Chinese",
    maxChars = 24,
    -- ★ v2.0.7：prompt 换成 corrections（权重词纠错表，多行文本）
    corrections = "",
    -- ★ v2.0.9：不再是"按钮是否放出"，而是"版本号是否已标记 ⚠"
    diagShown = false,
    -- v2.0.9：诊断信息窗口（每次点版本号重建一个，见 App:ShowDiagWin）
    diagWin = nil,
    -- ★ v4.0.0：noGap 字段删除（字幕无间隙功能整体移除），识别侧 no_gap 恒 false。
    noPunct = false,          -- 句末无标点（v4.0.0 回归）
    allPunct = false,        -- 去除标点符号（行内标点也删）
    srt = WORK_DIR .. "\\subtitle.srt",
    -- ★ 状态文件和日志放【纯 ASCII】目录。
    --   Lua 要 exists()/tail() 读它们，而 io.open 走 ANSI API 打不开中文路径
    --   （实测：明明存在的文件被判定为不存在）。
    --   只有最终产出的 SRT 留在中文目录（那是给用户看的，Lua 不用读）。
    statusFile = PROBE_WORK .. "\\status.txt",
    logFile = PROBE_WORK .. "\\engine.log",
    -- 落轨（把字幕挂到时间线字幕轨）的两个中间文件
    landFile = PROBE_WORK .. "\\land.lua",
    landCfg  = PROBE_WORK .. "\\land.json",
    landStartedAt = nil,
    -- 识别完成后【自动导入媒体池】用的另一组文件
    --   （和落轨分开，避免两个流程互相读到对方的结果）
    importFile = PROBE_WORK .. "\\import.lua",
    importCfg  = PROBE_WORK .. "\\import.json",
    importStartedAt = nil,
    -- ── 粗剪：按删除的字幕区间切除时间线（v3.2.6）───────────────
    --   ★ 记录的是【时间区间】不是行号：删完之后行号全变了，时间不会。
    --   cuts = { {s=12.34, e=15.90, n=3}, ... }，单位秒，时间线时间。
    cutFile = PROBE_WORK .. "\\cut.lua",
    cutCfg  = PROBE_WORK .. "\\cut.json",
    cutStartedAt = nil,
    cuts = {},
    cutSrt = "",               -- cuts 属于哪个 SRT：换了字幕文件，旧记录自动作废
    lastCut = "",              -- 上一次切除写的还原清单（rt\cut_pre_*.json）
    -- ★ v4.0.0：引擎固定为原地增量剪辑，撤销一律原生 Ctrl+Z；
    --   原来的 cutMode / lastCutEngine / cutEngine 状态字段（区分 rebuild/inplace）已删除。
    cutPad = 0.10,             -- 切口前后各多切一点（秒），留气口
    cutMerge = 0.30,           -- 相邻两个删除区间挨得比这还近就并成一段
    -- 导出用的中间文件
    exportFile = PROBE_WORK .. "\\export.lua",
    exportCfg  = PROBE_WORK .. "\\export.json",
    exportStartedAt = nil,
    -- worker 相关
    cmdId = 0,
    workerOk = nil,
    srtBase = "",
    txt = "",
    count = 0,
    pyStartedAt = nil,
    prevPage = nil,
    -- ★ 会话标识：每次运行本脚本都换一个，worker 靠 (sess,id) 去重。
    --   没有它的时候，第二次运行脚本发出的 id=1 会被 worker 当成
    --   上一次的重复命令【静默丢弃】→ 干等超时 → 回退 os.execute 闪黑窗。
    -- ★ 必须足够唯一：worker 按 (sess, id) 去重，sess 撞了会把
    --   新命令当"上一次的重复"静默丢掉 → 干等超时 → 回退闪黑窗。
    --   Lua 5.1 的 math.random 默认种子是固定的，光靠它同一秒内会重复，
    --   所以把 os.clock()（进程内高精度计时）也拼进去。
    sess = tostring(os.time()) .. "-" .. tostring(math.random(100000, 999999))
        .. "-" .. string.format("%06d", math.floor((os.clock() % 1) * 1000000)),
    -- 异步探测（读时间线素材）的起始时间
    probeStartedAt = nil,
    -- 常驻 ASR 服务是否已预热过（每次会话只预热一次）
    srvWarm = false,
    -- worker 保活 ping 的时间戳
    lastPingAt = 0,
    -- 上一次的状态文字（避免每 0.5 秒重复设置同一个 Label）
    lastStatus = nil,
    modelPath = "",

    -- ── 字幕编辑器 ──────────────────────────────────────────────
    subs = {},                 -- 当前字幕：{ {s=开始秒, e=结束秒, t=文本}, ... }
    subsLua = PROBE_WORK .. "\\subs.lua",        -- dump 输出（Lua 表）
    -- ★ v2.0.8：全文高频热词。由 subs_io.py 在 dump 字幕时顺手算好写在这里，
    --   Lua 只负责读出来拼成一行字（见 App:RefreshHotwords）。
    hotwordsLua = PROBE_WORK .. "\\hotwords.lua",
    subsCfg = PROBE_WORK .. "\\subs_cfg.json",   -- 传给 subs_io.py 的配置
    subsJson = PROBE_WORK .. "\\subs_in.json",   -- 保存时上行给 Python 的数据
    -- ★ v4.2.0（B1）：「从时间线字幕轨读取」的来源标记（轨号/条数/错误）。
    --   与 subs.lua 同一次写出，Lua 读完即可显示"来源：STn"，也可据此提示
    --   「保存会写到新文件」。
    subsSrc = PROBE_WORK .. "\\subs_src.lua",
    -- ★ v4.8.25：dump 的解析结果标记。文件有内容却解析出 0 块时
    --   （编码不对 / 时间码不规范），subs_io.py 把原因写在这里，
    --   Lua 读到就提示用户 —— 否则界面只是一个空表格，看不出哪里坏了。
    subsErr = PROBE_WORK .. "\\subs_err.lua",
    subsPending = false,       -- 是否正在等 dump 结果
    subsStartedAt = nil,
    selRow = nil,              -- 当前选中的字幕行号
    -- ★ v2.0.8：关窗重入锁。之前点一次关不掉就再点，Close 事件反复进入，
    --   每进一次 Cleanup 就下发一次 kill（实测日志里连出 5 条 id=10~14）。
    closing = false,
    -- ★ 代码主动改写编辑框内容时要置位，否则会触发 TextChanged，
    --   把同一段文字又回灌一遍（参考版本里的 suppressEditor 是同一作用）。
    suppressEdit = false,
    -- 表格里有改动，但还没写进 SRT
    subsDirty = false,
    -- ★ 代码主动改写【表格行】内容时要置位，否则会触发 Tree.ItemChanged，
    --   被当成"用户在格子里手改了字"，反复回写（B 方案的就地编辑防递归）。
    suppressTreeEdit = false,

    -- ── 查找高亮 ──────────────────────────────────────────────
    -- matchRows[k] = 第 k 处匹配所在的行号（一条字幕里出现多次算多处）
    -- matchTotal  = 匹配总处数；curMatch = 当前停在第几处
    matchKw = nil,
    matchRows = nil,
    matchTotal = 0,
    curMatch = nil,
    -- ★ v3.2.0：命中行的哈希集合 hitSet[row]=true —— PaintRow 每行都要问
    --   "这行命中了吗"，用集合是 O(1)，搜"的"这种上千处的词时整表刷新才不卡。
    hitSet = {},
    -- ★ v3.2.5：每行【已画上的】底色状态缓存：""=无色 / "hit"=暗金 / "cur"=亮金。
    --   底色是跨进程写属性，几百行反复写会很慢；状态没变就一个字节都不写。
    --   ★ 表格重建（FillSubTree）时必须清空，否则新行会被误判成"已经上过色"。
    tintState = {},
    tintWarned = false,        -- BackgroundColor 不可用时只提示一次
    -- ★ v3.1.5：查找框文本的事件缓存。
    --   有些 Resolve 版本上 FindInput.Text 读回来不是字符串（读不到/抛错），
    --   于是关键词永远是 ""，命中数恒为 0、黄色也不出现。
    --   TextChanged 事件的 ev.Text 是另一条可靠来源，先存这里当兜底。
    findKwCache = "",

    -- ── 选中行集合（v3.1.1：由表格原生多选驱动）──
    checked = {},            -- checked[row] = true 表示该行当前被选中
                             --   ★ 由 SyncSelection() 从 Tree 的 SelectedItems
                             --     同步过来，是"哪几行"的权威记录
    suppressSelSync = false, -- 程序性改 Selected 时置位，挡住 SelectionChanged 重入
    selDirty = false,        -- 选中集合待重读（事件里状态未落定，改在 Tick 里读）
    subsUndo = nil,          -- 已废弃：v4.3.0 起用 undoStack（保留字段仅为兼容旧数据）
    -- ★ v4.3.0：多级撤销栈。每项 = { subs=深拷贝, cuts=深拷贝, cutSrt="" }。
    --   原来只有一层快照，连续删两次只能撤回最后一次；且旧快照是【浅拷贝】，
    --   而行文本是就地改的（App.S.subs[i].t = ...），所以"替换后撤销"实际无效。
    --   现在改为栈 + 深拷贝，两个问题一起解决。
    undoStack = {},
    UNDO_MAX = 50,
    cutsUndo = nil,          -- ★ v4.0.4：删行会同步累加切除区间到 cuts，撤销必须与
                             --   subs 对称回滚，否则「删行→撤销→再粗剪」会误切已恢复字幕
    cutSrtUndo = nil,        -- ★ v4.0.4：cutSrt 的快照，撤销删行时一并还原
    lastUndoKind = "",       -- ★ v4.0.2：最近一次可撤销操作 "subs"(字幕编辑) | "cut"(粗剪)
                             --   以前只靠 lastCut 是否非空判断，粗剪后 lastCut 永不复位 →
                             --   之后所有字幕编辑的「撤销」都被永久屏蔽。改用显式类型分流。
    textFocus = false,        -- 任一文本输入框（查找/替换/编辑框）聚焦中，
                             --   Backspace 此时留给删字，不删行

    -- ── 子进程退出码回传（worker.py 写的 rc_<sess>_<id>.lua）───
    curTask = nil,               -- { id=, script=, rcFile=, log=, startedAt= }
    ignoreRc = false,            -- 用户主动「停止」时不拿退出码报错
}

-- ── 工具 ──────────────────────────────────────────────────────────
local function P(msg) print("[AsrSubs] " .. tostring(msg)) end

-- 错误日志目录：3.0 放 <根>\data\logs，2.x 就落在根目录。
-- ★ 为什么还要"探测存在"：Lua 建不了多级目录（io.open 不能建目录、
--   os.execute 会闪黑窗）。安装器会把 data\logs 建好；开发机上没有这个
--   目录时就退回根目录，行为与 2.x 一致，不会因为写不进日志而丢诊断信息。
local function _dirExists(p)
    if type(p) ~= "string" or p == "" then return false end
    if type(bmd) == "table" and type(bmd.direxists) == "function" then
        local ok, r = pcall(bmd.direxists, p)
        if ok and r then return true end
    end
    -- 兜底：能读到自己写的占位文件，或者已经有过日志，就认为目录在
    return _fileOk(p .. "\\AsrSubs_error.log")
end

local LOG_DIR = BASE_DIR
do
    local cand = nil
    if PATHS and type(PATHS.logs) == "string" and PATHS.logs ~= "" then
        cand = PATHS.logs
    elseif LAYOUT ~= "legacy" then
        cand = BASE_DIR .. "\\data\\logs"
    end
    if cand and _isAsciiPath(cand) and _dirExists(cand) then LOG_DIR = cand end
end
local ERRLOG = LOG_DIR .. "\\AsrSubs_error.log"
-- ★ ELog 缓冲：识别过程中每秒能产出十几条日志，每条都 open/close 一次
--   文件会白等一堆 IO。攒够 ELOG_FLUSH_N 条、或过了 ELOG_FLUSH_SEC 秒，
--   再一次写掉。ELogReset 和进程退出前会强制落盘，不会丢。
local ELOG_BUF, ELOG_AT = {}, os.time()   -- AT 初值取当前时刻，别让第一条就触发孤零零的一次落盘
local ELOG_FLUSH_N, ELOG_FLUSH_SEC = 20, 3
local function ELogFlush()
    if #ELOG_BUF == 0 then return end
    local f = io.open(ERRLOG, "a")
    if f then
        for _, l in ipairs(ELOG_BUF) do f:write(l .. "\n") end
        f:close()
    end
    ELOG_BUF = {}
    ELOG_AT = os.time()
end
local function ELog(msg)
    ELOG_BUF[#ELOG_BUF + 1] = os.date("%Y-%m-%d %H:%M:%S") .. "  " .. tostring(msg)
    if #ELOG_BUF >= ELOG_FLUSH_N then
        ELogFlush()
    elseif os.time() - ELOG_AT >= ELOG_FLUSH_SEC then
        ELogFlush()
    end
end

local function ELogCritical(msg)
    -- ★ v4.0.2：关键错误【立即落盘】，不等 3 秒缓冲。
    --   插件被 worker / Resolve 硬崩打断时，缓冲区里最后几条往往是真因
    --   （退出码、引擎最后一行日志），攒着没 flush 就随进程一起没了 ——
    --   这正是以前排查「退出码 1/3」要反复复现的痛点。
    --   做法：先把待写的缓冲一起冲掉，再单独追加这一条并立刻关文件。
    ELogFlush()
    local f = io.open(ERRLOG, "a")
    if f then
        f:write(os.date("%Y-%m-%d %H:%M:%S") .. "  [CRIT] " .. tostring(msg) .. "\n")
        f:close()
    end
end

local function ELogReset()
    ELOG_BUF = {}
    ELOG_AT = os.time()
    local f = io.open(ERRLOG, "w")
    if f then
        f:write("=== run " .. os.date("%Y-%m-%d %H:%M:%S") .. " ===\n")
        f:close()
    end
end

-- ── 控件取值 ─────────────────────────────────────────────────────
--   ★★ Fusion 的 UI 属性名在不同版本上并不统一，这里统一从这里取：
--        · LineEdit  / TextEdit  → Text 和 PlainText 都试一遍
--        · CheckBox             → Checked（偶尔是数字），兜底 CheckState
--     之前只对输入框用 PlainText，结果 Resolve 21.1 的 LineEdit 读出来是
--     nil，字数设置一直 silently 走默认值 12 —— 用户改 50 也没反应。
--   ★ 每一步都包 pcall：GetItems() 返回的是 userdata，某些版本读不存在的
--     属性会直接抛错，那样整个按钮回调就废了（Fusion 还会静默吞异常）。
local function ctlGet(item)
    if item == nil then return nil, "nil-item" end
    local ok, v = pcall(function() return item.Text end)
    if ok and type(v) == "string" then return v, "Text" end
    ok, v = pcall(function() return item.PlainText end)
    if ok and type(v) == "string" then return v, "PlainText" end
    return nil, "unreadable"
end

-- ★ v4.0.2：把散落两处的「控件真值优先、读不到才用事件快照兜底」
--   收敛成一个只读 helper，行为与原来逐处内联完全一致（含空串语义）。
--   注意：空串是【有效值】（用户主动清空），只有 nil 才算"读不到"。
--   返回 value, source：source 供日志标注这次取值来自哪里，便于排障。
local function ctlTextOrSnap(item, snapVal)
    local v, src = ctlGet(item)
    if v ~= nil then return v, src end
    if snapVal ~= nil then return tostring(snapVal), "uiSnap" end
    return "", "default"
end

local function ctlSet(item, s)
    if item == nil then return false end
    local ok1 = pcall(function() item.Text = s end)
    local ok2 = pcall(function() item.PlainText = s end)
    return ok1 or ok2
end

local function ctlChecked(item)
    if item == nil then return false, "nil-item" end
    -- ★ v3.2.10：优先读 CheckState（Qt 枚举 0=未选 / 2=选中）。
    --   本机 Fusion 上 item.Checked 有"视觉已勾选、读回 false"的怪癖
    --   （见下面 v3.2.9 附近的实测注释），只读 Checked 会把勾选读成未勾选，
    --   导致 no_gap / all_punct 传错。CheckState 才是可靠真值，Checked 降为兜底。
    local ok, v = pcall(function() return item.CheckState end)
    if ok and type(v) == "number" then return v ~= 0, "CheckState(num)" end
    if ok and type(v) == "boolean" then return v, "CheckState" end
    ok, v = pcall(function() return item.Checked end)
    if ok and type(v) == "boolean" then return v, "Checked" end
    if ok and type(v) == "number" then return v ~= 0, "Checked(num)" end
    return false, "unreadable"
end

-- ★ 写勾选状态：Checked 与 CheckState 两个属性都试一遍（v1.28 新增）。
--   实测日志里 AllPunct 走的是 "removed"（Checked 读不出来），说明 Fusion
--   的 CheckBox 在不同版本里可写属性并不一致：只看 Checked 会漏。
--   这里两条路都走，谁成功用谁；返回是否至少成功一次，供调用方记录。
local function setChecked(item, val)
    if item == nil then return false end
    local b = val and true or false
    local done = false
    -- 优先 Checked（语义最贴近）
    local ok1 = pcall(function() item.Checked = b end)
    if ok1 then done = true end
    -- 再补 CheckState（Qt 的枚举：0=未选中, 2=选中）
    local ok2 = pcall(function() item.CheckState = b and 2 or 0 end)
    if ok2 then done = true end
    return done
end

-- ══════════════════════════════════════════════════════════════
-- ★ 以下四个基础函数基于 Resolve 脚本环境的实测结论。
--   实测：Resolve 21.1 的脚本 Lua 状态里
--     io / ffi / package / require / os.execute / bmd.readdir 可能是 nil
--   但
--     bmd.wait / bmd.fileexists / bmd.direxists / loadfile 一定可用
--   所以凡是与"等一等""文件在不在"有关的操作，一律优先走 bmd.*，
--   io.* 只作为兜底 —— 这样即使哪天换到被沙箱限制的环境也照样能跑。
-- ══════════════════════════════════════════════════════════════
local function waitSec(s)
    if type(bmd) == "table" and type(bmd.wait) == "function" then
        pcall(bmd.wait, s)
        return
    end
    -- ★ 兜底也绝不能启子进程：os.execute("ping ...") 会闪黑窗，
    --   而这个函数每次轮询都调（0.1 秒一次），闪起来根本没法用。
    --   改成纯 Lua 忙等（只在极端环境下才会走到，单次 0.1 秒，代价可接受）。
    local limit = tonumber(s) or 0.1
    if limit <= 0 then return end
    local t0 = os.clock()
    while (os.clock() - t0) < limit do end
end

-- ★ P1 安全加固：把参数安全地包进 cmd.exe 的双引号里。
--   cmd 把引号外的 & | < > ^ ( ) ! 当分隔符/转义符，双引号内大多被挡住；
--   但 " 会提前闭合引号、%VAR% 在引号内仍会展开 —— 这两类字符一律拒绝，
--   宁可拒绝启动子进程，也不让路径或参数被解释成命令。
local function cmdQuote(s)
    s = tostring(s or "")
    if s == "" then return nil end
    if s:find('["\r\n%%]') then return nil end
    return '"' .. s .. '"'
end

local function exists(path)
    if type(bmd) == "table" and type(bmd.fileexists) == "function" then
        local ok, r = pcall(bmd.fileexists, path)
        if ok and r == true then return true end
    end
    if io == nil then return false end
    local f = io.open(path, "r")
    if f then f:close() return true end
    return false
end

-- ★ 用 loadfile 读"Lua 格式"的数据文件，而不是解析 JSON。
--   好处：不用在 Lua 里做 JSON 模式匹配，中文/转义全部由 Lua 引擎自己处理。
--   返回一个 table；失败返回 nil。
local function loadLuaTable(path)
    if type(loadfile) ~= "function" then return nil end
    if not exists(path) then return nil end
    local ok1, chunk = pcall(loadfile, path)
    if not ok1 or type(chunk) ~= "function" then return nil end
    local ok2, t = pcall(chunk)
    if not ok2 or type(t) ~= "table" then return nil end
    return t
end

-- ══════════════════════════════════════════════════════════════
-- 设置持久化（rt\settings.lua）
--
-- 旧格式只有 python = "..." 一行（早期由手持编辑写入手），
-- 顶部的 readPySetting() 靠正则抠它 —— 所以 python 那一行
-- 【格式必须保持】，后面可以随便加键。
-- 现在扩成完整表：python + 界面七项 + 窗口 Geometry。
--   · 读：loadLuaTable + 逐项类型校验，脏值回默认（绝不崩）
--   · 写：tmp → remove → rename 原子写
--     （Windows 的 os.rename 不覆盖已存在的文件，必须先删）
--   · 保存时机：点「开始生成字幕」+ 关窗（自然提交点，
--     不依赖 ComboBox 的事件是否派发）
-- ══════════════════════════════════════════════════════════════
-- ★ v2.0.8：2 → 3。版本号一起变了，意味着【旧文件里的 win 一律不信】
--   （见 readSettings 末尾的判断）。这样历史遗留的"最大化残留尺寸"
--   不用手工去删 rt\settings.lua，升级后第一次启动就自动回到 800×600，
--   用户下次关窗再按新版本号把尺寸存回去，之后照常记住。
local SETTINGS_VER = 3
-- ★ v2.0.8：默认窗口 800×600（用户拍板，比 v2.0.7 的 1030×772 再小一圈）。
--   上一版之所以又开成满屏，是因为 rt\settings.lua 里存着
--   win = { 0, 23, 1463, 843 } —— 那是"最大化"时被 Close 存下来的值。
--   这台机器是 1920×1080 @125%，1463×843 就是最大化尺寸。
--   所以光改默认值没用，读回来的时候必须拦一道（见下面的 GEO_MAX_*）。
local DEFAULT_GEO = { 400, 100, 800, 600 }
-- 记住的窗口尺寸的合法区间。超过上限的一律当成"最大化残留"丢掉，
-- 回落到 DEFAULT_GEO —— 宁可每次都开 800×600，也不要再开成满屏。
local GEO_MIN_W, GEO_MIN_H = 760, 570
local GEO_MAX_W, GEO_MAX_H = 1400, 820
-- 每行宽度默认值：断句按【显示宽度】计（中文/全角=2，ASCII=1），
-- 24 ≈ 12 个汉字，和以前"每行 12 字"的观感一致。
local DEFAULT_MAX_CHARS = 24

-- 值 → Lua 字符串字面量（用户输入/控件值必须转义）
local function lstr(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\r", ""):gsub("\n", "\\n")
    return '"' .. s .. '"'
end

local function readSettings()
    local d = {
        python = "", lang = 0, model = 0, model_path = "",
        max_chars = DEFAULT_MAX_CHARS, all_punct = false, end_punct = false,
        -- ★ v4.8.21：长停顿断行（设置页开关）。false = 与旧版行为完全一致
        split_pause = false,
        -- ★ v1.0.1：素材旁同名 srt（默认 true，老设置里没有这一项时行为不变）
        sidecar = true,
        -- ★ v4.0.0：no_gap 字段移除（字幕无间隙功能删除），新增 end_punct（句末无标点）。
        -- ★ v2.0.7：prompt（提示词）整项废弃，换成 corrections（权重词纠错表）。
        --   旧设置里的 prompt 不再读 —— 那是自由文本，不是「正确=错误」格式，
        --   硬迁过来只会变成一堆解析不了的垃圾行。
        corrections = "", win = nil,
        -- v2.0.0：时间微调（音轨选择不持久化，见 BuildUI 里的说明）
        offset_sec = 0, min_dur = 0.04,
    }
    local t = loadLuaTable(SETTINGS_FILE)
    if type(t) ~= "table" then
        -- 旧格式 / 文件不存在：python 仍用正则抠（保持兼容）
        d.python = readPySetting()
        return d
    end
    if type(t.python) == "string" then d.python = t.python end
    local li = tonumber(t.lang)
    if li and li >= 0 and li < 16 and li == math.floor(li) then d.lang = li end
    local mi = tonumber(t.model)
    if mi and mi >= 0 and mi < 64 and mi == math.floor(mi) then d.model = mi end
    if type(t.model_path) == "string" then d.model_path = t.model_path end
    local mc = tonumber(t.max_chars)
    if mc and mc >= 4 and mc <= 200 then d.max_chars = math.floor(mc) end
    if type(t.all_punct) == "boolean" then d.all_punct = t.all_punct end
    -- ★ v4.0.0：句末无标点回归，读 end_punct（映射到 App.S.noPunct / 引擎 no_punct）
    if type(t.end_punct) == "boolean" then d.end_punct = t.end_punct end
    if type(t.split_pause) == "boolean" then d.split_pause = t.split_pause end
    if type(t.sidecar) == "boolean" then d.sidecar = t.sidecar end
    if type(t.corrections) == "string" then d.corrections = t.corrections end
    -- v2.0.0：整体偏移（±600 秒）与最短显示时长（0.04~10 秒）
    local off = tonumber(t.offset_sec)
    if off and off >= -600 and off <= 600 then d.offset_sec = off end
    local md = tonumber(t.min_dur)
    if md and md >= 0.04 and md <= 10 then d.min_dur = md end
    if type(t.win) == "table" and #t.win == 4 and tonumber(t.ver) == SETTINGS_VER then
        local x, y, w, h = t.win[1], t.win[2], t.win[3], t.win[4]
        if type(x) == "number" and type(y) == "number"
           and type(w) == "number" and type(h) == "number"
           and w >= GEO_MIN_W and h >= GEO_MIN_H
           and w <= GEO_MAX_W and h <= GEO_MAX_H then
            d.win = { math.floor(x), math.floor(y), math.floor(w), math.floor(h) }
        end
        -- ★ v2.0.8 两道闸门，缺一不可：
        --   ① ver 必须是本版本的（旧文件里的尺寸直接不信，见 SETTINGS_VER 注释）
        --   ② 尺寸必须落在 760×570 ~ 1400×820 之间
        --   用户最大化后关窗 → 存下 1463×843 → 下次启动开成满屏，
        --   这个坑已经踩了两轮，两道闸门各挡一种来路。
        --   越界【不报错、不修边，直接丢】→ BuildUI 回落 DEFAULT_GEO（800×600）。
    end
    return d
end

local function writeSettings(t)
    t = t or {}
    -- python 行保持旧格式（顶部 readPySetting 用正则抠它）
    local py = tostring(t.python or "")
    if py == "" then py = readPySetting() end
    local lines = {
        "-- AsrSubs 设置（由插件自动保存；也可手改，别破坏 return{...} 结构）",
        "return {",
        "    ver = " .. SETTINGS_VER .. ",",
        "    python = " .. lstr(py) .. ",",
        "    lang = " .. tostring(math.floor(tonumber(t.lang) or 0)) .. ",",
        "    model = " .. tostring(math.floor(tonumber(t.model) or 0)) .. ",",
        "    model_path = " .. lstr(t.model_path) .. ",",
        "    max_chars = " .. tostring(math.floor(tonumber(t.max_chars) or DEFAULT_MAX_CHARS)) .. ",",
        "    all_punct = " .. ((t.all_punct and "true") or "false") .. ",",
        "    end_punct = " .. ((t.end_punct and "true") or "false") .. ",",
        "    split_pause = " .. ((t.split_pause and "true") or "false") .. ",",
        -- ★ v1.0.1：素材旁同名 srt（缺省 true）
        "    sidecar = " .. ((t.sidecar ~= false and "true") or "false") .. ",",
        "    corrections = " .. lstr(t.corrections) .. ",",
        -- v2.0.0：时间微调
        "    offset_sec = " .. string.format("%g", tonumber(t.offset_sec) or 0) .. ",",
        "    min_dur = " .. string.format("%g", tonumber(t.min_dur) or 0.04) .. ",",
    }
    if type(t.win) == "table" and #t.win == 4 then
        lines[#lines + 1] = string.format(
            "    win = { %d, %d, %d, %d },",
            math.floor(t.win[1]), math.floor(t.win[2]),
            math.floor(t.win[3]), math.floor(t.win[4]))
    end
    lines[#lines + 1] = "}"
    local body = table.concat(lines, "\n") .. "\n"

    local tmp = SETTINGS_FILE .. "." .. tostring(os.time())
        .. tostring(math.floor((os.clock() or 0) * 1000)) .. ".tmp"
    local f = io.open(tmp, "w")
    if not f then return false end
    f:write(body)
    f:close()
    -- Windows rename 不覆盖已存在文件 → 先删再改名
    os.remove(SETTINGS_FILE)
    local ok = os.rename(tmp, SETTINGS_FILE)
    if not ok then pcall(os.remove, tmp) end
    return ok and true or false
end

local function tail(path, n)
    if io == nil then return "" end
    local f = io.open(path, "rb")
    if not f then return "" end
    local size = f:seek("end")
    f:seek("set", math.max(0, size - n))
    local d = f:read("*all") or ""
    f:close()
    return d
end

local function osPath(p) return p:gsub("/", "\\") end

-- 列出目录里匹配「前缀 + 后缀」的文件名。
-- ★★ 绝不用 io.popen('cmd /c dir /b ...')：会闪黑窗。
--    优先 bmd.readdir（实测可用，沙箱下可能是 nil），
--    拿不到就按 Resolve 的常见命名逐个试存在性。
local function listFiles(dir, prefix, suffix)
    local out = {}
    local lp, ls = prefix:lower(), suffix:lower()

    -- ★★ v1.2.1：bmd.readdir 的正确用法是「目录 + \*」。
    --   旧写法 bmd.readdir(dir) 有三个坑，实测（Resolve 21.1）全部踩中：
    --     ① 裸目录名会被当成 pattern，在【父目录】里按名字匹配 ——
    --        readdir("D:\AsrSubs\engine\rt") 返回 Parent="D:\AsrSubs\engine\"、
    --        唯一条目是 rt 目录本身，根本列不到 rt 里的文件；
    --     ② 每个条目是 table（字段 Name / IsDir / Size / WriteTime），
    --        不是字符串 —— tostring(条目) 得到 "table: 0x..."，永远匹配不上；
    --     ③ 表里还带 Pattern / Parent 两个字符串元字段，会被当文件名误判。
    --   后果：FindAudio 永远返回 nil → 渲染明明成功产出 WAV，却报
    --   "未生成音频文件（时间线可能没有音频轨）"（2026-11-06 真机实测）。
    if type(bmd) == "table" and type(bmd.readdir) == "function" then
        local base = tostring(dir or ""):gsub("[\\/]+$", "")
        local ok, files = pcall(bmd.readdir, base .. "\\*")
        if ok and type(files) == "table" then
            for _, ent in pairs(files) do
                local name = nil
                if type(ent) == "table" then
                    if ent.IsDir ~= true then name = ent.Name end
                elseif type(ent) == "string" then
                    name = ent
                end
                if name ~= nil then
                    local s = tostring(name)
                    if s:lower():sub(1, #lp) == lp
                        and s:lower():sub(-#ls) == ls then
                        out[#out + 1] = s
                    end
                end
            end
        end
    end
    if #out > 0 then return out end

    -- 兜底：Resolve 导出重名时会追加 _1 / _2 …
    for i = 0, 6 do
        local nm = prefix .. (i == 0 and "" or ("_" .. i)) .. suffix
        if exists(dir .. "\\" .. nm) then out[#out + 1] = nm end
    end
    return out
end

-- ★ Lua 没有 sleep。优先 bmd.wait（精确到 0.05 秒，且不启子进程），
--   没有 bmd.wait 时才退回到 ping。
local function sleepMS()
    waitSec(0.1)
end

-- 读取模型清单（Lua 不能列目录，Python 侧会生成这个文件）
--
-- ★★ 路径必须在【ASCII 目录】。改之前的两个问题：
--   ① 清单原本指向 TOOL_ROOT（D:\AsrSubs\engine\models.txt，中文），
--      而 Python 写的是 D:\AsrSubs\models.txt —— 两边根本不是同一个文件；
--   ② 就算指对了，Windows 下 Lua 的 io.open 走 ANSI 接口，中文路径打不开。
--   结果：模型下拉框永远只有硬编码的默认一项，装了新模型也看不到。
-- ★ 现在优先读 models.lua（loadfile 加载，连 io.open 都不需要）。
local MODEL_MANIFEST = ASCII_ROOT .. "\\models.txt"
local MODEL_MANIFEST_LUA = ASCII_ROOT .. "\\models.lua"
local DEFAULT_MODEL = "Qwen3-ASR-0.6B-hf"

-- 读模型清单。返回 {{name=.., path=..}, ...}
--   ★ path 由 Python 直接给出完整路径：模型不一定装在默认目录，
--     让 Lua 去拼 TOOL_ROOT\models\<名字> 的话，拼错就静默回退默认模型。
local function readModels()
    local out = {}
    local t = loadLuaTable(MODEL_MANIFEST_LUA)
    if t then
        for _, v in ipairs(t) do
            if type(v) == "table" then
                local n = tostring(v.name or ""):gsub("^%s+", ""):gsub("%s+$", "")
                local p = tostring(v.path or ""):gsub("^%s+", ""):gsub("%s+$", "")
                if n ~= "" then
                    out[#out + 1] = { name = n, path = (p ~= "" and p or nil) }
                end
            else
                -- 兼容旧格式（纯字符串数组）：只能靠拼
                local s = tostring(v or ""):gsub("^%s+", ""):gsub("%s+$", "")
                if s ~= "" then out[#out + 1] = { name = s, path = nil } end
            end
        end
        if #out > 0 then return out end
    end
    -- 兜底：读纯文本清单
    if io == nil then
        return { { name = DEFAULT_MODEL, path = nil } }
    end
    local f = io.open(MODEL_MANIFEST, "r")
    if not f then return { { name = DEFAULT_MODEL, path = nil } } end
    for line in f:lines() do
        local s = line:gsub("^%s+", ""):gsub("%s+$", "")
        if s ~= "" then out[#out + 1] = { name = s, path = nil } end
    end
    f:close()
    if #out == 0 then return { { name = DEFAULT_MODEL, path = nil } } end
    return out
end

-- 取清单里所有模型名（填下拉用）
local function pickModelNames(list)
    local names = {}
    for i, m in ipairs(list or {}) do names[i] = m.name end
    return names
end

-- ── 启动 ──────────────────────────────────────────────────────────
-- ── 环境自检 ────────────────────────────────────────────────────────
--   把"工具在哪、环境在不在"先摸一遍。换了电脑 / 挪了目录 / 重装了
--   DaVinci 之后，这些信息第一时间就能告诉你是哪里没对上，
--   而不是点半天没反应还不知道为什么。
--   ★ 只检查 ASCII 路径 —— 中文目录（模型）Lua 在 Windows 上探不了。
function App:SelfCheck()
    local report, bad = {}, {}
    report[#report + 1] = "引擎目录 " .. ASCII_ROOT
    report[#report + 1] = "运行环境 " .. PYTHON
    report[#report + 1] = "模型目录 " .. MODELS_DIR

    if not exists(ASCII_ROOT .. "\\resolve_bridge.py") then
        bad[#bad + 1] = "引擎脚本缺失：" .. ASCII_ROOT .. "\\resolve_bridge.py"
    end
    -- ★ v3.2.6：粗剪/落轨脚本缺了，按钮会点下去毫无反应（只能看到"任务超时"）
    if not exists(CUT_SCRIPT) then
        bad[#bad + 1] = "粗剪脚本缺失：" .. CUT_SCRIPT
    end
    if not exists(LAND_SCRIPT) then
        bad[#bad + 1] = "落轨脚本缺失：" .. LAND_SCRIPT
    end
    if not exists(PYTHON) then
        bad[#bad + 1] = "Python 环境缺失：" .. PYTHON
    end

    for _, s in ipairs(report) do ELog("  [路径] " .. s) end
    App.S.pathReport = report
    App.S.pathBad = bad
    if #bad > 0 then
        ELog("!! 环境自检发现 " .. #bad .. " 处问题")
        for _, s in ipairs(bad) do ELog("   - " .. s) end
    else
        ELog("环境自检通过")
    end
    return #bad == 0
end

function App.OnRun()
    ELogReset()
    ELog("OnRun 开始")
    pcall(function() App:SelfCheck() end)

    local ok, err = pcall(function()
        local r = resolve
        if not r and type(Resolve) == "function" then r = Resolve() end
        ELog("resolve = " .. tostring(r))
        if not r then ELog("中止：无法获取 resolve"); return end
        App.resolve = r          -- ★ 必须保存！StartRender 里要用
        -- ★ v1.1.1：记住"开窗时在哪一页"。关窗时只有真的动过页面才切回去，
        --   没动过就绝不碰 —— 见 ReturnToPrevPage / CleanupRender 的说明。
        pcall(function() App.S.openPage = tostring(r:GetCurrentPage() or "") end)

        local pm = r:GetProjectManager()
        ELog("projectManager = " .. tostring(pm))
        if not pm then ELog("中止：GetProjectManager 返回空"); return end

        local project = pm:GetCurrentProject()
        ELog("project = " .. tostring(project))
        if not project then ELog("中止：请先打开一个项目"); return end
        App.project = project

        local timeline = project:GetCurrentTimeline()
        ELog("timeline = " .. tostring(timeline))
        if not timeline then ELog("中止：请先打开一个时间线"); return end
        App.timeline = timeline
        App.tlName = timeline:GetName() or "未命名"
        ELog("timelineName = " .. tostring(App.tlName))

        local fusion = r:Fusion()
        ELog("fusion = " .. tostring(fusion))
        if not fusion then ELog("中止：resolve:Fusion() 返回空"); return end

        local ui = fusion.UIManager
        ELog("UIManager = " .. tostring(ui))
        if not ui then ELog("中止：fusion.UIManager 为空"); return end

        ELog("bmd = " .. tostring(bmd))
        if not bmd then ELog("中止：bmd 全局为空"); return end

        local dispatcher = bmd.UIDispatcher(ui)
        ELog("dispatcher = " .. tostring(dispatcher))
        if not dispatcher then ELog("中止：UIDispatcher 返回空"); return end
        App.S.dispatcher = dispatcher

        -- 注意：不在这里 mkdir（会闪 CMD 窗口），改由 Python 侧创建目录
        ELog("进入 BuildUI")
        App:BuildUI(ui, dispatcher)
        ELog("BuildUI 返回（RunLoop 已退出）")
    end)

    if not ok then
        ELog("!!! OnRun 异常: " .. tostring(err))
        P("脚本错误: " .. tostring(err))
    end
    ELog("OnRun 结束")
end

-- ★ v2.0.1：ABOUT_TEXT 说明文字已随「关于」弹窗一并删除。


-- 秒 → "mm:ss.cc" / "h:mm:ss.cc"
local function fmtSec(x)
    x = tonumber(x) or 0
    if x < 0 then x = 0 end
    local h = math.floor(x / 3600)
    local m = math.floor((x % 3600) / 60)
    local s = x % 60
    if h > 0 then
        return string.format("%d:%02d:%05.2f", h, m, s)
    end
    return string.format("%02d:%05.2f", m, s)
end

-- 把表格里手敲的时间文本解析回「秒」。解析不了返回 nil —— 调用方会把该格
-- 还原成原值。支持 "SS.mm" / "MM:SS.mm" / "H:MM:SS.mm"，逗号当小数点。
local function parseTimeText(s)
    local raw = tostring(s or ""):gsub("%s", ""):gsub(",", ".")
    if raw == "" then return nil end
    local parts = {}
    for p in raw:gmatch("[^:]+") do parts[#parts + 1] = p end
    if #parts == 0 or #parts > 3 then return nil end
    local nums = {}
    for i, p in ipairs(parts) do
        if not p:match("^%d+%.?%d*$") then return nil end
        nums[i] = tonumber(p)
        if not nums[i] then return nil end
    end
    if #nums == 1 then return nums[1] end
    if #nums == 2 then return nums[1] * 60 + nums[2] end
    return nums[1] * 3600 + nums[2] * 60 + nums[3]
end

-- ══════════════════════════════════════════════════════════════
-- 查找命中的显示方式
--
-- ★★★ v3.2.0：整行不再上任何颜色（Tree 单元格只能整格 TextColor，
--   一染色就是整段字幕变黄，用户明确拒绝）。
-- ★★★ v3.2.2：表格里恢复【干净原文】，一个符号都不加；关键词高亮
--   改到表格外面的「命中预览」Label 上做（Label 支持 HTML，见下方
--   htmlMark），那里可以真正只染关键词几个字。
--   表格与数据始终是原文，编辑框 / SRT / 导出都不受影响。
--
--   stripHitMarks 必须定义在【所有调用点之前】：ItemChanged 在 1800 行
--   左右就要用它兜底。Lua 的 local 只对定义之后的代码可见，放后面会变成
--   nil 全局 → 运行期 "attempt to call a nil value"（这个坑踩过两次了）。
local OPEN_MARK  = "【"
local CLOSE_MARK = "】"

-- ── 关键词高亮用的 HTML ─────────────────────────────────────────
-- 官方 UIManager 文档：Label / Button / TextEdit 会自动识别并渲染 Text 里的
-- 基础 HTML；Tree / TreeItem 不在这个名单里（单元格只能整格 TextColor）。
-- 本机实测：TextEdit 的 HTML 属性可写，写完后 PlainText 能无损读回原文
-- （换行 / < > & / 引号都对；只有首尾空格会被压缩 → 那种情况自动退回纯文本）。
--
-- 做法：表格里保持干净原文（一个符号都不加），高亮做在【编辑区】：
--   下野山<span style="background-color:#6B4E00;color:#FFD24A">三</span>到底长什么
-- 见 App:RenderSubEditRich。
local HTML_BG = "#6B4E00"    -- 关键词底色（琥珀）
local HTML_FG = "#FFD24A"    -- 关键词字色（亮黄）

-- 转义 HTML 特殊字符，避免字幕里的 < > & 被当成标签吃掉
local function htmlEsc(s)
    s = tostring(s or "")
    s = s:gsub("&", "&amp;")
    s = s:gsub("<", "&lt;")
    s = s:gsub(">", "&gt;")
    s = s:gsub('"', "&quot;")
    return s
end

-- 把 s 里所有 kw 包成带底色的 span（先转义再包，标签不会被字幕内容打乱）
local function htmlMark(s, kw)
    local esc = htmlEsc(s)
    if not kw or kw == "" then return esc end
    local ek = htmlEsc(kw)
    local span = '<span style="background-color:' .. HTML_BG ..
                 ';color:' .. HTML_FG .. ';">' .. ek .. '</span>'
    local out, from = {}, 1
    while true do
        local p = esc:find(ek, from, true)
        if not p then break end
        out[#out + 1] = esc:sub(from, p - 1)
        out[#out + 1] = span
        from = p + #ek
    end
    out[#out + 1] = esc:sub(from)
    return table.concat(out)
end

-- 反向：把 【kw】 还原成 kw（从单元格读文字时用，别让标记混进字幕数据）
local function stripHitMarks(s, kw)
    if not s or not kw or kw == "" then return s end
    local needle = OPEN_MARK .. kw .. CLOSE_MARK
    if not s:find(needle, 1, true) then return s end
    local out, from = {}, 1
    while true do
        local p = s:find(needle, from, true)
        if not p then break end
        out[#out + 1] = s:sub(from, p - 1) .. kw
        from = p + #needle
    end
    out[#out + 1] = s:sub(from)
    return table.concat(out)
end

-- ── 界面 ──────────────────────────────────────────────────────────
-- 把当前界面值收集成设置表（读控件的优先级和「开始」按钮一致：
-- 变更事件备份 → 直接读控件 → 默认值），供保存用。
function App:CurrentSettings()
    local it = App.S.items or {}
    local snap = App.S.uiSnap or {}
    local t = {
        lang = 0, model = 0, model_path = "",
        max_chars = DEFAULT_MAX_CHARS,
        all_punct = false, end_punct = false, corrections = "",
        offset_sec = 0, min_dur = 0.04,
    }
    pcall(function()
        t.lang = math.floor(tonumber((App.S.setItems and App.S.setItems.LangCombo and App.S.setItems.LangCombo.CurrentIndex) or (App.S.cfg and App.S.cfg.lang) or 0) or 0)
        t.model = math.floor(tonumber((App.S.setItems and App.S.setItems.ModelCombo and App.S.setItems.ModelCombo.CurrentIndex) or (App.S.cfg and App.S.cfg.model) or 0) or 0)
    end)
    -- ★ 同样改成【控件优先】（v1.28）：保存时要存用户当前在框里看到的那个值。
    --   旧写法 snap 优先 → 用户清空自定义模型路径后，保存下来的还是旧路径。
    -- ★ v4.0.2：改用公共 helper ctlTextOrSnap（行为等价：控件真值优先、
    --   空串有效、读不到才用快照）。空串仍会被 trim，逻辑不变。
    -- ★ v1.1.2：参数控件自 v4.8.0 起就在【设置弹窗】里（App.S.setItems）。
    --   这里和「开始」按钮一直在读主窗口的 App.S.items.MaxEdit / AllPunct /
    --   NoPunct / ModelPathEdit —— 那几个控件在主窗口【根本不存在】：
    --     ctlGet(nil)   → nil  → 退快照（快照一旦是空的，字数就退回 24）
    --     ctlChecked(nil) → false/"nil-item" → 勾选框每次保存都被写成 false
    --   这就是"去标点打不开"和"字数概率性变回 24"的根因。
    local mp, mpSrc = ctlGet(App.S.setItems and App.S.setItems.ModelPathEdit)
    if mpSrc == "nil-item" or mpSrc == "unreadable" then
        mp = (App.S.cfg and App.S.cfg.model_path) or snap.model or ""
    end
    if type(mp) ~= "string" then mp = "" end
    t.model_path = mp:gsub("^%s+", ""):gsub("%s+$", "")
    -- ★ 数字框 MaxEdit：空串要 fallback 到快照（有效数字才有意义），
    --   语义与字符串框不同，保留原来的专门逻辑，不走 helper。
    local mcTxt, mcSrc = ctlGet(App.S.setItems and App.S.setItems.MaxEdit)
    if mcSrc == "nil-item" or mcSrc == "unreadable" or mcTxt == "" then
        mcTxt = (App.S.cfg and App.S.cfg.max_chars and tostring(App.S.cfg.max_chars))
            or snap.max or ""
    end
    local mc = tonumber(mcTxt)
    if mc and mc >= 4 and mc <= 200 then t.max_chars = math.floor(mc) end
    -- ★ 空串也要当成有效值：用户把纠错表清空 = 不要纠错了。
    --   ctlTextOrSnap 同样保证空串是有效值（只有 nil 才兜底快照）。
    t.corrections = ctlTextOrSnap(App.S.items.CorrEdit, snap.corr)
    -- ★ 勾选框同样【控件真值优先】，快照只在读不出来时才兜底
    -- ★ v4.0.0：字幕无间隙（no_gap）删除；句末无标点（end_punct）回归
    local na, naSrc = ctlChecked(App.S.setItems and App.S.setItems.AllPunct)
    if naSrc == "nil-item" or naSrc == "unreadable" then
        na = (App.S.cfg and App.S.cfg.all_punct) and true or false
    end
    t.all_punct = na and true or false
    local ep, epSrc = ctlChecked(App.S.setItems and App.S.setItems.NoPunct)
    if epSrc == "nil-item" or epSrc == "unreadable" then
        ep = (App.S.cfg and App.S.cfg.end_punct) and true or false
    end
    t.end_punct = ep and true or false
    -- ★ v4.8.22：长停顿断行。控件在【设置弹窗】里（App.S.setItems），
    --   没开过设置时 ctlChecked(nil) 返回 false/"nil-item" —— 只兜 "unreadable"
    --   的话，每次保存都会把它写成 false。实测症状：勾上后关设置再开还在
    --   （那是 App.S.cfg 在撑），但整个插件关掉再开就丢了（文件里是 false）。
    --   所以 "nil-item" 也必须回落到 App.S.cfg。
    local sp, spSrc = ctlChecked(App.S.setItems and App.S.setItems.SplitPause)
    if spSrc == "nil-item" or spSrc == "unreadable" then
        sp = (App.S.cfg and App.S.cfg.split_pause) and true or false
    end
    t.split_pause = sp and true or false
    -- ★ v2.0.2：时间微调控件已移除，这两个值不再从界面读取。
    --   offset_sec 固定 0、min_dur 固定 0.04（= v1.28 行为）。
    t.offset_sec = 0
    t.min_dur = 0.04
    return t
end

-- 保存设置（界面七项 + 窗口 Geometry）。失败只记日志，不打断用户。
function App:SaveSettingsNow()
    local ok, err = pcall(function()
        local t = self:CurrentSettings()
        local g = nil
        pcall(function() g = App.S.win and App.S.win.Geometry end)
        if type(g) == "table" and #g == 4 then t.win = g end
        if not writeSettings(t) then
            ELog("!! 设置保存失败（写不进 " .. SETTINGS_FILE .. "）")
        end
    end)
    if not ok then ELog("!! 保存设置异常: " .. tostring(err)) end
end

function App:BuildUI(ui, dispatcher)
    -- 窗口尺寸：上次记住的就用上次的（含用户拖动后的位置/大小），
    -- 没有记录才用默认 4:3。readSettings 里已校验过范围。
    local cfg = readSettings()
    App.S.cfg = cfg   -- ★ v4.8.0：参数唯一真源
    local geo = cfg.win or DEFAULT_GEO
    -- ★ v2.0.8：把最终采用的窗口尺寸记进日志。这个值被"最大化残留"坑过两轮，
    --   以后再出现"开成满屏"，看一眼这行就知道是读到了脏值还是默认值没生效。
    ELog(string.format("窗口尺寸 %d×%d @(%d,%d) 来源=%s",
        geo[3] or 0, geo[4] or 0, geo[1] or 0, geo[2] or 0,
        cfg.win and "设置" or "默认"))

    -- 所有控件都必须有 ID；ComboBox 的内容在 GetItems 之后再填（官方模式）
    -- ★ Geometry 顺序是 { x, y, 宽, 高 }，不是 {宽,高,x,y}！
    local win = dispatcher:AddWindow({
        ID = "AsrSubsTool",
        WindowTitle = "AsrSubs",
        -- ★ 窗口可以随时拖边框放大/缩小（日志区会自动跟着变高）。
        --   默认尺寸按用户拍板：宽 : 高 = 4 : 3。宽度沿用截图换算值 1030，
        --   高度 = 1030 × 3 / 4 ≈ 772（原来的 950 上下太空）。
        Geometry = geo,
        Spacing = 8,
        -- ★ 千万【不要】在这里给控件设 min-height。
        --   所有控件的最小高度加总以后，一旦超过窗口的可用高度，
        --   Qt 布局就压不下去，超出部分直接越界重叠 ——
        --   实测过：QTextEdit 90px × 2 + QTreeView 200px 就是元凶，
        --   窗口一缩小，左栏的文本框、按钮、日志区就叠在一起。
        --   这里只管字号，高度交给布局按 Weight 自己分。
        StyleSheet = [[
            QWidget { font-size: 13px; }
            /* 「适配字幕」真能挂的时候才强调一下。没字幕可挂时它是
               disabled，由 Qt 自己画成灰的，不覆盖 disabled 的观感。
               注：这条靠 objectName 命中 #LandBtn；命中不了就是空操作，
               不影响任何功能，按钮的可用/禁用照样由 Enabled 决定。 */
            QPushButton#LandBtn:enabled { background-color: rgba(70, 110, 145, 210); }
            /* ★ v4.0.3：查找 / 替换行统一视觉（方案 B）。
               ① 输入框与三个按钮写同一套 4px 圆角 / 边框 / 背景 / 内边距，
                  消掉「直角弱边框输入框 vs 圆角高亮胶囊按钮」的风格打架。
                  圆角刻意只用 4px：万一某个 Resolve 版本对 QLineEdit 的
                  border-radius 不生效，输入框退化成直角，与 4px 按钮的
                  差异也很小，不会像 15px 胶囊那样一眼就崩。
               ② 两个输入框限宽 100~160px（qproperty-minimumWidth /
                  qproperty-maximumWidth 是 Qt 属性，Fusion 上最可靠；
                  min-width / max-width 是 CSS 兜底）。剩下的宽度交给
                  RowFindReplace 里的弹簧 Label，不再被 Weight=1 撑成一大片空。
               ③ 不设 min-height：v2.0.8 踩过 —— 最小高度加总超窗口可用
                  高度时 Qt 压不下去，缩小窗口就整片重叠。 */
            QLineEdit#FindInput, QLineEdit#ReplaceInput {
                border: 1px solid #585858;
                border-radius: 4px;
                background-color: #2a2a2a;
                color: #e0e0e0;
                padding: 2px 8px;
                qproperty-minimumWidth: 100;
                min-width: 100px;
                qproperty-maximumWidth: 160;
                max-width: 160px;
            }
            QPushButton#ReplaceOneBtn, QPushButton#ReplaceAllBtn,
            QPushButton#FindNextBtn, QPushButton#FindPrevBtn {
                border: 1px solid #585858;
                border-radius: 4px;
                background-color: #333333;
                color: #e0e0e0;
            }
            QPushButton#ReplaceOneBtn, QPushButton#ReplaceAllBtn {
                padding: 2px 12px;
            }
            QPushButton#ReplaceOneBtn:hover, QPushButton#ReplaceAllBtn:hover,
            QPushButton#FindNextBtn:hover, QPushButton#FindPrevBtn:hover {
                border: 1px solid #7a7a7a;
                background-color: #3d3d3d;
            }
            /* ★ v4.0.1：「▶ 下一处」压成窄窄的小图标（沿用），
               宽度仍由 qproperty-maximumWidth + max-width 双写到 26px，
               padding 归零、字号调小，让 ▶ 正好居中。 */
            QPushButton#FindNextBtn, QPushButton#FindPrevBtn {
                qproperty-maximumWidth: 26;
                max-width: 26px;
                padding: 0px;
                font-size: 11px;
            }
            /* ★ 时间码块（StatusTC）：只读 LineEdit，去掉边框和底色，
               让它看起来就是一行普通文字，但可以被选中复制。
               位置提示仍是 Label，保持"不可复制"的观感。 */
            QLineEdit#StatusTC {
                border: none;
                background: transparent;
                padding: 0px;
                /* ★ 时间码 00:00:00:00 共 10 个字符，宽度必须给足，
                   否则只读输入框内容为空时布局会把宽度压到几乎 0 而截断。
                   两条路都写，谁生效都行：
                   ① qproperty-minimumWidth —— 直接设 QWidget 的
                      minimumWidth 属性【最可靠】，Fusion 的 LineEdit
                      对纯 CSS 的 min-width 支持有限（92px 实测没撑开）；
                   ② min-width —— CSS 兜底。
                   另：等宽字体让数字对齐好读，且比比例字体更省宽度。 */
                qproperty-minimumWidth: 115;
                min-width: 115px;
                font-family: "Consolas", "Cascia Mono", monospace;
                font-size: 12px;
            }
            /* ★ v2.0.8：高频词行。和 StatusTC 一样去掉边框和底色，
               让它看着就是一行普通文字，但可以被选中复制
               （看到写错的词，复制进上面的纠错表最省事）。 */
            QLineEdit#HotEdit {
                border: none;
                background: transparent;
                padding: 0px;
                font-size: 11px;
            }
            /* ★ v2.0.9：版本号兼作诊断入口，必须长得像一行小字，
               不能像个按钮 —— 否则右下角突然多出个方块，很突兀。 */
            QPushButton#VerBtn {
                border: none;
                background: transparent;
                padding: 0px 2px;
                font-size: 11px;
            }
        ]],
    }, ui:VGroup{ ID = "Root", Spacing = 8,
        -- ★ v2.0.7：「AsrSubs」那行标题已按需求删除（窗口标题栏本来就有），
        --   时间线信息上移顶到第一行，省出的高度留给下面的纠错表。
        -- ★ v4.5.1：顶部一行 = 插件名 + 版本 + 当前时间线（原来只有"时间线：xxx"，
        --   版本号缩在右下角；合并后顶部信息一行说完，界面少一行、也更好认）。
        -- ★ v1.0.1：这行从 Label 改回【可点】的 Button（ID 沿用 VerBtn）。
        --   4.8 界面重构时 VerBtn 被删、版本号并进这行 Label，结果
        --   App:ShowDiagWin 的唯一入口跟着消失 —— 用户出问题时反而打不开诊断。
        --   QPushButton#VerBtn 的无边框透明小字样式仍在，看着还是普通一行字。
        ui:Button{ ID = "VerBtn",
                   Text = string.format("AsrSubs  v%s  ·  %s", VERSION, tostring(self.tlName)),
                   Weight = 0 },
        ui:VGap(4),

        -- ★ v4.0.9：字幕轨清单（Fusion UI 自绘，不依赖 Resolve 时间线 UI）。
        --   起因：Resolve 21.1 的官方 API 里【没有任何】轨道高度/折叠接口
        --   （.pyi 的 Timeline 类 72 个成员全查过，159 项工程设置也没有），
        --   字幕轨道头上也【不存在】折叠箭头（真机 5 个悬停点截图实证）。
        --   所以"缩最小"这条路走不通，改走 Fusion UI 路线：在插件面板里
        --   自绘一份轨道清单 —— 一眼知道有几条轨、哪条是插件的、哪条是空的，
        --   而且完全不碰时间线，天然不会遮挡视频轨。
        --   ★ Label 的宽度按【创建时】的文本算死（见 SelBar 那段注释），
        --     所以初始文本故意写足 3 条轨的宽度，后面改字才不会被裁掉。
        ui:HGroup{ ID = "RowTrackInfo", Weight = 0, Spacing = 6,
            ui:Label{ ID = "SubTrackInfo", Weight = 1,
                      Font = ui:Font{ PixelSize = 11 },
                      Text = "字幕轨 00 条：ST00(禁用·0000条)｜ST00(禁用·0000条)｜ST00(禁用·0000条)" },
            ui:Button{ ID = "RefreshTrackBtn", Text = "刷新", Weight = 0,
                       ToolTip = "重新读取当前时间线的字幕轨" },
            ui:Button{ ID = "CleanTrackBtn", Text = "清理空轨", Weight = 0,
                       ToolTip = "删除当前时间线上【空】的字幕轨（非空轨一律不动）" },
        },
        ui:VGap(4),

        -- ★ v4.7.1：上中下三段式【单窗口】布局
        --   上：控制区（开始/停止 + 可折叠的识别参数）
        --   中：左＝权重词编辑区，右＝字幕编辑区
        --   下：状态 + 动作按钮 + 热词（+ 可折叠日志）
        --   ★ 为什么不用弹窗：Fusion 的 UIDispatcher 上第二次 AddWindow 会静默失败
        --     （实测 winSet 为 nil），所以设置区改成「同一窗口内折叠」。
        ui:HGroup{ ID = "RowRun", Weight = 0, Spacing = 6,
            ui:Label{ ID = "SecCreate", Text = "从音频创建字幕", Weight = 0.5 },
            ui:Button{ ID = "StartBtn", Text = "开始生成", Weight = 1 },
            ui:Button{ ID = "StopBtn", Text = "停止", Weight = 1 },
        },
        -- 中：左右两栏
        ui:HGroup{ ID = "RowMid", Weight = 1, Spacing = 8,
            ui:VGroup{ ID = "LeftMid", Weight = 0.32, Spacing = 6,
                ui:Label{ ID = "CorrLabel", Text = "权重词纠错",
                          Font = ui:Font{ PixelSize = 11 }, Weight = 0 },
                ui:TextEdit{ ID = "CorrEdit", Weight = 0.72,
                             PlaceholderText = "人参=人生,人叁\n重庆=崇庆",
                             Events = { TextChanged = true } },
                -- ★ v4.8.1：热词并入左栏（原来单独占底部一行）
                ui:Label{ ID = "HotLabel", Text = "热词",
                          Font = ui:Font{ PixelSize = 11 }, Weight = 0 },
                ui:TextEdit{ ID = "HotEdit", Weight = 0.28, ReadOnly = true },
            },
            ui:VGroup{ ID = "RightMid", Weight = 0.68, Spacing = 6,
                ui:HGroup{ ID = "RowEditHead", Weight = 0,
                    ui:Label{ ID = "SecEdit", Text = "字幕编辑",
                              Font = ui:Font{ PixelSize = 13 }, Weight = 0.7 },
                    ui:Button{ ID = "LoadPoolBtn", Text = "媒体池字幕", Weight = 1 },
                    ui:Button{ ID = "LoadTrackBtn", Text = "时间线字幕", Weight = 1 },
                },
                ui:HGroup{ ID = "RowFindReplace", Weight = 0, Spacing = 6,
                    ui:LineEdit{ ID = "FindInput", PlaceholderText = "查找文本",
                                 Weight = 1, Events = { TextChanged = true } },
                    ui:Button{ ID = "FindNextBtn", Text = "▶", Weight = 0,
                               ToolTip = "下一处命中" },
                    ui:Label{ ID = "FindReplaceSpacer", Text = "", Weight = 1 },
                    ui:LineEdit{ ID = "ReplaceInput", PlaceholderText = "替换文本",
                                 Weight = 1, Events = { TextChanged = true } },
                    ui:Button{ ID = "ReplaceOneBtn", Text = "替换", Weight = 0 },
                    ui:Button{ ID = "ReplaceAllBtn", Text = "全部替换", Weight = 0 },
                },
                ui:HGroup{ ID = "SelBar", Weight = 0,
                    ui:CheckBox{ ID = "SelectAllChk", Text = "全选", Checked = false,
                                 Weight = 0 },
                    ui:Button{ ID = "InvertBtn", Text = "反选", Weight = 0 },
                    ui:Label{ ID = "HitLbl", Text = "命中 0000 行", Weight = 0 },
                    ui:Label{ ID = "SelCountLbl", Text = "已选 0000 / 0000 行", Weight = 0 },
                    ui:Label{ ID = "SelSpacer", Text = "", Weight = 1 },
                    ui:Button{ ID = "DelSelBtn", Text = "删除", Weight = 0 },
                },
                ui:Tree{ ID = "SubTree", ColumnCount = 3,
                         AlternatingRowColors = true, SortingEnabled = false,
                         WordWrap = true, UniformRowHeights = false,
                         HorizontalScrollMode = true,
                         SelectionMode = "ExtendedSelection",
                         Events = { ItemClicked = true,
                                   ItemDoubleClicked = true, ItemChanged = true,
                                   SelectionChanged = true },
                         Weight = 1 },
                ui:TextEdit{ ID = "SubEdit", Weight = 0.22,
                             Events = { TextChanged = true } },
                ui:HGroup{ ID = "RowStatus2", Weight = 0, Spacing = 6,
                    -- ★ v4.8.10：「删行即剪」放最左，紧贴左边缘
                    ui:CheckBox{ ID = "AutoCutChk", Text = "删行即剪", Weight = 0 },
                    ui:Label{ ID = "CutLbl", Text = "", Weight = 0 },
                    ui:Label{ ID = "CutSpacer", Text = "", Weight = 1 },
                    ui:Button{ ID = "UndoBtn", Text = "撤销", Weight = 0 },
                },
                -- ★ v4.8.9：四个动作按钮统一大小 + 整组右对齐（左边用弹簧撑开）
                ui:HGroup{ ID = "RowSave", Weight = 0, Spacing = 6,
                    ui:Label{ ID = "SaveSpacer", Text = "", Weight = 1 },
                    ui:Button{ ID = "UpdateSubsBtn", Text = "字幕更新", Weight = 1 },
                    ui:Button{ ID = "LandBtn", Text = "适配字幕", Weight = 1 },
                    ui:Button{ ID = "CutBtn", Text = "视频粗剪", Weight = 1 },
                    ui:Button{ ID = "TxtBtn", Text = "导出 TXT", Weight = 1 },
                },
            },
        },
        -- 下：状态 + 热词 + 诊断 + 可折叠日志
        ui:HGroup{ ID = "RowStatus", Weight = 0, Spacing = 6,
            ui:Label{ ID = "RowInfoLbl", Text = "第 0000 行 00:00:00:00 → 00:00:00:00 (0.00s)", Weight = 0 },
            ui:Label{ ID = "Status", Text = "就绪", Weight = 1 },
            ui:LineEdit{ ID = "StatusTC", Text = "", ReadOnly = true, Weight = 0.35 },
            ui:Button{ ID = "SetupBtn", Text = "设置", Weight = 0 },
        },
    })

    if not win then
        ELog("!! AddWindow 返回空")
        return
    end
    App.S.win = win
    App.S.ui = ui
    -- ★ 给窗口一个最小尺寸：拖得再小也不会把控件挤到重叠。
    --   （之前是靠在 StyleSheet 里给控件设 min-height 防重叠，结果反过来
    --     把布局撑爆 —— 现在改成限制【窗口】本身的最小尺寸，方向才对。）
    pcall(function() win.MinimumSize = { 760, 570 } end)
    ELog("窗口已创建")

    -- ★ 必须用 GetItems() 取控件，不能用 win.StartBtn
    local okItems, items = pcall(function() return win:GetItems() end)
    if not okItems or not items then
        ELog("!! GetItems 失败: " .. tostring(items))
        return
    end
    App.S.items = items
    ELog("GetItems 成功")

    -- ★ v4.8.0：参数控件已移入「设置」弹窗，主窗口里没有它们。
    --   用保存的设置给 uiSnap 播种 —— 各处读取都是「控件优先、读不到用快照」，
    --   所以没打开过设置也能正确识别。
    pcall(function()
        App.S.uiSnap = App.S.uiSnap or {}
        App.S.uiSnap.max = tostring(cfg.max_chars or DEFAULT_MAX_CHARS)
        App.S.uiSnap.corr = cfg.corrections or ""
        App.S.uiSnap.model = cfg.model_path or ""
        App.S.uiSnap.punct = cfg.end_punct and true or false
        App.S.uiSnap.punctAll = cfg.all_punct and true or false
    end)

    -- ★ v4.7.1：启动时把「识别参数」和「日志」折叠起来（主界面只留高频内容）
    pcall(function()
        App.S.setOpen = false
        for _, id in ipairs({ "LangLabel", "LangCombo", "ModelLabel", "ModelCombo",
                              "PathLabel", "ModelPathEdit", "ModelBrowseBtn",
                              "MaxLabel", "MaxEdit", "AllPunct", "NoPunct" }) do
            local o = items[id]
            if o then pcall(function() o.Visible = false end) end
        end
        if items.LogEdit then
            pcall(function() items.LogEdit.Visible = false end)
            pcall(function() items.LogEdit.Weight = 0 end)
        end
    end)

    -- ★ v4.0.9：开窗即显示字幕轨清单（不写日志，保持界面干净）。
    --   pcall 包住：轨道信息读不出来也不该影响主流程。
    pcall(function() self:RefreshSubTrackInfo(false) end)

    -- ★ v2.0.9：原来这里会把「诊断包」按钮先藏起来（v2.0.7 的做法）。
    --   现在按钮没了，入口是右下角的版本号，它本来就一直可见 —— 不用藏。
    --   出错时由 App:ShowDiag() 给版本号加上 ⚠ 提示。

    -- ★ v2.0.8：高频词框设成只读。★ 不写在 AddWindow 的控件表里 ——
    --   ReadOnly 不是每个 Resolve 版本都认，写错属性会让整个 AddWindow
    --   抛错、界面直接起不来。放在这里 pcall，认就认、不认就退化成可编辑，
    --   反正 App:RefreshHotwords 每次都是整行覆盖，用户手改也留不住。
    do
        local okR = pcall(function() items.HotEdit.ReadOnly = true end)
        ELog("高频词框只读 ok=" .. tostring(okR))
    end

    -- 环境自检结果写进界面日志（现在 Log 已经能用了）
    --   路径没问题时只说一句；有问题就把三处路径全列出来，方便对照。
    do
        local bad = App.S.pathBad or {}
        if #bad > 0 then
            self:Log("环境自检：有 " .. #bad .. " 处路径不对")
            for _, s in ipairs(bad) do self:Log("  · " .. s) end
            self:Log("  · 工具被移动过？请确认插件与引擎目录的相对位置没变")
        else
            for _, s in ipairs(App.S.pathReport or {}) do
                ELog("    " .. s)
            end
        end
    end

    -- ★ AddItems 必须传【表格】，不能逐个传字符串（官方用法）
    local langNames = {}
    for _, it in ipairs(LANGUAGES) do table.insert(langNames, it[1]) end
    local modelList = readModels()
    local modelNames = pickModelNames(modelList)

    -- ★ v4.8.24：这里原来有一段「启动阶段填充下拉」的 pcall，每次都必然报错：
    --   参数控件 v4.8.0 起全在设置弹窗里，启动时 App.S.setItems 恒为 nil，
    --   代码自己 error("参数控件在设置弹窗里，启动阶段跳过")，于是每开一次
    --   窗口就往日志写一条「下拉项填充 ok=false」。控件填充本来就在
    --   App.EnsureSettings 里做，这段是死代码，删掉。
    --   保留模型清单快照这一行：清单缺失时能一眼看出来。
    if #modelNames == 0 then
        ELog("可选模型: (清单为空) " .. MODEL_MANIFEST_LUA
             .. " 不存在或为空 —— 首次识别后会自动生成，也可重跑安装器")
    else
        ELog("可选模型: " .. table.concat(modelNames, ", "))
    end
    -- ★ v4.8.0：设置弹窗 —— 点击时创建（与「关于」同机制；启动阶段建第二个
    --   窗口会返回空，这是唯一可行时机）。改动即时写回 App.S.cfg + uiSnap。
    App.EnsureSettings = function()
        if App.S.winSet then return App.S.winSet end
        local w = nil
        -- ★ v4.8.2：记住弹窗大小/位置
        local geo = { 0, 0, 780, 646 }
        pcall(function()
            local fh = io.open(PROBE_WORK .. "\\set_win.txt", "r")
            if fh then
                local s = fh:read("*a") or ""; fh:close()
                local a, b, cx, cy = tostring(s):match("(-?%d+),(-?%d+),(%d+),(%d+)")
                -- ★ v4.8.14：只挡明显非法的值。之前要求 >=560x380，把用户拖小后的 428x538 也判非法，于是"记不住大小"。
                local A, B, CX, CY = tonumber(a), tonumber(b), tonumber(cx), tonumber(cy)
                if A and B and CX and CY and CX >= 320 and CY >= 260 then
                    geo = { A, B, CX, CY }
                    ELog(("[set] 恢复窗口几何 %d,%d,%d,%d"):format(A, B, CX, CY))
                else
                    ELog("[set] 几何文件数值非法，用默认：" .. tostring(s))
                end
            end
        end)
        local okW, errW = pcall(function()
            w = dispatcher:AddWindow({
                ID = "AsrSubsSettings",
                WindowTitle = "设置",
                Geometry = geo,
            }, ui:VGroup{ ID = "SetRoot", Spacing = 8,
                ui:Label{ ID = "SetHead", Text = "识别参数",
                          Font = ui:Font{ PixelSize = 13 }, Weight = 0 },
                ui:HGroup{ ID = "RowLang", Weight = 0,
                    ui:Label{ ID = "LangLabel", Text = "语言", Weight = 0.2 },
                    ui:ComboBox{ ID = "LangCombo", Weight = 0.8 } },
                ui:HGroup{ ID = "RowModel", Weight = 0,
                    ui:Label{ ID = "ModelLabel", Text = "识别模型", Weight = 0.2 },
                    ui:ComboBox{ ID = "ModelCombo", Weight = 0.8 } },
                ui:HGroup{ ID = "RowPath", Weight = 0,
                    ui:Label{ ID = "PathLabel", Text = "模型路径", Weight = 0.2 },
                    ui:LineEdit{ ID = "ModelPathEdit", Weight = 0.7 },
                    ui:Button{ ID = "ModelBrowseBtn", Text = "浏览…", Weight = 0 } },
                ui:HGroup{ ID = "RowMax", Weight = 0,
                    ui:Label{ ID = "MaxLabel", Text = "每行字数", Weight = 0.2 },
                    ui:LineEdit{ ID = "MaxEdit", Weight = 0.2 },
                    ui:Label{ ID = "SetGap", Text = "", Weight = 0.6 } },
                ui:HGroup{ ID = "RowOpt", Weight = 0,
                    ui:CheckBox{ ID = "AllPunct", Text = "去除标点符号", Weight = 1 },
                    ui:CheckBox{ ID = "NoPunct", Text = "句末无标点", Weight = 1 },
                    ui:CheckBox{ ID = "SplitPause", Text = "长停顿断行", Weight = 1 } },
                -- ★ v1.0.1：素材旁那份同名 srt 以前是强制写的（有的素材目录
                --   是只读/NAS/同步盘），给一个开关，默认仍然开着。
                ui:HGroup{ ID = "RowOpt2", Weight = 0,
                    ui:CheckBox{ ID = "SidecarChk", Text = "在素材旁生成同名 SRT", Weight = 1 },
                    ui:Label{ ID = "SidecarHint", Text = "（关掉后只留在 data\\output）", Weight = 1 } },
                ui:HGroup{ ID = "RowLogHead", Weight = 0,
                    ui:Label{ ID = "LogHead", Text = "运行日志",
                              Font = ui:Font{ PixelSize = 13 }, Weight = 1 },
                    ui:Button{ ID = "LogClearBtn", Text = "清空", Weight = 0 } },
                ui:TextEdit{ ID = "LogEdit", ReadOnly = true, Weight = 1 },
                ui:HGroup{ ID = "RowSetClose", Weight = 0,
                    ui:Label{ ID = "SetHint", Text = "改完直接关闭，参数即时生效", Weight = 1 },
                    ui:Button{ ID = "SetCloseBtn", Text = "关闭", Weight = 0 } },
            })
        end)
        if not okW or not w then
            ELog("!! 设置窗口创建失败: " .. tostring(errW))
            return nil
        end
        local okS, itS = pcall(function() return w:GetItems() end)
        if not okS or not itS then return nil end
        App.S.winSet = w
        App.S.setItems = itS
        pcall(function()
            itS.LangCombo:AddItems(langNames)
            itS.ModelCombo:AddItems(modelNames)
            local c = App.S.cfg or {}
            itS.LangCombo.CurrentIndex = tonumber(c.lang) or 0
            itS.ModelCombo.CurrentIndex = tonumber(c.model) or 0
            ctlSet(itS.ModelPathEdit, tostring(c.model_path or ""))
            ctlSet(itS.MaxEdit, tostring(c.max_chars or DEFAULT_MAX_CHARS))
            itS.AllPunct.Checked = c.all_punct and true or false
            itS.NoPunct.Checked = c.end_punct and true or false
            itS.SplitPause.Checked = c.split_pause and true or false
            -- ★ v1.0.1：素材旁 srt 开关（缺省 true —— 老用户升级后行为不变）
            pcall(function()
                itS.SidecarChk.Checked = (c.sidecar ~= false)
            end)
        end)
        local function saveGeo()
            pcall(function()
                local g = w.Geometry
                if type(g) == "table" and #g == 4 then
                    local fh = io.open(PROBE_WORK .. "\\set_win.txt", "w")
                    if fh then
                        fh:write(string.format("%d,%d,%d,%d", g[1], g[2], g[3], g[4]))
                        fh:close()
                    end
                end
            end)
        end
        App.SaveSetGeo = saveGeo

        -- ★ v4.8.4：读只读/输入框的稳妥封装（弹窗控件有时读 Text 为空，退回 PlainText）
        local function readLine(item)
            if not item then return nil end
            local v = nil
            pcall(function() v = item.Text end)
            if v == nil or v == "" then
                pcall(function() v = item.PlainText end)
            end
            return v
        end
        local function syncSet()
            local c = App.S.cfg or {}
            pcall(function()
                c.lang = tonumber(itS.LangCombo.CurrentIndex) or 0
                c.model = tonumber(itS.ModelCombo.CurrentIndex) or 0
                local mp = readLine(itS.ModelPathEdit)
                if mp ~= nil then
                    c.model_path = tostring(mp):gsub("^%s+", ""):gsub("%s+$", "")
                end
                local mc = tonumber(readLine(itS.MaxEdit) or "")
                if mc and mc >= 4 and mc <= 200 then c.max_chars = math.floor(mc) end
                c.all_punct = itS.AllPunct.Checked and true or false
                c.end_punct = itS.NoPunct.Checked and true or false
                c.split_pause = itS.SplitPause.Checked and true or false
                -- ★ v1.0.1：素材旁 srt 开关
                pcall(function()
                    c.sidecar = itS.SidecarChk.Checked and true or false
                end)
            end)
            App.S.cfg = c
            App.S.uiSnap = App.S.uiSnap or {}
            App.S.uiSnap.max = tostring(c.max_chars or DEFAULT_MAX_CHARS)
            App.S.uiSnap.corr = c.corrections or ""
            App.S.uiSnap.model = c.model_path or ""
            App.S.uiSnap.punct = c.end_punct and true or false
            App.S.uiSnap.punctAll = c.all_punct and true or false
            pcall(function() App:SaveSettingsNow() end)
        end
        pcall(function() w.On.LangCombo.CurrentIndexChanged = function() syncSet() end end)
        pcall(function() w.On.ModelCombo.CurrentIndexChanged = function() syncSet() end end)
        pcall(function() w.On.ModelPathEdit.TextChanged = function() syncSet() end end)
        pcall(function() w.On.MaxEdit.TextChanged = function() syncSet() end end)
        pcall(function() w.On.AllPunct.Clicked = function() syncSet() end end)
        pcall(function() w.On.NoPunct.Clicked = function() syncSet() end end)
        pcall(function() w.On.SplitPause.Clicked = function() syncSet() end end)
        -- ★ v1.0.1：素材旁 srt 开关
        pcall(function() w.On.SidecarChk.Clicked = function() syncSet() end end)
        pcall(function()
            w.On.ModelBrowseBtn.Clicked = function()
                pcall(function()
                    local fu = App.resolve and App.resolve:Fusion() or nil
                    if not fu or type(fu.RequestDir) ~= "function" then return end
                    local okD, dd = pcall(function() return fu:RequestDir(MODELS_DIR) end)
                    if okD and dd then
                        if type(dd) == "table" then dd = dd[1] end
                        if dd and tostring(dd) ~= "" then
                            ctlSet(itS.ModelPathEdit, tostring(dd))
                            syncSet()
                        end
                    end
                end)
            end
        end)
        pcall(function()
            w.On["AsrSubsSettings"].Close = function() pcall(syncSet); saveGeo(); pcall(function() w:Hide() end) end
        end)
        pcall(function()
            w.On.SetCloseBtn.Clicked = function()
                syncSet(); saveGeo()
                pcall(function() w:Hide() end)
            end
        end)
        pcall(function()
            w.On.LogClearBtn.Clicked = function()
                App.S.logLines = {}
                pcall(function() App:PaintLogNow() end)
            end
        end)
        pcall(function() w:Hide() end)
        return w
    end


    -- ★★ 第二道保险：用【变更事件】备份用户输入。
    --   万一某种 Resolve 版本连 Text / PlainText 都读不出来（这里是真的踩过：
    --   字数选项一直 silently 用默认的 12），只要用户在框里打过字，我们就
    --   把它记进 uiSnap —— 控件真值读不出来时才用这份数据。
    --   整体包 pcall：不支持的事件绑不上也只是没这份备份，不影响其它功能。
    --
    --   ★★ 更正（v1.28）：老写法是 `function(_, ...) snatch(k, ...)`，指望
    --      事件把新文本当参数带过来 —— 但官方文档写得很清楚：事件回调只收到
    --      一个属性字典（who / what / when / sender / modifiers），
    --      TextChanged【不带文本字段】。所以 select(1, ...) 恒为 nil，
    --      这份"第二道保险"从来没生效过，是死代码。
    --      要备份只能在这里【回读控件本身】。
    App.S.uiSnap = { max = "", corr = "", model = "", gap = nil, punct = nil,
                     punctAll = nil }
    pcall(function()
        local function snatch(key, item)
            local v = ctlGet(item)
            if type(v) == "string" then
                App.S.uiSnap[key] = v
            end
        end
        local okEv = pcall(function()
            win.On.MaxEdit.TextChanged = function() snatch("max", App.S.items.MaxEdit) end
            win.On.CorrEdit.TextChanged = function() snatch("corr", App.S.items.CorrEdit) end
            win.On.ModelPathEdit.TextChanged = function() snatch("model", App.S.items.ModelPathEdit) end
        end)
        ELog("输入框 TextChanged 绑定 ok=" .. tostring(okEv))

        -- CheckBox 的 Toggled 可能把勾选状态作为参数传进来；不同版本参数
        -- 位置不一样，干脆遍历一遍找一个 boolean。
        local function boolArg(...)
            for i = 1, select("#", ...) do
                local v = select(i, ...)
                if type(v) == "boolean" then return v end
            end
            return nil
        end
        local okTg = pcall(function()
            -- ★ v4.0.0：「字幕无间隙」删除 → 快照 gap 移除；「句末无标点」回归 → 快照 punct
            win.On.NoPunct.Toggled = function(_, ...)
                App.S.uiSnap.punct = boolArg(...)
            end
            win.On.AllPunct.Toggled = function(_, ...)
                App.S.uiSnap.punctAll = boolArg(...)
            end
        end)
        ELog("勾选框 Toggled 绑定 ok=" .. tostring(okTg))
    end)

    -- ★ 应用上次保存的设置（值已在 readSettings 里校验过类型/范围）。
    --   放在 uiSnap 建立之后：备份表也要同步，否则"读不回控件"的版本
    --   会把界面值当成空，点开始时又跳回默认值。
    pcall(function()
        if cfg.lang >= 0 and cfg.lang < #LANGUAGES then
            App.S.items.LangCombo.CurrentIndex = cfg.lang
        end
        if cfg.model >= 0 and cfg.model < #modelNames then
            App.S.items.ModelCombo.CurrentIndex = cfg.model
        end
        if cfg.model_path ~= "" then
            ctlSet(App.S.items.ModelPathEdit, cfg.model_path)
        end
        ctlSet(App.S.items.MaxEdit, tostring(cfg.max_chars))
        ctlSet(App.S.items.CorrEdit, cfg.corrections)
        -- ★ v2.0.2：时间微调的持久化随控件一并移除，这里不再回填。
        -- ★★ 回填勾选框时【绝不能同步写 uiSnap】—— 这是 v1.28 修掉的真 bug：
        --    旧代码在这里写了 uiSnap.punct / uiSnap.punctAll，而点「开始」时
        --    snap 又【无条件覆盖】控件真值，结果：开窗回填的瞬间快照就被
        --    钉死成 false，用户之后手动勾选就「勾了没效果」。
        --    现在：uiSnap 只由 Toggled 事件写，回填只动控件，快照不碰。
        setChecked(App.S.items.AllPunct, cfg.all_punct)
        setChecked(App.S.items.NoPunct, cfg.end_punct)
        App.S.uiSnap.max = tostring(cfg.max_chars)
        App.S.uiSnap.corr = cfg.corrections
        App.S.uiSnap.model = cfg.model_path
        -- punct / punctAll 故意不在这里写：让 Toggled 成为唯一写入源。
        ELog(string.format(
            "已恢复设置: lang=%d model=%d path=%q max=%d all_punct=%s end_punct=%s",
            cfg.lang, cfg.model, cfg.model_path, cfg.max_chars,
            tostring(cfg.all_punct), tostring(cfg.end_punct)))
    end)

    -- ★ 按钮初始状态必须在拿到控件后显式设置
    --   （写在构造函数里的 Enabled 不可靠，实测会显示反）
    ELog("Enabled 类型检查: StartBtn.Enabled=" .. type(items.StartBtn and items.StartBtn.Enabled)
        .. " StopBtn.Enabled=" .. type(items.StopBtn and items.StopBtn.Enabled))

    -- ★★ v1.2.0：开窗不再无条件放开「开始生成」，改由 CanStart 判。
    --   以前写死 = true：时间线是空的 / 环境缺脚本时按钮照样亮，点下去
    --   要么弹一句"已有任务进行中"，要么干等到超时才报错。
    pcall(function() self:RefreshStartBtn() end)
    pcall(function() items.StopBtn.Enabled = false end)
    pcall(function() items.LandBtn.Enabled = false end)
    -- ★ v3.2.6：还没删任何行时没有可切的区间，先禁用（RefreshCutBar 会再看一次）
    pcall(function() items.CutBtn.Enabled = false end)
    -- 导出按钮：还没识别出字幕时点了只会报错，先禁用
    pcall(function() items.TxtBtn.Enabled = false end)
    ELog("按钮状态 Start=" .. tostring(items.StartBtn and items.StartBtn.Enabled)
        .. " Stop=" .. tostring(items.StopBtn and items.StopBtn.Enabled))

    -- 事件绑定：注意是 .Clicked（不是 .Click）
    -- 取值方式全部照官方：CurrentIndex / PlainText / Checked
    -- ★ v2.0.1：「关于」按钮与 ShowAbout 弹窗已整项删除，此处不再绑定。

    -- ★ v4.7.1：点「诊断信息」= 打开诊断窗口（主界面底栏右侧）
    --   pcall 包住：诊断窗口只是个辅助功能，它挂了不能让主界面的事件绑定跟着断。
    win.On.VerBtn.Clicked = function()
        local okD, errD = pcall(function() App:ShowDiagWin() end)
        if not okD then
            ELog("诊断窗口异常: " .. tostring(errD))
            self:Log("错误：诊断窗口打不开（" .. tostring(errD) .. "）")
        end
    end

    -- 「浏览…」选模型文件夹
    --   ★ 用 Fusion 自带的 RequestDir：跟导出功能一样，不走 os.execute，
    --     所以不会闪黑窗。选完直接写回输入框，并同步到 uiSnap ——
    --     否则万一 Resolve 的 LineEdit 读不回值，这次选择就白做了。
    win.On.ModelBrowseBtn.Clicked = function()
            local okB, errB = pcall(function()
            local fu = App.resolve and App.resolve:Fusion() or nil
            if not fu then
                App:Log("取不到 Fusion，打不开选择框 —— 请手动粘贴路径。")
                return
            end
            if type(fu.RequestDir) ~= "function" then
                App:Log("当前 Resolve 不支持选择文件夹 —— 请手动粘贴路径。")
                return
            end
            local okD, d = pcall(function()
                return fu:RequestDir(MODELS_DIR)
            end)
            if not okD then
                App:Log("打开选择框失败：" .. tostring(d))
                return
            end
            -- 不同版本可能返回字符串或 {路径}
            if type(d) == "table" then d = d[1] end
            d = tostring(d or "")
            if d == "" then return end

            ctlSet(App.S.items.ModelPathEdit, d)
            -- ★ v1.1.2：路径框也在设置弹窗里，主窗口那个是 nil（选了没反应）
            pcall(function() ctlSet(App.S.setItems and App.S.setItems.ModelPathEdit, d) end)
            App.S.uiSnap = App.S.uiSnap or {}
            App.S.uiSnap.model = d
            App:Log("自定义模型：" .. d)
        end)
        if not okB then ELog("浏览按钮异常: " .. tostring(errB)) end
    end

    win.On.StartBtn.Clicked = function()
      -- ★ 包一层 pcall：事件处理里的错误会被 Resolve 静默吞掉，必须自己抓
      local okClick, clickErr = pcall(function()
        -- ★ v4.8.23：有未写回 SRT 的修改时先问一句。Start 会删掉旧字幕文件
        --   重新识别，没点「字幕更新」的编辑会跟着没 —— 粗剪那边早有这道闸，
        --   这里原来没有。
        if App.S.subsDirty then
          local go = self:Confirm("字幕表里有还没写回 SRT 的修改。\n重新识别会删掉旧字幕文件重来，这些修改会丢失。\n\n继续？")
          if not go then
            self:Log("已取消：先点「字幕更新」写回，或继续编辑")
            return
          end
        end
        -- ★★ v1.2.0：点下来时再确认一次前置条件。
        --   按钮状态由 Tick 每 3 秒刷新一次，中间最多有 3 秒的窗口 ——
        --   用户可能刚好在这段时间里关掉了时间线。这里再判一次，避免
        --   "按钮还是亮的，点下去却先渲染 20 秒再报错"。
        local okGo, whyGo = self:CanStart()
        if not okGo then
          self:Status("无法开始：" .. tostring(whyGo))
          self:Log("无法开始：" .. tostring(whyGo))
          pcall(function() self:RefreshStartBtn() end)
          return
        end
        local it = items

        -- 语言（ComboBox → CurrentIndex，0 基）
        local li = tonumber((App.S.setItems and App.S.setItems.LangCombo and App.S.setItems.LangCombo.CurrentIndex) or (App.S.cfg and App.S.cfg.lang) or 0) or 0
        local lang = "Chinese"
        if LANGUAGES[li + 1] then lang = LANGUAGES[li + 1][2] end

        -- 每行最多字数：手动填写，非法则回到 12
        -- ★ 取值优先级【已改为控件优先】（v1.28 热修）：
        --   先直接读输入框里的真实内容，读不到再退回变更事件备份，
        --   最后才是默认 24。
        --   旧写法是"快照优先"，和勾选框踩的是同一个坑：开窗回填时
        --   把快照写成 24，之后只要快照非空就一直压住控件真值 ——
        --   用户在框里改成 8，读到的却仍是 24，"字数限制没生效"。
        --   注：Resolve 21.1 的 LineEdit 读 PlainText 是 nil，
        --   ctlGet 内部已做 Text/PlainText 多路兜底，这里直接用它。
        local snap = App.S.uiSnap or {}
        -- ★ v4.8.4：字数控件在设置弹窗里，主窗口读不到；一律以 App.S.cfg 为准
        local mcTxt, mcSrc = nil, "cfg"
        if App.S.cfg and App.S.cfg.max_chars then
            mcTxt = tostring(App.S.cfg.max_chars)
        end
        if not mcTxt or mcTxt == "" then
            mcTxt, mcSrc = snap.max, "uiSnap(max)"
        end
        local mc = tonumber(mcTxt)
        if not mc or mc < 4 then
            mc = tonumber(snap.max) or DEFAULT_MAX_CHARS
        end
        if mc < 4 then mc = DEFAULT_MAX_CHARS end
        if mc > 200 then mc = 200 end
        ELog(("MaxEdit 读取 via=%s raw=%q -> %d"):format(
            tostring(mcSrc), tostring(mcTxt), mc))

        -- 模型：自定义路径优先；否则用下拉里选中的那个
        -- ★★ 取值优先级【已改为控件优先】（v1.28 热修，和短语框同一批）：
        --    旧写法 snap 优先 —— 而 snap.model 在开窗回填时就被写成上次
        --    保存的值（见「已恢复设置」那段），之后只要非空就永远压住控件，
        --    用户改了「自定义模型路径」也不生效，得关掉工具重开才行。
        --    现在：控件读得出来就用控件的（空串 = 用户主动清空 → 走下拉框
        --    分支，语义正确），只有读不出来才退回快照。
        local customTxt, customSrc = ctlGet(App.S.items.ModelPathEdit)
        local custom = (customTxt ~= nil) and customTxt or (snap.model or "")
        if type(custom) ~= "string" then custom = "" end
        ELog(("ModelPathEdit 读取 via=%s raw=%q"):format(
            tostring(customSrc), custom))
        custom = custom:gsub("^%s+", ""):gsub("%s+$", "")
        local modelPath = custom
        if modelPath == "" then
            local mi = tonumber((App.S.setItems and App.S.setItems.ModelCombo and App.S.setItems.ModelCombo.CurrentIndex) or (App.S.cfg and App.S.cfg.model) or 0) or 0
            local models = readModels()
            local m = models[mi + 1] or models[1]
            local name = (m and m.name) or DEFAULT_MODEL
            -- ★ 清单里的 path 可能是【旧机器/旧布局】留下的绝对路径
            --   （payload 自带的 models.lua 就写着 D:\AsrSubs\models\...）。
            --   装到新位置后照抄它，配置里就存了一份不存在的路径：
            --   界面回显看着对，引擎静默回退默认模型 —— 又是"看着生效其实没有"。
            --   所以先认当前 MODELS_DIR 下的同名目录，再考虑清单给的老路径。
            local localPath = MODELS_DIR .. "\\" .. name
            if exists(localPath) then
                modelPath = localPath
            else
                modelPath = (m and m.path) or localPath
            end
        end

        -- 权重词纠错表（v2.0.7 取代原来的「短语列表 / 提示」）
        -- ★★ 取值优先级【控件优先】—— 和 v1.28 修过的"快照优先"是同一类坑：
        --    快照在开窗回填时就被写成上次保存的值，之后永远非空 ⇒ 用户改了
        --    表也不生效，得关掉工具重开才行。
        --    现在：控件读得出来就用控件的（空串 = 用户主动清空，同样要尊重，
        --    否则规则删不掉），只有控件读不出来（unreadable）才退回快照。
        local crTxt, crSrc = ctlGet(App.S.items.CorrEdit)
        local corr = (crTxt ~= nil) and crTxt or (snap.corr or "")
        if type(corr) ~= "string" then corr = "" end
        ELog(("CorrEdit 读取 via=%s 长度=%d"):format(tostring(crSrc), #corr))

        -- 勾选框（CheckBox → Checked，兜底 CheckState；再兜底 Toggled 备份）
        -- ★★ 优先级已反转（v1.28 修复）：【控件真值优先，快照只做兜底】。
        --    旧写法是"只要 snap 非 nil 就无条件覆盖控件"，而开窗回填时
        --    又正好写了快照 ⇒ 用户勾了框也读到 false（日志实证 false(Checked)）。
        --    现在语义：只有控件【读不出来】（unreadable）时才退回快照，
        --    这样手动勾选的结果永远不会被陈旧的快照盖掉。
        -- ★ v4.0.0：「句末无标点」回归 —— 从 NoPunct 勾选框读取。
        --   引擎侧参数一直是 no_punct（trim_punct），只是之前界面没暴露它。
        --   「字幕无间隙」已删除：识别流程的 no_gap 固定 false（见下方 Start 传参）。
        -- ★ v1.1.2：勾选框在【设置弹窗】里，主窗口没有 → 必须读 setItems，
        --   读不到（从没打开过设置）就用已保存的 cfg。原来读 App.S.items 恒
        --   nil，"去除标点符号/句末无标点"勾了等于没勾（日志里 punct=false(nil-item)）。
        local np, npSrc = ctlChecked(App.S.setItems and App.S.setItems.NoPunct)
        if npSrc == "nil-item" or npSrc == "unreadable" then
            np, npSrc = (App.S.cfg and App.S.cfg.end_punct) and true or false, "cfg"
        end
        local na, naSrc = ctlChecked(App.S.setItems and App.S.setItems.AllPunct)
        if naSrc == "nil-item" or naSrc == "unreadable" then
            na, naSrc = (App.S.cfg and App.S.cfg.all_punct) and true or false, "cfg"
        end

        -- ★ "去除标点符号"包含"句末无标点"：两个都勾就按"全部"处理，
        --   在引擎里也是 all 优先（不然会出现"两个开关互相打架"的观感）。
        local pMode = "关"
        if na then
            pMode = "全部"
        elseif np then
            pMode = "句末"
        end
        ELog(("勾选读取 punct=%s(%s) all=%s(%s) -> %s"):format(
            tostring(np), tostring(npSrc), tostring(na), tostring(naSrc), pMode))

        ELog(("点击开始: lang=%s max=%d punct=%s all=%s model=%s")
            :format(lang, mc, tostring(np), tostring(na), modelPath))

        -- 设置持久化：点「开始」是一次自然提交点（值都是刚读准的）
        self:SaveSettingsNow()

        -- ★ v4.0.0：字幕无间隙功能已删除，Start 去掉 noGap 参数；
        --   noPunct 接回界面勾选值；引擎 args 仍写 no_gap（恒 false）/ no_punct 两个字段。
        self:Start(lang, corr, mc, np, modelPath, na)

      end)

      if not okClick then
        ELog("!! 开始按钮异常: " .. tostring(clickErr))
        self:Log("错误：" .. tostring(clickErr))
        self:Status("出错，详见日志")
        -- ★ v1.2.0：相位必须先落定，再刷按钮 —— 反过来的话 RefreshStartBtn
        --   会按"上一拍的相位"判，把刚刚失败的界面又点亮成可点。
        App.S.phase = "error"
        pcall(function() self:RefreshStartBtn() end)
        pcall(function() items.StopBtn.Enabled = false end)
      end
    end

    win.On.LandBtn.Clicked = function()
        local okClick, clickErr = pcall(function()
            self:DoLand()
        end)
        if not okClick then
            ELog("!! 适配字幕按钮异常: " .. tostring(clickErr))
            self:Log("错误：" .. tostring(clickErr))
            self:Status("适配字幕出错，详见日志")
        end
    end

    -- ★ v3.2.6：粗剪。点下去先弹确认（段数/秒数/代价），确认后才真动时间线。
    win.On.CutBtn.Clicked = function()
        local okClick, clickErr = pcall(function()
            self:DoCut()
        end)
        if not okClick then
            ELog("!! 视频粗剪按钮异常: " .. tostring(clickErr))
            self:Log("错误：" .. tostring(clickErr))
            self:Status("视频粗剪出错，详见日志")
        end
    end

    win.On.TxtBtn.Clicked = function()
        local okClick, clickErr = pcall(function()
            self:StartExport({ "txt" })
        end)
        if not okClick then
            ELog("!! 导出TXT异常: " .. tostring(clickErr))
            self:Log("错误：" .. tostring(clickErr))
        end
    end

    win.On.StopBtn.Clicked = function()
        -- ★ v4.8.25：点「停止」只停当前任务，保留预热好的常驻识别服务 ——
        --   以前会连服务一起杀，下次识别又得重新加载模型（约 12 秒）。
        -- ★ v1.0.1：提示语按【当前阶段】说。以前不管停在渲染/落轨/导出，
        --   一律说"识别模型保持加载，下次更快" —— 那些阶段根本没有模型。
        local ph = App.S.phase
        self:Cleanup(true)
        if ph == "recognizing" then
            self:Status("已停止（识别模型保持加载，下次更快）")
        elseif ph == "rendering" or ph == "render_cfg" then
            self:Status("已停止（渲染设置已还原）")
        elseif ph == "landing" then
            self:Status("已停止（请确认字幕轨上没有多出半批条目）")
        elseif ph == "cutting" then
            self:Status("已停止（时间线可能已改动，可用 Ctrl+Z 退回）")
        elseif ph == "exporting" then
            self:Status("已停止（已复制出去的文件会保留）")
        else
            self:Status("已停止")
        end
    end

    -- ── 字幕编辑器事件 ──────────────────────────────────────────
    -- ★★★ 本项目【第二大坑】：事件回调只传【一个 ev 表】，没有额外不定参数。
    --   之前写成 function(_, ...) 靠遍历 select("#", ...) 取行号 ——
    --   实际传进来 0 个额外参数，循环 0 次，行号恒为 nil。
    --   后果：不只是双击没反应，【连单击选中都是坏的】——
    --   表格点不动 → selRow 永远为空 → 下方编辑框拿不到内容，
    --   表现出"那个能打字的框没什么作用"。
    --   正确写法（参考 DaVinci ASR）：在回调里取 tree:CurrentItem()，
    --   再反查它的行号（★ v3.2.0：# 行号列撤掉后，行号存在不显示的
    --   Text[3] 槽位里，见下面的 itemToRow）
    -- ★ v3.2.0：「#」行号列撤掉后，行号改从不可见的 Text[3] 槽位读。
    --   万一那个槽位读不回来（不同版本行为不一），退回 Qt 的
    --   IndexOfTopLevelItem（已实测本机存在这个方法）拿 0 基下标 +1。
    local function itemToRow(item)
        if not item then return nil end
        local idx = nil
        pcall(function() idx = tonumber(tostring(item.Text[3])) end)
        if idx and App.S.subs[idx] then return idx end
        local zero = nil
        local tree = App.S.items and App.S.items.SubTree
        pcall(function() zero = tree:IndexOfTopLevelItem(item) end)
        if type(zero) == "number" and zero >= 0 and App.S.subs[zero + 1] then
            return zero + 1
        end
        return nil
    end

    local function rowFromTree()
        local items = App.S.items
        if not items or not items.SubTree then return nil end
        local item = nil
        pcall(function() item = items.SubTree:CurrentItem() end)
        return itemToRow(item)
    end

    -- ★ 全部包 pcall：不同 Resolve 版本的事件签名不完全一致，
    --   绑不上只影响对应功能，不能让整个窗口起不来。
    pcall(function()
        win.On.SubTree.ItemClicked = function(ev)
            local row = rowFromTree()
            ELog("[click] row=" .. tostring(row) .. " subs=" .. tostring(#(App.S.subs or {})))
            if row then
                App.S.selRow = row
                -- 用户手动点了行：把"当前第几处"对齐到这一行。
                -- 不这样做的话，接着点「查找下一处」会从别处继续数，
                -- 状态栏的序号和眼睛看到的位置对不上。
                App.S.curMatch = self:MatchIndexAtRow(row)
                self:SelectRow(row)
                -- ★ v4.8.3 兜底：富文本写入失败时，至少把纯文本放进编辑框
                pcall(function()
                    local it2 = App.S.items
                    if it2 and it2.SubEdit then
                        local cur = tostring(it2.SubEdit.PlainText or "")
                        if cur == "" then
                            local b2 = App.S.subs and App.S.subs[row]
                            it2.SubEdit.PlainText = tostring((b2 and b2.t) or "")
                        end
                    end
                end)
                -- ★ 单击 = 选中并展示：内容填进下方编辑框，
                --   表格本身在这个 Resolve 版本上不能就地输入
                --   （ItemIsEditable 无效且不报错）。
                -- ★★★ v3.1.8：单击【立刻把光标送进编辑框】并全选。
                --   用户要求：点一行就应该能马上改，不该还要再双击一次。
                --   代价（可以接受）：焦点离开表格后，方向键不再在表格里
                --   上下移动；Backspace 也从"删选中行"变成"删字"——
                --   删行改用工具条右侧的「删除」按钮。
                -- ★ v3.1.1：选择由表格原生多选负责（Ctrl/Shift 点行），
                --   状态在 SelectionChanged → SyncSelection 里同步。
                self:RefreshRowInfo()
                -- ★ v3.2.5：手动点行 → 表格里的「当前处亮金」要跟着走。
                --   走增量分支：只还原上一个亮金行、给这一行（若命中）上亮金，
                --   两行改动，不整表重绘。点的是非命中行时 curMatch = nil，
                --   上一个亮金行会自然退回暗金。
                --   ★ 先判"有没有在搜"：没有关键词时调用会退化成整表重绘，
                --     每次点击都刷 2000 行，白卡一下。
                if App.S.matchKw and App.S.matchKw ~= "" then
                    pcall(function() self:ApplyFindHighlight(false) end)
                end
                self:FocusSubEdit()
                -- ★ v3.1.3：每次点击都标一次脏，下一拍 Tick 重读选中集合。
                --   SelectionChanged 在"点同一行"时不会派发，只靠它计数会停。
                App.S.selDirty = true
            end
        end
    end)

    -- ★ A 方案：双击一行 = 准备改它 —— 内容填进编辑框、全选、把光标抢过去。
    --   要让它触发，ui:Tree 里必须写 Events = { ItemDoubleClicked = true }。
    pcall(function()
        win.On.SubTree.ItemDoubleClicked = function(ev)
            local row = rowFromTree()
            if row then
                App.S.selRow = row
                self:SelectRow(row)
                self:FocusSubEdit()
            end
        end
    end)


    -- ★ B 方案开关（单元格就地编辑）：在格子里直接打字后触发。
    --   能不能生效取决于 ItemIsEditable 是否被这个 Resolve 版本支持；
    --   不支持也【不会报错】，双击仍走上面那条（跳进编辑框），
    --   两条路互不干扰。
    pcall(function()
        win.On.SubTree.ItemChanged = function(ev)
            if App.S.suppressTreeEdit then return end
            local items = App.S.items
            if not items or not items.SubTree then return end
            local item = nil
            pcall(function()
                item = (ev and ev.item) or items.SubTree:CurrentItem()
            end)
            if not item then return end
            local idx = itemToRow(item)
            if not idx or not App.S.subs[idx] then return end
            local b = App.S.subs[idx]

            -- 第 3 列（0 基 2）：字幕文本
            -- ★ 读不到就【直接跳过】—— 绝不能把"读失败"当成"空字符串"，
            --   否则会把这一行字幕文本整个清空（刷新 / 跨进程 IPC 读不出来时）。
            local raw = nil
            pcall(function() raw = item.Text[2] end)
            if raw ~= nil then
                local one = tostring(raw):gsub("\r", " "):gsub("\n", " ")
                -- ★ v3.2.2：表格里已经是干净原文（v3.2.0 那种 【】 标记已废弃）。
                --   这一句保留作【兼容兜底】：万一某个会话还留着旧版写进去的
                --   【kw】，读回数据时顺手剥掉，别让标记混进 SRT。
                one = stripHitMarks(one, App.S.matchKw)
                if one ~= tostring(b.t or "") then
                    b.t = one
                    App.S.subsDirty = true
                end
            end

            -- 第 0、1 列：允许改时间，但格式不对就【还原】，别把时间码敲坏
            local function fixTime(col, key)
                local raw = nil
                pcall(function() raw = tostring(item.Text[col] or "") end)
                if raw == nil then return end
                local v = parseTimeText(raw)
                if v then
                    if math.abs((tonumber(b[key]) or 0) - v) > 0.0005 then
                        b[key] = v
                        App.S.subsDirty = true
                    end
                end
                -- 无论解析成功与否，都按「当前真值」刷新一次显示：
                --   成功 → 规范化格式；失败 → 还原用户敲坏的内容
                App.S.suppressTreeEdit = true
                pcall(function() item.Text[col] = fmtSec(b[key]) end)
                App.S.suppressTreeEdit = false
            end
            fixTime(0, "s")
            fixTime(1, "e")

            -- ★ v3.1.1：这里原来有一段"读原生复选框状态同步勾选"的代码，
            --   已随复选框方案一并去掉 —— 现在选中与否由 SelectionChanged
            --   统一同步，ItemChanged 只管"格子里改了字/时间"。
            --   ★ 但改了字要立刻反映到状态条上，所以补一次脏标记刷新。
            if App.S.subsDirty then self:RefreshDirty() end
        end
    end)
    -- ★ 编辑框实时同步：每敲一个字，表格里那一行立刻跟着变。
    --   有了它，「更新字幕」就只剩下"写进文件"这一件事，语义才清楚。
    pcall(function()
        win.On.SubEdit.TextChanged = function()
            if App.S.suppressEdit then return end
            local i = App.S.selRow
            if not i or not App.S.subs[i] then return end
            local it = App.S.items
            if not it then return end
            local txt = ""
            pcall(function() txt = tostring(it.SubEdit.PlainText or "") end)
            local one = txt:gsub("\r\n", " "):gsub("\r", " "):gsub("\n", " ")
            App.S.subs[i].t = one
            App.S.subsDirty = true
            -- ★ v3.2.5：改字可能改变这一行的【命中状态】（加了或删了关键词），
            --   先就地更新命中集合，再让下面的 PaintRow 按新状态上色 ——
            --   否则黄底要等到下次重建索引才跟上，看起来像"改完不动"。
            --   命中与否发生翻转时索引也要重建（matchRows 里的处数变了），
            --   重建后把"当前处"对回正在编辑的这一行，亮金不会因此丢掉。
            local hitKw = App.S.matchKw
            if hitKw and hitKw ~= "" then
                local c2, from2 = 0, 1
                while true do
                    local p2 = one:find(hitKw, from2, true)
                    if not p2 then break end
                    c2 = c2 + 1
                    from2 = p2 + #hitKw
                end
                App.S.hitSet = App.S.hitSet or {}
                local wasHit = App.S.hitSet[i] and true or false
                if c2 > 0 then App.S.hitSet[i] = true else App.S.hitSet[i] = nil end
                if wasHit ~= (c2 > 0) then
                    local keepRow = i
                    self:RebuildMatches()
                    if keepRow then App.S.curMatch = self:MatchIndexAtRow(keepRow) end
                    self:ApplyFindHighlight(true)
                    self:RefreshSelBar()
                end
            end
            -- ★ 回写表格单元格时要挡住 ItemChanged —— 否则会被当成
            --   "用户在格子里手改了字"，来回触发（B 方案的就地编辑防递归）。
            -- ★ v3.2.0：交给 PaintRow 统一写显示文本（表格永远是原文）。
            App.S.suppressTreeEdit = true
            pcall(function() self:PaintRow(i) end)
            App.S.suppressTreeEdit = false
            -- ★ v3.2.3：打字过程中【不】重排编辑区（会重置光标），
            --   高亮在失焦时（下面 LostFocus）统一刷新一次。
            -- ★ v3.1.1：改字立刻把状态条刷成「未写回时间线」。
            --   原来只置 subsDirty 不刷新标签，改了半天还显示"已写回"，
            --   用户完全看不出还有东西没保存。
            self:RefreshDirty()
        end
    end)
    -- ★ 查找框一有输入就重建索引、刷新高亮，并【自动定位到第一处】让字幕
    --   编辑框同步显示命中行（v4.0.0：用户要求取消「确认」按钮，边打边看）。
    pcall(function()
        win.On.FindInput.TextChanged = function(ev)
            -- ★ v3.1.5：先尽量从事件参数里取文本（IME 上屏时这条最可靠），
            --   属性读不到时就用它当关键词。
            local t = nil
            if ev then
                pcall(function() t = ev.Text end)
                if type(t) ~= "string" then pcall(function() t = ev.text end) end
            end
            if type(t) == "string" then App.S.findKwCache = t end
            self:RebuildMatches()
            -- ★ v3.1.3：关键词只负责【染色】，不再顺手把命中行选中。
            --   原因：Qt 的选中底色画在背景色之上，一旦命中行被选中，
            --   黄色高亮就被盖住 —— 用户看到的是"搜了但没黄"。
            --   现在两者彻底分开：黄色 = 命中，深色带 = 你选的。
            self:ApplyFindHighlight(true)
            self:RefreshSelBar()
            -- ★ v4.0.0：输入即定位第一处 + 同步字幕编辑框（替代原「确认」）。
            --   只定位、不选中；播放头跳转走 QueueJump 的 0.2s 防抖，连敲不乱跳。
            if (App.S.matchTotal or 0) > 0 then
                App.S.curMatch = 1
                local row = App.S.matchRows[1]
                if row then
                    App.S.selRow = row
                    local b = App.S.subs[row]
                    local tc = (b and b.s) and self:SecToTimecode(b.s) or nil
                    self:SelectRow(row, true, tc)   -- 滚到该行 + 渲染编辑框（同步出现）
                    self:RefreshRowInfo()
                    if b and b.s then
                        self:StatusTimecode(tc or string.format("%.2fs", tonumber(b.s) or 0))
                        self:QueueJump(b.s)
                    end
                    self:Status(string.format("第 1 / %d 处", App.S.matchTotal))
                end
            else
                self:Status("未找到：" .. tostring(self:FindKeyword()))
            end
        end
    end)
    pcall(function()
        win.On.FindNextBtn.Clicked = function() self:FindNext() end
    end)
    pcall(function()
        win.On.ReplaceOneBtn.Clicked = function() self:ReplaceInSubs(false) end
    end)
    pcall(function()
        win.On.ReplaceAllBtn.Clicked = function() self:ReplaceInSubs(true) end
    end)
    pcall(function()
        win.On.UpdateSubsBtn.Clicked = function() self:UpdateSubRow() end
    end)
    pcall(function()
        win.On.LoadPoolBtn.Clicked = function() self:LoadFromMediaPool() end
        -- ★ v4.2.0（B1）：从时间线字幕轨【只读】载入编辑区
        win.On.LoadTrackBtn.Clicked = function() self:LoadSubsFromTrack() end
        -- ★ v4.0.9：手动刷新字幕轨清单。
        --   Resolve 里换集、手工加/删字幕轨之后，点一下就能看到最新清单。
        pcall(function()
            win.On.RefreshTrackBtn.Clicked = function() self:RefreshSubTrackInfo(true) end
        end)
        -- ★ v4.2.1（B2）：清理【空】字幕轨（非空轨一律不动）
        pcall(function()
            win.On.CleanTrackBtn.Clicked = function() self:CleanEmptySubTracks() end
        end)
    end)

    -- ★ v3.1：勾选列相关事件（全部 pcall 包裹，单点失败不影响窗口）
    pcall(function()
        win.On.SelectAllChk.Clicked = function() self:ToggleSelectAll() end
    end)
    pcall(function()
        win.On.InvertBtn.Clicked = function() self:InvertSelection() end
    end)
    pcall(function()
        win.On.DelSelBtn.Clicked = function() self:DeleteSelectedRows() end
    end)
    pcall(function()
        win.On.UndoBtn.Clicked = function() self:UndoDelete() end
    end)
    -- ★ v4.8.0：日志按钮 → 打开「设置」弹窗（运行日志并进设置里）
    -- ★ v4.8.0：设置按钮 → 打开「设置」弹窗（点击时创建）
    pcall(function()
        win.On.SetupBtn.Clicked = function()
            local ok, w = pcall(function() return App.EnsureSettings and App.EnsureSettings() end)
            if ok and w then
                pcall(function() w:Show() end)
                local n = -1
                pcall(function() n = self:PaintLogNow() end)
                ELog("[set] 打开设置，日志字符数=" .. tostring(n))
                pcall(function() App.S.items.SetupBtn.Text = "设置" end)
            else
                self:Log("错误：设置窗口打不开")
            end
        end
    end)
    -- ★ v4.4.0（方案 A）：删行即剪开关
    pcall(function()
        win.On.AutoCutChk.Clicked = function()
            local items = App.S.items
            App.S.autoCut = (items and items.AutoCutChk and items.AutoCutChk.Checked) and true or false
            if App.S.autoCut then
                self:Log("已开启「删行即剪」：删除字幕行后会自动写回并切除对应视频（保留空隙，字幕时间码不变）")
            else
                self:Log("已关闭「删行即剪」：删除字幕行不再自动切视频")
            end
        end
    end)
    -- ★ v4.0.0：「◀ 上一处」「确认」「删除高亮字词」按钮均已删除，绑定一并去掉
    pcall(function()
        win.On.SubTree.SelectionChanged = function()
            -- ★ v3.1.3：不在事件里直接读选中集合。
            --   事件派发时 Qt 的选中状态可能还没落定（读出来是上一批），
            --   这就是"选了 5 行、工具条只认 2 行"的根源。改成打标记，
            --   下一拍 Tick 里再读，那时状态一定是稳定的。
            App.S.selDirty = true
        end
    end)
    -- 焦点护栏：任一文本输入框聚焦时，Backspace 用来删字而不是删行
    -- ★ v3.2.3：SubEdit 额外记一个 editFocus —— textFocus 是"任一输入框"
    --   的合并标志，拿它判断"正在改字幕"会误判（在查找框打字时也是 true），
    --   那样搜索时编辑区就不会重渲染高亮。
    pcall(function()
        win.On.SubEdit.GotFocus = function()
            App.S.textFocus = true
            App.S.editFocus = true
        end
    end)
    pcall(function()
        win.On.SubEdit.LostFocus = function()
            App.S.textFocus = false
            App.S.editFocus = false
            -- ★ v3.2.3：改完字离开编辑区 → 重新渲染一次高亮
            --   （打字期间不动，怕重置光标；这里补上）
            -- ★ v3.2.4：关窗时同样会触发 LostFocus，那种情况下不再碰控件
            if App.S.closing then return end
            pcall(function() self:RenderSubEditRich() end)
        end
    end)
    pcall(function()
        win.On.FindInput.GotFocus = function() App.S.textFocus = true end
    end)
    pcall(function()
        win.On.FindInput.LostFocus = function() App.S.textFocus = false end
    end)
    pcall(function()
        win.On.ReplaceInput.GotFocus = function() App.S.textFocus = true end
    end)
    pcall(function()
        win.On.ReplaceInput.LostFocus = function() App.S.textFocus = false end
    end)
    pcall(function()
        win.On.Key = function(ev)
            if App.S.textFocus then return end
            local k = ev and (ev.Key or ev.key)
            if k == "Backspace" then
                self:DeleteSelectedRows()
                return
            end
            -- ★ v4.0.0：粗剪引擎固定为原地增量剪辑，撤销一律放行给原生 Ctrl+Z
            --   （cut_inplace 已把整次改动包成一个 BeginUndo/EndUndo 步，一次退完）。
            --   插件不再拦截 Ctrl+Z 去做「切回原时间线」（那条路已删除）。
            --   文本框内的撤销也走原生。这里保留按键钩子仅为兼容旧版环境，不再触发撤销动作。
        end
    end)

    -- 窗口关闭事件：用字面量 ID，不要用 win.ID（可能为 nil，会导致崩溃）
    --
    -- ★★★ v2.0.8 重写。之前的现象：点 × 关不掉，再点、再点，日志里
    --   连出 5 条 kill（id=10~14），窗口就是不消失。原因有三层：
    --     ① 没有先隐藏窗口 —— 用户点完看不到反应，自然会继续点；
    --     ② Close 每进一次就 Cleanup 一次，重复下发 kill；
    --     ③ 最要命的是 `dispatcher:ExitLoop()` 被 pcall 包着，报错被吞掉，
    --        日志里什么都留不下（实测：日志停在 kill 之后就断了，
    --        既没有 "RunLoop 已退出" 也没有 "OnRun 结束"）。
    --   所以现在：先藏窗口 → 每步单独 pcall + 单独打日志 → 定时器兜底重试。
    win.On["AsrSubsTool"].Close = function()
        if App.S.closing then return end     -- 重入锁：第二次点直接忽略
        App.S.closing = true
        ELog("Close：开始收尾")

        -- ① 先把窗口藏掉。点 × 就该立刻看到它消失，不能等后面的
        --    存设置 / 杀进程跑完 —— 那几步任意一步慢半秒，观感就是"关不掉"。
        local okH, errH = pcall(function() win:Hide() end)
        ELog("Close 隐藏窗口 ok=" .. tostring(okH) .. " err=" .. tostring(errH))

        -- ② 存设置（Geometry / 各项选择都是这一刻的准值）
        local okS, errS = pcall(function() self:SaveSettingsNow() end)
        ELog("Close 存设置 ok=" .. tostring(okS) .. " err=" .. tostring(errS))
        pcall(function() ELogFlush() end)

        -- ③ 收尾：删渲染任务 / 回到原页面 / 杀掉 worker
        local okC, errC = pcall(function() self:Cleanup() end)
        ELog("Close 收尾 ok=" .. tostring(okC) .. " err=" .. tostring(errC))
        pcall(function() ELogFlush() end)

        -- ④ 退循环。★ 这里【不】停定时器 —— 停掉的话 On.Timeout 里那道
        --    兜底重试就没机会跑了；定时器会在 RunLoop 返回之后，
        --    由 BuildUI 末尾统一 Stop（见 "RunLoop 已退出" 那两行）。
        local okE, errE = pcall(function() dispatcher:ExitLoop() end)
        ELog("Close ExitLoop ok=" .. tostring(okE) .. " err=" .. tostring(errE))
        pcall(function() ELogFlush() end)
    end

    -- ══════════════════════════════════════════════════════════════
    -- ★★★★ 本项目【头号坑】：定时器必须显式创建 + Start()
    --
    --   Fusion 的 UIManager **不会**因为绑定了 dispatcher.On.Timeout
    --   就自动产生超时事件！必须先用 ui:Timer{...} 建一个定时器对象，
    --   再调用它的 :Start()，Timeout 才会按 Interval 周期触发。
    --
    --   我之前只写了 `dispatcher.On.Timeout = function() ... end`，
    --   以为轮询就跑起来了 —— 结果 **Tick 一次都没执行过**，
    --   症状正是用户反复反馈的"界面永远停在识别中/导出中，
    --   其实后台早就跑完了"。这个 bug 骗了我整整 6 轮排查。
    --
    --   官方写法（Steak Underwater 论坛 UITimer 示例）：
    --       timer = ui:Timer{ ID=..., Interval=100, SingleShot=false }
    --       disp.On.Timeout = function(ev) timer_funcs[ev.who]() end
    --       timer:Start()
    --   Interval 单位是【毫秒】。
    -- ══════════════════════════════════════════════════════════════
    local TIMER_ID = "AsrSubsPoll"
    local okT, timerObj = pcall(function()
        return ui:Timer({ ID = TIMER_ID, Interval = 500, SingleShot = false })
    end)
    ELog("创建定时器 ok=" .. tostring(okT) .. " timer=" .. tostring(timerObj))
    App.timer = (okT and timerObj) or nil

    -- ══════════════════════════════════════════════════════════════
    -- ★★★ v2.0.6：第二个定时器，只管「播放头待跳」这一件事。
    --
    --   为什么不复用上面那个 500ms 的 Tick：
    --     Tick 里会 loadLuaTable 读 rc 文件、查进度、刷日志，把它改成
    --     120ms 就等于把这些磁盘/界面操作提速 4 倍，纯属浪费。
    --     而防抖只需要一个"数拍子"的极轻判断。
    --
    --   为什么需要防抖（「查找下一个」连点卡顿的根因）：
    --     每次点击都会 tl:SetCurrentTimecode() 移播放头，这会让 Resolve
    --     的查看器去 seek 并解码目标帧，一次几十到一百多毫秒，而且是
    --     同步阻塞。连点 10 次 = 排队 1 秒多，用户手停了队列还在放，
    --     看起来就是"还在自动往下跳"。
    --     现在点击只更新表格/高亮/状态栏（纯 Qt，微秒级），播放头改成
    --     "手停下来约 0.2 秒后跳一次"，落在最后那一处，不丢不偏。
    --
    --   ★ 这里刻意【不新增 local】：BuildUI 里的局部变量已经很多，Lua 每个
    --     函数有 200 个 local 的上限，超了报 "too many local variables"，
    --     而且是加载期直接失败、语法检查查不出来。所以复用 okT/timerObj，
    --     定时器 ID 直接用字面量。
    -- ══════════════════════════════════════════════════════════════
    okT, timerObj = pcall(function()
        return ui:Timer({ ID = "AsrSubsJump", Interval = 120, SingleShot = false })
    end)
    ELog("创建跳转定时器 ok=" .. tostring(okT) .. " timer=" .. tostring(timerObj))
    App.jumpTimer = (okT and timerObj) or nil

    dispatcher.On.Timeout = function(ev)
        -- ev.who = 触发事件的定时器 ID（可能同时存在多个定时器）
        local who = ev and ev.who

        -- ★★★ v2.0.8：关窗兜底。实测某些 Resolve 版本上，从 Close 回调里
        --   调 `dispatcher:ExitLoop()` 不生效 —— 窗口关了、日志停在 kill 之后，
        --   RunLoop 却一直在转。所以只要 closing 已置位，每一拍都再补调一次；
        --   从定时器上下文调是最可靠的路径，最多 0.5 秒内一定退得出去。
        --   顺手做个上限计数，真退不出去也别无限重试、把日志刷爆。
        if App.S.closing then
            App.S.closeTries = (App.S.closeTries or 0) + 1
            pcall(function() dispatcher:ExitLoop() end)
            if App.S.closeTries == 10 then
                ELog("!! 关闭兜底已重试 10 次仍未退出 RunLoop")
                pcall(function() ELogFlush() end)
            end
            return
        end

        -- ★ 必须 pcall：回调里一旦抛错，定时器会静默失效，
        --   症状和"忘了 Start"一模一样（界面永远不动），几乎无法排查。
        --
        -- ★ v2.0.6：两个定时器共用一个 On.Timeout，靠 ev.who 分流。
        --   · who == nil（个别版本不带这个字段）→ 只跑 Tick。
        --     跳转交给 Tick 里的秒级兜底（见 08_tick.lua），
        --     宁可播放头晚 3 秒跳，也不能让防抖节拍算乱。
        if who == nil or who == TIMER_ID then
            local ok, err = pcall(function() self:Tick() end)
            if not ok then
                -- ★ v4.0.2：Tick 异常会「每拍都抛」，直接 ELogCritical 会把
                --   错误日志刷爆。做频率抑制：距上次记录 ≥10 秒才即时落盘一次。
                --   这样既保证真因立刻可见（硬崩也不丢），又不会疯狂写盘。
                local now = os.time()
                if now - (App.S.tickErrAt or 0) >= 10 then
                    App.S.tickErrAt = now
                    ELogCritical("!! Tick 异常: " .. tostring(err))
                end
            end
        end
        if who == "AsrSubsJump" then
            local ok, err = pcall(function() self:FlushPendingJump() end)
            if not ok then ELog("!! 跳转定时器异常: " .. tostring(err)) end
        end
    end

    ELog("事件已绑定，准备显示窗口")
    self:Log("就绪")
    -- ★ v4.5.1：启动不再往日志里打印引擎/数据目录（它们在「诊断」里都有）。
    --   日志只留"发生了什么"，环境信息去诊断包看，界面更干净。
    ELog("引擎目录=" .. tostring(ASCII_ROOT) .. " 数据目录=" .. tostring(TOOL_ROOT))
    ELog("能力探测: io=" .. type(io) .. " loadfile=" .. type(loadfile)
         .. " os.execute=" .. type(os.execute) .. " bmd=" .. type(bmd))

    -- ★ 预热 worker：只拉起、不等待。
    --   这样用户点「开始生成字幕」时它已经就绪，不会因为等它启动而卡一下。
    if not self:WorkerAlive() then
        if PYTHON == "" then
            self:Log("错误：找不到可用的 Python 运行环境")
            self:Log("  · 在 " .. SETTINGS_FILE .. " 里写 python = \"…\\python.exe\" 可指定环境")
            ELog("预热 worker 中止：PYTHON 为空")
        else
            local py = exists(PYTHONW) and PYTHONW or PYTHON
            local qPy, qWorker = cmdQuote(py), cmdQuote(WORKER)
            if qPy and qWorker then
                local cmd = 'start "" /B ' .. qPy .. ' ' .. qWorker
                pcall(function() os.execute(cmd) end)
                ELog("已预热 worker（不等待）: " .. py)
            else
                ELog("预热 worker 拒绝：路径含 cmd 特殊字符")
            end
        end
    end

    -- ★★★ 必须显式 Show()，否则窗口不会出现（这就是"点了没反应"的原因）
    local okShow, showErr = pcall(function() win:Show() end)
    ELog("Show() ok=" .. tostring(okShow) .. " err=" .. tostring(showErr))
    if not okShow then
        P("窗口显示失败：" .. tostring(showErr))
    end

    -- ★★ 启动定时器（必须在 RunLoop 之前）
    if App.timer then
        local okS, sErr = pcall(function() App.timer:Start() end)
        ELog("定时器 Start ok=" .. tostring(okS) .. " err=" .. tostring(sErr))
        if not okS then
            self:Log("错误：轮询定时器启动失败，进度不会自动刷新")
        end
    else
        self:Log("错误：无法创建轮询定时器，进度不会自动刷新")
    end
    -- ★ v2.0.6：跳转定时器启动失败不报错 —— 它只管"播放头跟手"，
    --   挂了最多是播放头不跳，表格和高亮照常，不该弹错误吓人。
    if App.jumpTimer then
        local okS, sErr = pcall(function() App.jumpTimer:Start() end)
        ELog("跳转定时器 Start ok=" .. tostring(okS) .. " err=" .. tostring(sErr))
    end

    dispatcher:RunLoop()
    pcall(function() if App.timer then App.timer:Stop() end end)
    pcall(function() if App.jumpTimer then App.jumpTimer:Stop() end end)
    ELog("RunLoop 已退出")
end

-- ── 日志 / 状态栏 / 字幕命名 ─────────────────────────────────────
--   ★ v2.0.1：「关于」弹窗（LastRunText / ShowAbout）已整项删除，
--     本文件只保留日志、状态栏、字幕命名这些仍在用的部分。

-- ── 日志 ──────────────────────────────────────────────────────────
-- 日志最多保留这么多行。每次 Log 都要 table.concat 全文再赋给 PlainText，
-- 行多了以后一次刷新就要拼几十 KB 字符串，界面会越来越涩。
-- ★ 日志是【倒序】存的：logLines[1] 永远是最新一条，最新一行显示在最上面，
--   用户不用再去拖滚动条看新消息（本 Resolve 版本没法命令 TextEdit 滚到底）。
local LOG_MAX_LINES = 400
-- 界面日志区实际显示多少行（倒序，最新的在最上面，更早的没人翻）
local LOG_SHOW_LINES = 120
-- 字幕表格每批填充多少行（长音频时分批，避免一次性卡死界面）
local TREE_BATCH = 150

function App:Log(text)
    P(text)
    ELog("  [log] " .. tostring(text))
    -- ★ v4.5.0：日志统一格式「时:分:秒 级别 内容」，级别用符号区分，
    --   一眼能看出哪条是正常、哪条是警告/错误。原来是裸文本，长跑之后
    --   几十行混在一起分不清先后与轻重。
    local raw = tostring(text)
    local stamp = os.date("%H:%M:%S")
    local lvl = "·"
    if raw:find("错误", 1, true) or raw:find("失败", 1, true) then
        lvl = "✕"
    elseif raw:find("⚠", 1, true) then
        lvl = "!"
    elseif raw:find("完成", 1, true) or raw:find("成功", 1, true)
        or raw:find("已写回", 1, true) or raw:find("已删除", 1, true)
        or raw:find("已恢复", 1, true) then
        lvl = "✓"
    end
    -- 同一秒内重复同一句（Tick 里常见）直接吞掉，避免刷屏
    if App.S.lastLogRaw == raw
       and (os.time() - (App.S.lastLogAt or 0)) <= 2 then
        return
    end
    App.S.lastLogRaw = raw
    App.S.lastLogAt = os.time()
    -- ★ v4.5.3：去掉行首时间戳（用户要求：左下角不要时间）。
    --   级别符号只在「非普通」时才加，普通信息就是一句干净的文本。
    if lvl == "·" then
        text = raw
    else
        text = string.format("%s %s", lvl, raw)
    end
    -- ★ v2.0.7：日志里出现「错误」或「⚠」就把诊断入口亮出来。
    --   所有失败路径最终都会走 Log（Lua 侧各分支的错误、落轨条数不一致的
    --   警告都在这儿），比在每个失败分支里各加一行 ShowDiag() 可靠得多 ——
    --   那样只要漏一处，用户就永远看不到入口。
    --   ★ 只认这两个标记，不认「失败」：引擎日志里有「对齐失败（本段改用
    --     比例估算）」这种正常降级，按「失败」匹配会让入口每次跑都亮。
    --   ★ v2.0.9：亮出来的方式变了 —— 不再显示按钮，而是给右下角的
    --     版本号加一个 ⚠（见 App:ShowDiag）。
    local s = tostring(text)
    if not App.S.diagShown
       and (s:find("错误", 1, true) or s:find("⚠", 1, true)) then
        self:ShowDiag()
    end
    -- 倒序：新消息插到最前面（第 1 行），最旧的从尾部丢掉
    table.insert(App.S.logLines, 1, tostring(text))
    -- 倒序存储：最旧的在最后，直接从尾部丢，不用重建整张表
    while #App.S.logLines > LOG_MAX_LINES do
        table.remove(App.S.logLines)
    end
    -- ★ 节流：同一秒内的多条日志只拼一次字符串。
    --   识别过程中每秒能刷出十几条，每次都 concat 全文（400 行、几十 KB）
    --   会让界面明显变涩；合并后界面成本从"N 次拼接"降到"1 秒 1 次"。
    local now = os.time()
    if now ~= App.S.logFlushedAt then
        self:FlushLog()
    else
        App.S.logDirty = true
    end
end

-- 把日志真正刷进界面（FlushLog 单独拆出来，Tick 也要调它补刷）
function App:FlushLog()
    local it = App.S.items
    local log = App.S.items
    if not log or not log.LogEdit then return end
    App.S.logDirty = false
    App.S.logFlushedAt = os.time()
    -- 只拼最新那截：显示区就 120 行，拼 400 行纯属浪费
    local show = {}
    for i = 1, math.min(#App.S.logLines, LOG_SHOW_LINES) do
        show[i] = App.S.logLines[i]
    end
    pcall(function()
        -- 官方用 PlainText 属性赋值，不是 :SetText()
        log.LogEdit.PlainText = table.concat(show, "\n")
    end)
end

-- ★ v4.8.2：把日志整段强制刷进「设置」弹窗（绕过 1 秒节流）。
--   返回写入的字符数；-1 表示弹窗还没建过（没有日志控件可写）。
function App:PaintLogNow()
    local log = App.S.setItems
    if not log or not log.LogEdit then return -1 end
    local show = {}
    local n = math.min(#(App.S.logLines or {}), LOG_SHOW_LINES)
    for i = 1, n do show[i] = App.S.logLines[i] end
    local s = table.concat(show, "\n")
    pcall(function() log.LogEdit.PlainText = s end)
    App.S.logFlushedAt = os.time()
    App.S.logDirty = false
    return #s
end
function App:Status(text)
    -- ★ 状态每 0.5 秒刷新一次，但内容经常和上次一字不差
    --   （比如"识别中... 37%"卡在那儿）。重复赋值会让 Qt 反复重排，
    --   白烧 CPU。这里先比一次。
    if App.S.lastStatus == text then return end
    App.S.lastStatus = text
    local it = App.S.items
    if it and it.Status then
        pcall(function() it.Status.Text = text end)
    end
end

-- ★ 时间码单独一块（只读、可复制）：只显示 HH:MM:SS:FF。
--   传空串表示清空（比如开始新一轮识别时，旧时间码不该留在那儿）。
function App:StatusTimecode(tc)
    tc = tostring(tc or "")
    local it = App.S.items
    -- ★ v2.0.2：控件还没建好时【不要】写缓存。否则缓存被设成该值、控件却
    --   压根没赋上，等控件就绪后同值再进来会因缓存命中直接 return —— 框永远空白。
    if not it or not it.StatusTC then return end
    if App.S.lastTC == tc then
        -- 缓存命中也要核对控件真值：控件被重建/清空过时缓存是脏的。
        local cur = nil
        pcall(function() cur = tostring(it.StatusTC.Text or "") end)
        if cur == tc then return end
    end
    App.S.lastTC = tc
    pcall(function() it.StatusTC.Text = tc end)
    -- 只读控件在个别 Resolve 版本上赋值会失败，补一次兜底
    pcall(function()
        if tostring(it.StatusTC.Text or "") ~= tc then it.StatusTC.PlainText = tc end
    end)
end

-- 让字幕文件名跟着【源素材】走（123.mp4 → 123.srt）。
-- 好处：媒体池里的字幕项、素材旁的副本、字幕输出里的文件三者同名，最好找；
--       也避免 SRT 叫 "Timeline 1" 和工程里的时间线重名造成混淆。
function App:SetSrtName(src)
    local fname = tostring(src or ""):match("[^\\/]+$") or ""
    local base = fname:gsub("%.[%a%d]+$", "")
    base = base:gsub('[\\/:*?"<>|]', "_")
    if base == "" then return end
    App.S.srt = OUTPUT_DIR .. "\\" .. base .. ".srt"
    App.S.txt = OUTPUT_DIR .. "\\" .. base .. ".txt"
    App.S.srtBase = base
    ELog("字幕输出 = " .. App.S.srt .. "  文稿 = " .. App.S.txt)
end

-- ── 启动流程 ──────────────────────────────────────────────────────
-- ★ v2.0.7：第 2 个参数由 prompt（提示词）换成 corrections（权重词纠错表）。
--   prompt 整项废弃：实测模型会把 prompt 当正文吐进字幕、位置还不对。
function App:Start(lang, corrections, maxChars, noPunct, modelPath, allPunct)
    if App.S.phase ~= "idle" and App.S.phase ~= "done" and App.S.phase ~= "error" then
        self:Status("已有任务进行中")
        return
    end

    -- 新一轮识别开始：清掉上一轮查找留下的时间码，别让它挂在状态栏上
    self:StatusTimecode("")

    App.S.langCode = lang
    App.S.corrections = corrections or ""
    App.S.maxChars = maxChars
    -- ★ v4.0.0：字幕无间隙功能删除，识别侧 no_gap 恒 false（不再有 noGap 参数/字段）
    App.S.noPunct = noPunct
    App.S.allPunct = allPunct and true or false
    -- ★ v4.8.21：长停顿断行（设置页开关）。读配置而不是加参数，避免动调用点
    App.S.splitPause = (App.S.cfg and App.S.cfg.split_pause) and true or false
    App.S.modelPath = modelPath or ""
    -- ★★ v1.2.0：裁剪区间必须每轮归零。
    --   它只在 App:OnProbe 里赋值（见「只取素材第 x 秒起的 y 秒」那段），
    --   而 Tick 的 probing 分支存在两条【绕过 OnProbe】的出口：
    --     ① 探测超时 20 秒但拿到 src_path.txt → 直接 StartPython(src)
    --     ② 探测超时且没有兜底文件 → 直接 StartRender()
    --   这时 App.S.trimDur 会留着上一轮的值，被 StartPython 原样写进
    --   args.json 的 audio_trim —— 引擎于是只抽本轮音频的一小段来识别，
    --   SRT 从 0 起算而画面没动，结果是「字幕整体前移 + 只覆盖开头一小段」。
    --   渲染路径更隐蔽：喂进去的是整条时间线的 WAV，却按上一轮的区间裁。
    App.S.trimFrom, App.S.trimDur = 0, 0
    App.S.logLines = {}

    -- ── v2.0.2：时间微调已从界面移除，固定为 v1.28 的默认值 ──
    -- ★ 「整体偏移」「最短显示」两个控件已按需求删除（用户改为手动在时间线上
    --   偏移）。这里仍保留 App.S.offsetSec / minDur 两个字段，因为：
    --   · 09_python.lua 写参数文件时要带 min_dur
    --   · 10_land.lua 写落轨参数时要带 offset_sec
    --   传固定值即可，不必再去改 Python 引擎，行为等价于 v1.28。
    App.S.offsetSec = 0
    -- ★ v4.8.23：最短显示 0.04 → 0.80 秒。实测有一行只显示 0.32 秒
    --   （"来加。"），一闪而过；0.8 秒是"三个字来得及看清"的底线。
    --   enforce_min_duration 只会把结束时间拉长到下一条之前 0.04 秒，不会重叠。
    App.S.minDur = 0.80

    -- 输出到好找的位置，文件名带时间线名（原来藏在 resolve_temp 里，用户根本找不到）
    local safeName = tostring(App.tlName or "字幕"):gsub('[\\/:*?"<>|]', "_")
    App.S.srt = OUTPUT_DIR .. "\\" .. safeName .. ".srt"
    App.S.txt = OUTPUT_DIR .. "\\" .. safeName .. ".txt"

    pcall(function() os.remove(App.S.srt) end)
    pcall(function() os.remove(App.S.txt) end)
    pcall(function() os.remove(App.S.importFile) end)
    pcall(function() os.remove(App.S.exportFile) end)
    pcall(function() os.remove(App.S.statusFile) end)
    pcall(function() os.remove(App.S.logFile) end)
    -- 进度/心跳的 Lua chunk 也要清掉，否则会读到上次运行的结果
    pcall(function() os.remove(PROBE_WORK .. "\\status.lua") end)
    pcall(function() os.remove(PROBE_WORK .. "\\hb.lua") end)
    -- 落轨结果同理（不清的话会立刻误判成"已完成"）
    pcall(function() os.remove(App.S.landFile) end)
    pcall(function() os.remove(App.S.landCfg) end)
    App.S.landStartedAt = nil

    -- ★ 这两个时间戳不清零的话，下一轮 Tick 一进来就算出"已经超时"，
    --   自动入库 / 导出会被瞬间判死（跑了 0 秒就报超时）。
    App.S.importStartedAt = nil
    App.S.exportStartedAt = nil
    App.S.probeStartedAt = nil
    App.S.syncStartedAt = nil
    -- 「更新字幕」留下的待办同步标记也要清（属于上一轮的字幕）
    App.S.pendingSync = nil
    -- ★★ v1.2.0：上一轮「导入超时后的补读窗口」必须作废。
    --   lateImportUntil 是 importing 超时时设的（+120 秒），Tick 里在
    --   【非忙碌阶段】读 import.lua 并报一句"（迟到）"。不清掉的话，新一轮
    --   刚跑完（phase=done 已不忙）会读到本轮自己的 import.lua，把这个
    --   正常结果当成上一轮的迟到结果再报一遍，状态栏文字也被覆盖。
    App.S.lateImportUntil = nil
    -- ★ worker 上一次不可用不代表这次也不可用（可能只是当时还没起来）。
    --   每轮重新探测，否则一旦失败就永久退化成"闪黑窗"的老路径。
    App.S.workerOk = nil
    App.S.lastStatus = nil
    -- ★ v1.0.1：环境告警（"CPU 模式"）每轮重新提示一次，不要因为上一轮说过就永久闭嘴
    App.S.warnLogged = nil

    -- 重置轮询状态
    App.S.pyStartedAt = nil
    App.S.count = 0
    App.S.realModel = ""        -- 引擎实际用的模型（回退时会和填的不一样）
    App.S.lastPct = 0
    App.S.lastSize = 0
    App.S.sameCount = 0
    App.S.restartTried = false
    App.S.renderStartedAt = os.time()
    -- 删掉上次导出的音频，避免"按文件判定"时把旧文件当成新产出
    -- （用 listFiles 而不是 io.popen：后者会闪黑窗）
    pcall(function()
        for _, nm in ipairs(listFiles(WORK_DIR, "qwen3_audio", ".wav")) do
            os.remove(WORK_DIR .. "\\" .. nm)
        end
    end)
    pcall(function() App.S.items.StartBtn.Enabled = false end)
    pcall(function() App.S.items.StopBtn.Enabled = true end)
    pcall(function() App.S.items.LandBtn.Enabled = false end)
    pcall(function() App.S.items.TxtBtn.Enabled = false end)
    pcall(function() App.S.items.StartBtn.Text = "开始生成" end)
    -- ★ v1.0.1：清掉上一轮的字幕表并锁住写回按钮。
    --   以前 Start 只关四个按钮，旧字幕还留在右侧表格里、「字幕更新」照样
    --   可点 —— 点一下就把【上一期】的字幕写进本期文件名（Start 刚把同名
    --   SRT 删掉、识别进程正在写同一路径），界面还会说"已保存 N 条"。
    pcall(function() self:FillSubTree({}) end)
    pcall(function() App.S.items.UpdateSubsBtn.Enabled = false end)
    pcall(function() App.S.items.CutBtn.Enabled = false end)

    -- 步骤 1：优先直接读时间线上的源文件（快），不行才走渲染
    --   ★ 这一步是【异步】的：只负责把探测脚本发出去，
    --     结果由 Tick 的 probing 分支收（见 App:StartProbe 的说明）。
    self:StartProbe()
end


-- ══════════════════════════════════════════════════════════════
-- 快速路径：读时间线上的源文件直接识别，跳过渲染。
--
-- ★ 为什么用外部 Python 脚本而不是在 Lua 里做：
--   1. Lua 侧 TimelineItem 方法名各版本不一致（21.1 是 GetStart()/GetSpeed()，
--      不是 GetStartFrame()），取不到值时返回 nil 而不报错，很难发现。
--   2. Lua 里异常被 Resolve 静默吞掉，只能看到"没反应"。
--      Python 侧能 try/except 并把结果和错误写进 JSON。
--
--   前置条件：首选项 → 系统 → 常规 → 外部脚本使用 = 本地 / 网络
-- ══════════════════════════════════════════════════════════════
-- ★★ 只【发出】探测命令，立刻返回。结果在 Tick 的 probing 分支里收。
--
--   原来这里是同步忙等：最多 150 次 × 0.1 秒 = 15 秒，
--   期间 RunLoop 根本转不起来 —— 窗口不能重绘、点不动、看着像"点了没反应"。
--   加上 EnsureWorker / RunTask 各自的等待，最坏能卡 27 秒。
--   现在整条链上最长的单次阻塞是 RunTask 等 worker 回执（2.5 秒上限）。
function App:StartProbe()
    local jsonPath = PROBE_WORK .. "\\src.json"
    local txtPath  = PROBE_WORK .. "\\src_path.txt"
    local luaPath  = PROBE_WORK .. "\\src.lua"
    App.S.srcLua   = luaPath
    App.S.srcTxt   = txtPath
    App.S.srcJson  = jsonPath
    pcall(function() os.remove(jsonPath) end)
    pcall(function() os.remove(txtPath) end)
    pcall(function() os.remove(luaPath) end)

    -- ★★ 交给常驻 worker 启动（os.execute 会闪黑窗，worker 不会）。
    --    路径全 ASCII；中文值走文件，不进命令行。
    if not self:RunTask(PROBE_SCRIPT, { jsonPath, txtPath, luaPath }) then
        -- 探测脚本没起来：别在 probing 里空等 20 秒，直接走渲染
        self:Log("读取时间线失败，改用渲染导出")
        self:StartRender()
        return
    end

    App.S.phase = "probing"
    App.S.probeStartedAt = os.time()
    self:Status("正在读取时间线素材…")
    ELog("探测已下发（异步）")
end

-- 探测有结果了：要么直接用源文件识别，要么退回渲染导出
function App:OnProbe(t)
    ELog("src.lua 加载成功")
    local errMsg = tostring(t.error or "")
    local hint   = tostring(t.hint or "")
    local src    = tostring(t.path or "")
    local any    = tostring(t.any or "")
    local name   = tostring(t.name or "")
    local nItems = tonumber(t.items) or 0
    -- 帧率 / 时间线起始帧：播放头联动跳转的时间码换算要用
    App.S.fps     = tonumber(t.fps) or 0
    App.S.tlStart = tonumber(t.tl_start) or 0

    -- ── v2.0.0：音轨清单 ────────────────────────────────────────
    -- 下拉项是开窗时一次性建好的（全部音轨 / A1..A8），这里只做两件事：
    --   1) 把用户选的那项夹到实际存在的音轨范围内（时间线换了、音轨变少）
    --   2) 多音轨时在日志里说清楚每条有几个片段，提示可以只取一条
    local nTracks = tonumber(t.n_tracks) or 0
    App.S.nTracks = nTracks
    if nTracks > 0 then
        local ti = t.track_items
        local parts = {}
        if type(ti) == "table" then
            for i = 1, nTracks do
                parts[#parts + 1] = string.format("A%d:%d 段", i, tonumber(ti[i]) or 0)
            end
        end
        if nTracks > 1 then
            self:Log(string.format("时间线有 %d 条音轨（%s）", nTracks,
                table.concat(parts, " / ")))
            self:Log("  · 多音轨会混合后一起识别")
        end
    end

    -- ★ 走渲染兜底时也要记住素材路径（哪怕它"不能直接识别"），
    --   否则落轨那一步的"在源素材旁边放一份 srt"会被静默跳过。
    if any ~= "" then App.S.audioPath = any end

    if errMsg ~= "" then self:Log("读取时间线失败：" .. errMsg) end
    if hint ~= "" and errMsg == "" then self:Log(hint) end

    -- ★★ 快速路径只认【单片段且未裁剪】。
    --   以前只要"第一个片段恰好没被裁剪"就直接拿它的源文件识别 ——
    --   多片段时间线里第 2 段之后的音频被无声丢掉，字幕却按整条
    --   时间线排，观众看到的就是"后半段没字幕"。必须走渲染。
    if src ~= "" and nItems ~= 1 then
        self:Log(string.format(
            "时间线上有 %d 个音频片段，为保证声画同步，走渲染导出", nItems))
        src = ""
    end

    -- ★ v1.1.3：掐头 / 从中间取用 / 尾部裁短的单片段，不再逼着渲染。
    --   识别时只抽「用到的这一段」音频（源第 src_sec 秒起、共 dur_sec 秒），
    --   字幕时间从 0 起，落轨时按片段起始帧整体偏移 —— 声画仍然对得上。
    App.S.trimFrom, App.S.trimDur = 0, 0
    if src ~= "" and (tonumber(t.fast) or 0) == 1 then
        local s0 = tonumber(t.src_sec) or 0
        local dd = tonumber(t.dur_sec) or 0
        if dd > 0.2 then
            App.S.trimFrom, App.S.trimDur = s0, dd
            self:Log(string.format(
                "只取素材第 %.2f 秒起的 %.2f 秒直接识别（跳过渲染）", s0, dd))
        end
    end

    if src == "" then
        if errMsg == "" and hint == "" then
            self:Log("时间线上没有可直接使用的素材，走渲染导出")
        end
        return self:StartRender()
    end

    -- ★★ 不要在这里用 exists() 判断素材存在性！
    --   Windows Lua 的 io.open 走 ANSI API，**打不开中文路径**，
    --   会把明明存在的文件判成"不存在"（实测踩过）。
    --   存在性已由 Python 用 os.path.exists 校验过。
    self:Log("快速路径：直接使用源文件，跳过渲染")
    if name ~= "" then self:Log("  " .. name) end
    self:Log("  " .. src)
    App.S.audioPath = src
    self:SetSrtName(src)

    return self:StartPython(src)
end

-- ══════════════════════════════════════════════════════════════
-- 渲染导出 WAV（兜底路径：快速路径用不了时才走这里）
--
-- ★★ 两段式：StartRender 只负责【切页 + 快照】，格式配置放进 Tick 的
--   render_cfg 相位、等交付页就绪后再做。
--   原因（实测踩坑）：OpenPage("deliver") 是异步的，切完立刻调
--   SetCurrentRenderFormatAndCodec / SetRenderSettings 会静默失败，
--   格式停在 "unknown"，渲染作业"成功"结束但一个文件都不产出，
--   最后报"未生成音频文件"—— 和真因（格式没设上）八竿子打不着。
--
--   以下实测结论与踩坑经验一致：
--   1. 必须先 OpenPage("deliver")，否则 AddRenderJob/StartRendering 会静默失败
--   2. StartRendering 偶尔返回 false（交付页还在切），等 0.5 秒重试一次即可
--   3. 导出前要快照用户的渲染设置，跑完恢复 —— 否则把用户交付页改得乱七八糟
--   4. JobStatus 是本地化字符串（中文/葡语/俄语…），绝对不能拿它和
--      "Complete" 比 —— 见 App:Tick 里的判定
--   5. 'Audio Only' 内置预设在非英文 Resolve 上名字被本地化，加载必失败
--      → 改用官方 DaVinci ASR 同款做法：ImportRenderPreset 导入自带的
--        render_to_wav.xml（格式写死在 XML 里，不依赖任何本地化名字），
--        导入后再读回校验，不是 WAV 就明确报错，绝不静默产出空文件
-- ══════════════════════════════════════════════════════════════
function App:StartRender()
    local proj = App.project

    -- ① 切到交付页（不切的话渲染根本起不来）。
    --    这里只切页 + 记相位；格式配置等页面就绪，见 App:ConfigRender。
    App.S.prevPage = App.resolve:GetCurrentPage()
    -- ★ v1.1.1：只有真的切过页，关窗/结束时才允许切回去（见 ReturnToPrevPage）
    App.S.pageMoved = true
    pcall(function() App.resolve:OpenPage("deliver") end)

    self:Status("正在配置音频导出...")
    self:Log("导出时间线音轨（WAV）")
    self:Log("（导出期间请勿关闭本窗口）")

    -- ② 清掉我们自己的旧作业（不动用户自己的渲染任务）
    --    卡在 'Ready' 的僵尸作业会让 IsRenderingInProgress() 永远为 true，
    --    导致之后每次导出都被阻塞 —— 必须清。
    self:RemoveStaleJobs()

    -- ③ ★ 快照用户当前的交付页设置，跑完要还原
    App.S.renderSnap = nil
    pcall(function()
        local name = "AsrSubsBackup-" .. os.date("!%Y%m%d-%H%M%S")
            .. "-" .. tostring(math.random(1000, 9999))
        if proj:SaveAsNewRenderPreset(name) then
            App.S.renderSnap = { presetName = name }
        end
    end)
    if not App.S.renderSnap then
        -- SaveAsNewRenderPreset 失败时的退路：至少记住格式和渲染模式
        local snap = {}
        pcall(function() snap.formatCodec = proj:GetCurrentRenderFormatAndCodec() end)
        pcall(function() snap.renderMode = proj:GetCurrentRenderMode() end)
        App.S.renderSnap = snap
    end

    -- ④ 等交付页就绪后再配置格式（Tick 的 render_cfg 相位驱动）
    App.S.renderCfgAt = os.time()
    App.S.phase = "render_cfg"
    ELog("已切到交付页，等页面就绪后配置导出格式")
end

-- 读回当前渲染格式，返回 "格式 / 编解码器" 文本（读不到返回 ""）
local function curFormatText(proj)
    local txt = ""
    pcall(function()
        local d = proj:GetCurrentRenderFormatAndCodec()
        if type(d) == "table" then
            local f = d.format or d.Format or d["Render Format"] or d.renderFormat
            local c = d.codec or d.Codec or d["Render Codec"] or d.renderCodec
            txt = tostring(f or "") .. " / " .. tostring(c or "")
        else
            txt = tostring(d or "")
        end
    end)
    return txt
end

-- 交付页就绪后：配置 WAV 导出格式 + 建作业 + 启动渲染。
-- 失败时自己收尾（CleanupRender + Fail），返回 false。
function App:ConfigRender()
    local proj = App.project

    -- ① 先把交付页切成"只导音频"视图（官方 DaVinci ASR 的第一步）。
    --   不做这一步，后面的格式/预设 API 在部分版本上不生效。
    pcall(function()
        proj:SetRenderSettings({ ExportVideo = false, ExportAudio = true })
    end)

    -- ② 导入自带的 WAV 预设（格式写死在 XML 里，不依赖本地化预设名）
    if RENDER_PRESET_XML and exists(RENDER_PRESET_XML) then
        pcall(function() proj:DeleteRenderPreset("render_to_wav") end)
        local okImp, imp = pcall(function()
            return App.resolve:ImportRenderPreset(RENDER_PRESET_XML)
        end)
        ELog("ImportRenderPreset ok=" .. tostring(okImp)
             .. " 返回=" .. tostring(imp))
        if okImp and imp ~= true then
            self:Log("注意：WAV 预设导入返回 " .. tostring(imp)
                     .. "，继续用格式探测")
        end
    else
        ELog("找不到 render_to_wav.xml（" .. tostring(RENDER_PRESET_XML)
             .. "），用格式探测")
    end

    -- ③ 交付页格式探测：只记录日志，【不再作为失败判据】
    --    ★ v1.2.1 实测（Resolve 21.1）：ExportVideo=false 的音频-only 模式下，
    --      GetCurrentRenderFormatAndCodec() 恒返回 {format="unknown", codec=""}，
    --      SetCurrentRenderFormatAndCodec() 恒返回 false —— 这两个接口读写的是
    --      *视频*格式，音频模式下根本拿不到音频格式。
    --      此前按这个返回值判失败，会把「作业属性完全正确的 WAV」误判成
    --      「无法设置音频导出格式」。真正的判据挪到 ⑤ 之后读作业属性。
    local fmtText = curFormatText(proj)
    ELog("交付页格式（音频模式下不可靠，仅供参考）：" ..
         (fmtText ~= "" and fmtText or "unknown"))
    if fmtText:lower():find("wav") == nil and fmtText:lower():find("wave") == nil then
        local fmtName, fmtExt, codec = "wav", "wav", "lpcm"
        pcall(function()
            local fmts = proj:GetAudioRenderFormats() or {}
            for name, ext in pairs(fmts) do
                if tostring(ext):lower() == "wav" then
                    fmtName, fmtExt = tostring(name), tostring(ext)
                end
            end
        end)
        pcall(function()
            local cs = proj:GetAudioRenderCodecs(fmtExt) or {}
            for desc, key in pairs(cs) do
                local d = tostring(desc):lower()
                local k = tostring(key):lower()
                if k:find("pcm") or d:find("pcm") or d:find("linear") then
                    codec = tostring(key)
                    break
                end
            end
        end)
        pcall(function()
            proj:SetCurrentRenderFormatAndCodec(fmtName, codec)
        end)
        ELog("格式探测后（仅供参考）：" ..
             (curFormatText(proj) ~= "" and curFormatText(proj) or "unknown"))
    end

    -- ④ 设置导出参数
    --    ★ 两种 key 拼写都给：老版本文档是 ExportVideo/ExportAudio，
    --      某些版本只认 IsExportVideo/IsExportAudio；未知 key 会被忽略，
    --      所以两个都写最稳。
    App.S.audioPrefix = "qwen3_audio_" .. os.date("!%Y%m%d-%H%M%S")
    local ok, err = pcall(function()
        return proj:SetRenderSettings({
            SelectAllFrames  = true,
            ExportVideo      = false,
            ExportAudio      = true,
            IsExportVideo    = false,
            IsExportAudio    = true,
            AudioFormat      = "wav",
            AudioCodec       = "lpcm",
            AudioBitDepth    = 16,
            AudioSampleRate  = 48000,
            TargetDir        = osPath(WORK_DIR),
            CustomName       = App.S.audioPrefix,
        })
    end)
    ELog("SetRenderSettings ok=" .. tostring(ok) .. " 返回=" .. tostring(err))

    if not ok or err ~= true then
        pcall(function() self:CleanupRender() end)
        pcall(function() self:ReturnToPrevPage() end)
        self:Fail("无法设置音频导出参数（交付页可能正被占用），请重试")
        return false
    end

    -- ④b 音轨隔离（v2.0.0）已在 v2.0.1 移除：不再改任何音轨开关，
    --     渲染就是整条时间线的混音，等同 v1.28 旧行为。

    -- ⑤ 建作业 + 启动（全部包 pcall：裸调用抛错会被静默吞掉 → 界面永久卡住）
    local okJob, jobId = pcall(function() return proj:AddRenderJob() end)
    ELog("AddRenderJob ok=" .. tostring(okJob) .. " 返回=" .. tostring(jobId))
    if not okJob or not jobId or jobId == "" then
        pcall(function() self:CleanupRender() end)
        pcall(function() self:ReturnToPrevPage() end)
        self:Fail("无法创建渲染任务，请检查交付页是否可用后重试")
        return false
    end

    -- ⑤b 校验作业属性（v1.2.2）：这是音频-only 模式下【唯一可靠】的判据。
    --     实测：作业属性里 VideoFormat=Wave、AudioCodec=lpcm、IsExportVideo=false
    --     都能如实读出来，而 GetCurrentRenderFormatAndCodec() 恒为 unknown。
    --     校验点：① 格式确实是 WAV/PCM；② 视频导出必须已关闭（否则会写 mp4）。
    -- ★ v1.2.2 修（P0）：v1.2.1 拿「输出文件名结尾 .wav」当格式判据，但
    --   OutputFilename 在部分版本里是目录 / 缺失，回退用的 CustomName
    --   （qwen3_audio_2026…）本来就没有扩展名 → 正确的 WAV 作业被误判、误删；
    --   IsExportVideo 若返回无 Is 前缀的 ExportVideo、nil 或数字 0，旧写法
    --   也一律判成「视频导出未关」。现在改成以作业属性字段为主判据，
    -- ★ 只有在【明确读到且明确不对】时才拦截；字段缺失 / 不认识一律放行，
    --   交给后面「渲染结束但没有产出文件」兜底，绝不误杀正确作业。
    do
        local jobInfo = nil
        pcall(function()
            for _, j in ipairs(proj:GetRenderJobList() or {}) do
                local jid = j.JobId or j.Id
                if jid and tostring(jid) == tostring(jobId) then
                    jobInfo = j
                    break
                end
            end
        end)
        if jobInfo then
            local outName = tostring(jobInfo.OutputFilename or jobInfo.CustomName or "")
            -- 字段名两套都认（SetRenderSettings 也是两套都写）；userdata 取不到
            -- 字段时由 pcall 兜住，不让校验本身把流程打断。
            local function pick(...)
                for _, k in ipairs({ ... }) do
                    local ok, v = pcall(function() return jobInfo[k] end)
                    if ok and v ~= nil then return v end
                end
                return nil
            end
            local function norm(v)
                if v == nil then return "" end
                local s = tostring(v)
                local low = s:lower()
                if s == "" or low == "unknown" or low == "none" then return "" end
                return s
            end
            -- 三态开关：true=明确关闭、false=明确开启、nil=读不到/不认识
            local function offState(v)
                if v == nil then return nil end
                if type(v) == "boolean" then return v == false end
                if type(v) == "number"  then return v == 0 end
                local s = tostring(v):lower()
                if s == "false" or s == "0" or s == "no" or s == "off" then return true end
                if s == "true"  or s == "1" or s == "yes" or s == "on" then return false end
                return nil
            end

            local vRaw = pick("IsExportVideo", "ExportVideo")
            local aRaw = pick("IsExportAudio", "ExportAudio")
            local fmt  = norm(pick("VideoFormat", "AudioFormat", "Format"))
            local vc   = norm(pick("VideoCodec"))
            local ac   = norm(pick("AudioCodec"))
            ELog(string.format(
                "作业属性：%s | V=%s A=%s | 格式=%s | 视频编码=%s | 音频编码=%s",
                outName, tostring(vRaw), tostring(aRaw),
                (fmt ~= "" and fmt or "-"), (vc ~= "" and vc or "-"),
                (ac ~= "" and ac or "-")))
            -- WAV 判据：优先看作业属性字段，其次才看输出名后缀。
            -- 两者都没读到 → isWav = nil（无法判定，放行）。
            local fmtAll = (fmt .. " " .. vc .. " " .. ac):lower()
            local isWav = nil
            if fmtAll:find("wav") or fmtAll:find("wave")
               or fmtAll:find("lpcm") or fmtAll:find("pcm")
               or outName:lower():sub(-4) == ".wav" then
                isWav = true
            elseif fmt ~= "" or vc ~= "" or ac ~= "" or outName ~= "" then
                isWav = false        -- 明确读到了、但确实不是 WAV
            end
            local videoOff = offState(vRaw)
            ELog("作业属性校验：WAV=" .. tostring(isWav)
                 .. "，视频导出关闭=" .. tostring(videoOff)
                 .. "（nil=字段读不到，放行交给无产出兜底）")
            -- 只在【明确异常】时拦截：读不到 / 不认识一律放行
            if isWav == false or videoOff == false then
                pcall(function() proj:DeleteRenderJob(jobId) end)
                pcall(function() self:CleanupRender() end)
                pcall(function() self:ReturnToPrevPage() end)
                self:Fail("导出格式异常（作业：" ..
                          (outName ~= "" and outName or "未知") ..
                          "，格式=" .. (fmt ~= "" and fmt or "未知") ..
                          "，音频编码=" .. (ac ~= "" and ac or "未知") ..
                          "，视频导出=" .. tostring(vRaw) ..
                          "）。请在交付页手动选一次 WAV 格式后重试")
                return false
            end
        else
            -- 读不到作业属性不拦，交给「渲染结束但没有产出文件」那条兜底
            ELog("作业属性读取失败，跳过格式校验")
        end
    end

    -- ★ 实测：交付页刚切过来时 StartRendering 偶尔返回 false，
    --   等半秒重试一次就能起来。这里照做。
    local started = false
    pcall(function() started = proj:StartRendering({ jobId }, false) end)
    if started ~= true then
        ELog("StartRendering 首次返回 " .. tostring(started) .. "，重试")
        waitSec(0.5)
        pcall(function() started = proj:StartRendering({ jobId }, false) end)
    end
    ELog("StartRendering = " .. tostring(started))

    if started ~= true then
        pcall(function() proj:DeleteRenderJob(jobId) end)
        pcall(function() self:CleanupRender() end)
        pcall(function() self:ReturnToPrevPage() end)
        self:Fail("无法启动渲染任务，请稍后重试（渲染队列可能被占用）")
        return false
    end

    App.S.jobId = jobId
    App.S.phase = "rendering"
    App.S.renderStartedAt = os.time()
    App.S.restartTried = false
    App.S.lastSize = -1
    App.S.sameCount = 0
    ELog("渲染已启动 jobId=" .. tostring(jobId))
    self:Status("导出音频中...")
    return true
end
-- 只删我们自己留下的作业（按文件名前缀识别），绝不碰用户的渲染任务
function App:RemoveStaleJobs()
    pcall(function()
        local jobs = App.project:GetRenderJobList() or {}
        for _, j in ipairs(jobs) do
            local nm = tostring(j.OutputFilename or "") .. tostring(j.CustomName or "")
            local id = j.JobId or j.Id
            if id and id ~= App.S.jobId and nm:find("qwen3_audio") then
                pcall(function() App.project:DeleteRenderJob(id) end)
            end
        end
    end)
end

-- 渲染结束（成功/失败/取消都要调）：删掉我们的作业 + 还原用户交付页设置
function App:CleanupRender()
    -- ★★ v1.1.1：这次【根本没配置过渲染】就直接返回，一个交付页的 API 都不碰。
    --   用户实测的 bug：打开 AsrSubs、什么都不做、关掉窗口，DaVinci 自己跳到
    --   交付页。根因是关窗走的 Cleanup 现在会调 CleanupRender，而它无条件执行
    --   `DeleteRenderPreset("render_to_wav")` —— Resolve 删渲染预设时会把界面
    --   切到交付页。没有快照、没有作业时，这里本来就没有任何东西要还原。
    if not App.S.renderSnap and not App.S.jobId then
        return
    end
    -- ★ v2.0.1：音轨隔离已移除，这里不再需要恢复音轨。
    if App.S.jobId then
        pcall(function() App.project:DeleteRenderJob(App.S.jobId) end)
        App.S.jobId = nil
    end
    -- 收掉我们导入的 WAV 预设，别在用户的交付页预设列表里留垃圾
    pcall(function() App.project:DeleteRenderPreset("render_to_wav") end)
    local snap = App.S.renderSnap
    App.S.renderSnap = nil
    -- ★ v1.2.1 诊断：看清走的哪个快照分支、还原前后格式读数
    --   （音频-only 模式下 GetCurrentRenderFormatAndCodec 读数不可靠，
    --    这里只作对照，不作为判据）
    local fmtBefore = curFormatText(App.project)
    if type(snap) ~= "table" then
        ELog("CleanupRender：无快照，跳过还原；格式="
             .. (fmtBefore ~= "" and fmtBefore or "unknown"))
        return
    end
    ELog("CleanupRender：快照="
         .. (snap.presetName
             and ("预设 " .. tostring(snap.presetName))
             or "退路(格式/模式)")
         .. "；还原前格式=" .. (fmtBefore ~= "" and fmtBefore or "unknown"))
    if snap.presetName then
        pcall(function() App.project:LoadRenderPreset(snap.presetName) end)
        pcall(function() App.project:DeleteRenderPreset(snap.presetName) end)
    else
        if type(snap.formatCodec) == "table" then
            local f = snap.formatCodec.format or snap.formatCodec["format"]
            local c = snap.formatCodec.codec or snap.formatCodec["codec"]
            if f and c then
                pcall(function() App.project:SetCurrentRenderFormatAndCodec(f, c) end)
            end
        end
        if snap.renderMode ~= nil then
            pcall(function() App.project:SetCurrentRenderMode(snap.renderMode) end)
        end
        -- 拿不到预设快照时，只能把导出开关恢复到常规默认，
        -- 总比把用户留在"仅音频"上强
        pcall(function()
            App.project:SetRenderSettings({
                ExportVideo = true, ExportAudio = true,
                IsExportVideo = true, IsExportAudio = true,
                SelectAllFrames = true,
            })
        end)
    end
    local fmtAfter = curFormatText(App.project)
    ELog("CleanupRender：还原后格式="
         .. (fmtAfter ~= "" and fmtAfter or "unknown"))
end

function App:ReturnToPrevPage()
    -- ★★ v1.1.1：只有【我们自己把页面切走过】才切回去。
    --   以前只要 App.S.prevPage 有值就 OpenPage 一次 —— 而 prevPage 是
    --   StartRender 留下的，关窗时若还留着旧值，就会在用户没渲染的情况下
    --   把界面拽回上一轮那页（实测表现为"关插件跳到交付页"）。
    --   现在用 pageMoved 这个一次性标志：StartRender 切页时置位，这里消费。
    if not App.S.pageMoved then return end
    App.S.pageMoved = nil
    local target = tostring(App.S.prevPage or App.S.openPage or "")
    if target == "" then return end
    local cur = ""
    pcall(function() cur = tostring(App.resolve:GetCurrentPage() or "") end)
    if cur == target then return end          -- 已经在目标页，别再切（会闪）
    pcall(function() App.resolve:OpenPage(target) end)
end

-- ── 主循环 ────────────────────────────────────────────────────────
-- 取一个日志文件的最后一条非空行（字节级 seek，不是逐行读全文）
local function lastLogLine(path)
    if not path or path == "" then return "" end
    local d = tail(path, 1500)
    if d == "" then return "" end
    local best = ""
    for line in (d .. "\n"):gmatch("(.-)\n") do
        local s = line:gsub("^%s+", ""):gsub("%s+$", "")
        if s ~= "" then best = s end
    end
    return best
end

function App:Tick()
    local ph = App.S.phase

    -- 日志节流补刷：同一秒里攒下的日志在这里一次性刷进界面
    if App.S.logDirty then self:FlushLog() end

    -- ★★ v1.2.0：「开始生成」可用性看门狗。
    --   换时间线、把片段删光、工程被关掉，这些都不会给插件发任何通知，
    --   而用户要求"条件不满足就别让点"。这里在【非忙碌相位】每 3 秒重算一次
    --   （相位切换那一下 EnableButtons 已经刷过，这里只兜环境在背后的变化）。
    --   ★ 上限 3 秒是算过的：CanStart 只数到第一个片段就返回，代价常数级；
    --   就算这样也不放进每 0.5 秒的主循环里，避免在大工程上白烧帧。
    if not BUSY_PHASES[ph] and App.S.items and App.S.items.StartBtn then
        local nowChk = os.time()
        if nowChk - (App.S.startChkAt or 0) >= 3 then
            App.S.startChkAt = nowChk
            pcall(function() self:RefreshStartBtn() end)
        end
    end

    -- ★ v4.8.3：设置弹窗可见时定期保存几何（点 X 关闭不一定触发 Close 事件）
    if App.S.winSet and App.SaveSetGeo then
        local nowT = os.time()
        if nowT ~= (App.S.geoSavedAt or 0) then
            App.S.geoSavedAt = nowT
            pcall(App.SaveSetGeo)
        end
    end

    -- ── v3.1.3：关键词看门狗（中文输入法兜底）──────────────────
    --   ★ 症状：查找框里明明写着"三"，工具条却是"命中 0"，也不见黄色。
    --   原因是中文输入法上屏时 LineEdit 的 TextChanged 在部分版本上
    --   不派发，索引压根没重建。这里每拍比一次字符串，不一致就重建 ——
    --   代价只是一次字符串比较，但IME、粘贴、脚本赋值全都能兜住。
    if App.S.items and App.S.items.FindInput then
        local kwNow = self:ReadFindKwRaw()
        if kwNow ~= nil and kwNow ~= App.S.matchKw then
            self:RebuildMatches()
            self:ApplyFindHighlight(true)
            self:RefreshSelBar()
        end
    end

    -- ── v3.1.3：选中集合补读 ───────────────────────────────────
    --   点击当下读不准（Qt 状态还没落定），事件里只打标记，这里再读。
    if App.S.selDirty then
        App.S.selDirty = false
        if not App.S.suppressSelSync then
            local ok, err = pcall(function() self:SyncSelection() end)
            if not ok then ELog("!! 选中同步异常: " .. tostring(err)) end
        end
    end

    -- ── v2.0.6 兜底：播放头待跳超时补跳 ──────────────────────
    --   正常路径由 120ms 的 jumpTimer 在约 240ms 后跳掉，这里永远看不到
    --   待跳残留。只有定时器因为 ev.who 判定异常没跑起来时，才会攒到
    --   这里 —— 宁可晚 3 秒跳，也不能让播放头彻底不动。
    if App.S.pendJump ~= nil and App.S.pendJumpWall
       and (os.time() - App.S.pendJumpWall) >= 3 then
        self:DoPendingJump()
    end

    -- ── 子进程退出码：worker 写回 rc_<sess>_<id>.lua ──────────
    --   ★ 以前只能轮询输出文件。进程如果在写出任何东西之前就崩了，
    --     Lua 要干等满超时（保存分支 20 秒）才报错，而且报的是
    --     "读取字幕超时"这种和真因无关的话。现在失败当时就能报，
    --     还能把日志最后一行直接贴出来。
    --   ★ 这里只做"报错 + 清场"，不动状态机 —— 渲染中的任务万一
    --     被 Fail 打断，渲染作业没人收。原有的超时逻辑照旧兜底。
    if App.S.curTask and not App.S.ignoreRc then
        local rc = loadLuaTable(App.S.curTask.rcFile)
        if rc and tonumber(rc.id) == App.S.curTask.id then
            local task = App.S.curTask
            App.S.curTask = nil
            pcall(function() os.remove(task.rcFile) end)
            local code = tonumber(rc.rc) or 0
            if code ~= 0 then
                local why = lastLogLine(task.log or "")
                local brief = tostring(task.script or "任务")
                    .. " 异常退出（退出码 " .. tostring(code) .. "）" .. why
                -- ★ v4.0.2：子进程异常退出是最需要排障的信息，立即落盘，
                --   不能等 3 秒缓冲 —— 万一紧接着 Resolve/插件自己也崩，
                --   这几行就是唯一能追溯真因的线索。
                ELogCritical(brief .. (task.log and task.log ~= ""
                    and (" | log=" .. task.log) or ""))
                self:Log("错误：" .. tostring(task.script or "任务")
                         .. " 异常退出（退出码 " .. tostring(code) .. "）")
                if why ~= "" then self:Log("  · " .. why) end
                if task.log and task.log ~= "" then
                    self:Log("  · 完整日志：" .. task.log)
                end
                -- ★ v1.0.1：必须连状态机一起收掉，不能只写日志。
                --   以前这里只有 Status/Log，phase 还停在 recognizing；
                --   若子进程在写出第一个心跳【之前】就崩（缺依赖 / import 失败），
                --   心跳判定走的是"hb==0 → 等满 300 秒"，界面就挂在
                --   "识别中 3%"、开始按钮灰着，长达 5 分钟没有任何可操作项。
                if App.S.phase == "render_cfg" or App.S.phase == "rendering" then
                    -- 渲染被打断：先把作业和交付页收拾掉，Fail 不管这些
                    pcall(function() self:CleanupRender() end)
                    pcall(function() self:ReturnToPrevPage() end)
                end
                self:Fail("任务失败：" .. tostring(task.script or "任务")
                          .. " 退出码 " .. tostring(code))
            end
        elseif os.time() - (App.S.curTask.startedAt or os.time()) > 900 then
            -- 挂了 15 分钟以上的命令别再跟了（worker 可能被重启过）
            App.S.curTask = nil
        end
    end

    -- 字幕表格分批续填：一批 150 行，剩下的下一拍继续，界面不会卡死
    if App.S.treeQueue then
        local i = App.S.treeQueue
        if i > #App.S.subs then
            App.S.treeQueue = nil
        else
            self:FillSubTreeBatch(i, i + TREE_BATCH - 1)
            App.S.treeQueue = i + TREE_BATCH
        end
    end

    -- ── 待办：把刚保存的字幕同步进媒体池（「更新字幕」的第二步）──────
    --   ★ 不能点完按钮就立刻启动同步：写 SRT 和读 SRT 是两个独立 python
    --     进程（worker 用 Popen 拉起，彼此不等待），同时跑会读到半截文件。
    --     这里等 1 秒（约两拍定时器）再动手。
    if App.S.pendingSync and os.time() >= App.S.pendingSync then
        App.S.pendingSync = nil
        -- ★ v4.3.2：SRT 已落盘 → 把「待剪区间」持久化（关插件再开也能粗剪）
        pcall(function() self:SaveCutSidecar() end)
        if App.S.cutAfterSync then
            -- ★ v4.4.0：删行即剪 —— 写回完成后自动起剪切，跳过 relang
            --   （cut_inplace 自己会把字幕按原时间码重挂，没必要再 relang 一次）
            App.S.cutAfterSync = false
            local okc, why = self:CanCut()
            if okc then
                self:Log("  · 删行即剪：开始切除（保留空隙，字幕按原时间码重挂）")
                self:StartCut("cut")
            else
                self:Log("  · 删行即剪跳过：" .. tostring(why))
            end
        elseif BUSY_PHASES[App.S.phase] then
            self:Log("  · 当前任务还没结束，这次没同步媒体池（字幕文件已是最新）")
        elseif App.S.srt and App.S.srt ~= "" then
            -- ★ v4.0.5 R2：字幕更新闭环。时间线开着就【重挂时间线】（删旧+落新），
            --   否则退化成只【同步媒体池】，避免无时间线时硬报错。
            local hasTL = false
            pcall(function()
                local pm = App.resolve and App.resolve:GetProjectManager()
                local pr = pm and pm:GetCurrentProject()
                hasTL = pr and pr:GetCurrentTimeline() and true or false
            end)
            if hasTL then
                self:Log("  · 字幕更新：时间线开着，闭环重挂字幕轨（R2）")
                self:StartLand("relang", "sync")
            else
                self:Log("  · 字幕更新：当前无时间线，仅同步媒体池（R2 退化）")
                self:StartLand("media", "sync")
            end
        end
    end

    -- ── 字幕编辑器：等 subs_io.py 写出 subs.lua（独立于主状态机）──
    if App.S.subsPending then
        local t = loadLuaTable(App.S.subsLua)
        if t then
            App.S.subsPending = false
            pcall(function() os.remove(App.S.subsLua) end)
            self:FillSubTree(t)
            -- ★ v2.0.8：同一次 dump 里 subs_io.py 也写了 hotwords.lua。
            --   它一定先于 subs.lua 落盘（见 subs_io.write_hotwords_lua 的说明），
            --   所以走到这里读到的必然是本轮的新数据。
            self:RefreshHotwords()
            -- ★ v4.8.25：解析失败要说清楚（空表格 + 一句"解析失败"，
            --   而不是让用户以为文件本来就是空的）。
            local errMark = loadLuaTable(App.S.subsErr)
            if type(errMark) == "table" then
                pcall(function() os.remove(App.S.subsErr) end)
                local emsg = tostring(errMark.err or "")
                if emsg ~= "" then
                    self:Log("⚠ " .. emsg)
                    self:Status("字幕文件解析失败，详见日志")
                end
            end
            -- ★ v4.2.0（B1）：「从时间线读取」的来源标记（轨号/条数/错误）
            local srcMark = loadLuaTable(App.S.subsSrc)
            if srcMark then
                pcall(function() os.remove(App.S.subsSrc) end)
                if tostring(srcMark.err or "") ~= "" then
                    self:Log("读取时间线字幕失败：" .. tostring(srcMark.err))
                elseif (tonumber(srcMark.count) or 0) > 0 then
                    self:Log(string.format(
                        "已从时间线 ST%d 只读载入 %d 条字幕。保存将写入新文件，不会改动任何已有 SRT。",
                        tonumber(srcMark.track) or 0, tonumber(srcMark.count) or 0))
                else
                    self:Log("时间线没有可读取的字幕（未找到「启用且非空」的字幕轨）。")
                end
            end
        elseif os.time() - (App.S.subsStartedAt or os.time()) > 20 then
            App.S.subsPending = false
            self:Log("错误：读取字幕超时（字幕文件仍在：" .. tostring(App.S.srt) .. "）")
        end
    end

    -- ── 交付页就绪等待 + 格式配置（渲染前的准备相位）────────────
    --   OpenPage("deliver") 是异步的：页面没切完就调格式 API 会静默
    --   失败（格式停在 unknown → 渲染零产出）。这里等页面真就绪了
    --   再配置，最多等 3 秒。
    if ph == "render_cfg" then
        local page = ""
        pcall(function() page = tostring(App.resolve:GetCurrentPage() or "") end)
        local waited = os.time() - (App.S.renderCfgAt or os.time())
        -- ★ v1.0.1：3 秒 → 10 秒，并把"正在等页面"写出来。
        --   OpenPage 是异步的（代码里自己都写了），千片段工程切交付页
        --   要读全部渲染预设，3 秒常常不够 → 误报"无法切换到交付页"。
        if page ~= "deliver" and waited < 10 then
            self:Status(string.format("正在切换到交付页…（已等 %d 秒）", waited))
            return                        -- 下一拍再问
        end
        if page ~= "deliver" then
            self:CleanupRender()
            self:Fail("无法切换到交付页（用于导出音频），请稍后重试")
            return
        end
        self:ConfigRender()
        return
    end

    if ph == "rendering" then
        -- ★★ 实测的两条铁律：
        --  (1) GetRenderJobStatus 返回的是【表】，不是两个返回值
        --      { JobStatus = ..., CompletionPercentage = 0-100, Error = ... }
        --  (2) JobStatus 是【本地化字符串】！中文 Resolve 上不是 "Complete"，
        --      葡语是 "Finalizado"/"Concluído"，俄语是 "Завершено"。
        --      → 绝对不能拿它和英文比。真正的完成判据只有两个：
        --          Error 为空  且  CompletionPercentage >= 100
        local rendering = false
        pcall(function() rendering = App.project:IsRenderingInProgress() end)

        local st = nil
        local okQ, res = pcall(function()
            return App.project:GetRenderJobStatus(App.S.jobId)
        end)
        if okQ then st = res else ELog("GetRenderJobStatus 异常: " .. tostring(res)) end

        local jobStatus, pct, jobError = "", 0, nil
        if type(st) == "table" then
            jobStatus = tostring(st.JobStatus or "")
            pct = tonumber(st.CompletionPercentage) or 0
            if st.Error ~= nil and tostring(st.Error) ~= "" then
                jobError = tostring(st.Error)
            end
        elseif type(st) == "string" then
            jobStatus = st
        end
        App.S.lastPct = pct

        local elapsed = os.time() - App.S.renderStartedAt

        -- 进度显示：优先用百分比，拿不到就看已导出的文件大小
        local audioNow, sizeNow = self:FindAudio()
        if pct > 0 then
            pcall(function() App.S.items.StartBtn.Text = string.format("导出中 %d%%", pct) end)
            self:Status(string.format("导出音轨... %d%%", pct))
        elseif sizeNow > 0 then
            pcall(function()
                App.S.items.StartBtn.Text = string.format("导出中 %.0f MB", sizeNow / 1048576)
            end)
            self:Status(string.format("导出音轨... %.0f MB", sizeNow / 1048576))
        end

        -- ── ① 真正出错：Error 非空
        if jobError then
            self:CleanupRender()
            self:ReturnToPrevPage()
            -- ★ v1.0.1：Resolve 给的 Error 是英文/本地化原文，只进日志，
            --   界面上说人话（以前直接把 "Failed to ..." 甩给用户）。
            self:Log("渲染作业报错：" .. jobError)
            self:Fail("导出失败：渲染作业报错，详见日志")
            return
        end

        -- ── ①b 排队等待（★ v1.0.1）
        --    IsRenderingInProgress 是【工程级】的：用户自己正在交付页渲别的
        --    东西时它一直是 true，于是下面"卡住"判定里的 (not rendering)
        --    永远不成立 —— 界面 0% 干挂到 15 分钟总超时，最后只说"导出超时"，
        --    真因（渲染队列被占）一句都没提。这里把它单独认出来并说清楚。
        if rendering and pct <= 0 and elapsed > 20 then
            self:Status(string.format(
                "等待渲染队列…（另有渲染正在进行，已等 %d 秒）", elapsed))
            pcall(function() App.S.items.StartBtn.Text = "等待渲染队列" end)
            if elapsed > 1800 then
                self:CleanupRender()
                self:ReturnToPrevPage()
                self:Fail("排队等待超过 30 分钟：请先在交付页把正在进行的渲染停下")
            end
            return
        end

        -- ── ② 完成：不再渲染 + 百分比到 100（不看 JobStatus 的文字）
        if not rendering and pct >= 100 then
            self:Log("音频导出完成")
            self:CleanupRender()
            self:ReturnToPrevPage()
            local audio = self:FindAudio()
            if audio then
                self:StartPython(audio)
            else
                self:Fail("未生成音频文件（时间线可能没有音频轨）")
            end
            return
        end

        -- ── ③ 卡住：没在渲染、也没到 100%、启动已超过宽限期
        --      实测：作业被排队却从没真正启动（停在 0% 无错误），
        --      重启一次就能起来。真错误的不重启（上面已处理）。
        local GRACE = 10
        if (not rendering) and pct < 100 and elapsed > GRACE then
            if not App.S.restartTried and pct == 0 then
                App.S.restartTried = true
                self:Log("渲染任务未启动，重启一次…")
                local restarted = false
                pcall(function()
                    restarted = App.project:StartRendering({ App.S.jobId }, false)
                end)
                if restarted == true then
                    App.S.renderStartedAt = os.time()
                    return
                end
            end
            -- 重启也没用，且确实没有产出文件 → 判失败
            if sizeNow <= 0 then
                self:CleanupRender()
                self:ReturnToPrevPage()
                self:Fail(string.format(
                    "渲染未开始（%d%%，状态“%s”，已等 %d 秒）", pct, jobStatus, elapsed))
                return
            end
        end

        -- ── ④ 兜底：只要"已经不在渲染"且文件不再增长，就认为写完了。
        --      覆盖两种情况：
        --        · st 为 nil（作业已被移除 / 接口返回空）
        --        · 百分比卡在 99 之类的边界值，永远等不到 100
        --      连续 3 次采样大小不变才算完成（单次判断会误判半成品）。
        if (not rendering) and elapsed > 10 then
            if sizeNow > 0 then
                if App.S.lastSize == sizeNow then
                    App.S.sameCount = (App.S.sameCount or 0) + 1
                else
                    App.S.lastSize = sizeNow
                    App.S.sameCount = 0
                end
                if App.S.sameCount >= 3 then
                    -- ★ v1.0.1：半截 WAV 不能当"导出完成"。
                    --   以前只判"文件不再变大"（3 拍 ≈1.5 秒）：用户在交付页
                    --   按了停止、磁盘满、作业被删，都会得到一份长度不足的
                    --   wav，然后被当成完整音轨送进识别 —— 字幕只覆盖前半段，
                    --   界面还报"完成 N 条"。这里用作业百分比兜一道。
                    if pct > 0 and pct < 50 then
                        self:CleanupRender()
                        self:ReturnToPrevPage()
                        self:Fail(string.format(
                            "音频导出被中断（只完成 %d%%），请重试", pct))
                        return
                    end
                    self:Log(string.format(
                        "音频导出完成（按文件判定 %.1f MB）", sizeNow / 1048576))
                    self:CleanupRender()
                    self:ReturnToPrevPage()
                    self:StartPython(audioNow)
                    return
                end
            elseif elapsed > 60 then
                self:CleanupRender()
                self:ReturnToPrevPage()
                self:Fail("拿不到渲染状态，且未生成音频文件")
                return
            end
        end

        -- ── ⑤ 总超时
        if elapsed > 900 then
            self:CleanupRender()
            self:ReturnToPrevPage()
            -- ★ v1.0.1：把"队列被占"这个最常见真因写进提示，别只报超时
            self:Fail("导出超时（15 分钟）：若交付页还有别的渲染在跑，先停下它再重试")
        end

    elseif ph == "probing" then
        -- ══════════════════════════════════════════════════════════════
        -- 异步等探测结果（原来是同步忙等 15 秒，界面整个卡死）
        --   每 0.5 秒只做一次 loadfile，代价可以忽略，界面全程可响应。
        -- ══════════════════════════════════════════════════════════════
        local t = loadLuaTable(App.S.srcLua or (PROBE_WORK .. "\\src.lua"))
        if t then
            self:OnProbe(t)
            return
        end
        local el = os.time() - (App.S.probeStartedAt or os.time())
        if el > 20 then
            -- 超时：先看有没有纯文本兜底产出，没有才走渲染
            local src = ""
            local tp = App.S.srcTxt or (PROBE_WORK .. "\\src_path.txt")
            if exists(tp) then
                src = tail(tp, 4000):gsub("[\r\n]", "")
                src = src:gsub("^%s+", ""):gsub("%s+$", "")
            end
            if src ~= "" then
                ELog("探测超时（20 秒），但拿到 src_path.txt，按快速路径继续")
                App.S.audioPath = src
                self:SetSrtName(src)
                return self:StartPython(src)
            end
            ELog("探测超时（20 秒），转渲染")
            self:Log("读取时间线超时，走渲染导出")
            self:StartRender()
        end

    elseif ph == "landing" then
        -- ══════════════════════════════════════════════════════════════
        -- 落轨：外部脚本把 SRT 挂到字幕轨后，会写一个 land.lua
        --   （return { ok=.., total=.., sidecar=.., item=.., err=.. }）
        -- 用 loadfile 加载 —— 与进度/心跳同一套机制。
        -- ══════════════════════════════════════════════════════════════
        local d = loadLuaTable(App.S.landFile)
        if d then
            App.S.phase = "done"
            if d.ok then
                local n = tonumber(d.total) or tonumber(d.placed) or 0
                local rep = tonumber(d.replaced) or 0
                local off = tonumber(d.offset) or 0
                if rep > 0 then
                    self:Log(string.format("  · 字幕轨上原有的 %d 条已替换", rep))
                end
                self:Status(string.format("完成：%d 条字幕已上轨", n))
                self:Log(string.format("已挂到时间线字幕轨：%d 条", n))

                -- ★★ v2.0.0：SRT 条数 vs 实际上轨条数比对。
                --    v1.28 实测 2169 条 SRT 只落上 2168 条，界面照样报
                --    "完成：2168 条"，少的那条无人知晓。批量 AppendToTimeline
                --    不给逐条回执，丢不丢由 Resolve 决定，我们只能把差异
                --    和嫌疑条目明确摆出来，不让用户以为全落上了。
                local srtTotal = tonumber(d.srt_total) or 0
                local lost = tonumber(d.lost) or 0
                if lost <= 0 and srtTotal > 0 and n < srtTotal then
                    lost = srtTotal - n
                end
                if lost > 0 then
                    self:Log(string.format(
                        "⚠ 字幕条数不一致：SRT 有 %d 条，字幕轨上只有 %d 条，少 %d 条",
                        srtTotal > 0 and srtTotal or (n + lost), n, lost))
                    local dg = tostring(d.diag or "")
                    if dg ~= "" then
                        self:Log("  · 可能原因：" .. dg)
                    end
                    self:Log("  · Resolve 会合并/丢弃「不足一帧」「同帧起始」或「时间重叠」的字幕条目")
                    self:Log("  · 需要逐条一致时：调大「每行字数」减少条目数")
                    self:Status(string.format("完成：%d 条已上轨（SRT %d 条，少 %d 条）",
                        n, srtTotal, lost))
                end

                -- ★ v4.0.4 架构红线：落轨永远只在当前（原）时间线上原地挂字幕，
                --   绝不新建时间线。历史"新建独立时间线"分支已彻底删除。
                self:Log("  · 字幕已直接落在当前（原）时间线，视频/音频未被改动")

                -- ★ v4.0.7 R2：闭环重挂时，提示删除了多少条旧插件字幕。
                --   ★ v4.0.6 的 write_lua 漏掉了 relang_cleared 字段 → Lua 读到
                --   nil → 永远显示"无旧字幕"，与实际删了几条完全对不上。
                --   已在 write_lua 补齐该字段。
                if (tostring(d.method) == "relang") then
                    local cleared = tonumber(d.relang_cleared) or 0
                    if cleared > 0 then
                        self:Log(string.format(
                            "  · 已删除旧插件字幕 %d 条并重挂（用户手工字幕轨已保留）", cleared))
                    elseif n > 0 then
                        self:Log("  · 时间线此前无本插件字幕，本次仅上轨（未删除旧条目）")
                    else
                        self:Log("  · 时间线无本插件字幕，本次仅同步媒体池（未改动时间线）")
                    end
                end

                local srtFirst = tonumber(d.srt_first) or 0
                if off > 0 then
                    self:Log(string.format(
                        "  · 已与素材对齐（素材起始帧 %d，字幕首条 %d 帧）", off, srtFirst))
                elseif srtFirst > 0 then
                    self:Log(string.format(
                        "  · 已与素材对齐（素材从 0 帧开始，字幕首条 %d 帧，开头是静音）",
                        srtFirst))
                else
                    self:Log("  · 已与素材对齐（素材从时间线 0 帧开始）")
                end
                local side = tostring(d.sidecar or "")
                if side ~= "" then
                    self:Log("  · 素材旁已生成：" .. side)
                end
                local item = tostring(d.item or "")
                if item ~= "" then
                    self:Log("  · 已导入媒体池：" .. item)
                end
            else
                local err = tostring(d.err or "")
                self:Status("适配字幕失败")
                self:Log("错误：挂轨未成功" .. (err ~= "" and ("：" .. err) or ""))
                self:Log("  · 字幕文件在：" .. App.S.srt)
                self:Log("  · 手动导入：菜单「文件 → 导入 → 字幕文件」选它，"
                    .. "或从媒体池拖到字幕轨")
                self:Log("  · 自动挂轨需要 首选项 → 系统 → 常规 → 外部脚本使用 = 本地/网络")
            end

            -- ★ v4.0.9：落轨后轨道状况变了（可能新建了轨 / 换了目标轨），
            --   立刻刷新面板顶部的字幕轨清单 —— 这正是用户要"第一时间知道
            --   有几条轨道"的场景。
            pcall(function() self:RefreshSubTrackInfo(true) end)
            self:EnableButtons()
            return
        end

        -- ★ v3.2.7：落轨进度文件诊断 —— 把「挂轨超时」黑盒打开。
        --   progress 由 land_subtitle.py 在「启动 / 连上 / 落轨 / 完成」各节点覆盖写入，
        --   这里每隔一个 tick 读一次，把当前阶段显示到状态栏（阶段变化才记日志）。
        local prog = loadLuaTable(App.S.landFile .. ".progress")
        if prog then
            local stg = tostring(prog.stage or "")
            local pmsg = tostring(prog.msg or "")
            if stg ~= "" and pmsg ~= "" then
                self:Status("挂轨进行中：" .. pmsg)
                if stg ~= (App.S.lastLandStage or "") then
                    App.S.lastLandStage = stg
                    self:Log("  · 挂轨进度：" .. pmsg)
                end
            end
        end

        -- 超时兜底：先看 progress 文件，判断卡在哪一步（v3.2.7）
        local el = os.time() - (App.S.landStartedAt or os.time())
        if el > 40 then
            App.S.phase = "done"
            -- ★ v1.0.1：超时只代表【本端放弃等待】，Python 还在往字幕轨里写。
            --   以前这里不杀进程、还把「停止」灰掉，又提示"再点一次重试" ——
            --   两条落轨脚本并发写同一条轨，正是最怕的字幕重影。
            pcall(function() self:KillTasks(false) end)
            self:EnableButtons()
            local prog = loadLuaTable(App.S.landFile .. ".progress")
            local stg = prog and tostring(prog.stage or "") or ""
            local diag
            if not prog then
                diag = "落轨脚本似乎未启动（worker 未派发任务，或 Python 环境异常）"
            elseif stg == "start" then
                diag = "落轨脚本已启动，但连接 Resolve 卡住（Resolve 正忙 / 外部脚本被占用 / API 无响应）"
            elseif stg == "connected" then
                diag = "已连上 Resolve，但挂轨步骤卡住（时间线无响应，或弹窗未关闭）"
            elseif stg == "landing" then
                diag = "字幕正在写入字幕轨，但超过 40 秒未完成（条目过多或 Resolve 卡顿）"
            elseif stg == "done" then
                diag = "落轨已结束并写出结果，但本端未能读取到 land.lua（文件被占用？）"
            else
                diag = "落轨状态未知（stage=" .. stg .. "）"
            end
            self:Status("适配字幕超时")
            self:Log("错误：挂轨超时 —— " .. diag)
            self:Log("  · 字幕文件在：" .. App.S.srt)
            self:Log("  · 已尝试终止挂轨进程；等 5~10 秒确认时间线上没有多出字幕后再重试；"
                .. "或手动：菜单「文件 → 导入 → 字幕文件」选它")
        end

    elseif ph == "importing" then
        -- ══════════════════════════════════════════════════════════════
        -- 识别完自动做的那一步：把 SRT 放进媒体池（+ 素材旁一份）。
        --   不动时间线 —— 原地挂字幕轨要等用户点「适配字幕」。
        -- ══════════════════════════════════════════════════════════════
        local d = loadLuaTable(App.S.importFile)
        if d then
            App.S.phase = "done"
            if d.ok then
                local item = tostring(d.item or "")
                if item ~= "" then
                    self:Log("  · 已导入媒体池：" .. item)
                end
                local side = tostring(d.sidecar or "")
                if side ~= "" then
                    self:Log("  · 素材旁已生成：" .. side)
                end
                self:Status(string.format("完成：%d 条字幕", tonumber(App.S.count) or 0))
            else
                local err = tostring(d.err or "")
                self:Log("错误：导入媒体池未成功" .. (err ~= "" and ("：" .. err) or ""))
                self:Log("  · 自动导入需要 首选项 → 系统 → 常规 → 外部脚本使用 = 本地/网络")
            end
            self:Log("")
            self:Log("要让字幕上时间线，点「适配字幕」")
            self:EnableButtons()
            -- 识别完成后顺带把字幕载入右侧编辑区
            self:LoadSubs(true)
            return
        end

        local el = os.time() - (App.S.importStartedAt or os.time())
        if el > 30 then
            App.S.phase = "done"
            self:EnableButtons()
            self:Log("错误：导入媒体池超时（字幕文件仍在：" .. App.S.srt .. "）")
            -- ★ v4.8.24：超时也要把字幕载进右侧表格。
            --   以前只有"读到结果"才 LoadSubs，于是界面上出现
            --   「完成 72 条字幕」+ 空白表格 + 「适配字幕」点不动的组合，
            --   而 SRT 其实已经躺在磁盘上了。
            self:Log("  · 字幕已经生成，先载入编辑区；可稍后重试「适配字幕」")
            -- ★ v1.0.1：超时只代表本端放弃等待。python 往往只是慢，结果随后
            --   还会写进 import.lua —— 留一个补读窗口，别再让"界面说超时、
            --   媒体池其实已经在里面"这种自相矛盾的情况出现。
            App.S.lateImportUntil = os.time() + 120
            self:LoadSubs(true)
        end

    elseif ph == "syncing" then
        -- ══════════════════════════════════════════════════════════════
        -- 「更新字幕」之后的自动同步：把最新的 SRT 重新导入媒体池。
        --   不动时间线 —— 要重挂字幕轨仍然走「适配字幕」。
        --   import_to_pool 会先删掉媒体池里【同名且是字幕类型】的旧项再导入，
        --   所以媒体池里不会堆出好几份，等于原地替换。
        -- ══════════════════════════════════════════════════════════════
        local d = loadLuaTable(App.S.importFile)
        if d then
            App.S.phase = "done"
            self:EnableButtons()
            if d.ok then
                local item = tostring(d.item or "")
                self:Status("字幕已保存并同步媒体池")
                self:Log("媒体池已同步：" .. (item ~= "" and item or App.S.srt))
                local side = tostring(d.sidecar or "")
                if side ~= "" then
                    self:Log("  · 素材旁副本已更新：" .. side)
                end
            else
                local err = tostring(d.err or "")
                self:Status("字幕已保存，媒体池同步失败")
                self:Log("错误：媒体池同步失败" .. (err ~= "" and ("：" .. err) or ""))
                self:Log("  · 自动同步需要 首选项 → 系统 → 常规 → 外部脚本使用 = 本地/网络")
                self:Log("  · 字幕文件本身已是最新：" .. App.S.srt)
            end
            return
        end
        local el = os.time() - (App.S.syncStartedAt or os.time())
        if el > 30 then
            App.S.phase = "done"
            self:EnableButtons()
            self:Status("字幕已保存，媒体池同步超时")
            self:Log("错误：媒体池同步超时（字幕文件本身已是最新：" .. App.S.srt .. "）")
            self:Log("  · 检查 首选项 → 系统 → 常规 → 外部脚本使用 = 本地/网络")
        end

    elseif ph == "cutting" then
        -- ══════════════════════════════════════════════════════════════
        -- 粗剪：Python 在原时间线上重建完，写 cut.lua 回来。
        --   ★ 成功之后必须 LoadSubs 重新读 SRT：字幕时间已经被改写，
        --     界面里那份是旧的，不重读就是"看着还对、其实全错位"。
        -- ══════════════════════════════════════════════════════════════
        local d = loadLuaTable(App.S.cutFile)
        if d then
            App.S.phase = "done"
            self:EnableButtons()
            -- ★ v4.0.0：引擎固定为原地增量剪辑，只有 cut 一种结果，不再有 restore 分支。
            --   字幕仍用原 SRT（原地剪切保留空隙、时间码不变），不另存「· 粗剪.srt」。
            local function useSrt(p)
                p = tostring(p or "")
                if p ~= "" and p ~= tostring(App.S.srt or "") then
                    App.S.srt = p
                    return true
                end
                return false
            end
            if d.ok then
                self:Log(string.format("  · %d 段删除记录 → 时间线共 %d 个片段",
                    tonumber(d.cut_segs) or 0, tonumber(d.clips) or 0))
                -- ★ v4.8.21：切口长度 ≠ 时长缩短。保留空隙（不 ripple），
                --   切在中间/头部只留空洞；只有切到尾部才会真变短。
                self:Log(string.format(
                    "  · 切口 %.2f 秒 ＝ 时长缩短 %.2f 秒 ＋ 保留空隙 %.2f 秒",
                    tonumber(d.cut_s) or tonumber(d.removed) or 0,
                    tonumber(d.saved_s) or 0, tonumber(d.gap_s) or 0))
                self:Log(string.format("  · 时间线 %.2f 秒（切前 %.2f 秒）",
                    tonumber(d.dur_after) or tonumber(d.duration) or 0,
                    tonumber(d.dur_before) or tonumber(d.duration) or 0))
                self:Log("  · 引擎：原地剪切（在当前这条时间线上直接切，未新建）")
                local st = tostring(d.timeline or "")
                if st ~= "" then
                    self:Log("  · 时间线「" .. st .. "」其余片段保持原样")
                end
                if (tonumber(d.subs) or 0) > 0 then
                    self:Log(string.format("  · 字幕重挂 %d 条（时间码不变）", tonumber(d.subs) or 0))
                end
                if useSrt(d.srt) then
                    self:Log("  · 字幕文件已切到：" .. tostring(App.S.srt))
                end
                if (tonumber(d.srt_rows) or 0) == 0 then
                    self:Log("⚠ 没读到字幕内容——视频已切好，字幕保持原样")
                end
                local lost = tonumber(d.lost) or 0
                if lost > 0 then
                    self:Log(string.format("⚠ 有 %d 条字幕没能落到时间线", lost))
                end
                local bk = tostring(d.backup or "")
                if bk ~= "" then
                    self:Log("  · 剪切记录：" .. bk .. "（撤销请按 Ctrl+Z）")
                    self:Log("  · 不满意就在时间线面板按 Ctrl+Z，一次退回")
                end
                App.S.cuts = {}
                App.S.lastCut = bk
                App.S.undoStack = {}   -- ★ v4.3.0：粗剪后清空字幕撤销栈
                -- ★ v4.3.2：切口已执行 → 待剪区间作废，同步清掉 sidecar
                pcall(function()
                    local cp = self:CutSidecarPath()
                    if cp then os.remove(cp) end
                end)
                App.S.subsDirty = false
                -- ★ v4.0.2：声明最近可撤销操作是"粗剪"，撤销按钮据此提示 Ctrl+Z。
                --   之后若用户再改字幕，SnapshotUndo 会把它改回 "subs"，撤销恢复正常。
                App.S.lastUndoKind = "cut"
                self:LoadSubs(true)
                -- ★ v4.8.21：把"缩短"和"留空"分开说，别让用户以为时长少了
                self:Status(string.format("已粗剪 %.1fs（缩短 %.1fs / 留空 %.1fs）",
                    tonumber(d.cut_s) or tonumber(d.removed) or 0,
                    tonumber(d.saved_s) or 0, tonumber(d.gap_s) or 0))
                self:RefreshCutBar()
                self:RefreshDirty()
            else
                local err = tostring(d.err or "")
                self:Status("切除失败")
                self:Log("错误：切除未完成" .. (err ~= "" and ("：" .. err) or ""))
                local bk = tostring(d.backup or "")
                if bk ~= "" then
                    -- Python 在动手前就写了还原清单，所以失败也能退回去
                    App.S.lastCut = bk
                    self:Log("  · 剪切记录：" .. bk)
                    self:Log("  · 不满意可在时间线面板按 Ctrl+Z，一次退回")
                else
                    self:Log("  · 未生成剪切记录；若时间线异常，请按 Ctrl+Z 或重新导入素材")
                end
            end
            return
        end

        local el = os.time() - (App.S.cutStartedAt or os.time())
        if el > 300 then
            App.S.phase = "error"
            self:Fail("切除超时（5 分钟）")
        end

    elseif ph == "exporting" then
        local d = loadLuaTable(App.S.exportFile)
        if d then
            App.S.phase = "done"
            self:EnableButtons()
            if d.ok then
                self:Status(string.format("已导出 %d 个文件", tonumber(d.paths) or 0))
                self:Log("已导出到：" .. tostring(d.dir))
                for ln in tostring(d.names or ""):gmatch("[^\r\n]+") do
                    self:Log("  · " .. ln)
                end
            else
                self:Status("导出失败")
                self:Log("错误：导出失败：" .. tostring(d.err or ""))
            end
            return
        end
        local el = os.time() - (App.S.exportStartedAt or os.time())
        if el > 30 then
            App.S.phase = "done"
            self:EnableButtons()
            self:Status("导出超时")
            self:Log("错误：导出超时")
        end

    elseif ph == "recognizing" then
        -- ══════════════════════════════════════════════════════════════
        -- 进度读取：两个通道
        --  ① 首选 status.lua —— Python 写的 `return {pct=..,count=..}`
        --     用 loadfile 加载。**实测 loadfile 在本机 Resolve 一定可用**，
        --     而 io.open 在 Windows 上既不支持中文路径、沙箱里还可能是 nil。
        --  ② 兜底 status.txt —— 纯文本 "百分比%|条数"
        -- ══════════════════════════════════════════════════════════════
        local pct, cnt, serr = 0, 0, ""
        local smsg = ""                 -- ★ v4.8.24：阶段文案（抽音频/加载模型/第 N 段）
        local swarn = ""                -- ★ v1.0.1：引擎侧的环境告警（如"CPU 模式"）
        local st = loadLuaTable(PROBE_WORK .. "\\status.lua")
        local tStamp = 0
        if st then
            pct = tonumber(st.pct) or 0
            cnt = tonumber(st.count) or 0
            tStamp = tonumber(st.t) or 0
            serr = tostring(st.err or "")
            smsg = tostring(st.msg or "")
            swarn = tostring(st.warn or "")
            -- ★ 实际跑起来的模型（自定义路径无效时这里会带 ⚠ 说明）
            if tostring(st.model or "") ~= "" then
                App.S.realModel = tostring(st.model)
            end
        else
            local c = ""
            if exists(App.S.statusFile) then c = tail(App.S.statusFile, 48) end
            pct = tonumber(c:match("(%d+)%%") or "0") or 0
            cnt = tonumber(c:match("%%|(%d+)") or "0") or 0
        end
        App.S.count = cnt

        -- ── 引擎明确报错？（Python 写 err 字段）
        --   ★ 必须和"完成"分开判断：以前失败路径也报 pct=100，
        --     于是界面显示"未生成字幕"，用户完全不知道出了什么事。
        if serr ~= "" then
            App.S.phase = "error"
            self:Status("识别失败")
            self:Log("识别失败：" .. serr)
            self:Log("--- 引擎输出 ---")
            local lg = tail(App.S.logFile, 1200)
            if lg ~= "" then self:Log(lg) end
            self:EnableButtons()
            return
        end

        if pct > 0 and pct < 100 then
            pcall(function()
                App.S.items.StartBtn.Text = string.format("识别中 %d%%", pct)
            end)
            -- ★ v4.8.24：有阶段文案就用它（"正在加载识别模型…"），
            --   比干巴巴的"识别中... 3%"能说明问题 —— 实测模型加载会停 11 秒。
            -- ★ v1.0.0：剩余时间改由 App:EtaText 统一算（滑动窗口速率，
            --   甩掉模型加载那一段；旧版直接拿「已用/百分比」外推，短素材前段
            --   会高估三十秒以上）。
            local etaTxt = self:EtaText(pct)
            -- ★ v1.0.1：环境告警（例如"有显卡却在跑 CPU"）常驻在状态栏尾巴，
            --   并只往日志写一次完整解释 —— 以前这条信息只在诊断包里，
            --   面板上完全看不出来，用户只会觉得"这插件就是慢"。
            local warnTxt = (swarn ~= "" and (" · " .. swarn) or "")
            if swarn ~= "" and not App.S.warnLogged then
                App.S.warnLogged = true
                self:Log("⚠ 当前为 " .. swarn .. "：检测到 NVIDIA 显卡却没有用上"
                    .. "（识别慢 5~10 倍）。重跑安装器并选 GPU 版即可提速。")
            end
            if smsg ~= "" then
                self:Status(string.format("%s（%d%%%s）%s", smsg, pct, etaTxt, warnTxt))
            else
                self:Status(string.format("识别中... %d%%%s%s", pct, etaTxt, warnTxt))
            end
        elseif pct <= 0 and smsg ~= "" then
            -- 抽音频 / 加载模型阶段 pct 还是 0，只靠文案告诉用户在动
            self:Status(smsg)
        end

        -- ── 完成？只看进度，不去 exists 中文路径的 srt（Lua 打不开）
        if pct >= 100 then
            self:Finish()
            return
        end

        -- ── 引擎还活着吗？
        -- ★ 用【心跳文件】判断，不用 tasklist 查进程：
        --   io.popen 查进程会阻塞界面（每 0.5 秒一次非常卡），
        --   而且脚本是用 pythonw 启动的，查 python.exe 永远查不到
        --   → 之前因此误报"识别进程已退出"。
        if App.S.pyStartedAt == nil then App.S.pyStartedAt = os.time() end
        local elapsed = os.time() - App.S.pyStartedAt

        if elapsed > 8 then
            local hb = 0
            local hbt = loadLuaTable(PROBE_WORK .. "\\hb.lua")
            if hbt then hb = tonumber(hbt.t) or 0 end
            if hb == 0 then hb = tStamp end   -- 兜底：用进度文件里的时间戳

            local stale
            if hb > 0 then
                stale = (os.time() - hb) > 30
            else
                -- 心跳文件都还没出现（模型加载中）→ 给足宽限
                stale = elapsed > 300
            end

            if stale then
                App.S.phase = "error"
                self:Status("识别进程已退出")
                self:Log("识别进程没有响应（心跳已停），可能已崩溃")
                self:Log("--- 引擎输出 ---")
                local lg = tail(App.S.logFile, 1500)
                if lg ~= "" then self:Log(lg) end
                self:EnableButtons()
            end
        end
    end

    -- ★ v1.0.1：导入超时后的补读窗口（见 importing 分支）。
    --   非忙碌阶段才读，免得把新一轮的 import.lua 当成本轮的。
    if App.S.lateImportUntil and not BUSY_PHASES[App.S.phase] then
        if os.time() > App.S.lateImportUntil then
            App.S.lateImportUntil = nil
        else
            local dl = loadLuaTable(App.S.importFile)
            if dl then
                App.S.lateImportUntil = nil
                if dl.ok then
                    local it2 = tostring(dl.item or "")
                    self:Log("  · 媒体池导入结果（迟到）："
                        .. (it2 ~= "" and it2 or "已导入"))
                    self:Status(string.format("完成：%d 条字幕（媒体池已导入）",
                        #App.S.subs))
                else
                    self:Log("  · 媒体池导入（迟到）失败：" .. tostring(dl.err or ""))
                end
            end
        end
    end

    -- ── 收尾：预热常驻识别服务（每次会话只做一次）──────────────
    if ph == "done" and not App.S.srvWarm
        and (tonumber(App.S.count) or 0) > 0 then
        App.S.srvWarm = true
        pcall(function() self:WarmAsrServer() end)
    end

    -- ── 空闲保活 ────────────────────────────────────────────────
    --   worker 空闲 20 分钟会自己退出。它一退，下次点「开始」就得重新
    --   os.execute 拉它 —— 那就会再闪一次黑窗。这里每 30 秒发一个
    --   ping（只是刷新"最近有命令"时间，不启任何进程），把它留住。
    if ph == "idle" or ph == "done" or ph == "error" then
        local nowT = os.time()
        if nowT - (App.S.lastPingAt or 0) >= 30 then
            App.S.lastPingAt = nowT
            self:PingWorker()
        end
    end
end

-- 返回: 完整路径, 字节数（找不到时返回 nil, 0）
function App:FindAudio()
    local files = listFiles(WORK_DIR, "qwen3_audio", ".wav")
    if #files == 0 then return nil, 0 end

    -- ★ 文件名是 qwen3_audio_<YYYYMMDD-HHMMSS>.wav，按字符串排序即按时间先后；
    --   显式取【最新】的一份，避免 bmd.readdir 返回顺序不稳定时误取到旧文件。
    table.sort(files)
    local path = WORK_DIR .. "\\" .. files[#files]
    local size = 0
    if io ~= nil then
        local fh = io.open(path, "rb")
        if fh then size = fh:seek("end") or 0; fh:close() end
    end
    return path, size
end

-- ── 启动 Python ───────────────────────────────────────────────────
-- JSON 字符串转义（只处理必须转义的字符）
-- ★ 中文不转义也不改写：Lua 以 UTF-8 字节原样写出，Python 按 UTF-8 读即可
local function jstr(s)
    s = tostring(s or "")
    s = s:gsub("\\", "\\\\"):gsub('"', '\\"')
    s = s:gsub("\r", "\\r"):gsub("\n", "\\n"):gsub("\t", "\\t")
    return '"' .. s .. '"'
end

-- ══════════════════════════════════════════════════════════════
-- ★★ 后台执行器 worker.py 的客户端
--
-- 【为什么要有这一层】
--   os.execute 在 Windows 上必经 cmd.exe，而 cmd.exe 被 Resolve 这种
--   没有控制台的 GUI 进程拉起时，Windows 会新建一个控制台窗口 ——
--   哪怕命令瞬间结束也会「闪一下黑窗」。
--   原来一次「开始生成字幕」要经历 探测 + 识别 两次 os.execute，
--   加上建目录/列目录的 io.popen，用户会看到好几次黑窗。
--
-- 【怎么解决的】
--   worker.py 用 pythonw 启动一次（GUI 子系统，本身无窗口），之后常驻：
--     Lua 写 cmd.json  →  worker 用 CREATE_NO_WINDOW 启动任务  →  写 res.lua
--   全程零窗口。worker 不在时自动拉起（这时会闪一次，且只在首次），
--   worker 完全拉不起来时回退到原来的 os.execute —— 功能不受影响。
-- ══════════════════════════════════════════════════════════════

-- worker 是否还活着：看心跳文件的时间戳
function App:WorkerAlive()
    local t = loadLuaTable(WHB_FILE)
    if not t then return false end
    local ts = tonumber(t.t) or 0
    if ts <= 0 then return false end
    return (os.time() - ts) < 10
end

function App:EnsureWorker()
    if self:WorkerAlive() then return true end

    -- ★ 窗口一创建就预热过 worker（且空闲时一直 ping 保活），
    --   所以正常情况下走到这里它已经活着了，下面的等待基本不会触发。
    if PYTHON == "" then
        -- 绝不静默：以前会退化成 os.execute 一个空路径，
        -- 用户看到的是"任务超时"，完全指不到真因
        self:Log("错误：找不到 Python 运行环境，任务无法启动")
        self:Log("  · 在 " .. SETTINGS_FILE .. " 里写 python = \"…\\python.exe\" 可指定环境")
        App.S.workerOk = false
        return false
    end
    local py = exists(PYTHONW) and PYTHONW or PYTHON
    local qPy, qWorker = cmdQuote(py), cmdQuote(WORKER)
    if qPy and qWorker then
        local cmd = 'start "" /B ' .. qPy .. ' ' .. qWorker
        pcall(function() os.execute(cmd) end)
        ELog("已尝试拉起 worker")
    else
        ELog("拉起 worker 拒绝：路径含 cmd 特殊字符")
    end

    for _ = 1, 30 do          -- 最多等 3 秒（原来 6 秒，太久）
        sleepMS()
        if self:WorkerAlive() then
            ELog("worker 就绪")
            App.S.workerOk = true
            return true
        end
    end
    ELog("worker 启动超时")
    App.S.workerOk = false
    return false
end

-- 命令号（每条命令都要和 sess 一起组成唯一键，见 worker.py 的说明）
function App:CmdId()
    App.S.cmdId = (App.S.cmdId or 0) + 1
    return App.S.cmdId
end

-- 写一条命令到 cmd.json。body 是已经拼好的字段文本。
-- ★ 必须带 sess：worker 用 (sess,id) 去重，少了 sess 的话，
--   第二次运行本脚本时发出的 id=1 会被当成上次的重复命令丢掉。
function App:SendCmd(id, body)
    local f = io.open(CMD_FILE, "wb")
    if not f then return false end
    f:write('{\n  "id": ' .. tostring(id) .. ',\n')
    f:write('  "sess": ' .. jstr(App.S.sess) .. ',\n')
    f:write(body)
    f:write('\n}\n')
    f:close()
    return true
end

-- 保活：让 worker 知道"还有人在用"，别自己退出
function App:PingWorker()
    if not self:WorkerAlive() then return false end
    local id = self:CmdId()
    if not self:SendCmd(id, '  "cmd": "ping"') then return false end
    return true
end

-- args 可以是字符串（单个参数）或字符串数组
local function argList(args)
    if type(args) == "string" then return { args } end
    if type(args) == "table" then return args end
    return {}
end

-- 兜底：worker 不可用时退回老办法（会闪一次窗，但功能完整）
function App:RunDirect(script, args)
    if PYTHON == "" then
        self:Log("错误：找不到 Python 运行环境，无法启动 " .. tostring(script))
        return false
    end
    local py = exists(PYTHONW) and PYTHONW or PYTHON
    -- ★ P1：所有参数经 cmdQuote 校验，含 " / % / 换行的一律拒绝启动，
    --   避免用户选的目录名或任务参数被 cmd 解释成命令（命令注入）。
    local qPy, qScript = cmdQuote(py), cmdQuote(script)
    if not qPy or not qScript then
        self:Log("错误：运行环境或脚本路径含非法字符，已拒绝启动子进程")
        ELog("RunDirect 拒绝：路径含 cmd 特殊字符")
        return false
    end
    local cmd = 'start "" /B ' .. qPy .. ' ' .. qScript
    for _, a in ipairs(argList(args)) do
        local q = cmdQuote(a)
        if not q then
            self:Log("错误：任务参数含非法字符，已拒绝启动子进程")
            ELog("RunDirect 拒绝：参数含 cmd 特殊字符")
            return false
        end
        cmd = cmd .. ' ' .. q
    end
    ELog("直接启动: " .. cmd)
    pcall(function() os.execute(cmd) end)
    return true
end

-- 通过 worker 启动一个任务脚本，等它确认「已启动」
function App:RunTask(script, args)
    local list = argList(args)

    if App.S.workerOk == false then
        return self:RunDirect(script, list)
    end

    if not self:EnsureWorker() then
        App.S.workerOk = false
        return self:RunDirect(script, list)
    end

    local id = self:CmdId()
    pcall(function() os.remove(RES_FILE) end)
    -- 记下这一条命令。Tick 靠它去接 worker 回传的退出码
    -- （rt\rc_<sess>_<id>.lua）——不出事时它只是被安静地清掉。
    -- ★ sess 必须和 worker.py 的清洗规则一致：去掉非 [0-9A-Za-z_-]
    --   的字符再截前 8 位，否则两边文件名对不上，退出码永远接不到。
    local s0 = tostring(App.S.sess or ""):gsub("[^0-9A-Za-z_%-]", "")
    local safeSess = s0:sub(1, 8)
    if safeSess == "" then safeSess = "s" end
    App.S.curTask = {
        id = id, script = script, startedAt = os.time(),
        rcFile = PROBE_WORK .. "\\rc_" .. safeSess .. "_" .. tostring(id) .. ".lua",
        log = "",
    }
    App.S.ignoreRc = false

    local body = '  "script": ' .. jstr(script) .. ',\n  "args": ['
    for i, a in ipairs(list) do
        if i > 1 then body = body .. ", " end
        body = body .. jstr(a)
    end
    body = body .. "]"

    if not self:SendCmd(id, body) then
        App.S.workerOk = false
        return self:RunDirect(script, list)
    end

    -- worker 现在 0.15 秒扫一次命令文件，正常 0.2~0.5 秒就有回执。
    -- ★ 这段死等是在 RunLoop 线程里跑的，期间整个界面点不动。
    --   上限压到 1.2 秒：等不到就回退到直接启动（会闪一次窗，但功能完整），
    --   比让用户干瞪 2.5 秒好。
    for _ = 1, 12 do
        local t = loadLuaTable(RES_FILE)
        if t and tonumber(t.id) == id then
            ELog("任务已下发 id=" .. tostring(id)
                .. " ok=" .. tostring(t.ok) .. " pid=" .. tostring(t.pid))
            -- 把 worker 给的日志路径记下来：一出事直接把路径报给用户
            if App.S.curTask and App.S.curTask.id == id then
                App.S.curTask.log = tostring(t.log or "")
            end
            if t.ok == false or t.ok == nil then
                -- ★ worker 明确说没起来（互斥 busy / 找不到脚本 / 启动失败）。
                --   以前这里无脑 return true，调用方把 phase 设成
                --   recognizing/landing 之后干等满超时才报错，
                --   白等几十秒还以为是 Resolve 卡了。
                App.S.curTask = nil
                local why = tostring(t.err or "worker 未接受该任务")
                if t.busy then why = "已有同一个任务在运行，稍等一下再点" end
                self:Log("错误：" .. why)
                self:Status("任务未能启动")
                return false
            end
            return true
        end
        sleepMS()
    end

    ELog("worker 无响应，回退直接启动")
    return self:RunDirect(script, list)
end

-- 让 worker 杀掉它启动的所有任务（停止按钮用）
-- ★ 不用 taskkill：那也是 os.execute，一样会闪窗
-- ★ v4.8.25：加参数 all。
--    · KillTasks()     → 发 "kill"，只停当前任务，【保留】预热好的常驻 ASR 服务
--    · KillTasks(true) → 发 "killall"，连常驻服务一起收（关窗口 / 退出插件时）
--  以前一律 "kill"，用户点「停止」会把预热服务一起杀掉，下次识别多等 12 秒加载。
function App:KillTasks(all)
    -- 被 kill 的进程退出码是非 0，不能拿它当"任务失败"报
    App.S.ignoreRc = true
    if self:WorkerAlive() then
        local id = self:CmdId()
        pcall(function() os.remove(RES_FILE) end)
        if all then
            self:SendCmd(id, '  "cmd": "killall"')
            ELog("已下发 killall id=" .. tostring(id) .. "（含常驻服务）")
        else
            self:SendCmd(id, '  "cmd": "kill"')
            ELog("已下发 kill id=" .. tostring(id) .. "（保留常驻服务）")
        end
        return
    end

    -- ★ worker 不在时的兜底：用 worker 落下的 pids.lua【按 PID 精确杀】。
    --   绝对不能退化成 taskkill /F /IM python.exe —— 那会把这台机器上
    --   所有 python 进程（用户自己的脚本、别的工具）一起干掉。
    local pids = loadLuaTable(PIDS_FILE)
    local list = {}
    if pids then
        for _, v in pairs(pids) do
            local p = tonumber(v)
            if p and p > 0 then list[#list + 1] = p end
        end
    end
    -- ★★ v1.2.0：all=true 时把常驻识别服务也带上。
    --   pids.lua 里只有【worker 拉起的子进程】（worker.py 的 sync_pids 只遍历
    --   自己的 procs），而常驻 asr_server 不是它拉起的，压根不在这份清单里。
    --   于是 worker 一旦已死、关窗口走的正是这条兜底路 —— 服务永远收不掉，
    --   一直占着 2~3GB 显存，直到用户自己开任务管理器。这是实测发现的
    --   "KillTasks 收了 all 参数却没用上"的资源泄漏。
    --   服务 pid 在 asr_hb.txt 第一行（JSON：{"pid":1234,...}；老格式是
    --   空格分隔的第 4 段，这里两种都认）。
    if all then
        local hb = io.open(PROBE_WORK .. "\\asr_hb.txt", "rb")
        if hb then
            local raw = (hb:read("*l") or "")
            hb:close()
            local sp = tonumber(raw:match('"pid"%s*:%s*(%d+)'))
            if not sp then
                local parts = {}
                for p in raw:gmatch("%S+") do parts[#parts + 1] = p end
                if #parts >= 4 then sp = tonumber(parts[4]) end
            end
            if sp and sp > 0 then
                local dup = false
                for _, v in ipairs(list) do if v == sp then dup = true end end
                if not dup then
                    list[#list + 1] = sp
                    ELog("兜底结束：附带常驻识别服务 pid=" .. tostring(sp))
                end
            end
        end
    end
    if #list == 0 then
        ELog("没有可结束的任务进程")
        return
    end
    local cmd = "cmd /c taskkill /F"
    -- ★ P1：PID 必须是正整数，别把外部字符串拼进 taskkill 命令行
    for _, p in ipairs(list) do
        local n = tonumber(p)
        if n and n > 0 then cmd = cmd .. " /PID " .. tostring(math.floor(n)) end
    end
    cmd = cmd .. " 2>nul"
    ELog("按 PID 结束: " .. cmd)
    pcall(function() os.execute(cmd) end)
end

function App:StartPython(audio)
    self:Log("启动识别引擎...")

    -- ★★ 用【参数文件】而不是命令行传值。
    --   命令行按 GBK 解读参数，中文路径（SRT 输出目录、素材路径）会变乱码。
    --   参数文件是 UTF-8 JSON，Python 读出来完全正确。
    local argsFile = PROBE_WORK .. "\\args.json"
    local f = io.open(argsFile, "wb")
    if not f then
        self:Fail("无法写入参数文件")
        return
    end
    f:write("{\n")
    f:write('  "audio": ' .. jstr(audio) .. ",\n")
    f:write('  "srt": ' .. jstr(App.S.srt) .. ",\n")
    f:write('  "txt": ' .. jstr(App.S.txt or "") .. ",\n")
    f:write('  "lang": ' .. jstr(App.S.langCode) .. ",\n")
    f:write('  "max_chars": ' .. tostring(App.S.maxChars or 12) .. ",\n")
    -- ★ v4.0.0：字幕无间隙功能删除 → 识别侧 no_gap 恒 false（引擎 remove_gaps 参数保留兼容）
    f:write('  "no_gap": false,\n')
    f:write('  "no_punct": ' .. (App.S.noPunct and "true" or "false") .. ",\n")
    f:write('  "strip_all_punct": ' .. (App.S.allPunct and "true" or "false") .. ",\n")
    f:write('  "split_pause": ' .. (App.S.splitPause and "true" or "false") .. ",\n")
    -- ★ v2.0.7：prompt 恒为空（整项废弃，见 05_ui.lua 里权重词纠错那段说明）。
    --   字段保留着，是为了兼容还没升级的 Python 侧 / 旧诊断包解析。
    f:write('  "prompt": "",\n')
    -- ★ 权重词纠错表：多行文本，jstr 会把换行转成 \n，JSON 里是合法的
    f:write('  "corrections": ' .. jstr(App.S.corrections or "") .. ",\n")
    f:write('  "status": ' .. jstr(App.S.statusFile) .. ",\n")
    f:write('  "model": ' .. jstr(App.S.modelPath or "") .. ",\n")
    -- ★ v2.0.0：最短显示时长（秒）。默认 0.04，和 v1.28 行为一致。
    f:write('  "min_dur": ' .. string.format("%.3f", tonumber(App.S.minDur) or 0.04) .. ",\n")
    -- ★ v1.1.3：只识别素材中的这一段 [起点, 时长]（秒）；没有就整段
    if (tonumber(App.S.trimDur) or 0) > 0.2 then
        f:write('  "audio_trim": [' .. string.format("%.3f, %.3f",
            tonumber(App.S.trimFrom) or 0, tonumber(App.S.trimDur) or 0) .. "],\n")
    end
    f:write('  "log": ' .. jstr(App.S.logFile) .. "\n")
    f:write("}\n")
    f:close()
    ELog("参数文件已写入: " .. argsFile)

    self:Log("使用模型：" .. (App.S.modelPath or "(自动)"))
    self:Log("素材：" .. tostring(audio))

    -- 命令行里只有纯 ASCII 路径，编码问题彻底绕开
    if not self:RunTask(BRIDGE, argsFile) then
        -- 起不来就把场子还给用户（互斥 busy / 启动失败）。
        -- 不这么做的话 phase 卡在 recognizing，要等满超时才动。
        App.S.phase = "error"
        self:EnableButtons()
        return
    end

    App.S.phase = "recognizing"
    -- ★ v4.8.25：记下起点，供状态栏推算「还要多久」
    App.S.recogStartedAt = os.time()
    -- ★ v1.0.0：每轮重新起算 ETA 的三样状态（窗口、加载终点、上一次估值）
    App.S.etaHist = nil
    App.S.etaLoadAt = nil
    App.S.etaLast = nil
    self:Status("识别中...")
end

-- ── 剩余时间估算（v1.0.0 重写）────────────────────────────────────
-- ★ 为什么换算法：旧版是「已用时间 / 当前百分比」直接外推，可"已用时间"里
--   含模型加载（实测 7~17 秒），而百分比里不含 —— 分母被算大。116 秒素材在
--   19% 时给「约剩 1 分 20 秒」，实际只剩 44 秒（**高估 36 秒**）。
--   现在两条线索加权：**最近 60 秒的速率占七成**（跟得上快慢变化）、
--   **扣掉加载后的全程均速占三成**（压住个别片段的抖动）。
--   实测回放（116 秒素材 / 10 分钟素材，按界面实际显示的字比）：
--     · 旧公式：误差中位 7 秒 / 13 秒，最大 73 秒 / 61 秒
--     · 新公式：误差中位 3 秒 / 15 秒，最大 11 秒 / 61 秒
--   短素材前段那个 70 秒级的高估被抹平了，长素材持平。
function App:EtaText(pct)
    local S = App.S
    local now = os.time()
    local t0 = S.recogStartedAt or now
    local el = now - t0

    -- 模型加载结束的那一拍：bridge 在 load() 之后才报 6
    if pct >= 6 and not S.etaLoadAt then S.etaLoadAt = now end
    local loadSpan = (S.etaLoadAt or t0) - t0
    local work = el - loadSpan                    -- 真正在识别的时间

    -- 滑动窗口：只留最近 90 秒、最多 240 拍（Tick 约 0.5 秒一次）
    -- ★ 必须【每拍都记】（同一秒内只记一次），不能"pct 变了才记"：
    --   某个片段卡住十几秒时 pct 不动，稀疏记录里就找不到"60 秒前的参照点"，
    --   窗口会一路退到很早以前，算出来的速率完全是旧的。
    local h = S.etaHist
    if not h then h = {}; S.etaHist = h end
    if #h == 0 or h[#h].t ~= now then
        table.insert(h, { t = now, p = pct })
    else
        h[#h].p = pct
    end
    while #h > 2 and (now - h[1].t > 90 or #h > 240) do table.remove(h, 1) end

    -- 门槛：低于 15% 时窗口样本太少，估了也是瞎猜；扣掉加载后不足 20 秒同理
    if pct < 15 or pct >= 100 or work < 20 then return "" end

    local base = #h
    while base > 1 and now - h[base].t < 60 do base = base - 1 end
    local b = h[base]
    if not b or now - b.t < 5 or pct <= b.p then return "" end

    local winLeft = (100 - pct) / ((pct - b.p) / (now - b.t))   -- 窗口速率
    local globLeft = work * (100 - pct) / pct                   -- 全程均速
    -- ★ v1.0.1：改取两者【较大值】再留 25% 余量。
    --   实测（99.2 秒素材 / CPU / 实时系数 0.77）：pct 75% 时旧公式给「约剩
    --   10~15 秒」，实际还剩 38 秒 —— 最后一段含强制对齐的重试、并条、
    --   长条再切，耗时远大于按百分比外推。估算宁可多报一档，也不要报少了
    --   让用户以为卡住。
    local left = math.max(winLeft, globLeft) * 1.25
    local last = S.etaLast
    if last and left > 0 and math.abs(left - last) > last * 0.5 then
        left = (left + last) / 2               -- 抖得厉害就取中，别让数字乱跳
    end
    S.etaLast = left

    if left >= 60 then
        return string.format("，约剩 %d 分钟", math.max(1, math.floor((left + 30) / 60)))
    elseif left >= 10 then
        return string.format("，约剩 %d 秒", math.floor((left + 5) / 10) * 10)
    end
    return ""
end

-- ── 完成 ──────────────────────────────────────────────────────────
function App:Finish()
    pcall(function() App.S.items.StartBtn.Text = "开始生成" end)

    -- ★ 字幕条数由 Python 写进状态文件（Lua 的 io.open 打不开中文路径的 srt）
    local count = tonumber(App.S.count or 0)

    if count <= 0 then
        App.S.phase = "done"
        self:EnableButtons()
        -- ★ v1.0.1：别只丢一句"未生成字幕"。真因通常是"这段没人说话 /
        --   语言选错 / 自定义模型回退"，用户看到结论不知道下一步做什么。
        self:Status("未识别到字幕（可能没人声、或语言选错）")
        self:Log("未识别到字幕：引擎跑完了，但没有产出任何文本。")
        self:Log("  · 常见原因：整段是音乐/环境声、说话声太小、语言选错、模型下错")
        self:Log("  · 引擎输出见下方，可据此判断是哪一种")
        self:Log("--- 引擎输出 ---")
        local lg = tail(App.S.logFile, 1200)
        if lg ~= "" then self:Log(lg) end
        return
    end

    self:Status(string.format("完成　%d 条字幕", count))
    self:Log(string.format("识别完成：%d 条字幕", count))

    -- ★ 回显【实际使用】的模型，而不是用户填的那个。
    --   自定义路径不存在/没有 config.json 时引擎会静默回退到默认模型 ——
    --   不回显的话用户会一直以为自己在跑 1.7B。
    if App.S.realModel and App.S.realModel ~= "" then
        self:Log("实际模型：" .. App.S.realModel)
    end
    self:Log("字幕文件：" .. App.S.srt)
    if App.S.txt and App.S.txt ~= "" then
        self:Log("文稿文件：" .. App.S.txt)
    end

    -- ★ 识别完【自动】把字幕放进媒体池（+ 素材旁一份），
    --   不再等到点「适配字幕」才做。
    --   但**不自动动时间线** —— 原地改字幕轨是要用户确认的动作。
    self:StartLand("media")
end

-- ══════════════════════════════════════════════════════════════════
-- 预热常驻 ASR 服务（asr_server.py）
--
-- ★ 为什么：模型加载（ASR + 对齐）每次 6~7 秒，而 3 分半的素材识别
--   本身才 30 秒。识别成功一次之后就把服务拉起来，下次直接复用，
--   省掉这 6~7 秒和一次 CUDA 初始化。
-- ★ 它只是加速器：拉不起来 / 崩了 / 模型不匹配，resolve_bridge.py
--   都会自动退回本地加载，功能一点不受影响。所以全流程包 pcall。
-- ══════════════════════════════════════════════════════════════════
function App:WarmAsrServer()
    pcall(function()
        local f = io.open(SRV_PRELOAD, "wb")
        if f then
            f:write(tostring(App.S.modelPath or ""))
            f:close()
        end
    end)
    pcall(function() self:RunTask(ASR_SERVER, {}) end)
    ELog("已预热 ASR 常驻服务")
end

-- ══════════════════════════════════════════════════════════════════
-- 「适配字幕」：把已生成的 SRT 挂到字幕轨 + 导入媒体池 + 素材旁副本
--   实际动作全在外部脚本 land_subtitle.py 里做（Lua 侧只读结果）。
--   ★ 为什么不在 Lua 里做：AppendToTimeline / ImportMedia 一旦参数不对，
--     会把整个 Resolve 的 API 会话搞死（实测崩过两次），隔离到独立进程更安全。
-- ══════════════════════════════════════════════════════════════════
-- 「适配字幕」到底能不能用。
-- ★★ 判据必须是【表格里的字幕条数】，不能是识别计数 App.S.count：
--    用「读取媒体池字幕」载入时 count 是 0，而 EnableButtons 早就改成
--    按 #subs 判断了 —— 两边不一致，按钮是亮的、点了却报"还没有字幕"。
--    真实症状：从媒体池读进 19 条字幕，点「适配字幕」毫无反应。
function App:CanLand()
    if #App.S.subs == 0 then return false end
    if not App.S.srt or App.S.srt == "" then return false end
    return true
end

-- ══════════════════════════════════════════════════════════════════
-- 「开始生成」能不能点（v1.2.0）
--
-- ★ 为什么要有它：以前 StartBtn 在开窗和 EnableButtons 里都被【无条件】
--   置为可点。时间线是空的、工程被关掉、Python 运行时缺失时按钮照样亮，
--   点下去的结果分别是"渲染半天没产出""干等 20 秒探测超时""任务超时才报错"。
--   用户明确要求：条件不满足时按钮就该是灰的，不要让人点了才知道不行。
--
-- ★ 判据全部用【同步、零成本】的 API，绝不在这里跑 Python：
--     · 相位：BUSY_PHASES 内一律灰（渲染/识别/落轨/导出/粗剪/同步）
--     · 环境：SelfCheck 已经把缺的引擎脚本 / Python 记进 App.S.pathBad
--     · 时间线：必须取得到，且音轨或视频轨上至少有一个片段
--   ★ 只看"有没有片段"，不做全量统计 —— 找到第一个就返回，上千片段的
--     时间线上代价也是常数级。
function App:CanStart()
    if BUSY_PHASES[App.S.phase] then return false, "任务进行中" end

    local bad = App.S.pathBad
    if type(bad) == "table" and #bad > 0 then
        return false, "环境自检未通过（" .. tostring(bad[1]) .. "）"
    end

    local tl = nil
    pcall(function()
        local r = App.resolve
        if not r then return end
        local pm = r:GetProjectManager()
        if not pm then return end
        local proj = pm:GetCurrentProject()
        if not proj then return end
        tl = proj:GetCurrentTimeline()
    end)
    -- 取不到就退回开窗时缓存的句柄；换过工程时缓存会失效，所以它只是兜底
    if not tl then tl = App.timeline end
    if not tl then return false, "没有打开时间线" end
    App.timeline = tl

    local found = false
    local okRead = pcall(function()
        for _, kind in ipairs({ "audio", "video" }) do
            local cnt = tonumber(tl:GetTrackCount(kind)) or 0
            for i = 1, cnt do
                local its = tl:GetItemListInTrack(kind, i)
                if its and #its > 0 then found = true; return end
            end
        end
    end)
    -- ★★ 读不出来就【不拦】。宁可让用户点一下看到真错误，也不要因为某个
    --   Resolve 版本返回了非表对象（拿不到 #）就把按钮永久锁死 —— 那是
    --   比"点了才知道不行"更糟的体验。实测：代理对象上取 # 会抛
    --   "attempt to get length of a POBJECT value"。
    if not okRead then return true, "" end
    if not found then return false, "时间线上没有片段" end
    return true, ""
end

-- 按 CanStart 的结果刷新按钮；原因存进 App.S.startWhy，供日志/诊断用。
function App:RefreshStartBtn()
    local it = App.S.items
    if not it or not it.StartBtn then return end
    local ok, why = self:CanStart()
    pcall(function() it.StartBtn.Enabled = ok and true or false end)
    App.S.startWhy = ok and nil or why
    return ok, why
end

-- ★ v4.2.2（修复 4）：本次要落的字幕，之前是不是本插件落的？
--   判据 = 边车 <srt>.landed.json 是否存在（Python 侧只在落轨成功后写它）。
--   有边车 → 落轨会复用专用轨并按跨度删旧，不会重影，无需打扰用户。
function App:HasLandSidecar()
    local srt = tostring(App.S.srt or "")
    if srt == "" then return false end
    local f = io.open(srt .. ".landed.json", "rb")
    if f then
        f:close()
        return true
    end
    return false
end

function App:DoLand()
    if BUSY_PHASES[App.S.phase] then
        self:Status("任务进行中，请稍候")
        self:Log("当前任务还没结束，等它跑完再点「适配字幕」")
        return
    end

    if not self:CanLand() then
        self:Status("还没有字幕")
        self:Log("还没有字幕可挂。请先点「开始生成字幕」，或用「读取媒体池字幕」载入。")
        return
    end

    -- ★ v4.2.2（修复 4）：时间线已有带内容的字幕轨、但【没有边车】证明是本插件落的
    --   —— 这时直接再落一批就会和现有字幕叠在一起（重影）。先问一句再动手。
    --   有边车时不问：那说明是插件自己的轨，落轨流程会先删旧再挂新。
    if not self:HasLandSidecar() then
        local okc, proceed = pcall(function()
            local tracks = self:ReadSubTracks()
            local parts = {}
            for _, t in ipairs(tracks) do
                if (tonumber(t.count) or 0) > 0 then
                    parts[#parts + 1] = string.format("ST%d(%d条)", t.idx, t.count)
                end
            end
            if #parts == 0 then return true end
            local msg = string.format(
                "当前时间线已有 %d 条带字幕的字幕轨：%s\n\n"
                .. "继续「适配字幕」会再挂一批新的，可能与你现有的字幕重叠（重影）。\n\n"
                .. "要继续吗？",
                #parts, table.concat(parts, "、"))
            return self:Confirm(msg)
        end)
        if okc and proceed == false then
            self:Status("已取消")
            self:Log("已取消适配字幕（时间线已有字幕，且没有本插件的落轨记录）。")
            return
        end
    end

    self:Log("")
    self:Log("适配字幕")
    self:Log("  · 字幕文件：" .. App.S.srt)
    self:StartLand()
end

-- ══════════════════════════════════════════════════════════════════
-- 把字幕挂到时间线的【字幕轨】+ 导入媒体池 + 在源素材旁放一份 srt
--
-- ★ 为什么这段逻辑放在外部 Python 脚本里而不是 Lua 里：
--   1. Lua 侧的异常会被 Resolve 静默吞掉，落轨失败时只能看到"没反应"
--   2. ★★ 字幕媒体项给 AppendToTimeline 传 startFrame/endFrame 会
--      把整个 Resolve 当场打崩（实测崩了两次）—— 这类坑集中在 Python 里守，
--      Lua 只负责"启动 + 读结果"
--   3. 中文路径在 Lua 的 io.open / 命令行里都不可靠
--
--   前置条件：首选项 → 系统 → 常规 → 外部脚本使用 = 本地/网络
-- ══════════════════════════════════════════════════════════════════
-- mode = "media"    只导入媒体池 + 素材旁副本（识别后自动入库 / 更新字幕后的同步）
-- mode = "timeline" 落轨（只在当前时间线原地挂字幕轨，绝不新建，由「适配字幕」触发）
-- mode = "relang"   ★ v4.0.5 R2：字幕更新闭环 —— 删旧插件字幕 + 落新字幕，
--                   保留用户手工加的其它字幕轨；时间线无本插件旧字幕时退化为只同步媒体池。
-- reason = "sync"   来自「更新字幕」的同步：走独立的 syncing 阶段，
--                   日志文案和识别后那次自动入库区分开
function App:StartLand(mode, reason)
    mode = tostring(mode or "timeline")
    local isMedia = (mode == "media")
    local isRelang = (mode == "relang")
    local isSync = (isMedia or isRelang) and (reason == "sync")
    -- relang 与 timeline 共用 landFile/landCfg（都落到当前时间线字幕轨）
    local outLua = isMedia and App.S.importFile or App.S.landFile
    local cfgFile = isMedia and App.S.importCfg or App.S.landCfg

    pcall(function() os.remove(outLua) end)

    local f = io.open(cfgFile, "wb")
    if not f then
        App.S.phase = "done"
        self:EnableButtons()
        self:Log("错误：无法写入参数，字幕已存到上面的路径")
        -- ★ v4.8.24：字幕文件本身是好的，表格不该空着
        self:LoadSubs(true)
        return
    end
    f:write("{\n")
    f:write('  "srt": ' .. jstr(App.S.srt) .. ",\n")
    f:write('  "media": ' .. jstr(App.S.audioPath or "") .. ",\n")
    f:write('  "mode": ' .. jstr(mode) .. ",\n")
    -- ★ v2.0.0：用户填的「整体偏移(秒)」。落轨脚本会把它换算成帧，
    --   叠加到它自己算出的素材起始偏移上（只在完整落轨时生效；
    --   只导媒体池那一步不动时间轴，加了也没意义）。
    f:write('  "offset_sec": ' .. string.format("%.3f", tonumber(App.S.offsetSec) or 0) .. ",\n")
    -- ★ v1.0.1：素材旁同名 srt 开关（设置页可关；缺省 true）
    f:write('  "sidecar": ' .. (((App.S.cfg and App.S.cfg.sidecar == false) and "false") or "true") .. ",\n")
    f:write('  "out_lua": ' .. jstr(outLua) .. "\n")
    f:write("}\n")
    f:close()
    ELog("落轨参数已写入 " .. cfgFile .. " mode=" .. mode)

    -- 命令行里只有纯 ASCII 路径（中文值都在 json 里）
    if not self:RunTask(LAND_SCRIPT, cfgFile) then
        App.S.phase = "done"
        self:EnableButtons()
        -- ★ v1.0.1：这条以前是【完全静默】的 —— 状态栏写着"完成 N 条字幕"、
        --   表格也有字，唯独媒体池没有，日志一个字都没有，用户只能怀疑人生。
        self:Log("错误：媒体池导入未能启动（worker 拒绝或不可用）")
        self:Log("  · 自动导入需要 首选项 → 系统 → 常规 → 外部脚本使用 = 本地/网络")
        self:Status("字幕已生成，媒体池未导入")
        -- ★ v4.8.24：任务没起来也要把 SRT 载进表格 —— 字幕是存在的，
        --   表格空着会让「适配字幕」一直灰着，用户以为识别白做了。
        self:LoadSubs(true)
        return
    end

    if isMedia then
        -- 两种语义共用同一个输出文件（importFile），靠 phase 区分
        if isSync then
            App.S.phase = "syncing"
            App.S.syncStartedAt = os.time()
            self:Status("正在同步媒体池...")
        else
            App.S.phase = "importing"
            App.S.importStartedAt = os.time()
            self:Status("正在导入媒体池...")
        end
    else
        -- timeline / relang 都走 landing 阶段（落时间线字幕轨）
        App.S.phase = "landing"
        App.S.landStartedAt = os.time()
        App.S.lastLandStage = nil
        if isRelang then
            self:Status("正在重挂更新后的字幕...")
            self:Log("正在把更新后的字幕重新挂到时间线字幕轨（删旧+落新，保留手工字幕轨）…")
        else
            self:Status("正在挂到字幕轨...")
            self:Log("正在把字幕挂到时间线字幕轨…")
        end
    end

    -- ★ v4.8.24：导入/落轨阶段也点亮「停止」。
    --   落轨最长等 40 秒、导入 30 秒，以前这两个阶段按钮是灰的，
    --   卡住时用户除了等没有别的选择（App:Cleanup 本来就能 kill 任务）。
    pcall(function() App.S.items.StopBtn.Enabled = true end)
end

-- ══════════════════════════════════════════════════════════════
-- 字幕编辑器
--
-- ★ 为什么读写 SRT 要绕 Python：SRT 在中文目录里，Lua 的 io.open 走
--   ANSI 接口打不开中文路径。数据流：
--     读：subs_io.py dump → rt\subs.lua   → Lua 用 loadfile 读
--     写：Lua 拼 JSON    → rt\subs_in.json → subs_io.py save → 覆写 SRT
-- ══════════════════════════════════════════════════════════════

-- ★ fmtSec / parseTimeText 已上移到文件前部（BuildUI 之前）。
--   原因：Lua 的 local 只对【定义之后】的代码可见，而 BuildUI 里的
--   就地编辑回调（ItemChanged）要用到它们。留在原处的话，回调里
--   parseTimeText 会被解析成全局变量 → nil → "attempt to call a nil value"。

-- 把查找词转成 Lua 模式的字面量（中文不受影响，只转义 ASCII 元字符）
local function patternEscape(s)
    return (tostring(s):gsub("([%^%$%(%)%%%.%[%]%*%+%-%?])", "%%%1"))
end

-- ══════════════════════════════════════════════════════════════
-- 查找：重复序号 + 关键词高亮 + 跳到对应行
--
-- ★★ 为什么整块放在这里（patternEscape 之后、FillSubTreeBatch 之前）：
--    Lua 的 local 只对【定义之后】的代码可见。分批填表
--    （FillSubTreeBatch）也要调 PaintRow，放在文件后部的话它会拿到
--    nil —— 和之前 fmtSec/parseTimeText 那个 bug 同一类。
-- ══════════════════════════════════════════════════════════════
-- ★★★ v3.2.0：文字上色（TextColor / 黄点图标）全部删除 ——
--    Tree 单元格只能整格 TextColor，一染就是"整段字幕变黄"，用户拒绝。
-- ★★★ v3.2.5（方案 A）：恢复【底色】高亮 —— 命中行只给「字幕文本」格
--    铺琥珀底色，文字颜色不动（不会整段变黄）。优先级天然满足要求：
--      Qt 多选高亮（系统选中色画在 BackgroundColor 之上）> 当前处亮金 > 命中暗金。
--    词级高亮仍在编辑区（TextEdit HTML，见 RenderSubEditRich / htmlMark）。
--    3 列：0=开始 1=结束 2=字幕文本（# 行号列已撤掉）
local TXT_COL = 2

-- ── 命中行底色两档（沿用旧版验证过的琥珀色系）────────────────────
local HILITE_COLOR = { R = 0.30, G = 0.26, B = 0.10, A = 0.60 }  -- 命中：暗金
local HILITE_CUR   = { R = 0.58, G = 0.44, B = 0.10, A = 0.90 }  -- 当前处：亮金
local HILITE_CLEAR = { R = 0.0, G = 0.0, B = 0.0, A = 0.0 }      -- 清除（透明，还给斑马纹）
-- 给「字幕文本」格上底色。★ 失败静默：个别 Resolve 版本的 TreeItem 不暴露
--   BackgroundColor，那种情况黄底只是不显示，跳转 / 计数照旧能用。
local function tintCell(item, color)
    if not item then return false end
    return (pcall(function() item.BackgroundColor[TXT_COL] = color end))
end

-- 取第 row 行（1 基）的树项。TopLevelItem 是 0 基下标。
-- ★ 包 pcall：行数刚变化时（比如删了一批）下标可能越界，越界不该把
--   整个刷新流程带崩，最多这一行没刷新。
local function rowItem(tree, row)
    if not tree or not row or row < 1 then return nil end
    local it = nil
    pcall(function() it = tree:TopLevelItem(row - 1) end)
    return it
end

-- 单行刷新：干净原文 + 命中底色（v3.2.5 方案 A）。
--   · 文本：表格里永远是干净原文（v3.2.2 起不再加【】），
--     只在内容真的变了才写（跨进程写属性开销大，几百行要省着用）
--   · 底色：命中行只染「字幕文本」格（时间列保持干净）。
--     优先级：Qt 多选高亮（系统选中色画在 BackgroundColor 之上，天然覆盖）
--     > 当前处亮金 > 命中暗金 —— 被选中的行黄色让位，不需要特殊处理。
--     状态没变就一个字节都不写（tintState 缓存）。
--   · 写之前挡掉 ItemChanged，否则会被当成"用户在格子里改了字"
function App:PaintRow(row)
    local items = App.S.items
    if not items or not items.SubTree then return end
    local b = App.S.subs and App.S.subs[row]
    if not b then return end
    local it = rowItem(items.SubTree, row)
    if not it then return end
    -- ★ 表格里永远是干净原文。旧版本可能写进过【】，这里一写就把残留冲掉。
    local display = tostring(b.t or ""):gsub("\n", " ")
    local cur = nil
    pcall(function() cur = it.Text[TXT_COL] end)
    if cur ~= display then
        local oldGuard = App.S.suppressTreeEdit
        App.S.suppressTreeEdit = true
        pcall(function() it.Text[TXT_COL] = display end)
        App.S.suppressTreeEdit = oldGuard
    end
    -- ★ v3.2.5：命中底色三态：""(清) / "hit"(暗金) / "cur"(亮金=当前处)
    local want = ""
    if App.S.hitSet and App.S.hitSet[row] then
        want = "hit"
        if App.S.curMatch and App.S.matchRows
           and App.S.matchRows[App.S.curMatch] == row then
            want = "cur"
        end
    end
    App.S.tintState = App.S.tintState or {}
    if App.S.tintState[row] == want then return end
    -- 新行默认就是无色：第一次"清"不必真写，省一整轮跨进程调用
    if want == "" and App.S.tintState[row] == nil then
        App.S.tintState[row] = ""
        return
    end
    App.S.tintState[row] = want
    local color = (want == "cur") and HILITE_CUR
               or (want == "hit") and HILITE_COLOR
               or HILITE_CLEAR
    local ok = tintCell(it, color)
    if (not ok) and (not App.S.tintWarned) then
        App.S.tintWarned = true
        self:Log("[paint] 表格 BackgroundColor 不可用 → 命中行黄底不显示（功能不受影响）")
    end
end

-- 整表刷新（命中集合或字幕内容变了才走）
function App:PaintAllRows()
    for i = 1, #App.S.subs do self:PaintRow(i) end
end

-- 统计 kw 在 s 里出现的次数（纯文本匹配，不走 Lua 模式）
local function countIn(s, kw)
    if kw == "" then return 0 end
    local cnt, from = 0, 1
    while true do
        local p = s:find(kw, from, true)
        if not p then break end
        cnt = cnt + 1
        from = p + #kw
    end
    return cnt
end

-- 当前查找框里的词
-- ★★★ v3.1.5：读查找框文本 —— 多路回退，读不到返回 nil（不是 ""）。
--
--   为什么不能只用 el.Text：有些 Resolve / Fusion 版本上这个属性
--   要么抛错、要么读回来不是字符串（userdata）。旧代码用
--   tostring(...or "") 把这三种情况全糊成 ""，于是关键词永远为空，
--   命中数恒为 0、也不会有任何黄色 —— 症状正好是"框里写着字，命中 0"。
--
--   返回 nil 而不是 "" 很重要：调用方据此判断"这次没读到"，
--   从而回退到 TextChanged 事件缓存，而不是把索引清空。
function App:ReadFindKwRaw()
    local el = App.S.items and App.S.items.FindInput
    if not el then return nil end
    local v = nil
    pcall(function() v = el.Text end)          -- 主流：属性
    if type(v) == "string" then return v end
    pcall(function() v = el:GetText() end)     -- 备选：方法
    if type(v) == "string" then return v end
    pcall(function() v = el.String end)        -- 备选：Fusion 早期命名
    if type(v) == "string" then return v end
    return nil
end

function App:FindKeyword()
    local v = self:ReadFindKwRaw()
    if v ~= nil then
        App.S.findKwCache = v
        return v
    end
    -- 读不到控件就用事件缓存；缓存也没有才认 ""。
    return App.S.findKwCache or ""
end

-- 重建匹配索引：matchRows[k] = 第 k 处所在行号
-- ★ 关键词变过、或字幕内容变过，都必须重建 —— 否则"第几处"是假的。
function App:RebuildMatches()
    local kw = self:FindKeyword()
    local prevKw = App.S.matchKw
    App.S.matchKw = kw
    App.S.matchRows = {}
    -- ★ v3.2.0：命中行的哈希集合。PaintRow 每行都要问"这行命中了吗"，
    --   用集合是 O(1)；原来每行都遍历一遍 matchRows，搜"的"这种词
    --   （上千处）时整表刷新会明显变慢。
    App.S.hitSet    = {}
    App.S.matchTotal = 0
    App.S.curMatch = nil
    -- ★ v2.0.6：关键词变了，之前排队待跳的位置就作废了（它对应的是旧
    --   关键词的命中点）。不清掉的话，用户改完词停手 0.2 秒，播放头会
    --   跳到上一个关键词的位置上，看起来像"跳错地方"。
    App.S.pendJump = nil
    App.S.pendJumpAge = nil
    App.S.pendJumpWall = nil
    if kw == "" then
        if prevKw ~= kw then
            ELog(string.format("[search] kw=<> hits=0 rows=%d  (keyword empty)", #App.S.subs))
        end
        return
    end
    for i = 1, #App.S.subs do
        local c = countIn(tostring(App.S.subs[i].t or ""), kw)
        for _ = 1, c do
            App.S.matchTotal = App.S.matchTotal + 1
            App.S.matchRows[App.S.matchTotal] = i
        end
        if c > 0 then App.S.hitSet[i] = true end
    end
    -- ★ v3.1.5：留一行诊断痕迹。命中数异常时（框里有字却命中 0），
    --   看这行就能判断是"关键词没读到"还是"匹配不到"。
    if prevKw ~= kw then
        ELog(string.format("[search] kw=[%s] hits=%d rows=%d",
                           kw, App.S.matchTotal, #App.S.subs))
    end
end

-- 第 row 行在匹配索引里的最后一处（用户手动点了某行时用）
function App:MatchIndexAtRow(row)
    if not App.S.matchRows then return nil end
    local last = nil
    for k, r in ipairs(App.S.matchRows) do
        if r == row then
            last = k
        elseif r > row then
            break
        end
    end
    return last
end

-- 全量重绘：先全清、再按索引重新上色。
-- ★ 只在【关键词变了 / 字幕内容变了 / 表格重建】时走这里。
-- ★ v3.1：直接交给 PaintRow 统一处理（命中 / 勾选 / 命中当前 三态），
--   保证勾选青色与命中琥珀不互相覆盖。
function App:PaintAllMatches()
    local items = App.S.items
    if not items or not items.SubTree then return end
    local curRow = nil
    if App.S.curMatch and App.S.matchRows then
        curRow = App.S.matchRows[App.S.curMatch]
    end
    for i = 1, #App.S.subs do self:PaintRow(i) end
    -- ★ v3.2.3：关键词变了 → 编辑区里的高亮也要跟着变。
    --   但用户正在【编辑区】打字时不动：设置内容会重置光标，打字会断。
    --   （用 editFocus 而不是 textFocus：后者在查找框里打字时也是 true）
    if not App.S.editFocus then
        pcall(function() self:RenderSubEditRich() end)
    end
    -- 记下这次全量绘制的状态，供增量分支判断"能不能只动两行"
    App.S.hlKw = App.S.matchKw
    App.S.hlN = App.S.matchRows and #App.S.matchRows or 0
    App.S.hlCurRow = curRow
end

-- 把高亮刷进表格。关键词变过 / 字幕内容变过都调它。
--
-- ★★ v2.0.3：加了 full 参数与增量分支。
--    v2.0.2 及以前，「查找下一个」每点一次都走全量重绘：
--      先对【所有】行清色（2000 行 = 2000 次 TopLevelItem + 2000 次属性写），
--      再对【所有命中】重新上色（搜"的"这种词，命中几百上千处）。
--    每次点击都是几千次跨进程调用 → 用户感受到的就是"点一下要等一下"，
--    1 小时以上的长字幕尤其明显。
--    实际上点"下一处"只改变【一处】的高亮位置：把上一个当前行恢复成普通
--    命中色、把新的当前行染成当前色，两次调用就够了。
function App:ApplyFindHighlight(full)
    local items = App.S.items
    if not items or not items.SubTree then return end
    -- ★ 默认【全量】：不传参数的调用方（现在和以后新增的）一律走最保守的
    --   整表重绘，绝不会出现"改了内容颜色不更新"。只有「查找下一个」这种
    --   明确知道"命中集合没变"的地方才显式传 false 走增量。
    if full == nil then full = true end
    local kw = self:FindKeyword()
    if kw ~= App.S.matchKw or not App.S.matchRows then
        self:RebuildMatches()
        full = true          -- 索引刚重建，必须整表重刷
    end
    local n = App.S.matchRows and #App.S.matchRows or 0
    if full or App.S.hlKw ~= kw or App.S.hlN ~= n then
        return self:PaintAllMatches()
    end

    -- ── 增量：只动"上一个当前行"和"新的当前行" ──
    local curRow = nil
    if App.S.curMatch and App.S.matchRows then
        curRow = App.S.matchRows[App.S.curMatch]
    end
    local old = App.S.hlCurRow
    if old == curRow then return end
    if old then self:PaintRow(old) end
    if curRow then self:PaintRow(curRow) end
    App.S.hlCurRow = curRow
end


-- 下发 dump：让 Python 把 SRT 转成 Lua 表
function App:LoadSubs(quiet)
    if not App.S.srt or App.S.srt == "" then
        if not quiet then self:Log("还没有字幕文件，先生成字幕。") end
        return
    end
    -- ★ v4.0.2：换字幕时把「作废的删除记录」讲清楚，不再静默归零。
    --   cutSrt 记录了删除区间属于哪份字幕；载入另一份时这些区间不再成立。
    --   以前 CutStats 直接 return 0,0，用户只看到「将切除 N 段」凭空消失。
    --   这里在载入新字幕前，若发现旧记录且 SRT 已变，就提示一次并清空，
    --   让状态条/按钮同步归零。粗剪成功后 cuts 已清空，不会再触发。
    pcall(function()
        if #(App.S.cuts or {}) > 0
           and (App.S.cutSrt or "") ~= (App.S.srt or "") then
            self:Log("⚠ 换了字幕文件，之前记录的 " .. tostring(#App.S.cuts)
                     .. " 段删除区间已作废（属于上一份字幕）。如需粗剪，请重新选中要剪的行再删除。")
            App.S.cuts = {}
            App.S.cutSrt = ""
            self:RefreshCutBar()
        end
    end)
    -- ★ v4.3.2：同一份 SRT 再次载入时，恢复上次会话留下的待剪区间
    pcall(function() self:LoadCutSidecar() end)
    local f = io.open(App.S.subsCfg, "wb")
    if not f then
        self:Log("错误：无法写入字幕读取参数")
        return
    end
    f:write("{\n")
    f:write('  "mode": "dump",\n')
    f:write('  "srt": ' .. jstr(App.S.srt) .. ",\n")
    f:write('  "out": ' .. jstr(App.S.subsLua) .. "\n")
    f:write("}\n")
    f:close()

    pcall(function() os.remove(App.S.subsLua) end)
    -- ★ v4.2.0（B1）：普通 dump 前清掉上一次「从时间线读取」留下的来源标记，
    --   否则 Watcher 会把旧标记当成这一轮的结果，误报"从 STn 载入"。
    pcall(function() os.remove(App.S.subsSrc) end)
    -- ★ v4.8.25：解析失败标记同理，不清就会把上一轮的报错当成本轮结果
    pcall(function() os.remove(App.S.subsErr) end)
    if not self:RunTask(SUBS_IO, App.S.subsCfg) then
        -- ★ worker 拒绝（互斥 busy / 找不到脚本）：别置 pending 去干等 20 秒
        --   超时 —— 那样报的是"读取字幕超时"，和真因无关。直接说清楚。
        App.S.subsPending = false
        self:Log("错误：字幕读取未能启动（worker 拒绝或不可用）")
        return
    end
    App.S.subsPending = true
    App.S.subsStartedAt = os.time()
end

-- 用一份字幕数据重建表格
function App:FillSubTree(blocks)
    local items = App.S.items
    if not items or not items.SubTree then return end
    App.S.subs = blocks or {}
    local tree = items.SubTree
    pcall(function() tree:Clear() end)
    pcall(function()
        tree:SetHeaderLabels({ "开始", "结束", "字幕文本" })
        -- ★ v3.2.0：撤掉「#」列后剩下 3 列，宽度重新分配。
        tree.ColumnWidth[0] = 96
        tree.ColumnWidth[1] = 96
        tree.ColumnWidth[2] = 460
    end)
    -- ★ v3.1：重建表格 = 勾选集合作废（新 TreeItem 默认未勾选）
    App.S.checked = {}
    -- ★ v3.2.5：底色缓存同理必须清空。新的 TreeItem 默认没有底色，
    --   若沿用旧的 tintState，PaintRow 会以为"这行已经是暗金了"直接返回，
    --   结果就是重建后整表都不上色。
    App.S.tintState = {}
    -- ★ 分批填：几百条字幕一次性 NewItem/AddTopLevelItem 会把界面卡死
    --   （Qt 每加一行都重排一次）。第一批同步填，剩下的交给 Tick 续填。
    App.S.treeQueue = nil
    local total = #App.S.subs
    self:FillSubTreeBatch(1, math.min(total, TREE_BATCH))
    if total > TREE_BATCH then
        App.S.treeQueue = TREE_BATCH + 1
    end
    App.S.suppressEdit = true
    pcall(function() items.SubEdit.PlainText = "" end)
    App.S.suppressEdit = false
    -- 表格里有东西才让点这些按钮（字幕也可能来自「读取媒体池字幕」）
    local has = (#App.S.subs > 0)
    pcall(function() items.UpdateSubsBtn.Enabled = has end)
    -- 「适配字幕」按 CanLand 判：没字幕可挂时保持 disabled（灰的），
    -- 不会显得"能点"——用户明确要求过这一点
    pcall(function() items.LandBtn.Enabled = self:CanLand() end)
    pcall(function() items.TxtBtn.Enabled = has end)
    -- 字幕内容整个换过 → 旧匹配索引全部作废，重建并按新内容整表上色
    --
    -- ★★ v3.1.6：这三步【必须各自 pcall】。
    --   真实的坑：上色那一步抛过一次 "attempt to call global 'rowItem'"，
    --   整个 FillSubTree 就在这里中断 —— 后面的 RefreshSelBar 永远执行不到，
    --   于是工具条一直停在布局时的初始文本"命中 0 / 已选 0 / 0 行"，
    --   看起来像"计数功能坏了"，其实是根本没被调用过。
    pcall(function() self:RebuildMatches() end)
    pcall(function() self:ApplyFindHighlight(true) end)
    -- 表格重建后刷新选择工具条 / 脏标记 / 当前行信息
    pcall(function() self:RefreshSelBar() end)
    pcall(function() self:RefreshDirty() end)
    pcall(function() self:RefreshRowInfo() end)
end

-- 填表格的 [from, to] 区间（闭区间），供 FillSubTree 分批调用
function App:FillSubTreeBatch(from, to)
    local items = App.S.items
    if not items or not items.SubTree then return end
    local tree = items.SubTree
    for i = from, math.min(to, #App.S.subs) do
        local b = App.S.subs[i]
        if not b then break end
        local ok = pcall(function()
            local item = tree:NewItem()
            -- ★ v3.2.0：3 列布局。# 行号列已撤掉，行号改存 Text[3] 这个
            --   「不可见的内部槽位」（ColumnCount=3，第 3 列不显示，不占宽度），
            --   rowFromTree / CollectSelected 仍靠它反查行号。
            item.Text[0] = fmtSec(b.s)           -- 开始
            item.Text[1] = fmtSec(b.e)           -- 结束
            item.Text[2] = (tostring(b.t or ""):gsub("\n", " "))  -- 字幕文本
            -- 行号存进不显示的第 3 列（内部槽位，不占宽度）。
            -- 单独兜一层：万一某个版本在 ColumnCount=3 时拒绝写第 3 列，
            -- 也不能把整行创建带崩（itemToRow 还有 IndexOfTopLevelItem 兜底）
            pcall(function() item.Text[3] = tostring(i) end)
            pcall(function()
                local f = {
                    ItemIsSelectable    = true,
                    ItemIsDropEnabled   = true,
                    ItemIsTristate      = false,
                    ItemNeverHasChildren = false,
                    ItemIsDragEnabled   = true,
                    ItemIsEditable      = true,
                    ItemIsUserCheckable = false,
                    ItemIsEnabled       = true,
                }
                if item.SetFlags then
                    item:SetFlags(f)
                else
                    item.Flags = f
                end
            end)
            tree:AddTopLevelItem(item)
        end)
        if not ok then
            self:Log("错误：表格填充中断在第 " .. i .. " 行")
            break
        end
    end

    -- 新建的这一批行立刻按当前查找词 + 勾选态上色（PaintRow 同时尊重
    -- 命中高亮与勾选青色，统一一处逻辑，避免两套重绘打架）。
    local hi = math.min(to, #App.S.subs)
    for r = from, hi do self:PaintRow(r) end
end

-- 把当前字幕数据写成 Python 能读的 JSON（写回 SRT / 生成文稿共用）
function App:WriteSubsJson()
    if #App.S.subs == 0 then return false end
    local f = io.open(App.S.subsJson, "wb")
    if not f then
        self:Log("错误：无法写入字幕数据")
        return false
    end
    -- ★ 文本为空的行【不写出去】。
    --   编辑框是实时同步的，打字中途把内容删空是很自然的动作；
    --   以前会把它当成一条空字幕写进 SRT（时间轴还在、内容没了）。
    --   识别流程本来就有一道"空块兜底"过滤，手动编辑绕过了它，这里补上。
    local parts, kept = {}, 0
    for _, b in ipairs(App.S.subs) do
        local t = tostring(b.t or ""):gsub("^%s+", ""):gsub("%s+$", "")
        if t ~= "" then
            kept = kept + 1
            parts[#parts + 1] = string.format(
                '    { "s": %.3f, "e": %.3f, "t": %s }',
                tonumber(b.s) or 0, tonumber(b.e) or 0, jstr(t))
        end
    end
    if kept == 0 then
        f:close()
        self:Log("错误：所有字幕都是空的，没有内容可保存。")
        return false
    end
    f:write('{\n  "blocks": [\n')
    f:write(table.concat(parts, ",\n"))
    f:write("\n  ]\n}\n")
    f:close()
    return true
end

-- 把当前字幕数据写回 SRT
-- ★ v4.5.0：第一次保存时把「编辑版」另存为新文件，原件留在媒体池。
--   诉求：识别产出的 SRT 是【原始结果】，剪过的字幕应该是【另一份】。
--   · 只 fork 一次：文件名带 _edit 后缀就不再 fork；
--   · 从时间线读取生成的 track_load_* 已经算"编辑产物"，不再套后缀；
--   · cutSrt 跟着改，否则「视频粗剪」会因为"换了字幕文件"判定作废。
function App:EnsureEditSrt()
    local srt = tostring(App.S.srt or "")
    if srt == "" then return end
    local low = srt:lower()
    if low:match("_edit%.srt$") or low:match("track_load_") then return end
    local old = srt
    local stem = srt:gsub("%.[Ss][Rr][Tt]$", "")
    local new = stem .. "_edit.srt"
    App.S.srt = new
    if App.S.txt and App.S.txt ~= "" then
        App.S.txt = (App.S.txt:gsub("%.[Tt][Xx][Tt]$", "")) .. "_edit.txt"
    end
    if (App.S.cutSrt or "") == old then App.S.cutSrt = new end
    self:Log("  · 编辑版另存为：" .. new)
    self:Log("    （原始字幕保留不动，媒体池里会多出这一份）")
end

function App:SaveSubs(quiet)
    if #App.S.subs == 0 then
        if not quiet then self:Log("字幕为空，无需保存。") end
        return false
    end
    if not self:WriteSubsJson() then return false end

    -- ★ 文稿必须跟着一起重新生成。识别时那份 txt 只反映当时的识别结果，
    --   用户在编辑器里改过之后它就过期了，导出会出现"改了没生效"。
    if not App.S.txt or App.S.txt == "" then
        if App.S.srt and App.S.srt ~= "" then
            -- 没记过文稿路径（比如字幕是从媒体池读进来的），按 SRT 同名推一个
            App.S.txt = (App.S.srt:gsub("%.[Ss][Rr][Tt]$", "")) .. ".txt"
        end
    end

    local c = io.open(App.S.subsCfg, "wb")
    if not c then return false end
    c:write("{\n")
    c:write('  "mode": "save",\n')
    c:write('  "srt": ' .. jstr(App.S.srt) .. ",\n")
    c:write('  "in": ' .. jstr(App.S.subsJson) .. ",\n")
    c:write('  "txt": ' .. jstr(App.S.txt or "") .. "\n")
    c:write("}\n")
    c:close()
    if not self:RunTask(SUBS_IO, App.S.subsCfg) then
        -- ★ 任务没起来就不能报"已写回"：否则 SRT 其实还是旧的，
        --   紧接着的「更新字幕 / 适配字幕」会用到过期内容。
        if not quiet then self:Log("错误：字幕写回未能启动（worker 拒绝或不可用）") end
        return false
    end
    if not quiet then
        self:Log("字幕已写回：" .. tostring(App.S.srt))
    end
    return true
end

-- ★ 只按当前字幕重新生成文稿，不碰 SRT。
--   「导出 TXT」在复制之前先跑一次它，保证导出去的是修改过的最新版本。
function App:SyncTxt()
    if #App.S.subs == 0 then return false end
    if not App.S.txt or App.S.txt == "" then
        if not App.S.srt or App.S.srt == "" then return false end
        App.S.txt = (App.S.srt:gsub("%.[Ss][Rr][Tt]$", "")) .. ".txt"
    end
    if not self:WriteSubsJson() then return false end
    local c = io.open(App.S.subsCfg, "wb")
    if not c then return false end
    c:write("{\n")
    c:write('  "mode": "totxt",\n')
    c:write('  "in": ' .. jstr(App.S.subsJson) .. ",\n")
    c:write('  "txt": ' .. jstr(App.S.txt) .. "\n")
    c:write("}\n")
    c:close()
    if not self:RunTask(SUBS_IO, App.S.subsCfg) then return false end
    return true
end

-- ══════════════════════════════════════════════════════════════
-- 把表格滚到第 row 行，并尽量让这一行落在【可视区第一行】
--
-- ★ 用户实测 bug（v1.28）：点「查找下一个」时高亮会亮，但表格纹丝不动，
--   命中行只要在视口外就完全看不到 —— 等于"没跳转"。
--
-- 根因（对照官方文档 Support\Developer\Workflow Integrations\README.txt
-- 里「UIManager Elements」的 Tree 方法表）：
--   Tree 支持的是 ScrollToItem(item)，**根本没有 SetCurrentItem**。
--   原代码把两句塞进【同一个 pcall】里：
--       tree:SetCurrentItem(item)   ← 方法不存在 → 抛错
--       tree:ScrollToItem(item)     ← 上一句已抛错，这句永远执行不到
--   而 pcall 把错误吞掉了 → 表现成"点了什么也没发生"。
--
-- ★ ScrollToItem 对应 Qt 的 scrollToItem(item, EnsureVisible)：
--   只在【看不见】时才滚，而且滚到"刚好看得见"为止（可能停在中间）。
--   想让它顶到第一行，用「先首、再尾、后目标」三步：
--       ① 滚到第 1 行    —— 视图回顶，保证下一步一定会动
--       ② 滚到最后一行  —— 视图落到底，目标行被推到视口【上方之外】
--       ③ 滚到目标行    —— 此时目标在上方，EnsureVisible 向上滚到
--                          刚好露出它为止，正好落在第一行
--   ① ② ③ 是连续同步调用，Qt 要等事件循环空闲才重绘，看不到中间态。
--   若目标行离末尾不足一屏，则不可能顶到第一行，此时只保证"可见"。
-- ══════════════════════════════════════════════════════════════
function App:ScrollRowToTop(row)
    local items = App.S.items
    if not items or not items.SubTree then return end
    local tree = items.SubTree
    local total = #App.S.subs
    if total <= 0 then return end
    if row < 1 then row = 1 end
    if row > total then row = total end

    -- TopLevelItem 是 0 基下标，行号是 1 基
    local function itemAt(idx)
        local it = nil
        pcall(function() it = tree:TopLevelItem(idx - 1) end)
        return it
    end
    -- 滚一个 item。方法名按 Resolve 版本兜底，每句各自 pcall，互不连坐
    local function scrollTo(it)
        if not it then return end
        if pcall(function() tree:ScrollToItem(it) end) then return end
        if pcall(function() tree:SetCurrentItem(it) end) then return end
        pcall(function() tree.CurrentItem = it end)
    end

    local target = itemAt(row)
    if not target then return end

    -- ★★ v2.0.3：原来是「回顶 → 落底 → 再滚到目标」三趟，目的是让目标行
    --    顶到可视区第一行。但三趟都是全表滚动，在 2000 行的表上，这一项
    --    就占了点击延迟的一大半。
    --    现在只滚一次：目标保证可见（可能落在视口下沿而不是顶行）。
    --    "顶到第一行"纯属观感，不值得为它付 200ms。
    scrollTo(target)
end

-- 选中第 i 行（表格高亮 + 编辑框同步）
-- ★ scroll=true：把这一行滚到字幕框可视区第一行（「查找下一个」跳转用）
-- ★ v2.0.6：多了个可选参数 tc —— 调用方已经算好时间码时直接传进来，
--   省一次重复换算。「查找下一个」里本来要在 SelectRow 和 FindNext 各算一遍
--   （同一个 b.s，算两遍结果必然一样）。
function App:SelectRow(i, scroll, tc)
    local items = App.S.items
    if not items or not items.SubTree then return end
    if scroll then self:ScrollRowToTop(i) end
    local b = App.S.subs[i]
    if b then
        -- ★ 程序性赋值同样会触发 TextChanged，必须挡掉：
        --   否则同一段文字会被再写回一次，还会把 subsDirty 误标成"有改动"。
        --   ★ v3.2.3：内容改由 RenderSubEditRich 写入 —— 命中行写富文本
        --   （关键词真黄），普通行写纯文本。函数内部自己挡 TextChanged。
        pcall(function() self:RenderSubEditRich() end)
        -- ★ v2.0.2：点选一行时也在状态栏显示它在时间线上的位置。
        --   原来只有「查找下一个」会写时间码，直接点行是空的 —— 同一块信息
        --   两套行为，用起来很割裂。算不出来就降级成秒数，不留空框。
        if b.s then
            local t = tc or self:SecToTimecode(b.s)
            self:StatusTimecode(t or string.format("%.2fs", tonumber(b.s) or 0))
        end
    end
end

-- 把光标移进字幕编辑框并全选（单击/双击一行时用）
function App:FocusSubEdit()
    local items = App.S.items
    if not items or not items.SubEdit then return end
    -- ★★★ Qt 的 setFocus 需要【FocusReason 参数】。
    --   写成无参的 :SetFocus() 时，Fusion 会因为参数不足直接抛错 ——
    --   而我们外面套了 pcall，错误被静默吞掉，症状就是
    --   "点了行、文字也填进去了、但就是没有光标，还得手动点一下输入框"。
    --   先按 Qt 签名传参；个别版本不认再退回无参写法。
    local okSet = pcall(function()
        items.SubEdit:SetFocus("OtherFocusReason")
    end)
    if not okSet then
        pcall(function() items.SubEdit:SetFocus() end)
    end
    pcall(function() items.SubEdit:SelectAll() end)
end

-- ★ 「更新字幕」= 把表格里的修改写进 SRT 文件（落盘）。
--   编辑框里的改动在打字那一刻就已经实时写回表格了（见 SubEdit.TextChanged），
--   所以这个按钮【不管】提交编辑，它只负责保存。
--   ★ 顺带去掉了「清空编辑框 = 删除该条」这个副作用：
--     实时同步下，打字中途把内容删空会把整条字幕误删，太危险。
function App:UpdateSubRow()
    if #App.S.subs == 0 then
        self:Status("还没有字幕")
        self:Log("还没有字幕可保存。先点「开始生成字幕」，或用「读取媒体池字幕」载入一份。")
        return
    end
    -- ★ v4.5.0：编辑版另存，别覆盖识别原件
    self:EnsureEditSrt()
    if self:SaveSubs(false) then
        App.S.subsDirty = false
        self:RefreshDirty()
        self:Status(string.format("已保存 %d 条字幕", #App.S.subs))
        self:Log(string.format("字幕已写回文件：%d 条", #App.S.subs))
        -- ★ 紧接着自动同步一次媒体池（媒体池不监视文件变化，不重新导入
        --   显示的还是旧内容）。延迟 1 秒启动，避开"写文件/读文件"撞车。
        if App.S.srt and App.S.srt ~= "" then
            App.S.pendingSync = os.time() + 1
        else
            self:Log("  · 没有记住字幕文件路径，跳过媒体池同步")
        end
    else
        self:Status("保存失败，详见日志")
    end
end

-- 把 Resolve 播放头移到指定秒（查找/替换跳转时联动，字幕即点即看）
-- ★ 两个官方实测细节：
--   1. 不在 edit/cut/color/fairlight/deliver 页面时先切回 edit ——
--      否则 SetCurrentTimecode 静默无效（点了没反应）
--   2. 时间码是 "HH:MM:SS:FF" 字符串，帧号 = 时间线起始帧 + 秒×帧率
--      （长直播时间线从 01:00:00 开始，起始帧非 0，必须加 base）
function App:JumpPlayhead(sec)
    local tl = App.timeline
    if not tl then return false end
    local fps = App.S.fps or 0
    if fps <= 0 then
        -- ★ v2.0.2：原来是 tl.GetSetting(...)（点号）。Fusion 里对象方法
        --   必须用【冒号】传 self，点号会把 "timelineFrameRate" 当成 self
        --   塞进第一个参数，调用必然出错 —— 被 pcall 吞掉后 fps 仍是 0，
        --   于是外层直接 return false，播放头永远跳不动、时间码也跟着空白。
        pcall(function()
            fps = tonumber(tl:GetSetting("timelineFrameRate")) or 0
        end)
    end
    if not fps or fps <= 0 then return false end
    sec = tonumber(sec) or 0
    if sec < 0 then sec = 0 end

    pcall(function()
        local pg = App.resolve and App.resolve:GetCurrentPage() or nil
        if pg and pg ~= "edit" and pg ~= "cut" and pg ~= "color"
           and pg ~= "fairlight" and pg ~= "deliver" then
            App.resolve:OpenPage("edit")
        end
    end)

    local tc = self:SecToTimecode(sec, tl, fps)
    if not tc then return false end
    local ok = pcall(function() return tl:SetCurrentTimecode(tc) end)
    return ok and true or false
end

-- ★ 秒 → 时间码 "HH:MM:SS:FF"（帧号 = 时间线起始帧 + 秒×帧率）
--   抽成独立函数：状态栏要显示同一个时间码，不能各算各的，
--   否则跳转位置和显示可能对不上（尤其长直播时间线起始帧非 0）。
--   fps<=0 或拿不到时间线时返回 nil，调用方自行降级。
function App:SecToTimecode(sec, tl, fps)
    tl = tl or App.timeline
    if not tl then return nil end
    local f = tonumber(fps) or App.S.fps or 0
    if not f or f <= 0 then
        -- ★ v2.0.2：点号 → 冒号，理由同 JumpPlayhead（点号调用必失败）。
        pcall(function()
            f = tonumber(tl:GetSetting("timelineFrameRate")) or 0
        end)
    end
    if not f or f <= 0 then return nil end
    local s = tonumber(sec) or 0
    if s < 0 then s = 0 end
    -- ★★ v2.0.6：起始帧优先用缓存（App.S.tlStart 由 07_flow 在载入时间线时
    --    从 timeline_source 的 tl_start 存好），拿不到才去问 Resolve。
    --    原来每次换算都调 tl:GetStartFrame()，而「查找下一个」一次点击里
    --    要算 3 遍时间码 → 3 次跨进程 IPC 纯属白花。缓存后这一步是纯算术。
    local base = App.S.tlStart
    if base == nil then
        base = 0
        pcall(function() base = tonumber(tl:GetStartFrame()) or 0 end)
    end
    -- ★ 帧率取整后必须 ≥ 1：否则 `fr % 0` 会直接抛错（fps 落在 (0, 0.5) 时）。
    local fint = math.floor(f + 0.5)
    if fint < 1 then fint = 1 end
    local fr = math.floor((base or 0) + s * f + 0.5)
    return string.format("%02d:%02d:%02d:%02d",
        math.floor(fr / (3600 * f)),
        math.floor((fr / (60 * f)) % 60),
        math.floor((fr / f) % 60),
        math.floor(fr % fint))
end

-- ══════════════════════════════════════════════════════════════════
-- ★★ v2.0.6：播放头跳转改「防抖」——连点不再排队，停手后跳一次。
--
--   问题现场：长字幕里连点「查找下一个」，点的时候有点延迟，手停下来
--   之后画面还在自己往下跳。
--
--   根因：每次点击都同步调 tl:SetCurrentTimecode()，这会让 Resolve 的
--   查看器去 seek 并解码目标帧，一次几十到一百多毫秒，且是阻塞调用。
--   连点 10 次就排了 1 秒多的活，手停了队列还在放 —— 看起来就是"自动
--   往下跳"。这跟表格重绘无关（那部分 v2.0.3 已经削掉了）。
--
--   改法：点击时只记下"想跳到哪"，不真的跳。由 120ms 的轻定时器
--   （App.jumpTimer）数拍子：这个目标已经稳定 2 拍（约 240ms）→ 跳一次并清空。
--     · 正常单击（人手间隔通常 150~400ms）→ 每次都跳，几乎无感
--     · 快速连点 → 表格/高亮/计数每次都即时更新，画面只跳最后那一处
--   落点永远是最后点的那一处，不会丢、不会偏。
-- ══════════════════════════════════════════════════════════════════
-- ★ 放 App 表里而不是 local：13 个分片会拼成一个 chunk，顶层 local 有 200 个
--   上限，能不加就不加（真撞上了报的是"too many local variables"，很难查）。
--
-- ★★ 为什么用「tick 计数」而不是 os.clock() 算时间：
--    Lua 的 os.clock() 返回的是【CPU 时间】，不是墙上时间。界面线程绝大部分
--    时间都阻塞在 RunLoop 里等事件，CPU 时间走得比墙上时间慢得多，用它做
--    防抖会变成"手停了半秒还没跳"或者"根本没跳"，而且完全不可预测。
--    os.time() 又只有秒级精度，0.2 秒的窗口量不出来。
--    唯一可靠的是定时器自己的节拍：Interval=120ms，两拍就是 240ms。
--    定时器是墙上时间驱动的，所以数拍子 = 数墙上时间。
App.JUMP_TICKS = 2          -- 稳定 2 拍（约 240ms）才真跳

-- 记一个"待跳位置"，不立即执行
function App:QueueJump(sec)
    sec = tonumber(sec)
    if not sec then return end
    App.S.pendJump = sec
    App.S.pendJumpAge = 0
    -- 兜底计时（秒级）：万一跳转定时器因为 ev.who 判定异常没跑起来，
    -- 由 Tick 在 2~3 秒后补跳一次，别让播放头永远不动。
    App.S.pendJumpWall = os.time()
end

-- 由 jumpTimer 每 120ms 调一次：目标稳定够久了就跳，然后清空
function App:FlushPendingJump()
    if App.S.pendJump == nil then return end
    App.S.pendJumpAge = (App.S.pendJumpAge or 0) + 1
    if App.S.pendJumpAge < App.JUMP_TICKS then return end
    self:DoPendingJump()
end

-- 真正执行待跳（FlushPendingJump 与 Tick 兜底共用）
function App:DoPendingJump()
    local sec = App.S.pendJump
    if sec == nil then return end
    -- 先清空再跳：SetCurrentTimecode 是阻塞的，万一它抛错，
    -- 不清空会导致下一次 tick 反复重试同一个坏位置。
    App.S.pendJump = nil
    App.S.pendJumpAge = nil
    App.S.pendJumpWall = nil
    pcall(function() self:JumpPlayhead(sec) end)
end

-- 查找下一处
-- 查找下一处
-- ★ 状态栏报的是"第 X / Y 处"，不是"第 X 行"——同一个词在一条字幕里
--   可能出现多次，行号会重复，报行号等于没报。Y 是总处数，
--   所以"找到第 3 处、一共 5 处"一眼就知道还有多少要改。
-- ★ v4.0.0：「确认」按钮删除，第一处定位已并入 FindInput.TextChanged（边打边看）。
function App:FindNext()
    local kw = self:FindKeyword()
    if kw == "" then
        self:Status("请先输入查找内容")
        return
    end
    if #App.S.subs == 0 then
        self:Status("字幕表为空")
        return
    end
    if kw ~= App.S.matchKw or not App.S.matchRows then
        self:RebuildMatches()
    end
    if App.S.matchTotal == 0 then
        self:Status("未找到：" .. kw)
        self:Log("未找到：" .. kw)
        return
    end
    -- 循环：数到末尾就绕回第一处
    local idx = (App.S.curMatch or 0) + 1
    if idx > App.S.matchTotal then idx = 1 end
    App.S.curMatch = idx
    local row = App.S.matchRows[idx]
    App.S.selRow = row
    -- ★★ v2.0.6：时间码先算一次，SelectRow 和下面的状态栏共用。
    --   原来 SelectRow 内部算一遍、这里再算一遍，同一个 b.s 算两次结果必然
    --   一样，纯浪费（v2.0.6 之前每次还要额外跨进程问一次起始帧）。
    local b = App.S.subs[row]
    local tc = nil
    if b and b.s then tc = self:SecToTimecode(b.s) end
    -- SelectRow(row, true) 内部只滚一次（v2.0.3 起），把目标行滚进可视区
    self:SelectRow(row, true, tc)
    -- ★ v3.1：同步刷新当前行信息（时间码 / 时长）
    self:RefreshRowInfo()
    -- ★ v2.0.3：false = 增量高亮。只改"上一处/这一处"两行的颜色，
    --   不再整表重绘 —— 这是"查找下一个"从卡顿变即时的关键。
    self:ApplyFindHighlight(false)
    -- ★ 位置提示只写进 Status（Label，不可复制），时间码单独写进
    --   StatusTC（只读 LineEdit，可复制），两者互不干扰。
    --   「播放头」三个字已按需求去掉。
    -- ★ 「（第 N 行）」已按需求去掉：行号和"第几处"重复，
    --   而且同一个词在一条字幕里可能出现多次，报行号反而容易误导。
    self:Status(string.format("第 %d / %d 处", idx, App.S.matchTotal))
    -- ★★ v2.0.2 修 bug：原来写成
    --      if b and self:JumpPlayhead(b.s) then 显示 else 清空 end
    --    也就是【播放头跳转失败 → 时间码被清空】。而 JumpPlayhead 在
    --    拿不到帧率、当前页面不支持、SetCurrentTimecode 抛错时会返回 false，
    --    结果就是题干说的情况：关键词都高亮了、表格也滚到命中了，
    --    唯独状态栏那个只读时间码框是空的。
    --    时间码只是"这条字幕在时间线上的位置"，和"能不能跳过去"是两件事，
    --    必须解耦：先算、先显示，跳转成败只影响播放头，不影响显示。
    if b and b.s then
        -- 拿不到帧率时降级成秒数，绝不留给用户一个空框
        self:StatusTimecode(tc or string.format("%.2fs", tonumber(b.s) or 0))
        -- ★★ v2.0.6：这里原来直接 pcall(JumpPlayhead) —— 每次点击都同步
        --   seek 一次，连点就排队，手停了队列还在放（"自动往下跳"）。
        --   改成排队 + 防抖：点击只记目标，停手 0.2 秒后才真跳一次。
        --   表格跳行、高亮、上面那行"第 X / Y 处"依然是逐次即时的。
        self:QueueJump(b.s)
    else
        self:StatusTimecode("")
    end
end

-- 查找上一个（与 FindNext 对称，idx 减一、到头绕回末尾）
function App:FindPrev()
    local kw = self:FindKeyword()
    if kw == "" then
        self:Status("请先输入查找内容")
        return
    end
    if #App.S.subs == 0 then
        self:Status("字幕表为空")
        return
    end
    if kw ~= App.S.matchKw or not App.S.matchRows then
        self:RebuildMatches()
    end
    if App.S.matchTotal == 0 then
        self:Status("未找到：" .. kw)
        return
    end
    local idx = (App.S.curMatch or 1) - 1
    if idx < 1 then idx = App.S.matchTotal end
    App.S.curMatch = idx
    local row = App.S.matchRows[idx]
    App.S.selRow = row
    local b = App.S.subs[row]
    local tc = nil
    if b and b.s then tc = self:SecToTimecode(b.s) end
    self:SelectRow(row, true, tc)
    self:RefreshRowInfo()
    self:ApplyFindHighlight(false)
    self:Status(string.format("第 %d / %d 处", idx, App.S.matchTotal))
    if b and b.s then
        self:StatusTimecode(tc or string.format("%.2fs", tonumber(b.s) or 0))
        self:QueueJump(b.s)
    else
        self:StatusTimecode("")
    end
end

-- 替换（all=true 为全部替换，否则只替换当前选中行）
-- ★ v4.0.0：「删除高亮字词」已并入此功能（查找框有词 + 替换框留空 + 全部替换）。
--   关键词改用 FindKeyword() 多路回退读取 —— 与搜索定位同源，避免某些 Resolve
--   版本上 FindInput.Text 读不到导致"日志说没找到、替换却按另一个词执行"的不一致。
function App:ReplaceInSubs(all)
    local kw = self:FindKeyword()
    local rp = ""
    pcall(function() rp = tostring(App.S.items.ReplaceInput.Text or "") end)
    if kw == "" then
        self:Status("请先输入查找内容")
        return
    end
    local n = #App.S.subs
    if n == 0 then return end

    local targets
    if all then
        targets = {}
        for i = 1, n do targets[i] = i end
    else
        if not App.S.selRow then
            self:Status("先点一行或「查找下一个」")
            return
        end
        targets = { App.S.selRow }
    end

    -- ★ v4.0.2：替换也要可撤销。以前只有删除拍快照，替换后点「撤销」
    --   要么撤不掉、要么撤到更早的删除（语义混乱）。这里在真正改动【之前】
    --   拍一份快照（SnapshotUndo 内部同时把 lastUndoKind 标成 "subs"）。
    self:SnapshotUndo()
    local cnt = 0
    local pat = patternEscape(kw)
    local repl = rp:gsub("%%", "%%%%")   -- 替换串里的 % 也要转义
    for _, i in ipairs(targets) do
        local t = tostring(App.S.subs[i].t or "")
        if t:find(kw, 1, true) then
            App.S.subs[i].t = t:gsub(pat, repl)
            cnt = cnt + 1
        end
    end
    if cnt == 0 then
        -- ★ v4.3.0：什么都没替换 → 把刚才压进去的那一步弹掉，别留假快照
        if App.S.undoStack and #App.S.undoStack > 0 then
            table.remove(App.S.undoStack)
        end
        self:Status("没有可替换的内容")
        return
    end
    -- ★ v3.2.1：替换只改编辑区里的数据，**不自动写文件**。
    --   「字幕更新」是唯一写入口 —— 两个入口各写一次的时候，
    --   用户根本分不清到底哪一下保存的、也不好在写回前整体复核。
    App.S.subsDirty = true
    self:FillSubTree(App.S.subs)
    self:RefreshDirty()
    self:Status(string.format("已替换 %d 处（未写回，点「字幕更新」生效）", cnt))
    self:Log(string.format("替换 %d 处：%s → %s", cnt, kw, rp))
    -- 单行替换：播放头跟着跳到这一行（全部替换跳的地方太多，不跳）
    if not all and App.S.selRow then
        local b = App.S.subs[App.S.selRow]
        if b then
            local ok = pcall(function() return self:JumpPlayhead(b.s) end)
            if ok then
                self:StatusTimecode(self:SecToTimecode(b.s) or "")
            end
        end
    end
end

-- ══════════════════════════════════════════════════════════════
-- ★ 勾选列删除（v3.1 重构）：原生复选框列 + Lua 权威勾选集合 App.S.checked
--   ★ v3.2.1：删除 / 删字 / 撤销 / 替换 一律【只改内存 + 标脏】，
--   写文件与同步媒体池只由「字幕更新」一个入口负责
--   （同一件事两个入口各写一次，用户分不清到底是哪一下保存的）。
--   逻辑只依赖 Lua 集合 App.S.checked；树内原生复选框只作"尽力可视化"，
--   不同 Resolve 版本 CheckState API 名不同也只会影响图形，不影响功能。
-- ══════════════════════════════════════════════════════════════

-- 命中行的【去重行数】（一条字幕多次命中只算一次）
function App:HitCount()
    if not App.S.matchRows then return 0 end
    local seen, n = {}, 0
    for _, r in ipairs(App.S.matchRows) do
        if not seen[r] then seen[r] = true; n = n + 1 end
    end
    return n
end

-- 当前勾选行数
function App:CheckedCount()
    local n = 0
    for _, v in pairs(App.S.checked) do if v then n = n + 1 end end
    return n
end

-- ★ v3.1.3：把"当前到底选中了哪几行"从表格里读出来。
--   两条路，按可靠性降级：
--     ① tree:SelectedItems()  —— 一次拿到全部，最快；
--     ② 逐行读 item.Selected   —— 慢（N 次跨进程读），但凡是能多选的版本都支持。
--   两条都不通就返回 nil，调用方保留上一次的记录（总比把选择清空好）。
function App:CollectSelected()
    local tree = App.S.items and App.S.items.SubTree
    if not tree then return nil end
    local out, got = {}, false

    pcall(function()
        local list = tree:SelectedItems()
        if type(list) == "table" then
            got = true
            for _, it in ipairs(list) do
                local r = tonumber(tostring(it.Text[3] or ""))
                if r then out[r] = true end
            end
        end
    end)

    if not got then
        for i = 1, #App.S.subs do
            local it = rowItem(tree, i)
            if it then
                local s = nil
                pcall(function() s = it.Selected end)
                if type(s) == "boolean" then
                    got = true
                    if s then out[i] = true end
                end
            end
        end
    end

    if not got then return nil end
    return out
end

-- ★ v3.1.1：选择状态由【表格原生多选】驱动。
--   把当前选中的行收集进 App.S.checked（保持这个名字，其它方法不用改），
--   并且只重绘"这次变了的那几行"，不做整表重绘 —— 2000 条字幕时
--   整表重绘会让每次点击卡顿明显。
-- ★ v3.1.3：读取改用 CollectSelected（SelectedItems 不可用时自动降级为
--   逐行读 Selected），并在 Tick 里调用 —— 事件回调执行时 Qt 的选中状态
--   可能还没落定，那时读出来是上一批，表现为"选了 5 行只认 2 行"。
function App:SyncSelection()
    if App.S.suppressSelSync then return end
    local items = App.S.items
    local tree = items and items.SubTree
    if not tree then return end

    local cur = self:CollectSelected()
    if not cur then return end

    local old = App.S.checked or {}
    -- ★ 顺序很重要：必须【先】把权威集合换成新的，再重绘。
    --   反过来的话 PaintRow 读到的还是旧集合，底色会画在错的行上。
    App.S.checked = cur

    -- ★ v3.1.2：原来这里会往第 0 列写 / 清 "✔" 当选中标记。
    --   标记列撤掉后第 0 列就是行号了，再写就把行号覆盖成 ✔，
    --   于是 rowFromTree 全部失效（点行拿不到行号）。这段必须整段删掉。

    -- 只重绘"这次变了"的行：取消的 + 新选中的，不做整表重绘
    for r in pairs(old) do if not cur[r] then self:PaintRow(r) end end
    for r in pairs(cur) do if not old[r] then self:PaintRow(r) end end
    self:RefreshSelBar()
end

-- 兼容旧调用名（几条旧路径还在调它），直接转给 SyncSelection
function App:SyncCheckedFromTree() self:SyncSelection() end

-- ★ v4.0.9：读取当前时间线的字幕轨清单。
--   返回 { {idx=, name=, count=, enabled=}, ... }；读不到就返回空表。
--   ★ 每次都重新取【当前】时间线 —— App.timeline 是开窗那一刻的快照，
--     Resolve 里换集之后它就过期了，「刷新」按钮必须能拿到新的那条。
function App:ReadSubTracks()
    local out = {}
    local tl = nil
    pcall(function()
        local r = App.resolve
        if not r then return end
        local pm = r:GetProjectManager()
        if not pm then return end
        local project = pm:GetCurrentProject()
        if not project then return end
        tl = project:GetCurrentTimeline()
    end)
    if not tl then tl = App.timeline end
    if not tl then return out end

    local n = 0
    pcall(function() n = tonumber(tl:GetTrackCount("subtitle")) or 0 end)
    for i = 1, n do
        local cnt = 0
        local en = false
        local nm = ""
        pcall(function()
            local its = tl:GetItemListInTrack("subtitle", i)
            cnt = (its and #its) or 0
        end)
        -- ★ GetIsTrackEnabled 读不到（返回 nil）时按【禁用】处理：
        --   nil ~= false 会误判成"启用"，宁可信"禁用"也不能虚报。
        local raw = nil
        pcall(function() raw = tl:GetIsTrackEnabled("subtitle", i) end)
        en = (raw == true)
        -- 字幕轨的 GetTrackName 在部分版本上返回空串，回退成 ST编号
        pcall(function() nm = tostring(tl:GetTrackName("subtitle", i) or "") end)
        out[#out + 1] = { idx = i, name = nm, count = cnt, enabled = en }
    end
    return out
end

-- ★ v4.0.9：刷新面板顶部的字幕轨清单。
--   showLog=true 时把完整清单也写进界面日志（Label 被裁时可在日志里复制查）。
function App:RefreshSubTrackInfo(showLog)
    local tracks = self:ReadSubTracks()
    local parts = {}
    for i = 1, #tracks do
        local t = tracks[i]
        local nm = t.name
        if nm == nil or nm == "" then nm = "ST" .. tostring(t.idx) end
        parts[#parts + 1] = string.format("%s(%s·%d条)",
            nm, t.enabled and "启用" or "禁用", t.count)
    end
    local txt
    if #tracks == 0 then
        txt = "字幕轨 0 条"
    else
        txt = string.format("字幕轨 %d 条：%s", #tracks, table.concat(parts, "｜"))
    end
    local it = App.S.items
    -- ★ v4.5.1：顺带刷新顶部「插件名 · 版本 · 时间线」，换时间线后不会停在旧名字
    --   ★ 整段包 pcall：单测里的假 items 是严格表（访问未知键会抛错），
    --     不能让它把 RefreshSubTrackInfo 整个带崩。
    pcall(function()
        if it and it.VerBtn then
            local nm = "未命名"
            pcall(function()
                if App.timeline then nm = App.timeline:GetName() or nm end
            end)
            it.VerBtn.Text = string.format("AsrSubs  v%s  ·  %s", VERSION, nm)
        end
    end)
    if it and it.SubTrackInfo then
        pcall(function() it.SubTrackInfo.Text = txt end)
    end
    if showLog then
        -- ★ v4.5.0：txt 本身已以「字幕轨 N 条…」开头，别再套一层前缀
        --   （此前日志里会出现「字幕轨：字幕轨 1 条：…」这种重复）
        pcall(function() self:Log(txt) end)
    end
    return txt
end

-- ★ v4.2.1（B2）：清理当前时间线上的【空】字幕轨。
--   安全边界（与 cleanup_empty_sub_tracks.py 一致）：
--     · 只删条目数 == 0 的轨；非空轨一律不碰；
--     · 从【大到小】删，避免索引位移；
--     · 删之前弹二次确认，列出将删的轨号；
--     · 全程 pcall，单轨失败不影响其它轨。
function App:CleanEmptySubTracks()
    local tracks = self:ReadSubTracks()
    if #tracks == 0 then
        self:Log("当前时间线没有字幕轨，无需清理。")
        return
    end
    local empties = {}
    for _, t in ipairs(tracks) do
        if (tonumber(t.count) or 0) == 0 then empties[#empties + 1] = t end
    end
    if #empties == 0 then
        self:Log(string.format("没有空字幕轨（共 %d 条轨，都有字幕），无需清理。", #tracks))
        return
    end
    local names = {}
    for _, t in ipairs(empties) do names[#names + 1] = "ST" .. tostring(t.idx) end
    local msg = string.format(
        "将删除 %d 条【空】字幕轨：%s\n\n非空轨一律不动（你看得见的字幕不会消失）。",
        #empties, table.concat(names, "、"))
    if not self:Confirm(msg) then
        self:Log("已取消清理空字幕轨。")
        return
    end
    local tl = App.timeline
    pcall(function()
        local r = App.resolve
        if r then
            local pm = r:GetProjectManager()
            local proj = pm and pm:GetCurrentProject()
            local t = proj and proj:GetCurrentTimeline()
            if t then tl = t end
        end
    end)
    if not tl then
        self:Log("错误：取不到当前时间线。")
        return
    end
    local removed, failed = 0, 0
    for i = #empties, 1, -1 do
        local idx = empties[i].idx
        local ok = false
        pcall(function() ok = tl:DeleteTrack("subtitle", idx) and true or false end)
        if ok then removed = removed + 1 else failed = failed + 1 end
    end
    self:Log(string.format("空字幕轨清理：删除 %d 条%s",
        removed, failed > 0 and ("，失败 " .. tostring(failed) .. " 条") or "。"))
    pcall(function() self:RefreshSubTrackInfo(false) end)
end

-- 刷新选择工具条：命中 / 已选·总行 / 删除按钮文案 + 全选三态
--   ★ v3.1.7：按钮文案改成「删除」（靠右对齐，见 SelBar 布局）。
--   ★ 命中这里统计的是【行数】不是【出现次数】：一行里出现两次也只算一行，
--     和「已选 N / 总行 」用同一个单位，两个数字才有可比性。
function App:RefreshSelBar()
    local items = App.S.items
    if not items then return end
    pcall(function() items.HitLbl.Text = string.format("命中 %d 行", self:HitCount()) end)
    pcall(function() items.SelCountLbl.Text = string.format("已选 %d / %d 行", self:CheckedCount(), #App.S.subs) end)
    pcall(function() items.DelSelBtn.Text = "删除" end)
    self:SyncSelAllChk()
end

-- 同步"全选"复选框：全勾→勾上，否则不勾
--   ★ 不用三态：TriState 在部分版本上不认，会在建窗口时就抛错。
function App:SyncSelAllChk()
    local items = App.S.items
    if not items or not items.SelectAllChk then return end
    local total, chk = #App.S.subs, self:CheckedCount()
    local on = (total > 0 and chk == total)
    pcall(function() items.SelectAllChk.Checked = on end)
end

-- 全选切换（点工具条"全选"：全选 ↔ 全清）
function App:ToggleSelectAll()
    local items = App.S.items
    local tree = items and items.SubTree
    if not tree then return end
    local all = (#App.S.subs > 0 and self:CheckedCount() == #App.S.subs)
    local want = not all
    -- ★ 程序性改 Selected 会再触发 SelectionChanged，这里上一把锁，
    --   避免边改边同步（2000 行时会自己把自己拖死）。
    App.S.suppressSelSync = true
    for i = 1, #App.S.subs do
        local it = rowItem(tree, i)
        if it then pcall(function() it.Selected = want end) end
    end
    App.S.suppressSelSync = false
    App.S.selDirty = true        -- ★ 下一拍 Tick 按真实状态再校准一次
    self:SyncSelection()
end

-- 反选：把当前选择的整体取反
function App:InvertSelection()
    local items = App.S.items
    local tree = items and items.SubTree
    if not tree then return end
    App.S.suppressSelSync = true
    for i = 1, #App.S.subs do
        local it = rowItem(tree, i)
        if it then
            local cur = false
            pcall(function() cur = (it.Selected == true) end)
            pcall(function() it.Selected = not cur end)
        end
    end
    App.S.suppressSelSync = false
    App.S.selDirty = true        -- ★ 下一拍 Tick 按真实状态再校准一次
    self:SyncSelection()
end

-- ★ v3.1.3：原来的 AutoCheckMatches（搜索即选中命中行）已删除。
--   原因见 TextChanged 里的注释：Qt 把选中底色画在单元格背景之上，
--   命中行一旦被自动选中，黄色高亮就被整片盖掉，用户看到的是
--   "框里有词、命中却看不见"。现在职责单一化：
--     · 关键词 → 只染黄（PaintRow 里优先级最高）
--     · 选中   → 只表示"我要对这几行动手"

-- 单级撤销快照（删除 / 删字前调用）
function App:SnapshotUndo()
    -- ★ v4.3.0：压入撤销栈（深拷贝），最多保留 UNDO_MAX 步。
    App.S.undoStack = App.S.undoStack or {}
    local cap = App.S.UNDO_MAX or 50
    if #App.S.undoStack >= cap then table.remove(App.S.undoStack, 1) end
    local function copyrow(r, keys)
        local o = {}
        if keys then
            for _, k in ipairs(keys) do o[k] = r[k] end
        else
            for k, v in pairs(r) do o[k] = v end
        end
        return o
    end
    local snap = { subs = {}, cuts = {}, cutSrt = App.S.cutSrt or "" }
    for i = 1, #App.S.subs do snap.subs[i] = copyrow(App.S.subs[i]) end
    for i = 1, #(App.S.cuts or {}) do
        snap.cuts[i] = copyrow(App.S.cuts[i], { "s", "e", "n" })
    end
    table.insert(App.S.undoStack, snap)
    App.S.lastUndoKind = "subs"
end

-- 撤销：按【最近一次操作类型】分流（v4.0.2 修复 lastCut 永不复位导致的屏蔽 bug）
function App:UndoDelete()
    if App.S.lastUndoKind == "cut" then
        -- ★ v4.0.0：粗剪是原地增量剪辑，整次改动包成一个 BeginUndo/EndUndo 步，
        --   撤销 = 时间线面板原生 Ctrl+Z，一次退完。不再有"切回原时间线"分支。
        self:Status("上次是视频粗剪：到时间线面板按 Ctrl+Z，一次退回")
        self:Log("视频粗剪的撤销 = 原生 Ctrl+Z（整次改动已合成一个撤销步）")
        if App.S.lastCut and App.S.lastCut ~= "" then
            self:Log("  · 剪切记录：" .. tostring(App.S.lastCut))
        end
        return
    end
    -- 字幕编辑（删除 / 替换 / 删字词 / 上一次字幕撤销）走内存快照回滚
    local stack = App.S.undoStack or {}
    if #stack == 0 then
        self:Status("没有可撤销的操作")
        return
    end
    -- ★ v4.3.0：从栈顶弹一步（多级撤销）。快照是深拷贝，替换也撤得动。
    local snap = table.remove(stack)
    App.S.subs = snap.subs
    App.S.cuts = snap.cuts or {}
    App.S.cutSrt = snap.cutSrt or ""
    App.S.selRow = nil
    App.S.subsDirty = true
    self:FillSubTree(App.S.subs)
    -- ★ v3.2.1：撤销同样只改内存，不自动写回 SRT
    self:RefreshDirty()
    self:Status(string.format("已撤销一步（还可撤销 %d 步；未写回，点「字幕更新」生效）", #stack))
    self:Log(string.format("撤销一步（剩余 %d 步）", #stack))
end

-- 删除勾选行（带快照 + 二次确认）
function App:DeleteSelectedRows()
    -- ★ v3.1.3：删之前先按表格的真实选中状态重读一遍。
    --   用户点完「删除」的瞬间，App.S.checked 可能还停在上一拍，
    --   直接照着删就会"少删"或"删错行"。
    App.S.selDirty = false
    self:SyncSelection()
    local rows = {}
    for r, v in pairs(App.S.checked) do if v then rows[#rows + 1] = r end end
    table.sort(rows)
    if #rows == 0 then
        self:Status("没有选中的行")
        return
    end
    -- ★ v4.0.4（文案 E）：措辞要符合新工作流。删行只在编辑区生效，同时
    --   记下这几行的「切除区间」供粗剪用；写回后点「视频粗剪」剪视频，
    --   字幕由粗剪脚本自动重挂，不需要再点「适配字幕」（旧文案有误导）。
    if not self:Confirm(string.format("确定删除选中的 %d 行字幕？\n删除只在编辑区生效，同时记下这几行对应的时间区间，供「视频粗剪」使用。\n确认后先点「字幕更新」写回 SRT，再点「视频粗剪」剪掉对应视频段落（字幕会自动重新落轨）。", #rows)) then
        return
    end
    self:SnapshotUndo()
    self:DeleteSubsRows(rows)
end

-- ★ v4.0.0：「删除高亮字词」按钮删除，死代码函数 DeleteHighlightTerm 一并移除。
--   等价操作：查找框输入词 → 替换框留空 → 点「全部替换」（ReplaceInSubs(true)）。

-- 从模型里删除指定行号（升序数组），重建表格（v3.2.1：不写文件，只标脏）
function App:DeleteSubsRows(rows)
    if not rows or #rows == 0 then return 0 end
    -- ★ v3.2.6：先记时间区间再删 —— 删完行号就变了，之后再想换算已经不可能。
    self:RecordCuts(rows)
    local kill = {}
    for _, r in ipairs(rows) do kill[r] = true end
    local out = {}
    for i = 1, #App.S.subs do
        if not kill[i] then out[#out + 1] = App.S.subs[i] end
    end
    if #out == #App.S.subs then return 0 end   -- 没有真正删掉（行号越界等）
    App.S.subs = out
    App.S.selRow = nil
    App.S.subsDirty = true
    -- FillSubTree 内部会自动 RebuildMatches + 重绘高亮
    self:FillSubTree(App.S.subs)
    -- ★ v3.2.1：只标记"SRT 有改动"，不自动写文件（见 RefreshDirty 的口径）
    self:RefreshDirty()
    local n = #rows
    if self:AutoCutAfterDelete() then
        self:Status(string.format("已删除 %d 行，正在自动写回并粗剪…", n))
    else
        self:Status(string.format("已删除 %d 行（未写回，点「字幕更新」生效）", n))
    end
    self:Log(string.format("批量删除 %d 行", n))
    return n
end

-- ★ v4.4.0（方案 A）：「删行 → 自动写回 → 自动粗剪」一条龙。
--   触发条件：勾了「删行即剪」+ 有删除区间 + 当前时间线有视频轨。
--   ★ 剪切仍走 cut_inplace：DeleteClips(ripple=False)，**保留空隙**，
--     剩下 a/c/e 各自占原位，字幕按原时间码重挂 —— 不会挤成 "ace"。
function App:AutoCutAfterDelete()
    if not App.S.autoCut then return false end
    if #(App.S.cuts or {}) == 0 then return false end
    -- ★ v4.4.1 修复：时间线对象缓存在 **App.timeline**（L1004 赋值）。
    --   4.4.0 写成了 App.S.timeline —— 那个字段从未被赋值，永远是 nil，
    --   于是 hasVideo 恒为假，每次删行都静默跳过（日志：当前时间线没有视频轨）。
    local tl = App.timeline
    if not tl then
        pcall(function()
            local r = App.resolve
            if r then
                local pm = r:GetProjectManager()
                local proj = pm and pm:GetCurrentProject()
                tl = proj and proj:GetCurrentTimeline() or nil
            end
        end)
        if tl then App.timeline = tl end
    end
    local hasVideo = false
    pcall(function() hasVideo = (tl and (tl:GetTrackCount("video") or 0) > 0) or false end)
    if not hasVideo then
        self:Log("  · 删行即剪：当前时间线没有视频轨，跳过自动粗剪")
        return false
    end
    -- ★ v4.5.0：自动粗剪也要先把编辑版另存（不覆盖识别原件）
    self:EnsureEditSrt()
    -- 先写回 SRT（剪切按磁盘上的 SRT 算时间，必须是最新的）
    if not self:SaveSubs(true) then
        self:Log("  · 删行即剪中止：字幕写回失败")
        return false
    end
    App.S.subsDirty = false
    self:RefreshDirty()
    App.S.pendingSync = os.time() + 1
    App.S.cutAfterSync = true
    self:Log(string.format("  · 删行即剪：已写回 SRT，%d 段待切，1 秒后自动粗剪", #App.S.cuts))
    return true
end

-- ══════════════════════════════════════════════════════════════
-- 粗剪：按删除的字幕区间切除视频（v3.2.6）
--
-- ★ 为什么记「时间区间」而不是「行号」：
--   删除是批量的，删完之后行号全都变了。时间才是稳定的坐标，
--   而且视频要切的本来就是时间。
-- ★ 只有「删行」产生切除区间。「删除高亮字词」改的是文本本身
--   （那句话还在，只是少了几个字），不该把视频也剪掉。
-- ══════════════════════════════════════════════════════════════

-- 把刚删掉的那几行的时间并进待切除区间
--   rows 必须是【删除前】的行号 —— 调用方要在改动 App.S.subs 之前调。
function App:RecordCuts(rows)
    if not rows or #rows == 0 then return end
    local add = {}
    for _, r in ipairs(rows) do
        local b = App.S.subs and App.S.subs[r]
        if b then
            local s, e = tonumber(b.s), tonumber(b.e)
            if s and e and e > s then add[#add + 1] = { s = s, e = e } end
        end
    end
    if #add == 0 then return end
    App.S.cutSrt = App.S.srt or ""

    local all = {}
    for _, c in ipairs(App.S.cuts or {}) do all[#all + 1] = { s = c.s, e = c.e } end
    for _, c in ipairs(add) do all[#all + 1] = c end
    table.sort(all, function(x, y) return x.s < y.s end)

    -- ★ 相邻区间必须合并：两段之间只隔零点几秒的话，切出来就是一个
    --   闪一帧的碎片，还白多一个切口。
    local tol = tonumber(App.S.cutMerge) or 0.30
    local merged = {}
    for _, c in ipairs(all) do
        local last = merged[#merged]
        if last and (c.s - last.e) <= tol then
            if c.e > last.e then last.e = c.e end
            last.n = (last.n or 1) + 1
        else
            merged[#merged + 1] = { s = c.s, e = c.e, n = 1 }
        end
    end
    App.S.cuts = merged
    self:RefreshCutBar()
end

-- 待切除区间统计：段数 / 总秒数
function App:CutStats()
    local n, total = 0, 0
    -- ★ 换到别的字幕文件（= 另一条素材/另一期）时，旧的删除记录不再成立
    if (App.S.cutSrt or "") ~= (App.S.srt or "") then return 0, 0 end
    for _, c in ipairs(App.S.cuts or {}) do
        n = n + 1
        total = total + math.max(0, (tonumber(c.e) or 0) - (tonumber(c.s) or 0))
    end
    return n, total
end

-- ★ v4.0.2：粗剪门槛【唯一真源】。RefreshCutBar 的按钮亮态、CanCut 的
--   点击拦截、CutLbl 的提示文案，全部走这里 —— 以前两处判据不一致，
--   导致「按钮亮着，点下去才被拒」。现在一个函数说了算，不再漂移。
--   返回 ok, reason：ok=true 可立即粗剪；ok=false 时 reason 是可读原因。
function App:CutGate()
    if BUSY_PHASES[App.S.phase] then
        return false, "任务进行中，请稍候"
    end
    if #App.S.subs == 0 then
        return false, "还没有字幕"
    end
    -- ★ 换字幕导致旧删除记录作废：单独判在 n==0 之前，给更贴切的解释
    if (App.S.cutSrt or "") ~= (App.S.srt or "") and #(App.S.cuts or {}) > 0 then
        return false, "换了字幕文件，旧的删除记录已作废，请重新删除要剪的行"
    end
    local n = self:CutStats()
    if n == 0 then
        return false, "还没有删除记录：切除的依据是「删除」留下的时间区间"
    end
    if not App.S.srt or App.S.srt == "" then
        return false, "没有关联的 SRT 文件"
    end
    -- ★ 切除是按【磁盘上的 SRT】算时间的。界面里还有没写回的删除时，
    --   磁盘那份是旧的：切完字幕会带着已删的行、时间也对不上。必须先写回。
    if App.S.subsDirty then
        return false, "先点「字幕更新」写回，再点「视频粗剪」"
    end
    -- ★ 刚点完写回时文件还在被 worker 写（pendingSync 是完成信号），
    --   这时候读会读到半截文件。
    if App.S.pendingSync then
        return false, "字幕正在写回，等一两秒再点「视频粗剪」"
    end
    return true, ""
end

-- ★ v4.3.2：待剪区间的持久化
--   背景：粗剪依据是「你删掉了哪些行」这份【内存记账】，关掉插件就没了；
--   重开后 SRT 里那些行已经不存在，插件无法反推 → 粗剪按钮永远灰着。
--   做法：字幕写回后把 cuts 连同 SRT 指纹写进 <srt>.cuts.json；
--   下次载入同一份 SRT（指纹一致）时恢复。指纹不一致就作废并提示。
function App:CutSidecarPath()
    local srt = tostring(App.S.srt or "")
    if srt == "" then return nil end
    return srt .. ".cuts.json"
end

function App:FileSig(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local data = f:read("*a") or ""
    f:close()
    local sum = 0
    for i = 1, #data do
        sum = (sum * 31 + data:byte(i)) % 4294967296
    end
    return string.format("%d-%d", #data, sum)
end

function App:SaveCutSidecar()
    local p = self:CutSidecarPath()
    if not p then return end
    if #(App.S.cuts or {}) == 0 then
        pcall(function() os.remove(p) end)
        return
    end
    local sig = self:FileSig(App.S.srt)
    if not sig then return end
    local parts = {}
    for _, c in ipairs(App.S.cuts) do
        parts[#parts + 1] = string.format('    {"s": %.3f, "e": %.3f}',
            tonumber(c.s) or 0, tonumber(c.e) or 0)
    end
    local body = string.format(
        '{\n  "srt": %s,\n  "sig": %s,\n  "cuts": [\n%s\n  ]\n}\n',
        jstr(App.S.srt), jstr(sig), table.concat(parts, ",\n"))
    local f = io.open(p, "wb")
    if not f then return end
    f:write(body)
    f:close()
end

function App:LoadCutSidecar()
    local p = self:CutSidecarPath()
    if not p then return end
    local f = io.open(p, "rb")
    if not f then return end
    local body = f:read("*a") or ""
    f:close()
    local sig = body:match('"sig"%s*:%s*"([^"]*)"')
    local cur = self:FileSig(App.S.srt)
    if not sig or sig ~= cur then
        pcall(function() os.remove(p) end)
        self:Log("  · 发现旧的删除记录，但字幕文件已变，已作废。")
        return
    end
    local cuts = {}
    for s, e in body:gmatch('"s"%s*:%s*([%d%.%-]+)%s*,%s*"e"%s*:%s*([%d%.%-]+)') do
        local ss, ee = tonumber(s), tonumber(e)
        if ss and ee and ee > ss then cuts[#cuts + 1] = { s = ss, e = ee, n = 1 } end
    end
    if #cuts == 0 then return end
    App.S.cuts = cuts
    App.S.cutSrt = App.S.srt or ""
    self:RefreshCutBar()
    self:Log(string.format(
        "  · 已恢复上次会话的 %d 段待剪区间（点「视频粗剪」即可，先确认再剪）", #cuts))
end

-- 刷新状态条上的切除预览 + 按钮可用性
function App:RefreshCutBar()
    local items = App.S.items
    if not items then return end
    local n, total = self:CutStats()
    local ok, reason = self:CutGate()
    -- ★ v4.8.12：不再往状态条写提示（那里已经够挤），改挂到「视频粗剪」按钮的悬停提示：
    --   可用 → 显示"将切 N 段 · X 秒"；不可用 → 显示原因（如"先点字幕更新写回"）。
    pcall(function() items.CutLbl.Text = "" end)
    if items.CutBtn then
        pcall(function()
            if ok then
                items.CutBtn.ToolTip = string.format("将切除 %d 段 · %.1f 秒", n, total)
            elseif n > 0 then
                items.CutBtn.ToolTip = "不可用：" .. tostring(reason)
            else
                items.CutBtn.ToolTip = "删除字幕行后，这里可以切掉对应视频"
            end
        end)
        pcall(function() items.CutBtn.Enabled = ok end)
    end
end

-- 切除前的门槛检查（点按钮时用；判据与按钮亮态共用 CutGate）
function App:CanCut()
    return self:CutGate()
end

-- ══════════════════════════════════════════════════════════════
-- ★ v4.0.0：粗剪引擎【固定为原地增量剪辑】（「字幕无间隙」功能删除后，
--   不再需要「新建时间线重建」那条路 —— 闭合空隙的场景已不存在）。
--   原地剪辑：在【当前这条时间线】上直接裁/删被删字幕覆盖的片段，
--   其余片段、转场、调色、Fusion 特效全部保留；整次改动包成一个原生撤销步。
-- ══════════════════════════════════════════════════════════════
function App:DoCut()
    local ok, why = self:CanCut()
    if not ok then
        self:Status(why)
        self:Log(why)
        return
    end
    local n, total = self:CutStats()
    local pad = tonumber(App.S.cutPad) or 0.10
    -- ★ 确认框把"代价"写清楚：只动被删段落覆盖的片段，其余全保留。
    local msg = string.format(
        "将按 %d 段删除记录进行视频粗剪：\n"
        .. "  · 切掉约 %.1f 秒（每个切口前后各多留 %.2f 秒气口）\n"
        .. "  · 在【当前这条时间线】上原地切，不新建时间线\n"
        .. "  · 只动被删段落覆盖的那几个片段，其余片段 / 转场 / 调色 / 特效全部保留\n"
        .. "  · 被切开的那一个片段会拆成两段（新的一段继承原片段调色与视频属性，\n"
        .. "    但转场不会跟过来）\n"
        .. "  · 字幕按原时间码重新落轨（保留空隙，时间码不变）\n"
        .. "  · 只重挂本插件的字幕，你手工添加的字幕轨会保留\n"
        .. "  · 会写一份剪切记录（仅供排查）；不满意就按 Ctrl+Z，一次退回\n\n"
        .. "确定粗剪？", n, total, pad)
    if not self:Confirm(msg) then return end

    self:Log("")
    self:Log(string.format("视频粗剪：%d 段 / 约 %.1f 秒", n, total))
    self:StartCut("cut")
end

-- 写参数 → 起 Python → 交给 Tick 的 cutting 相位收尾
function App:StartCut(mode)
    mode = tostring(mode or "cut")
    pcall(function() os.remove(App.S.cutFile) end)

    local f = io.open(App.S.cutCfg, "wb")
    if not f then
        self:Status("无法写入切除参数")
        self:Log("错误：无法写入 " .. tostring(App.S.cutCfg))
        return
    end
    f:write("{\n")
    f:write('  "mode": ' .. jstr(mode) .. ",\n")
    f:write('  "srt": ' .. jstr(App.S.srt or "") .. ",\n")
    f:write('  "pad": ' .. string.format("%.3f", tonumber(App.S.cutPad) or 0.10) .. ",\n")
    f:write('  "work": ' .. jstr(PROBE_WORK) .. ",\n")
    -- ★ v4.0.0：引擎固定为 inplace（原地增量剪辑）。「字幕无间隙」功能已删除，
    --   不再读勾选态、不再写 no_gap；也不再有 restore（切回原时间线）分支。
    local engine = "inplace"
    f:write('  "engine": ' .. jstr(engine) .. ",\n")
    f:write('  "cuts": [')
    local first = true
    for _, c in ipairs(App.S.cuts or {}) do
        if not first then f:write(",") end
        first = false
        f:write(string.format("\n    [%.3f, %.3f]",
            tonumber(c.s) or 0, tonumber(c.e) or 0))
    end
    f:write("\n  ],\n")
    f:write('  "out_lua": ' .. jstr(App.S.cutFile) .. "\n")
    f:write("}\n")
    f:close()

    if not self:RunTask(CUT_SCRIPT, App.S.cutCfg) then
        App.S.phase = "done"
        self:EnableButtons()
        return
    end
    App.S.phase = "cutting"
    App.S.cutStartedAt = os.time()
    self:Status("正在原地粗剪视频...")
    -- ★ v4.8.24：粗剪最长要跑 300 秒（见 Tick 里的 cutting 超时），
    --   以前这段时间「停止」是灰的，用户只能看着不动。
    pcall(function() App.S.items.StopBtn.Enabled = true end)
    self:Log("正在当前时间线上原地剪切（其余片段、转场、调色不动）…")
end

-- 二次确认对话框（ui:MessageBox 不可用时回退为"直接执行"，不阻断功能）
function App:Confirm(msg)
    local ok, r = pcall(function() return ui:MessageBox(msg, "确认", "OkCancel") end)
    if not ok then return true end
    return (r == true or r == "OK" or r == "Ok")
end

-- 刷新脏标记（SRT 有改动 / 已写回 SRT）
-- ★ 注意口径：这里只反映【SRT 文件】是否已写回。
--   字幕真正落到时间线要靠「适配字幕」（= 写回时间线），是另一件事，
--   不能混在同一个标记里说。
function App:RefreshDirty()
    local items = App.S.items
    if not items then return end
    -- ★ v4.8.11：脏标记不再常显在界面上，改为「字幕更新」按钮的悬停提示 + 日志。
    --   表格上方已经有「已选 x / y 行」，状态区再挂一句"SRT 有改动"属于重复信息。
    local dirty = App.S.subsDirty and true or false
    pcall(function() items.DirtyLbl.Text = "" end)
    pcall(function()
        if items.UpdateSubsBtn then
            items.UpdateSubsBtn.ToolTip = dirty and "SRT 有改动，点这里写回" or ""
        end
    end)
    if dirty ~= (App.S.dirtyLogged == true) then
        App.S.dirtyLogged = dirty
        if dirty then
            self:Log("SRT 有改动（未写回）")
        end
    end
    -- ★ v4.0.2：脏标记变化时，「视频粗剪」按钮的亮态/提示文案要跟着变。
    pcall(function() self:RefreshCutBar() end)
end

-- 刷新当前行信息（第 X 行 开始→结束 (时长)）
function App:RefreshRowInfo()
    local items = App.S.items
    if not items or not items.RowInfoLbl then return end
    local i = App.S.selRow
    local b = i and App.S.subs[i]
    if not b then
        pcall(function() items.RowInfoLbl.Text = "" end)
        return
    end
    local s = self:SecToTimecode(b.s) or string.format("%.2fs", tonumber(b.s) or 0)
    local e = self:SecToTimecode(b.e) or string.format("%.2fs", tonumber(b.e) or 0)
    local dur = (tonumber(b.e) or 0) - (tonumber(b.s) or 0)
    pcall(function()
        items.RowInfoLbl.Text = string.format("第 %d 行  %s → %s  (%.2fs)", i, s, e, dur)
    end)
end

-- ── 编辑区里的关键词高亮（v3.2.3）─────────────────────────────────
-- Tree 单元格只能整格染色，所以高亮不做在表格里；但 TextEdit 支持 HTML：
-- 写入带 <span> 的富文本，界面上就是「只有关键词这几个字变黄」，
-- 而 PlainText 读回来仍是干净原文，不改数据、不影响写回 SRT。
--
-- ★ 两条硬约束（都实测过）：
--   1) 富文本会压缩首尾空格，所以写完必须【回读校验】，不一致就退回纯文本；
--   2) 设置内容会重置光标，所以只在「换行」时渲染，用户正在打字时不重排。
function App:RenderSubEditRich()
    local items = App.S.items
    if not items or not items.SubEdit then return end
    -- ★ v3.2.4：窗口正在关闭时【不要】再动控件。
    --   关窗会触发 LostFocus → 这里再去写 HTML，等于在一个正在销毁的
    --   窗口上操作，Resolve 的绑定层可能抛错（控制台里的
    --   "[string ...]:31: attempt to index local 'ontbl'" 就是那类报错，
    --   ontbl 是 Resolve 内部的 "on table"，不是本插件的变量）。
    if App.S.closing then return end
    local te = items.SubEdit
    local i = App.S.selRow
    local b = i and App.S.subs[i]
    if not b then return end
    local plain = tostring(b.t or "")
    local kw = App.S.matchKw
    local old = App.S.suppressEdit
    App.S.suppressEdit = true

    -- 没有查找词 / 这一行命中不到 → 保持纯文本（富文本残留会让打字继承黄字）
    if not kw or kw == "" or not plain:find(kw, 1, true) then
        if App.S.editRich then
            pcall(function() te.PlainText = plain end)
            App.S.editRich = false
        end
        App.S.suppressEdit = old
        return
    end

    -- htmlMark 已完成「转义 + 给关键词套 span」，这里再把换行转成 <br/>
    local body = htmlMark(plain, kw):gsub("\n", "<br/>")
    local ok = pcall(function() te.HTML = body end)
    local back = nil
    pcall(function() back = te.PlainText end)
    if (not ok) or back ~= plain then
        -- 富文本这条路不通（属性不存在 / 回读被改写）→ 退回纯文本，宁可不黄
        pcall(function() te.PlainText = plain end)
        App.S.editRich = false
        if App.S.htmlWarned ~= true then
            App.S.htmlWarned = true
            self:Log("[paint] 编辑区富文本不可用或回读不一致 → 已退回纯文本")
        end
    else
        App.S.editRich = true
    end
    App.S.suppressEdit = old
end

-- 从媒体池里找一个 .srt 载入编辑区
-- 扫描媒体池里【所有】的 .srt（递归子文件夹，最多 6 层）
function App:CollectPoolSrts()
    local out = {}
    pcall(function()
        local mp = App.project and App.project:GetMediaPool() or nil
        if not mp then return end
        local root = mp:GetRootFolder()
        if not root then return end
        local function walk(folder, depth)
            if depth > 6 then return end
            for _, c in ipairs(folder:GetClipList() or {}) do
                local p = tostring(c:GetClipProperty("File Path") or "")
                if p:lower():sub(-4) == ".srt" then
                    out[#out + 1] = p
                end
            end
            for _, sf in ipairs(folder:GetSubFolderList() or {}) do
                walk(sf, depth + 1)
            end
        end
        walk(root, 0)
    end)
    -- 同一份字幕可能在媒体池里被引用多次，去重（路径比较不分大小写）
    local seen, uniq = {}, {}
    for _, p in ipairs(out) do
        local k = p:lower()
        if not seen[k] then seen[k] = true; uniq[#uniq + 1] = p end
    end
    return uniq
end

-- ★ v4.2.0（B1）：从【时间线字幕轨】只读载入编辑区。
--   只读：不落轨、不改轨、不删任何条目、不碰媒体池、不写边车。
--   选轨口径与落轨/翻译一致：第一条「启用且非空」的字幕轨。
--   ★ 安全门禁：载入后 App.S.srt 指向一个【新的】另存文件，
--     这样后续「字幕更新」只会新建该文件，绝不会覆盖你已有的 SRT。
function App:LoadSubsFromTrack()
    local f = io.open(App.S.subsCfg, "wb")
    if not f then
        self:Log("错误：无法写入字幕读取参数")
        return
    end
    f:write("{\n")
    f:write('  "mode": "dump_track",\n')
    f:write('  "out": ' .. jstr(App.S.subsLua) .. ",\n")
    f:write('  "src": ' .. jstr(App.S.subsSrc) .. "\n")
    f:write("}\n")
    f:close()

    -- 另存路径：沿用当前 SRT 所在目录；没有就用 data\output。
    -- 文件名纯 ASCII + 时间戳，避开中文/空格路径的不确定性。
    local dir = tostring(App.S.srt or ""):match("^(.*)[/\\][^/\\]*$") or ""
    if dir == "" then dir = ASCII_ROOT .. "\\data\\output" end
    App.S.srt = string.format("%s\\track_load_%s.srt", dir, os.date("%Y%m%d-%H%M%S"))
    App.S.cuts = {}
    App.S.cutSrt = ""
    pcall(function() self:RefreshCutBar() end)

    pcall(function() os.remove(App.S.subsLua) end)
    pcall(function() os.remove(App.S.subsSrc) end)
    if not self:RunTask(SUBS_IO, App.S.subsCfg) then
        App.S.subsPending = false
        self:Log("错误：字幕读取未能启动（worker 拒绝或不可用）")
        return
    end
    App.S.subsPending = true
    App.S.subsStartedAt = os.time()
    self:Log("正在从时间线字幕轨读取（只读）…保存将写入新文件：" .. App.S.srt)
end

function App:LoadFromMediaPool()
    local list = self:CollectPoolSrts()
    if #list == 0 then
        self:Status("媒体池无 SRT")
        self:Log("媒体池里没找到 .srt 字幕文件。先生成字幕，或手动把 srt 导入媒体池。")
        return
    end
    if #list == 1 then
        -- 只有一个，没有歧义，直接载入
        App.S.srt = list[1]
        self:Log("从媒体池载入字幕：" .. list[1])
        self:LoadSubs()
        return
    end
    -- ★ 有多个：不猜、不按时间线名默认，弹出来让你自己挑
    self:ShowSrtPicker(list)
end

-- 媒体池里有多个 .srt 时的选择窗
function App:ShowSrtPicker(list)
    local d = App.S.dispatcher
    local ui = App.S.ui
    if not d or not ui then
        App.S.srt = list[1]
        self:Log("错误：弹不出选择窗，改用第一个：" .. list[1])
        self:LoadSubs()
        return
    end
    if App.S.pickerWin then
        pcall(function() App.S.pickerWin:Hide() end)
        App.S.pickerWin = nil
    end

    local w = d:AddWindow({
        ID = "AsrSubsPicker",
        WindowTitle = "选择字幕",
        Geometry = { 380, 220, 640, 160 },
    }, ui:VGroup{ ID = "PickRoot", Spacing = 8,
        ui:Label{ ID = "PickLabel",
                  Text = string.format("媒体池里找到 %d 个字幕，请选择要载入的：", #list),
                  Weight = 0 },
        ui:ComboBox{ ID = "PickCombo", Weight = 0 },
        ui:HGroup{ ID = "PickRow", Weight = 0,
            ui:Button{ ID = "PickOk", Text = "载入", Weight = 0.5 },
            ui:Button{ ID = "PickCancel", Text = "取消", Weight = 0.5 },
        },
    })
    if not w then
        App.S.srt = list[1]
        self:Log("错误：选择窗创建失败，改用第一个：" .. list[1])
        self:LoadSubs()
        return
    end

    local it = w:GetItems()
    App.S.pickerList = list
    if it and it.PickCombo then
        for _, p in ipairs(list) do
            -- 完整路径太长会把下拉框撑爆，只显示文件名；
            -- 遇到同名文件再补上所在文件夹，免得分不清是哪一份
            local name = p:match("([^\\/]+)$") or p
            local parent = p:match("([^\\/]+)[\\/][^\\/]+$")
            if parent then name = name .. "　·　" .. parent end
            it.PickCombo:AddItem(name)
        end
        pcall(function() it.PickCombo.CurrentIndex = 0 end)
    end

    w.On.PickOk.Clicked = function()
        local idx = 0
        pcall(function() idx = tonumber(it.PickCombo.CurrentIndex) or 0 end)
        local picked = App.S.pickerList[idx + 1] or App.S.pickerList[1]
        w:Hide()
        if picked then
            App.S.srt = picked
            self:Log("从媒体池载入字幕：" .. picked)
            self:LoadSubs()
        end
    end
    w.On.PickCancel.Clicked = function() w:Hide() end
    -- ★ 右上角 × 走的也是窗口 Close 事件，不绑就点了没反应
    w.On["AsrSubsPicker"].Close = function() w:Hide() end

    App.S.pickerWin = w
    w:Show()
end

-- ══════════════════════════════════════════════════════════════════
-- 导出：把 srt / txt 复制到用户选的文件夹
--   选文件夹用 Fusion 自带的 RequestDir（不走 os.execute，不闪窗）；
--   真正的复制交给 Python —— 目标路径可能含中文，Lua 打不开。
-- ══════════════════════════════════════════════════════════════════
function App:PickDir()
    local dir = ""
    -- ★ v1.1.2：先把插件窗口藏起来，再弹系统的文件夹选择框。
    --   Fusion 的 RequestDir 是【模态】对话框，而插件窗口是 UIManager 顶层窗：
    --   窗口还在的时候，选择框会被顶到它【后面】—— 用户看不见、点不到，
    --   而脚本正卡在 RequestDir 里等选择，整个插件就此"假死、关不掉"。
    --   藏掉窗口是这类模态遮挡的标准解法；不管选没选，最后都要放回来。
    local win = App.S.win
    local hidden = false
    pcall(function()
        if win then win:Hide(); hidden = true end
    end)
    pcall(function()
        local fu = App.resolve and App.resolve:Fusion() or nil
        if not fu then return end
        if type(fu.RequestDir) ~= "function" then return end
        local d = fu:RequestDir(OUTPUT_DIR)
        if type(d) == "table" then d = d[1] end
        if type(d) == "string" and d ~= "" then dir = d end
    end)
    if hidden then
        pcall(function() win:Show() end)
        pcall(function() App.S.closing = false end)
    end
    return dir
end

function App:StartExport(kinds)
    if BUSY_PHASES[App.S.phase] then
        self:Status("任务进行中，请稍候")
        self:Log("当前任务还没结束，等它跑完再导出。")
        return
    end
    if #App.S.subs == 0 then
        self:Log("还没有字幕可导出。先点「开始生成字幕」，或用「读取媒体池字幕」载入一份。")
        return
    end

    -- ★ 导出前先按【当前】字幕把文稿重新生成一遍。识别时那份 txt 是旧稿，
    --   不重生成的话导出去的就不是你改过的版本。
    --   放在选文件夹【之前】启动：用户挑文件夹那几秒足够它跑完；
    --   这一步只写 .txt，不会动 SRT。
    self:SyncTxt()
    if not App.S.txt or App.S.txt == "" then
        self:Log("错误：拿不到文稿路径，导出取消。")
        return
    end

    local dir = self:PickDir()
    if dir == "" then
        self:Log("未选择文件夹，导出取消。")
        return
    end

    pcall(function() os.remove(App.S.exportFile) end)
    local f = io.open(App.S.exportCfg, "wb")
    if not f then
        self:Log("错误：无法写入导出参数"); return
    end
    f:write("{\n")
    f:write('  "srt": ' .. jstr(App.S.srt) .. ",\n")
    f:write('  "txt": ' .. jstr(App.S.txt or "") .. ",\n")
    f:write('  "dir": ' .. jstr(dir) .. ",\n")
    f:write('  "kinds": [')
    for i, k in ipairs(kinds) do
        if i > 1 then f:write(", ") end
        f:write(jstr(k))
    end
    f:write("],\n")
    f:write('  "open": true,\n')
    f:write('  "out_lua": ' .. jstr(App.S.exportFile) .. "\n")
    f:write("}\n")
    f:close()

    if not self:RunTask(EXPORT_SCRIPT, App.S.exportCfg) then
        -- ★ worker 拒绝时导出根本没起来，直接报错，别进 exporting 干等超时。
        self:Fail("导出任务未能启动（worker 拒绝或不可用）")
        return
    end
    App.S.phase = "exporting"
    App.S.exportStartedAt = os.time()
    self:Status("正在导出…")
    -- ★ v4.8.24：导出阶段也允许停止（30 秒超时以前只能干等）
    pcall(function() App.S.items.StopBtn.Enabled = true end)
    self:Log("导出到：" .. dir)
end

function App:Fail(msg)
    App.S.phase = "error"
    -- ★ v4.0.2：主动失败也立即落盘（失败原因往往就是排障的关键一行）
    ELogCritical("Fail: " .. tostring(msg))
    self:Status("失败：" .. tostring(msg))
    self:Log("错误：" .. tostring(msg))
    self:EnableButtons()
end

function App:EnableButtons()
    if App.S.win then
        -- ★★ v1.2.0：不再无条件置可点，改按前置条件判（见 App:CanStart）。
        --   走到这里的相位是 idle/done/error，都不在 BUSY_PHASES 内，
        --   所以只要时间线还有片段、环境没问题，按钮就会正常亮起来。
        --   ★ 先复位按钮文案再判，否则"识别中 47%"会跟着灰按钮一起留在界面上。
        pcall(function() App.S.items.StartBtn.Text = "开始生成" end)
        pcall(function() self:RefreshStartBtn() end)
        pcall(function() App.S.items.StopBtn.Enabled = false end)
        -- 「适配字幕」「导出文稿」「更新字幕」：表格里得真有字幕才让点。
        -- ★ 判据用【表格里的字幕条数】而不是识别计数 —— 字幕也可能是
        --   用「读取媒体池字幕」载入的，那种情况下 count 是 0，
        --   按老写法按钮会一直灰着点不动。
        pcall(function() App.S.items.LandBtn.Enabled = self:CanLand() end)
        -- 导出文稿不再依赖识别时留下的那份 txt 路径：导出前会按当前字幕
        -- 重新生成（App:SyncTxt），所以只要表格里有内容就能导出。
        pcall(function() App.S.items.TxtBtn.Enabled = (#App.S.subs > 0) end)
        pcall(function() App.S.items.UpdateSubsBtn.Enabled = (#App.S.subs > 0) end)
        -- ★ v3.2.6：切除按钮只看「有没有待切除区间」，有才亮
        self:RefreshCutBar()
    end
end

-- ── 全文高频热词（v2.0.8）─────────────────────────────────────────
-- 数据不是这边算的：subs_io.py 在 dump 字幕时已经用 jieba 统计好，
-- 写到 rt\hotwords.lua（格式：return {total=, thr=, n=, words={{词,次数},..}}）。
-- 这边只负责读出来拼成一行「长辈 123  ·  人生 40  ·  …」。
--
-- 为什么不在 Lua 里分词：Lua 没有中文分词库，硬做只能退化成"二字组合"，
-- 结果全是「会长」「长前」这种碎片，没法看。
--
-- 拿不到数据（字幕为空 / jieba 缺失 / 文件还没写出来）就清空，
-- 绝不报错 —— 它只是个参考信息，不该影响主流程。
function App:RefreshHotwords()
    local it = App.S.items
    if not it or not it.HotEdit then return end
    local t = loadLuaTable(App.S.hotwordsLua)
    if type(t) ~= "table" or type(t.words) ~= "table" or #t.words == 0 then
        pcall(function() it.HotEdit.PlainText = "" end)
        -- ★ v4.8.24：没有 jieba 时 Python 侧不再用"二字滑窗"凑数（那会产出
        --   「位朋」「里最」这类伪词，实测 5 个热词里 2 个看不懂），
        --   改成在这里明确告诉用户缺什么。
        if type(t) == "table" and t.no_jieba then
            -- ★ v1.0.1：把"怎么修"写具体。以前只说"重跑安装器或 pip install jieba"，
            --   用户不知道该用哪个 python；实测这台机器 runtime 里根本没装
            --   jieba（老安装包没把它列进依赖），热词那行就一直是空的。
            local fix = "runtime\\Scripts\\python.exe -m pip install jieba"
            pcall(function()
                it.HotLabel.Text = "热词（需 jieba，未安装）"
                it.HotEdit.PlainText = "未安装 jieba，热词统计已关闭。"
                    .. "修复：在安装目录下执行 " .. fix
                    .. "（或重跑安装器，新版已自带 jieba）"
            end)
            if not App.S.jiebaHinted then
                App.S.jiebaHinted = true
                self:Log("热词需要 jieba，当前运行环境里没有。修复：" .. fix)
                self:Log("  · 装好后重新载入字幕（或重开插件）即可，不用重新识别")
            end
        else
            pcall(function() it.HotLabel.Text = "热词" end)
        end
        return
    end
    local parts = {}
    for _, kv in ipairs(t.words) do
        if type(kv) == "table" and kv[1] ~= nil then
            parts[#parts + 1] = tostring(kv[1]) .. " " .. tostring(kv[2])
        end
    end
    -- ★ v2.0.10：组与组之间只用空格，不用「·」。
    --   每组本身是「词 次数」，所以至少得留两个空格，
    --   一个空格会变成「人们 82 长辈 44」，看着像一串数字。
    pcall(function() it.HotEdit.PlainText = table.concat(parts, "  ") end)
    -- 标签上标出阈值，用户才知道"为什么这几个词上榜"
    pcall(function()
        it.HotLabel.Text = string.format("热词 ≥%s", tostring(t.thr or "?"))
    end)
    ELog(string.format("热词：共 %s 词，阈值 %s 次，展示 %d 个",
        tostring(t.total or "?"), tostring(t.thr or "?"), #parts))
end

-- ★ v4.8.25：keepServer=true 时【保留】预热好的常驻 ASR 服务。
--   用户点「停止」只是想停这一个任务，不该把加速器一起关掉
--   （关掉后下次识别要多等约 12 秒重新加载模型）。
--   关窗口 / 退出插件仍然全杀，那里它占着显存必须收掉。
function App:Cleanup(keepServer)
    -- ★ v1.0.1：先把交付页设置还原。以前只有"正常跑完"才走 CleanupRender，
    --   渲染/导出途中按「停止」或直接关窗，用户的交付页会永久留在
    --   "仅音频 WAV"，预设列表里还多出 render_to_wav / AsrSubsBackup；
    --   更糟的是下一轮 StartRender 会拿这份【已经坏了】的设置当"用户原样"
    --   重新快照，原始设置再也回不来（复现路径：开始生成 → 渲染阶段按停止）。
    pcall(function() self:CleanupRender() end)
    if App.S.jobId then
        pcall(function() App.project:DeleteRenderJob(App.S.jobId) end)
        App.S.jobId = nil
    end
    self:ReturnToPrevPage()
    -- ★ v1.0.1：不再清空 audioPath。它是"素材从哪来"的唯一记录，落轨脚本
    --   靠它决定要不要在素材旁放一份 srt —— 一停止就抹掉，随后的「适配字幕」
    --   会静默跳过那一步，用户永远不知道少了个文件。每轮 Start 都会重写它，
    --   留着没有副作用。
    -- ★ 关窗口时无条件全杀：除了正在跑的识别任务，预热的常驻 ASR 服务也是
    --   worker 的子进程 —— 必须一起收掉，否则它会一直占着显存。
    --   点「停止」时只停任务（keepServer=true），服务留着下次继续用。
    self:KillTasks(not keepServer)
    pcall(function() os.remove(App.S.landFile) end)
    pcall(function() os.remove(App.S.landCfg) end)
    pcall(function() os.remove(App.S.importFile) end)
    pcall(function() os.remove(App.S.importCfg) end)
    pcall(function() os.remove(App.S.exportFile) end)
    pcall(function() os.remove(App.S.exportCfg) end)
    App.S.phase = "idle"
    self:EnableButtons()
end

-- ── 诊断（v2.0.9 改版）───────────────────────────────────────────
-- v2.0.0~v2.0.8 是一个「诊断包」按钮：点一下打包 zip 并打开目录。
-- v2.0.9 起入口收进右下角的【版本号】：点它先弹窗把内容直接显示出来，
-- 窗口里再给一个「导出诊断包(zip)」—— 想看内容不用等打包、不用解压，
-- 要发给人再导出。按钮本身删掉了。
--
-- 内容全部由 Lua 现读，不启 Python（点开即显示）：
--   rt\env.lua        Python / torch / transformers / 自检结果
--   rt\last_run.lua   上次识别的条数、时长、RTF、设备、模型
--   rt\engine.log     引擎日志尾巴
--   AsrSubs_error.log 界面日志尾巴
--   + 版本号、三个关键路径、当前设置摘要
-- 只有「CPU 核数 / 操作系统版本 / 显卡驱动版本」这几项 Lua 拿不到，
-- 它们仍然只在导出的 zip（diag.py 生成的环境信息.txt）里。

-- 给版本号加上 ⚠，表示"这轮出过错，可以点我看看"。
-- ★ 必须先判界面建好没有，再置标志位：BuildUI 早期就有日志会带「错误」，
--   那时候 items 还不存在，若先把 diagShown 置成 true，⚠ 就永远出不来。
function App:ShowDiag()
    if App.S.diagShown then return end
    local it = App.S.items
    if not it or not it.SetupBtn then return end
    App.S.diagShown = true
    -- ★ v4.8.0：诊断入口删了；出错时把「设置」按钮标成 ⚠（日志在设置弹窗里）
    local ok = pcall(function() it.SetupBtn.Text = "设置 ⚠" end)
    pcall(function()
        if it.LogToggleBtn then it.LogToggleBtn.Text = "日志 ⚠" end
    end)
    ELog("设置入口已标记异常 ok=" .. tostring(ok))
    self:Log("  · 出错详情见「设置」→ 运行日志")
end

-- 取文件末尾 n 行。★ 开头那一行大概率是被截断的半行（tail 是按字节截的，
-- 可能正好切在一个汉字的中间），直接丢掉，否则会看到乱码。
local function tailLines(path, n, maxBytes)
    local s = tail(path, maxBytes or 12000)
    if s == nil or s == "" then return "" end
    local lines = {}
    for ln in (s .. "\n"):gmatch("(.-)\n") do
        lines[#lines + 1] = ln
    end
    local st = #lines - (n or 40) + 1
    if st < 2 then st = 2 end          -- 跳过被截断的第一行
    if st > #lines then return "" end
    local out = {}
    for i = st, #lines do out[#out + 1] = lines[i] end
    return table.concat(out, "\n")
end

-- 拼出「诊断信息」窗口里那一大段文字。
function App:DiagText()
    local L = {}
    local function add(s) L[#L + 1] = tostring(s) end
    -- 估算显示宽度：UTF-8 中文 3 字节占 2 列，ASCII 1 字节占 1 列。
    -- ★ 不能直接用 string.format("%-12s")：Lua 的 %s 按【字节】补空格，
    --   「插件版本」12 字节等于没补，「Python」6 字节补 6 个空格，
    --   两行反而对不齐。这里自己算列宽再补 ASCII 空格才对得上。
    local function dispw(s)
        s = tostring(s)
        local ascii = 0
        for i = 1, #s do
            if s:byte(i) < 0x80 then ascii = ascii + 1 end
        end
        return ascii + ((#s - ascii) / 3) * 2
    end
    local function kv(k, v)
        local pad = 12 - dispw(k)
        if pad < 1 then pad = 1 end
        add("  " .. tostring(k) .. string.rep(" ", pad) .. "：" .. tostring(v))
    end

    add("AsrSubs 诊断信息")
    add(string.rep("─", 46))
    kv("插件版本", VERSION)
    kv("生成时间", os.date("%Y-%m-%d %H:%M:%S"))
    add("")

    -- ── 运行环境（worker 启动时写下的 env.lua）──
    add("【运行环境】")
    local env = loadLuaTable(PROBE_WORK .. "\\env.lua")
    if type(env) == "table" then
        kv("Python", env.python or "（未知）")
        kv("torch", env.torch or "（未知）")
        kv("transformers", env.transformers or "（未知）")
        kv("环境自检", (env.ok == true) and "通过" or "未通过")
    else
        add("  （还没有 env.lua —— 引擎这一轮没启动过）")
    end
    add("")

    -- ── 关键路径 ──
    add("【关键路径】")
    kv("引擎目录", ASCII_ROOT)
    kv("数据目录", TOOL_ROOT)
    kv("模型目录", MODELS_DIR)
    kv("界面日志", ERRLOG)
    add("")

    -- ── 当前设置 ──
    add("【当前设置】")
    local c = readSettings()
    kv("每行字数", c.max_chars)
    kv("去标点", c.all_punct and "全部" or (c.end_punct and "句末" or "关"))
    kv("自定义模型", (c.model_path ~= "" and c.model_path) or "（未填）")
    local n_rule = 0
    if c.corrections and c.corrections ~= "" then
        for ln in (c.corrections .. "\n"):gmatch("(.-)\n") do
            local s = ln:gsub("^%s+", ""):gsub("%s+$", "")
            if s ~= "" and s:sub(1, 1) ~= "#" then n_rule = n_rule + 1 end
        end
    end
    kv("纠错规则", n_rule .. " 行")
    add("")

    -- ── 上次运行（worker 每次识别完写的 last_run.lua）──
    add("【上次识别】")
    local lr = loadLuaTable(PROBE_WORK .. "\\last_run.lua")
    if type(lr) == "table" then
        if tonumber(lr.t) then
            kv("完成时间", os.date("%Y-%m-%d %H:%M", tonumber(lr.t)))
        end
        kv("字幕条数", lr.blocks or "?")
        local dur = tonumber(lr.duration)
        local ela = tonumber(lr.elapsed)
        if dur then kv("素材时长", string.format("%.1f 分钟", dur / 60)) end
        if ela then kv("识别用时", string.format("%.1f 分钟", ela / 60)) end
        if tonumber(lr.rtf) then kv("实时率 RTF", string.format("%.2f", tonumber(lr.rtf))) end
        kv("设备", lr.device or "?")
        kv("模型", lr.model or "?")
        kv("复用分段", (lr.cached == true) and "是" or "否")
    else
        add("  （还没有记录 —— 这一轮还没跑过识别）")
    end
    add("")

    -- ── 日志尾巴 ──
    add("【引擎日志（最后 40 行）】")
    local e1 = tailLines(App.S.logFile, 40, 14000)
    add((e1 ~= "" and e1) or "  （空）")
    add("")
    add("【界面日志（最后 30 行）】")
    local e2 = tailLines(ERRLOG, 30, 10000)
    add((e2 ~= "" and e2) or "  （空）")
    add("")
    add(string.rep("─", 46))
    add("需要反馈问题时，点下面【导出诊断包】，把生成的 zip 发过来即可。")
    add("（zip 里多出 CPU 核数、系统版本、显卡驱动版本这几项，Lua 取不到）")

    return table.concat(L, "\n")
end

-- 弹窗显示诊断信息。
-- ★ 每次点都【重建】窗口，不复用旧的 —— 和「选择字幕」弹窗（ShowSrtPicker）
--   同一个套路，那条路已经在实机跑通了。复用隐藏窗口再 Show() 理论上也行，
--   但 Fusion 的多窗口行为各版本不完全一致，不值得为省一点开销去赌。
function App:ShowDiagWin()
    local d = App.S.dispatcher
    local u = App.S.ui
    if not d or not u then
        self:Log("错误：界面还没准备好，无法打开诊断窗口")
        return
    end

    if App.S.diagWin then
        pcall(function() App.S.diagWin:Hide() end)
        App.S.diagWin = nil
    end

    local w = d:AddWindow({
        ID = "AsrSubsDiag",
        WindowTitle = "AsrSubs 诊断信息",
        Geometry = { 300, 140, 760, 560 },
        Spacing = 8,
        StyleSheet = [[ QWidget { font-size: 12px; } ]],
    }, u:VGroup{ ID = "DiagRoot", Spacing = 8,
        u:TextEdit{ ID = "DiagText", Weight = 1 },
        u:HGroup{ ID = "DiagRow", Weight = 0, Spacing = 8,
            u:Button{ ID = "DiagZip", Text = "导出诊断包(zip)", Weight = 0.6 },
            u:Button{ ID = "DiagClose", Text = "关闭", Weight = 0.4 },
        },
    })
    if not w then
        self:Log("错误：诊断窗口创建失败")
        return
    end

    local it = w:GetItems()
    -- ★ ReadOnly 不写在 AddWindow 的控件表里：不是每个版本都认这个属性，
    --   写错会让整个 AddWindow 抛错、窗口起不来。放这儿 pcall 更稳。
    local okR = pcall(function() it.DiagText.ReadOnly = true end)
    ELog("诊断窗口只读 ok=" .. tostring(okR))
    local okT = pcall(function() it.DiagText.PlainText = self:DiagText() end)
    if not okT then ELog("!! 诊断文本填充失败") end

    w.On.DiagZip.Clicked = function()
        pcall(function() w:Hide() end)
        local okD, errD = pcall(function() App:ExportDiag() end)
        if not okD then ELog("导出诊断包异常: " .. tostring(errD)) end
    end
    w.On.DiagClose.Clicked = function() w:Hide() end
    -- 右上角 × 只藏窗口，不退出主循环
    w.On["AsrSubsDiag"].Close = function() w:Hide() end

    App.S.diagWin = w
    w:Show()
    ELog("诊断窗口已打开")
end

function App:ExportDiag()
    local cfgFile = PROBE_WORK .. "\\diag.json"
    local outLua  = PROBE_WORK .. "\\diag.lua"
    -- 诊断包落点：3.0 统一放 data\logs（和错误日志同一个目录，用户好找）；
    -- 没有 paths.lua 的老部署退回根目录下的「AsrSubs 诊断」。
    local outDir  = LOG_DIR

    pcall(function() os.remove(outLua) end)
    local f = io.open(cfgFile, "wb")
    if not f then
        self:Log("无法写入诊断参数（rt 目录不可写）")
        return
    end
    f:write("{\n")
    f:write('  "rt": ' .. jstr(PROBE_WORK) .. ",\n")
    f:write('  "base": ' .. jstr(LOG_DIR) .. ",\n")
    f:write('  "out_dir": ' .. jstr(outDir) .. ",\n")
    f:write('  "out_lua": ' .. jstr(outLua) .. ",\n")
    f:write('  "open": true\n')
    f:write("}\n")
    f:close()

    if not self:RunTask(DIAG_SCRIPT, cfgFile) then
        self:Log("诊断包生成失败：后台执行器不可用")
        return
    end
    self:Log("正在打包诊断信息…（完成后自动打开所在文件夹）")
    self:Log("  " .. outDir)
    self:Status("诊断包生成中…")
end

-- ── 入口 ──────────────────────────────────────────────────────────
if resolve == nil and type(Resolve) == "function" then
    resolve = Resolve()
end
App.OnRun()
