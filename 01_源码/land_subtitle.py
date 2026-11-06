# -*- coding: utf-8 -*-
"""把识别出的 SRT 落到 DaVinci Resolve 时间线的【字幕轨】，并导入媒体池。

用法:
    land_subtitle.py <config.json>

config (UTF-8 JSON):
{
  "srt":     "<字幕输出>/Timeline 1.srt",
  "media":   "<源素材>.mp4",            -- 可选；给了就在它旁边也放一份 srt
  "out_lua": "<运行期>/land.lua",
  "mode":    "timeline" | "media" | "relang",  -- media=只入池；timeline=完整落轨；relang=字幕更新闭环(删旧+落新)
  "offset_sec": 0.0                     -- 整体微调偏移(秒)，可正可负(夹到0)
}

输出 out_lua（Lua chunk，供 Resolve 侧 loadfile 读取）：
    return { ok=true, placed=121, total=121, track=1, offset=0,
             sidecar=[[...]], item=[[...]], err=[[]], method="inplace" }

★★★★ 血泪教训（务必保持）：
  1. 字幕媒体项给 AppendToTimeline 【绝对不能】传 startFrame / endFrame，
     也不传 mediaType —— 实测：传 startFrame/endFrame 会把 Resolve 当场打崩
     （崩过两次）；传 mediaType 则字幕直接落不上（字幕轨为空）。
     字幕只传 { mediaPoolItem } 即可；recordFrame 对字幕轨会被 Resolve 忽略，
     定位靠【钉播放头到时间线起点 + 只启用一条空字幕轨】。
  2. 只删除「来源媒体项名与本文件同名」的字幕轨项，
     绝不按名字去删媒体池里任何「时间线」类型的项
     （曾因此误删用户的时间线）。

★★★ 落轨策略（本版核心，v4.0.4 架构红线）：
  【只在当前打开的时间线上原地落轨（inplace），绝不新建时间线。】
  对齐方法（本项目实测）：
    · 落轨前【禁用全部字幕轨】，再只在一条【空轨】上启用，保证
      AppendToTimeline 只会把字幕落进那条空轨、且从轨道头开始；
    · 落轨前用 SetCurrentTimecode(GetStartTimecode()) 把【播放头钉在
      时间线起点】——Resolve 对字幕轨虽忽略 recordFrame，但【尊重播放头】，
      字幕按 SRT 内部时间码铺在播放头处，故与视频（也始于时间线起点）对齐；
    · 真实 SRT 只传 { mediaPoolItem }，不传 recordFrame / trackIndex；
    · 视频起始偏移已由 main() 在 land_srt 内 bake 进时间码，落点无需再平移；
    · 旧字幕轨保持【禁用】（不依赖不可靠的 DeleteClips）避免重复显示；
    · 落完用 ID 差核对条数，并校验首条起始帧是否对齐预期偏移。若未对齐或
      落 0 条，直接把诊断信息返回用户，不再新建时间线兜底（历史兜底路径
      会误删时间线、且违背"只在原时间线操作"红线，v4.0.4 已删除）。
  · 粗剪（cut_timeline.py / cut_inplace.py）同样在当前时间线上原地增量剪辑，
    不新建时间线。

  为兼容更多安装位置（Studio 版、D 盘 Program Files）自动探测 fusionscript。
"""

import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback

# ★ v3.2.8：硬崩兜底。之前 inplace 路径会绕过 main() 异常捕获直接崩
#   （.log 0 字节、报退出码 1），开了 faulthandler 后任何 C 层崩溃都会
#   把 Python 栈 + 原生栈打到 stderr（→ .log），不再"无日志消失"。
import faulthandler
faulthandler.enable()

API = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
# ★ 兼容更多安装位置（含 Studio 版、D 盘 Program Files），换机器也能自动找到
LIB_CANDIDATES = [
    r"D:\DaVinci Resolve\fusionscript.dll",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Fusion\fusionscript.dll",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve Studio\fusionscript.dll",
    r"D:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
]


def find_lib():
    for p in LIB_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def connect():
    """连接正在运行的 Resolve（外部脚本）。失败返回 None。"""
    lib = find_lib()
    if not lib:
        return None
    os.environ["RESOLVE_SCRIPT_API"] = API
    os.environ["RESOLVE_SCRIPT_LIB"] = lib
    mods = os.path.join(API, "Modules")
    if mods not in sys.path:
        sys.path.insert(0, mods)
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except Exception:
        return None


def lua_str(s):
    """转成安全的 Lua 字符串字面量（UTF-8 原样写出，Lua 按字节处理）。"""
    out = []
    for ch in str(s if s is not None else ""):
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20:
            out.append("\\%d" % o)
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def write_lua(path, d):
    if not path:
        return
    d = d or {}
    lines = [
        "return {",
        "  ok = %s," % ("true" if d.get("ok") else "false"),
        "  placed = %d," % int(d.get("placed") or 0),
        "  total = %d," % int(d.get("total") or 0),
        "  track = %d," % int(d.get("track") or 0),
        "  replaced = %d," % int(d.get("replaced") or 0),
        "  offset = %d," % int(d.get("offset") or 0),
        "  sidecar = %s," % lua_str(d.get("sidecar")),
        "  item = %s," % lua_str(d.get("item")),
        "  new_timeline = %s," % lua_str(d.get("new_timeline")),
        "  first = %d," % int(d.get("first") or 0),
        "  clips = %d," % int(d.get("clips") or 0),
        "  srt_total = %d," % int(d.get("srt_total") or 0),
        "  lost = %d," % int(d.get("lost") or 0),
        "  diag = %s," % lua_str(d.get("diag")),
        "  err = %s," % lua_str(d.get("err")),
        "  method = %s," % lua_str(d.get("method")),
        "  relang_cleared = %d," % int(d.get("relang_cleared") or 0),
        "  reclaimed_track = %d," % int(d.get("reclaimed_track") or 0),
        "}",
    ]
    text = "\n".join(lines) + "\n"
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError:
        pass
    tmp = path + ".tmp.%d.tmp" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass


def write_progress(out_lua, stage, msg):
    """落轨进度文件：每次覆盖写成一个合法 Lua table，供 Lua 端 loadfile 读取。
    用途：把「挂轨超时」这个黑盒打开 —— 超时时 Lua 据此判断脚本卡在
    启动 / 连接 / 落轨 / 完成 哪一阶段，而不是笼统报「可能未开外部脚本」。
    用 .tmp + os.replace 做原子替换，避免 Lua 端 loadfile 读到半截文件。"""
    if not out_lua:
        return
    try:
        prog = out_lua + ".progress"
        tmp = prog + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("return { stage = %s, msg = %s, t = %d }\n"
                    % (lua_str(stage), lua_str(msg), int(time.time())))
        os.replace(tmp, prog)
    except OSError:
        pass


def begin_undo(proj, name):
    """把后续时间线改动包成一个原生撤销步（Resolve 18+）。失败静默。

    ★ 用途：粗剪/还原这种"清空 + 几百个片段重排"的操作，原生 Ctrl+Z 默认会
       一条一条往回退（用户观感就是"每步后退"）。包成一步后，即使焦点在
       时间线上，原生 Ctrl+Z 也能一次退回整段视频。
    """
    try:
        fn = getattr(proj, "BeginUndo", None)
        if callable(fn):
            fn(name or "AsrSubs")
    except Exception:
        pass


def end_undo(proj):
    try:
        fn = getattr(proj, "EndUndo", None)
        if callable(fn):
            fn()
    except Exception:
        pass


def copy_sidecar(srt, media):
    """在源素材旁边放一份同名 srt，方便用户直接找到。返回目标路径或空串。"""
    if not media:
        return ""
    mdir = os.path.dirname(media)
    if not mdir or not os.path.isdir(mdir):
        return ""
    base = os.path.splitext(os.path.basename(media))[0]
    dst = os.path.join(mdir, base + ".srt")
    if os.path.abspath(dst) == os.path.abspath(srt):
        return dst
    shutil.copyfile(srt, dst)
    return dst


SUFFIX = " · 字幕"


def fmt_ts(sec):
    """秒 → SRT 时间码 HH:MM:SS,mmm"""
    if sec < 0:
        sec = 0.0
    total = int(round(sec * 1000))
    h = total // 3600000
    m = (total % 3600000) // 60000
    s = (total % 60000) // 1000
    ms = total % 1000
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def count_srt(path):
    """数 SRT 里有多少条字幕（一个含 --> 的块算一条）。"""
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        return 0
    return sum(1 for b in re.split(r"\n\s*\n", text) if "-->" in b)


def srt_frame_diag(path, fps):
    """统计「上轨后会被 Resolve 合并/丢掉」的嫌疑条目。

    ★ 为什么需要：实测 2169 条 SRT 落轨后字幕轨只有 2168 条，
      界面只报了 2168，用户完全不知道少了一条。SRT 交给
      AppendToTimeline 是一整批，丢不丢是 Resolve 自己决定的，我们只能
      事先把「按帧折算后不合法」的条目找出来并说明原因：
        zero —— 起止落在同一帧（或结束早于开始），Resolve 会吞掉
        dup  —— 与上一条起始帧完全相同，两条会被并成一条
        ovl  —— 与上一条时间重叠（本条起始早于上一条结束）
    """
    out = {"zero": 0, "dup": 0, "ovl": 0, "items": []}
    if fps <= 0:
        return out
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    except OSError:
        return out

    prev_start = None
    prev_end = None
    for block in re.split(r"\n\s*\n", text):
        if "-->" not in block:
            continue
        line = next((ln for ln in block.splitlines() if "-->" in ln), "")
        ms = _TS_RE.findall(line)
        if len(ms) < 2:
            continue

        def to_sec(t):
            return (int(t[0]) * 3600 + int(t[1]) * 60
                    + int(t[2]) + int(t[3]) / 1000.0)

        st = int(round(to_sec(ms[0]) * fps))
        en = int(round(to_sec(ms[1]) * fps))
        if en <= st:
            out["zero"] += 1
            if len(out["items"]) < 5:
                out["items"].append("%s（零帧）" % line.strip())
        if prev_start is not None and st == prev_start:
            out["dup"] += 1
            if len(out["items"]) < 5:
                out["items"].append("%s（与上一条同帧起始）" % line.strip())
        if prev_end is not None and st < prev_end:
            out["ovl"] += 1
            if len(out["items"]) < 5:
                out["items"].append("%s（与上一条重叠 %.2f 秒）"
                                    % (line.strip(), (prev_end - st) / fps))
        prev_start = st
        prev_end = en
    return out


def shift_srt(src_path, dst_path, delta_sec):
    """把 SRT 的所有时间码整体平移 delta_sec 秒，写到 dst_path。

    ★ 为什么需要：字幕只能落在「空时间线」的第 0 帧起算，而素材在时间线上
      未必从第 0 帧开始。把时间码提前加上素材的起始偏移，落上去就正好对齐。
      只能加不能减（负时间码不合法），好在素材起始帧 >= 0。
    """
    with open(src_path, encoding="utf-8-sig") as f:
        text = f.read()
    if abs(delta_sec) < 1e-6:
        with open(dst_path, "w", encoding="utf-8") as f:
            f.write(text)
        return dst_path

    def parse(m):
        return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                + int(m.group(3)) + int(m.group(4)) / 1000.0)

    def repl(m):
        return fmt_ts(parse(m) + delta_sec)

    out = _TS_RE.sub(repl, text)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(out)
    return dst_path


# ★ v4.0.4：collect_clips()（精确复制原素材到新时间线）删除。
#   红线：不再新建/复制素材，只在现有时间线上落字幕。


def _clip_speed(it):
    """片段速度（1.0 = 常速）。区间裁剪换算源帧偏移要用。"""
    try:
        v = it.GetSpeed()
    except Exception:
        return 1.0
    if isinstance(v, dict):
        p = v.get("Percentage")
        return float(p) / 100.0 if p else 1.0
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return 1.0
    return fv / 100.0 if fv > 2.0 else fv


def find_offset(tl, media):
    """让字幕跟时间线上的素材对齐 —— 取该素材在时间线上的【起始帧】。

    为什么要这个：SRT 内部时间是从 0 开始的。如果素材不是从时间线第 0 帧
    开始（比如前面还有别的片段），直接按 recordFrame=0 落轨就会整体偏掉。
    用素材自己的起始帧当落轨位置，字幕就和音频严格同步。

    ★ 只在 video / audio 轨里找，字幕轨/生成器轨一律不碰。
    ★ 匹配策略（按可靠度排序，命中即返回）：
       1) 媒体池项「文件全路径」规范化后与 media 完全相等（最稳，避免子串误判）；
       2) 媒体池项「名字 / 文件名」与 media 去扩展名后全等；
       3) 子串兜底：名字或文件名包含 media 基名，取时间线上【最早】出现者；
       4) 实在匹配不到、但时间线上只有一段 video/audio，直接用它的起始帧。
    ★ 返回 (offset, how)：how 用于排障（路径/名字/子串/唯一片段/无）。
    """
    base = ""
    if media:
        base = os.path.splitext(os.path.basename(media))[0].lower()
    media_norm = ""
    if media:
        try:
            media_norm = os.path.normcase(os.path.abspath(media))
        except Exception:
            media_norm = (media or "").lower()

    _PATH_KEYS = ("File Path", "Filepath", "Clip Path", "Path", "File Name")

    def _clip_path(mi):
        """尽量拿到媒体池项的文件全路径（兼容不同 Resolve 版本字段名）。"""
        if not mi:
            return ""
        fp = ""
        try:
            props = mi.GetClipProperty() or {}
            if isinstance(props, dict):
                for k in _PATH_KEYS:
                    v = props.get(k)
                    if v:
                        fp = str(v)
                        break
        except Exception:
            fp = ""
        if not fp:
            for k in _PATH_KEYS:
                try:
                    v = mi.GetClipProperty(k)
                except Exception:
                    v = ""
                if v:
                    fp = str(v)
                    break
        return fp or ""

    def _clip_start(it):
        try:
            return int(round(float(it.GetStart() or 0)))
        except Exception:
            return 0

    exact = None          # 路径/名字精确命中
    fallback = 0          # 子串兜底（最早）
    earliest = None       # 时间线唯一片段兜底
    how = "无"
    for kind in ("video", "audio"):
        try:
            n = tl.GetTrackCount(kind) or 0
        except Exception:
            n = 0
        for i in range(1, n + 1):
            try:
                items = tl.GetItemListInTrack(kind, i) or []
            except Exception:
                items = []
            for it in items:
                st = _clip_start(it)
                if earliest is None or st < earliest:
                    earliest = st
                mi = None
                try:
                    mi = it.GetMediaPoolItem()
                except Exception:
                    mi = None
                fp = _clip_path(mi)
                fn = os.path.basename(fp).lower() if fp else ""
                nm = ""
                try:
                    nm = (mi.GetName() or "").lower() if mi else ""
                except Exception:
                    nm = ""
                # 1) 全路径精确
                if media_norm and fp:
                    try:
                        if os.path.normcase(os.path.abspath(fp)) == media_norm:
                            return st, "路径"
                    except Exception:
                        pass
                # 2) 名字/文件名全等（去扩展名）
                if base and (base == nm or base == fn):
                    return st, "名字"
                # 3) 子串兜底
                if base and not fallback and (base in nm or base in fn):
                    fallback = st
    # 4) 唯一片段兜底
    if fallback == 0 and earliest is not None:
        fallback = earliest
        how = "唯一片段"
    elif fallback != 0:
        how = "子串"
    return fallback, how


# ★ v4.0.4：clear_timeline()（清空新时间线所有轨道）删除。
#   不再新建时间线，也就不需要清空它。


# ★ Type 是【本地化字符串】：中文 Resolve 上是「字幕」，英文上是「Subtitle」。
#   只认中文的话，英文版上永远匹配不到 → 每次导入都往媒体池里堆一个新项，
#   跑几趟之后媒体池一屏同名字幕。这里两套判据都上。
_SUBTITLE_TYPES = {"字幕", "字幕文件", "subtitle", "subtitles", "caption"}


def _is_subtitle_clip(c, srt_path):
    """媒体池里的这一项，是不是"我们要替换掉的旧字幕"。"""
    try:
        props = c.GetClipProperty() or {}
    except Exception:
        props = {}
    if not isinstance(props, dict):
        props = {}
    t = str(props.get("Type") or props.get("type") or "").strip().lower()
    if t and (t in _SUBTITLE_TYPES or "subtitle" in t or t in ("字幕",)):
        return True
    # 跟语言无关的判据：来源文件就是这个 SRT
    fp = str(props.get("File Path") or props.get("Original File Path") or "")
    if fp and srt_path:
        try:
            return os.path.abspath(fp) == os.path.abspath(srt_path)
        except OSError:
            return False
    return False


# ★ v4.0.4：_ascii_srt()（ASCII 临时名副本，v3.2.11 起停用）删除。
#   中文/空格路径并非落轨失败原因，且会污染媒体池字幕候选列表。


# ---------------------------------------------------------------------------
# 导入媒体池：顺序铁律 + 两轮回退（v4.1.0）
#
# ★ 对齐「剪包助手」的真机取证结论：`ImportMedia` 失败时**返回空列表而不抛异常**，
#   触发条件是「脚本刚动过时间线 → Resolve 自动保存工程 → 紧接着导入」。
#   所以这里做两件事：
#     ① 调用方必须先 import_to_pool、再动时间线（顺序铁律，见 land_inplace）；
#     ② 万一仍是空列表，换一份副本路径重试一次，并整池递归回查。
#
# ★ 为什么第二轮的副本**保持原文件名**：本插件的旧项清理是「按名字 + 字幕类型」
#   匹配的。若改成随机名导入，池里会永远堆着 asr_land_xxx.srt 这种清不掉的项
#   （剪包助手敢用随机名，是因为它不做同名清理）。副本只换目录、不换文件名。
# ---------------------------------------------------------------------------
IMPORT_SETTLE_SEC = 0.8   # 导入前先让 Resolve 消化上一步（保存 / 改轨）
IMPORT_RETRY_SEC = 0.8    # 两轮之间的间隔


def _find_pool_item(mp, names):
    """在媒体池【递归】按名找已有项（根 + 所有子夹）。

    ★ 必须递归：ImportMedia 落池位置取决于「当时的当前文件夹」，不可预测；
      只在根目录找会漏掉被拖进 bin 的那一份。
    ★ 用显式栈而非递归函数：媒体池子夹可能很深，避免递归深度问题。
    """
    try:
        stack = [mp.GetRootFolder()]
    except Exception:
        return None
    seen = set()
    while stack:
        fd = stack.pop()
        if fd is None:
            continue
        key = id(fd)
        if key in seen:
            continue
        seen.add(key)
        try:
            for c in (fd.GetClipList() or []):
                try:
                    if (c.GetName() or "").strip() in names:
                        return c
                except Exception:
                    pass
        except Exception:
            pass
        try:
            stack.extend(fd.GetSubFolderList() or [])
        except Exception:
            pass
    return None


def _retry_copy(srt_path, attempt):
    """把 SRT 复制成「换目录、保文件名」的副本，供第二轮导入用。

    ★ 只换目录不换名（理由见上面 IMPORT_* 注释）。
    ★ 复制失败返回 None —— 调用方退回原路径，绝不能因为副本失败而中断落轨。
    """
    try:
        d = os.path.join(tempfile.gettempdir(),
                         "asr_land_%d_%d_%d" % (os.getpid(), attempt, int(time.time() * 1000)))
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, os.path.basename(srt_path))
        shutil.copyfile(srt_path, dst)
        return dst
    except Exception as e:
        print("[land] 第%d轮副本失败（退回原路径）：%s" % (attempt, e), flush=True)
        return None


def import_to_pool(mp, srt_path, resolver=None):
    """把 SRT 导入媒体池，返回 (媒体项, 基名)。

    ★ 删旧项时必须同时匹配【名字】和【字幕类型】——
      只按名字删会误删同名的时间线（时间线在媒体池里也是一个项，吃过亏）。
    ★ v4.1.0：两轮导入 + 每轮回查；全败才抛错。调用方必须先调本函数，
      再动时间线（顺序铁律）。
    ★ v4.3.1：两轮都失败时，再走一条**独立通路** `MediaStorage.AddItemListToMediaPool`
      （真机已确认可调用）。它不经过 MediaPool.ImportMedia，ImportMedia 整体失灵
      时仍可能把文件塞进池里。resolver 为空则跳过（老调用点保持兼容）。
    """
    base = os.path.splitext(os.path.basename(srt_path))[0]
    root = mp.GetRootFolder()
    stale = []
    # ★ 要连子文件夹一起扫：只在根文件夹里找旧字幕的话，
    #   用户把字幕拖进过某个 bin，再次导入就会在根目录再堆一份同名的，
    #   媒体池里出现两个"123.srt"，导入时间线时还不知道会挑哪个。
    folders = [root]
    try:
        # ★ 方法名必须是 GetSubFolderList()：Resolve 的 Folder 对象没有
        #   GetSubFolders()，写错会抛异常、被下面的 except 吞掉，
        #   于是"连子文件夹一起扫"静默失效（子文件夹里的字幕扫不到）。
        for sub in (root.GetSubFolderList() or []):
            if sub is not None:
                folders.append(sub)
    except Exception:
        pass
    for fd in folders:
        try:
            clips = fd.GetClipList() or []
        except Exception:
            continue
        for c in clips:
            try:
                if (c.GetName() or "").strip() == base \
                        and _is_subtitle_clip(c, srt_path):
                    stale.append(c)
            except Exception:
                pass
    if stale:
        try:
            mp.DeleteClips(stale)
        except Exception:
            pass

    # 让 Resolve 先消化上一步操作（调用方可能刚保存过工程 / 刚改过时间线）
    time.sleep(IMPORT_SETTLE_SEC)

    base_name = os.path.basename(srt_path)
    names = {base, base_name}
    trace = []
    for attempt in (1, 2):
        src = srt_path if attempt == 1 else (_retry_copy(srt_path, attempt) or srt_path)
        if attempt == 2:
            try:
                mp.SetCurrentFolder(root)
            except Exception:
                pass
            # 刷新文件夹视图（池视图滞后时 ImportMedia 会返回空）
            try:
                mp.RefreshFolders()
            except Exception:
                pass
        try:
            lst = mp.ImportMedia([src])
            if isinstance(lst, list) and lst:
                return lst[-1], base
            trace.append("第%d轮ImportMedia→%s/%s"
                         % (attempt, type(lst).__name__,
                            len(lst) if isinstance(lst, list) else -1))
        except Exception as e:
            trace.append("第%d轮ImportMedia异常:%s" % (attempt, e))
        time.sleep(IMPORT_RETRY_SEC)
        hit = _find_pool_item(mp, names)
        if hit:
            trace.append("第%d轮回查命中" % attempt)
            return hit, base

    # ★ v4.3.1 第三通路：MediaStorage.AddItemListToMediaPool（不经过 ImportMedia）
    if resolver is not None:
        try:
            ms = resolver.GetMediaStorage()
            adder = getattr(ms, "AddItemListToMediaPool", None)
            if callable(adder):
                added = adder([srt_path])
                if isinstance(added, list) and added:
                    trace.append("MediaStorage→list/%d" % len(added))
                    return added[-1], base
                trace.append("MediaStorage→%s" % type(added).__name__)
                time.sleep(IMPORT_RETRY_SEC)
                hit = _find_pool_item(mp, names)
                if hit:
                    trace.append("MediaStorage后回查命中")
                    return hit, base
            else:
                trace.append("MediaStorage 不可调用")
        except Exception as e:
            trace.append("MediaStorage异常:%s" % e)

    raise RuntimeError("导入字幕到媒体池失败（三种通路均未成功）：%s" % " / ".join(trace))


# ── 以下为 inplace（原地落轨）所需的辅助 ──────────────────────────────
def _set_track_enabled(tl, kind, idx, enabled):
    """屏蔽不同 Resolve 版本的 API 命名差异，禁用/启用某条轨。

    ★ v4.1.1 真机修正：正确方法名是 **SetTrackEnable（无 d）**。
      旧实现只试 SetTrackEnabled / SetTrackEnableState，而 21.1.2 上这两个
      属性都是 None（不可调用），于是本函数一直【静默返回 False】——
      "落轨前禁用其它轨、只启用目标轨"实际上从未生效，只是过去的时间线
      都只有一条字幕轨，所以没人发现。多轨时间线上它会直接把字幕落错轨。
    """
    for name in ("SetTrackEnable", "SetTrackEnabled", "SetTrackEnableState"):
        fn = getattr(tl, name, None)
        if callable(fn):
            try:
                fn(kind, idx, enabled)
                return True
            except Exception:
                pass
    return False


def _get_track_enabled(tl, kind, idx):
    for name in ("GetIsTrackEnabled", "GetTrackEnableState", "GetTrackEnabled"):
        fn = getattr(tl, name, None)
        if callable(fn):
            try:
                return bool(fn(kind, idx))
            except Exception:
                return None
    return None


def _enable_and_verify(tl, kind, idx, tries=5, wait=0.05):
    """启用某条轨，并【回读确认真的启用成功】。返回 True/False。

    ★ v4.1.1 真机教训：刚 AddTrack 出来的字幕轨不保证立刻可启用。
      启用失败时 AppendToTimeline 会落回上一条仍启用的轨，而落到【非空轨】
      会把整批字幕顺移到该轨已有内容之后（实测：目标 ST3 / 实际 ST2，
      首帧从 30 变成 420 = 用户字幕末尾 13s）。所以启用必须验证，不能赌。
    """
    for _ in range(max(1, tries)):
        _set_track_enabled(tl, kind, idx, True)
        state = _get_track_enabled(tl, kind, idx)
        if state is True:
            return True
        time.sleep(wait)
    return False


def subtitle_clip_ids(tl, track):
    try:
        items = tl.GetItemListInTrack("subtitle", track) or []
    except Exception:
        return set()
    ids = set()
    for c in items:
        try:
            uid = c.GetUniqueId()
        except Exception:
            uid = None
        if uid:
            ids.add(uid)
    return ids


def find_clip_by_id(tl, track, uid):
    try:
        items = tl.GetItemListInTrack("subtitle", track) or []
    except Exception:
        return None
    for c in items:
        try:
            if c.GetUniqueId() == uid:
                return c
        except Exception:
            pass
    return None


def _parse_srt_blocks(path, fps):
    """SRT → [{"sf": 起始帧, "ef": 结束帧, "text": 文本}]。解析失败返回 []。"""
    blocks = []
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as f:
            raw = f.read()
    except Exception:
        return blocks
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    def _sec(x):
        x = (x or "").strip().split()[0] if (x or "").strip() else ""
        hms = x.replace(",", ".").split(":")
        if len(hms) != 3:
            return None
        try:
            return int(hms[0]) * 3600 + int(hms[1]) * 60 + float(hms[2])
        except (TypeError, ValueError):
            return None

    for chunk in re.split(r"\n{2,}", raw):
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue
        ti = None
        for i, ln in enumerate(lines):
            if "-->" in ln:
                ti = i
                break
        if ti is None:
            continue
        a, _, b = lines[ti].partition("-->")
        s, e = _sec(a), _sec(b)
        if s is None or e is None:
            continue
        text = "\n".join(lines[ti + 1:]).strip()
        blocks.append({"sf": int(round(s * fps)), "ef": int(round(e * fps)), "text": text})
    return blocks


# ── API 优先：原生直改字幕文本 ─────────────────────────────────────────
# Resolve 的字幕条目是 TimelineItem，`SetName()` 是唯一的原生文本写入入口。
# 老版本曾判定它"不可靠"，但那是在"边车/归属"机制之前、且没有回读校验的用法。
# 本版策略：**先试原生直改，回读不一致就整批放弃、退回 SRT 往返** ——
# 既吃到 API 的性能（不导媒体池、不动轨道、秒级完成），又不承担静默失败风险。
_SETNAME_CAPS = {"checked": False, "ok": None}
_DIRECT_MATCH_TOL = 2      # 起始帧容差（帧）


def set_subtitle_text(item, text):
    """用原生 SetName 改一条字幕文本，回读校验。成功返回 True。"""
    fn = getattr(item, "SetName", None)
    if not callable(fn):
        return False
    try:
        ok = fn(text)
    except Exception:
        return False
    if ok is False:
        return False
    try:
        return (item.GetName() or "") == text
    except Exception:
        return False


def try_apply_texts_direct(tl, track, blocks, owned):
    """尝试原地改文本（不动轨道、不动媒体池）。返回 (ok, applied, why)。

    前置条件（缺一即放弃，交回 SRT 往返）：
      · owned        —— 整条轨都是本插件的；万一中途失败可由兜底整轨清掉重挂；
      · 条数一致     —— 直改无法删除/新增条目（删行的场景必须走往返）；
      · 每条按起始帧（±2）都能配上；
      · 每条 SetName 后回读一致。
    """
    if not blocks:
        return False, 0, "SRT 解析为空"
    if not owned:
        return False, 0, "轨道非独占（混有用户字幕），不直改"
    if _SETNAME_CAPS["ok"] is False:
        return False, 0, "本会话已确认 SetName 不可用"
    try:
        items = tl.GetItemListInTrack("subtitle", track) or []
    except Exception as e:
        return False, 0, "读取轨失败：%s" % e
    if len(items) != len(blocks):
        return False, 0, "条数不一致（轨 %d / SRT %d），直改无法增删" % (len(items), len(blocks))

    by_start = {}
    for it in items:
        try:
            by_start[int(round(float(it.GetStart() or 0)))] = it
        except Exception:
            pass

    pairs = []
    for b in blocks:
        it = by_start.get(b["sf"])
        if it is None:
            for off in range(-_DIRECT_MATCH_TOL, _DIRECT_MATCH_TOL + 1):
                if off == 0:
                    continue
                it = by_start.get(b["sf"] + off)
                if it is not None:
                    break
        if it is None:
            return False, 0, "起始帧 %d 匹配不到条目" % b["sf"]
        if it in [p[0] for p in pairs]:
            return False, 0, "起始帧重复匹配（第 %d 帧）" % b["sf"]
        pairs.append((it, b["text"]))

    applied = 0
    for it, text in pairs:
        if not set_subtitle_text(it, text):
            _SETNAME_CAPS["ok"] = False
            _SETNAME_CAPS["checked"] = True
            return False, applied, "SetName 回读校验失败（已改 %d 条，将整批重挂）" % applied
        applied += 1
    _SETNAME_CAPS["ok"] = True
    _SETNAME_CAPS["checked"] = True
    return True, applied, "SetName 直改成功"


def srt_first_frame(path, fps):
    """解析 SRT 第一条字幕的开始时间 → 时间线帧号。

    ★ v3.3.0：这是原地落轨的【正确对齐基准】。
      以前拿"素材在时间线上的起始帧 expected_offset"当期望首帧，遇到开头
      有静音的字幕（例如首条 00:00:00,967 → 58 帧）就会判成"错位"，然后
      把整轨字幕往前平移到 58 帧之前，把本来正确的字幕改错。
    """
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if "-->" not in line:
                    continue
                a = line.split("-->")[0].strip()
                hms = a.split(":")
                if len(hms) != 3:
                    continue
                sec = (int(hms[0]) * 3600 + int(hms[1]) * 60
                       + float(hms[2].replace(",", ".")))
                return int(round(sec * fps))
    except Exception:
        pass
    return 0


def _meta_spans(meta):
    """从边车 dict 里取出落轨记录的 (start,end) 帧区间列表。"""
    out = []
    for p in ((meta or {}).get("spans") or []):
        try:
            s = int(p[0]); e = int(p[1])
        except Exception:
            continue
        if e > s:
            out.append((s, e))
    return out


def _item_span(it):
    """取字幕条目的 (起帧, 止帧)；取不到返回 None。"""
    try:
        s = int(it.GetStart()); e = int(it.GetEnd())
    except Exception:
        return None
    return (s, e) if e > s else None


def _our_items_on_track(tl, track, spans, tol=2):
    """返回该字幕轨上【可确认是本插件落的】条目。

    判据：与边车记录的落轨帧区间逐条对上（±tol 帧）。
    真机上字幕条目的 GetMediaPoolItem() 返回 None，无法按来源匹配，
    边车 spans 是唯一可靠的身份凭据。
    """
    try:
        items = tl.GetItemListInTrack("subtitle", track) or []
    except Exception:
        return []
    if not spans:
        return []
    ours = []
    for it in items:
        sp = _item_span(it)
        if not sp:
            continue
        for (s, e) in spans:
            if abs(sp[0] - s) <= tol and abs(sp[1] - e) <= tol:
                ours.append(it)
                break
    return ours


def _clear_track(tl, track):
    """整轨清空某条字幕轨的条目，返回删除条数。

    ★ 专用轨归插件：按已确认的「字幕轨唯一性规则」，本插件独占一条轨，
      该轨上的条目全部由插件产生，可整轨清除；用户手工字幕在【其它轨】，
      不受影响。
    """
    try:
        items = tl.GetItemListInTrack("subtitle", track) or []
    except Exception:
        return 0
    if not items:
        return 0
    try:
        tl.DeleteClips(items, False)
    except Exception as e:
        print("[land] 清空字幕轨 %d 失败：%s" % (track, e), flush=True)
        return 0
    return len(items)


def _remove_our_items(tl, track, meta, owned):
    """删除该字幕轨上【本插件的】旧字幕，返回删除条数。

    ★ 优先按 spans 精确删除（只删能对上帧区间的，用户的其它字幕不动）；
      spans 缺失（老版本边车）时，按已确认的「专用轨归插件」模型整轨清除。
    """
    spans = _meta_spans(meta)
    items = _track_items(tl, track)
    if not items:
        return 0
    if spans:
        ours = _our_items_on_track(tl, track, spans)
        if ours:
            try:
                tl.DeleteClips(ours, False)
                print("[land] 清专用轨 %d 上本插件旧字幕 %d 条（按落轨跨度精确匹配）"
                      % (track, len(ours)), flush=True)
                return len(ours)
            except Exception as e:
                print("[land] 清专用轨旧字幕失败：%s" % e, flush=True)
                return 0
        # spans 没命中：若该轨条目数与 spans 数一致，说明只是位置漂了
        if owned and len(items) == len(spans):
            return _clear_track(tl, track)
        return 0
    if owned:
        return _clear_track(tl, track)
    return 0


def land_inplace(mp, tl, land_srt, expected_offset, fps, work, meta_key=None,
                 preloaded=None, resolver=None):
    """策略：直接在【当前（原）时间线】上落轨（本项目实测对齐法）。

    只动字幕轨，视频/音频原样保留。对齐核心（实测 Resolve 对字幕轨会忽略
    recordFrame，但【尊重播放头位置】并按 SRT 内部时间码铺开）：
      1) 先把所有字幕轨【禁用】，只在一条目标轨上【启用】，保证 AppendToTimeline
         只会把字幕落进那条轨、且从轨道头开始；
      2) 落轨前用 SetCurrentTimecode(GetStartTimecode()) 把【播放头钉在时间线起点】，
         字幕便落在时间线起点，与视频（也始于时间线起点）天然对齐；
      3) AppendToTimeline 只传媒体项 {item}，不传 recordFrame / trackIndex。
    视频起始偏移已由 main() 在 land_srt 内 bake 进时间码，落点无需再平移。

    ★ v4.0.7 修复1：优先复用【插件专用轨】（边车记录），不再"找不到空轨就
      AddTrack"——那会在已落过字幕的时间线上凭空多出一条空字幕轨（用户看到
      ST2 突然出现），而且字幕还落不进去。
    ★ v4.0.7 修复2：边车【只在落轨成功后】写；失败时保留旧边车不动，
      避免一次失败把后续「字幕更新」的定位带偏。
    ★ v4.0.7：落轨后按【各轨条目 ID 差集】探测字幕【实际落到哪条轨】，
      不再假设它落在我们启用的那条（真机证明二者可能不一致）。
    """
    meta_key = meta_key or land_srt
    meta = _read_landed_meta(meta_key)
    ded, why, owned = _dedicated_info(tl, meta)

    # ★ v4.0.8 修复4a：SRT 一条字幕都没有 → 直接早退。
    #   空 SRT 落轨必然 0 条，而落 0 条会走「失败回收」路径；不如在
    #   动时间线之前就返回，绝不碰任何字幕轨（也就不可能留下孤儿空轨）。
    #   ★ 只在【文件确实存在】时才判定 0 条：文件读不到（不存在/权限）时
    #     交给后面的 import_to_pool 去报明确错误，绝不误判成"空字幕"。
    try:
        _srt_exists = os.path.exists(land_srt)
    except Exception:
        _srt_exists = False
    if _srt_exists and count_srt(land_srt) <= 0:
        print("[land] SRT 无有效字幕条目（0 条），不落轨、不改动任何字幕轨", flush=True)
        print("[land] result=fail placed=0 total=0 track=0 first=0 srt_first=0 "
              "offset=%d aligned=False method=inplace reclaimed=0 empty_srt=1"
              % int(expected_offset), flush=True)
        return {
            "ok": False, "placed": 0, "track": 0,
            "first": 0, "srt_first": 0, "replaced": 0,
            "offset": expected_offset, "new_timeline": "", "clips": 0,
            "aligned": False, "temp_start": None, "method": "inplace",
            "dedicated": why, "cleared_old": 0, "reclaimed_track": 0,
            "diag": "SRT 无有效字幕条目（0 条），未落轨",
        }

    # ★★★ v4.1.0 顺序铁律（对齐剪包助手真机取证）：
    #   本行【之前不得有任何时间线写操作】（SetTrackEnable / AddTrack / SetCurrentTimecode
    #   / DeleteClips）。Resolve 在时间线变动后会「保存工程」，紧接着调 ImportMedia
    #   会偶发返回空列表（list/0），而且不抛异常 —— 表现为「落 0 条」。
    #   所以：媒体池先导入，时间线后动。
    #   · preloaded 由 relang_subtitles 传入（它已为同一份 SRT 导过），
    #     避免二次导入时把刚导入的项当「同名旧项」删掉。
    if preloaded:
        item, base = preloaded
    else:
        item, base = import_to_pool(mp, land_srt, resolver=resolver)

    # 1) 禁用所有字幕轨，并决定目标轨
    try:
        n = tl.GetTrackCount("subtitle") or 0
    except Exception:
        n = 0
    target = None
    cleared_old = 0
    # ★ v4.0.8 修复4：记录本次是否【自建】了字幕轨。
    #   只有自建的轨才允许在失败时被回收（绝不删用户的轨）。
    self_created = 0
    if ded:
        # 复用插件专用轨：先清掉本插件在它上面的旧字幕（顺带解决重复落轨重影）
        cleared_old = _remove_our_items(tl, ded, meta, owned)
        if not _track_items(tl, ded):
            target = ded
            print("[land] 复用插件专用字幕轨 %d（定位依据：%s）"
                  % (ded, why), flush=True)
        else:
            print("[land] 专用轨 %d 上还有非本插件条目（保留），改用空轨落新字幕"
                  % ded, flush=True)
    for i in range(1, n + 1):
        _set_track_enabled(tl, "subtitle", i, False)   # 先全部禁用
        if target is None and not _track_items(tl, i):
            target = i                                  # 复用第一条空轨
    if target is None:
        try:
            tl.AddTrack("subtitle")
            n2 = tl.GetTrackCount("subtitle") or 0
            if n2 > n:
                target = n2
                self_created = n2
                print("[land] 新建字幕轨 %d（此前没有可用的空轨）" % target, flush=True)
        except Exception:
            pass
    if target is None:
        target = n if n > 0 else 1

    def _abort(reason):
        """放弃本次落轨：恢复所有字幕轨启用态后返回失败。"""
        try:
            for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
                _set_track_enabled(tl, "subtitle", i, True)
        except Exception:
            pass
        print("[land] %s" % reason, flush=True)
        return {"ok": False, "placed": 0, "track": 0, "first": 0, "srt_first": 0,
                "replaced": 0, "offset": expected_offset, "new_timeline": "",
                "clips": 0, "aligned": False, "temp_start": None,
                "method": "inplace", "dedicated": why, "cleared_old": 0,
                "reclaimed_track": 0, "diag": reason}

    # ★ v4.1.1：目标轨必须【确认启用成功】且【确认为空】才允许落轨。
    #   启用失败会让 Append 落回别的轨；落到非空轨会把整批字幕顺移到该轨
    #   已有内容之后（真机：目标 ST3 落到 ST2，首帧 30 → 420）。
    #   两种情况都会污染用户字幕，宁可放弃本次落轨。
    if not _enable_and_verify(tl, "subtitle", target):
        return _abort("目标字幕轨 %d 启用失败，已放弃本次落轨（未追加任何条目）" % target)
    if _track_items(tl, target):
        return _abort("目标字幕轨 %d 非空，已放弃本次落轨（落轨要求空轨）" % target)

    # 2) 钉播放头到时间线起点（字幕落点 = 播放头；视频也始于时间线起点 → 对齐）
    #    ★ v4.1.0：先记住用户原来的播放头，落轨结束后还原，
    #      否则每次落轨播放头都被甩回 00:00:00。
    try:
        orig_tc = tl.GetCurrentTimecode()
    except Exception:
        orig_tc = None
    try:
        st = tl.GetStartTimecode()
        if st:
            tl.SetCurrentTimecode(st)
    except Exception:
        pass

    # 3) 落新字幕：只传媒体项，不传 recordFrame / trackIndex
    #    ★ v4.1.0：媒体项已在进入本函数时导入（顺序铁律），这里只取用，
    #      绝不能再调 import_to_pool —— 那会把刚导入的同名项当成旧项删掉。
    # ★ 落轨前记录【所有】字幕轨的条目 ID —— 落完后据此探测字幕实际落在哪条轨
    try:
        n_now = tl.GetTrackCount("subtitle") or 0
    except Exception:
        n_now = n
    before_ids = {}
    for i in range(1, n_now + 1):
        before_ids[i] = subtitle_clip_ids(tl, i)
    def _probe_new_ids():
        """按【各轨条目 ID 差集】探测本次实际落到哪条轨。"""
        landed_i, ids = target, set()
        try:
            n_after = tl.GetTrackCount("subtitle") or 0
        except Exception:
            n_after = n_now
        for i in range(1, n_after + 1):
            diff = subtitle_clip_ids(tl, i) - before_ids.get(i, set())
            if len(diff) > len(ids):
                ids, landed_i = diff, i
        return ids, landed_i

    # ★ v4.1.0：先 AppendToTimeline；没落上再换 InsertIntoTimeline（按播放头插入，
    #   此刻播放头已被钉在时间线起点，语义等价）。判据仍用 ID 差集，不看返回值
    #   （21.1 上 InsertIntoTimeline 成功也可能返回 None）。
    try:
        mp.AppendToTimeline([item])
    except Exception as e:
        print("[land] AppendToTimeline 异常：%s" % e, flush=True)
    new_ids, landed = _probe_new_ids()
    # ★ 用 callable 判，不用 hasattr：PyRemoteObject 暴露了属性名但值是 None
    #   （21.1.2 实测 `tl.InsertIntoTimeline` 存在但不可调用，hasattr 会误判）。
    _insert = getattr(tl, "InsertIntoTimeline", None)
    if not new_ids and callable(_insert):
        print("[land] AppendToTimeline 未落上，改用 InsertIntoTimeline", flush=True)
        try:
            _insert([item])
        except Exception as e:
            print("[land] InsertIntoTimeline 异常：%s" % e, flush=True)
        new_ids, landed = _probe_new_ids()
    if landed != target and new_ids:
        # ★ v4.1.1：落到别的轨 = 异常（目标轨已确认启用且为空）。
        #   以前"以实际落点为准"会把整批字幕留在用户的非空轨上，且被顺移到
        #   该轨末尾。现在改为：把刚落的条目删掉，直接放弃本次落轨。
        removed = 0
        try:
            for it in (tl.GetItemListInTrack("subtitle", landed) or []):
                try:
                    if it.GetUniqueId() in new_ids:
                        tl.DeleteClips([it], False)
                        removed += 1
                except Exception:
                    pass
        except Exception:
            pass
        return _abort("字幕落到 ST%d（目标 ST%d），已回滚误落的 %d 条并放弃本次落轨"
                      % (landed, target, removed))
    clips = [c for c in (find_clip_by_id(tl, target, uid) for uid in new_ids) if c]
    placed = len(clips)
    first = None
    # ★ v3.4.0：取所有新字幕里【最早】的那一条，不能拿 clips[0]。
    #   find_clip_by_id 返回的顺序不按时间（实测 14 条时 clips[0] 是第 3 条，
    #   start=495，于是明明对齐的字幕被判成"差 480 帧"。
    #   v4.0.4 起上层不再因此新建时间线兜底，只返回诊断信息）。
    for c in clips:
        try:
            v = int(round(float(c.GetStart() or 0)))
        except Exception:
            continue
        if first is None or v < first:
            first = v

    # 4) 校验对齐
    #   ★ v3.3.0：期望首帧 = 【SRT 首条字幕时间码】，不是"素材起始帧"。
    #     land_srt 里已经 bake 过素材偏移（main ⑤ 调 shift_srt），所以
    #     它的首条时间码就是字幕在时间线上该出现的位置，唯一正确基准。
    #   ★ 同时【删掉】原来的整轨平移校正：那段代码在误判时会把整轨字幕
    #     往前推，把本来对齐的字幕推到错位/轨道外（历次"错位""落 0 条"都源于此）。
    srt_first = srt_first_frame(land_srt, fps)
    tol = max(1, int(round(0.1 * fps)))
    aligned = (first is not None and abs(first - srt_first) <= tol)
    if first is None:
        print("[land] 对齐校验失败：字幕轨上没取到任何条目", flush=True)
    elif aligned:
        print("[land] 对齐校验通过：实际首帧 %d = SRT 首条 %d（素材起点 %d）"
              % (first, srt_first, expected_offset), flush=True)
    else:
        print("[land] 对齐校验未通过：实际首帧 %d / SRT 首条 %d / 素材起点 %d"
              "（差 %d 帧，不再自动平移，交上层换策略）"
              % (first, srt_first, expected_offset, first - srt_first), flush=True)

    # 一条都没落上时，把刚才禁用的字幕轨全部恢复启用——
    # 否则上层退回「新建空时间线」后，原时间线上的字幕会整个消失。
    if placed <= 0:
        try:
            for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
                _set_track_enabled(tl, "subtitle", i, True)
        except Exception:
            pass

    # ★ v4.0.8 修复4：回收本次【自建】却没落上字幕的空轨。
    #   真机复验暴露：落轨失败时 land_inplace 会先 AddTrack 新建一条字幕轨，
    #   落不上就把它留在时间线上 → 用户看到「ST2 凭空出现」的空轨。
    #   （这正是 v4.0.6 真机日志「整轨清除专用字幕轨 1 旧条目 53 条 →
    #     已挂到 0 条 → 边车写成 track=2」的那条路径，修复1/2 没堵住。）
    #   回收条件（三者同时成立才删，绝不误删用户的轨）：
    #     ① 这条轨是本次自己 AddTrack 建的（self_created > 0）；
    #     ② 字幕实际没落在它上面（落点轨 != 它，或压根没落上）；
    #     ③ 它当前是空的（一条条目都没有）。
    #   自建轨总是【最后一条】，删除后不影响其它轨的索引。
    reclaimed = 0
    if self_created:
        landed_on_it = (landed == self_created and placed > 0)
        if not landed_on_it and not _track_items(tl, self_created):
            if _delete_track(tl, self_created):
                reclaimed = self_created
        elif landed_on_it:
            print("[land] 自建字幕轨 %d 成功落上字幕，保留" % self_created, flush=True)

    # ★ v4.0.7 修复2：边车【只在落轨成功后】写。
    #   v4.0.6 是"无论成败都写"，一次失败的落轨会把边车覆写成
    #   {"track": <新建的空轨>, "spans": []}，把后续「字幕更新」的
    #   定位彻底带偏（旧字幕删不掉、新字幕也不落）。
    #   失败时保留旧边车不动，让下次仍能找回原来的专用轨。
    if placed > 0:
        spans = []
        for c in clips:
            sp = _item_span(c)
            if sp:
                spans.append(sp)
        try:
            tl_name = tl.GetName() or ""
        except Exception:
            tl_name = ""
        # owned：该轨上的条目是否【全部】由本插件产生。
        #   判据 = 轨上条目数 == 本次落下的条目数（说明没有混入用户条目）。
        #   owned=True  → 下次复用时可整轨清除；
        #   owned=False → 下次只按 spans 精确删，绝不误删用户字幕。
        total_items = len(_track_items(tl, target))
        owned_now = bool(total_items) and total_items <= placed
        _write_landed_meta(meta_key, target, spans, owned=owned_now,
                           tl_name=tl_name, count=placed)
    else:
        print("[land] 落轨 0 条，保留原边车不动（避免带偏下次定位）", flush=True)

    # ★ v4.1.0：还原用户原来的播放头（成功/失败都还原），否则每点一次落轨
    #   播放头都被甩回时间线起点。
    try:
        if orig_tc:
            tl.SetCurrentTimecode(orig_tc)
    except Exception:
        pass

    # ★ v4.1.0：一行可统计的结果摘要 —— 后续优化靠 grep "result=fail" 定量。
    try:
        total_blocks = count_srt(land_srt)
    except Exception:
        total_blocks = 0
    print("[land] result=%s placed=%d total=%d track=%d first=%d srt_first=%d "
          "offset=%d aligned=%s method=inplace reclaimed=%d"
          % ("ok" if placed > 0 else "fail", placed, total_blocks, target,
             first if first is not None else 0, srt_first, expected_offset,
             bool(aligned), reclaimed), flush=True)

    return {
        "ok": placed > 0,
        "placed": placed,
        "track": target,
        "first": first if first is not None else 0,
        "srt_first": srt_first,
        "replaced": cleared_old,
        "offset": expected_offset,
        "new_timeline": "",
        "clips": 0,
        "aligned": bool(aligned),
        "temp_start": None,
        "method": "inplace",
        "dedicated": why,
        "cleared_old": cleared_old,
        # ★ v4.0.8 修复4：本次回收掉的孤儿空轨索引（0 = 没回收）
        "reclaimed_track": reclaimed,
    }


# ★ v4.0.4：_LAST_DST / create_timeline_with_subs / land_via_empty_timeline
#   （新建时间线兜底路径）整体删除。红线：所有落轨只在现有时间线操作，
#   绝不新建时间线，也不再把原素材复制进新时间线。


def _is_our_subtitle_item(it, srt_path):
    """本条时间线字幕，来源是不是【本插件这次要重挂的 SRT】？

    ★ v4.0.5 R2：与 cut_inplace._is_our_subtitle 同判据（按来源媒体项名 / 路径
      stem 匹配），但实现放在本模块，避免与 cut_inplace 形成循环 import。
      落轨进程与粗剪进程是两个独立 Python 进程、内存不共享，只能靠
      【字幕条目的来源媒体项】跨进程识别本插件的字幕，保留用户手工字幕。
    """
    stem = os.path.splitext(os.path.basename(srt_path or ""))[0].lower()
    if not stem:
        return False
    try:
        mi = it.GetMediaPoolItem()
    except Exception:
        mi = None
    if not mi:
        return False
    try:
        nm = (mi.GetName() or "").strip().lower()
    except Exception:
        nm = ""
    if nm and nm == stem:
        return True
    try:
        props = mi.GetClipProperty() or {}
    except Exception:
        props = {}
    if isinstance(props, dict):
        fp = str(props.get("File Path") or props.get("Original File Path") or "")
        if fp and os.path.splitext(os.path.basename(fp))[0].lower() == stem:
            return True
    return False


def _landed_meta_path(srt_path):
    return srt_path + ".landed.json"


def _write_landed_meta(srt_path, track, spans, owned=True, tl_name="", count=0):
    """★ v4.0.6 R2 / v4.0.7：把本次落轨用的字幕轨索引 + 各条起止帧写进边车，
    供下次「字幕更新」整轨清除时定位【本插件的专用轨】（跨进程持久化）。

    ★ v4.0.7：新增 owned 字段。
      owned=True  → 该轨上的条目【全部】由本插件产生（落轨时该轨是空的），
                    复用时可整轨清除。
      owned=False → 该轨上混有非本插件条目（用户手工字幕），复用时只删
                    spans 命中的那些，绝不误删用户字幕。
    ★ v4.0.7：新增 tl（时间线名）/ count（条数）——
      tl 防止同一条 SRT 跨时间线串扰；
      count 用于边车被污染时，靠"条数吻合"交叉验证出真正的插件轨。
    """
    try:
        with open(_landed_meta_path(srt_path), "w", encoding="utf-8") as f:
            json.dump({"track": int(track),
                       "spans": [[int(s), int(e)] for s, e in (spans or [])],
                       "owned": bool(owned),
                       "tl": str(tl_name or ""),
                       "count": int(count or 0),
                       "ver": 2},
                      f, ensure_ascii=False)
    except Exception as e:
        print("[land] 写落轨边车失败（不影响主流程）：%s" % e, flush=True)


def _read_landed_meta(srt_path):
    """读边车；读不到/损坏返回 None（绝不让坏边车带偏后续流程）。"""
    try:
        with open(_landed_meta_path(srt_path), encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and (d.get("track") or d.get("spans")):
            return d
    except Exception:
        pass
    return None


def _track_items(tl, track):
    try:
        return tl.GetItemListInTrack("subtitle", track) or []
    except Exception:
        return []


def _delete_track(tl, track):
    """★ v4.0.8 修复4：删除一条【空】字幕轨（回收本次自建的孤儿轨）。

    真机已验证 `timeline.DeleteTrack("subtitle", idx)` 在 DaVinci Resolve 21.1
    上可用（官方 Scripting API v20.2 / v21.0.4 均有载）。

    安全约定：
      · 只删**空轨**（一条条目都没有）——绝不删有内容的轨；
      · 只删调用方明确指定的那一条（本函数不猜、不遍历）；
      · 失败一律吞掉异常并返回 False，绝不因此中断主流程。

    注意：删除后【后面】的轨索引会前移。调用方必须自行处理索引位移
    （本插件的回收场景里，被删的自建轨总是最后一条，无位移问题）。
    """
    try:
        if _track_items(tl, track):
            print("[land] 拒绝删除非空字幕轨 %d（仍有条目）" % track, flush=True)
            return False
        ok = bool(tl.DeleteTrack("subtitle", int(track)))
        if ok:
            print("[land] 已回收本次自建的空字幕轨 %d" % track, flush=True)
        else:
            print("[land] 回收空字幕轨 %d 失败（Resolve 拒绝删除）" % track, flush=True)
        return ok
    except Exception as e:
        print("[land] 回收空字幕轨 %d 异常：%s" % (track, e), flush=True)
        return False


def _dedicated_info(tl, meta):
    """定位【本插件的专用字幕轨】。

    返回 (track, reason, owned)：
      track  = 轨道索引（1 起）；None 表示没定位到
      reason = "spans"      边车跨度在某轨命中（最强证据）
             = "meta"       边车记录的轨且非空
             = "unique_busy" 旧边车（无跨度）+ 时间线上只有一条非空字幕轨
                             → 救回被失败落轨污染的边车，并认定该轨归插件
             = "meta_empty" 认边车（该轨当前为空）
             = "none"        没定位到
      owned  = 该轨是否完全由本插件产生（决定复用时能否整轨清除）
    """
    try:
        n = tl.GetTrackCount("subtitle") or 0
    except Exception:
        n = 0
    meta = meta or {}
    spans = _meta_spans(meta)
    try:
        rec = int(meta.get("track") or 0)
    except Exception:
        rec = 0
    rec_valid = 1 <= rec <= n
    owned = bool(meta["owned"]) if ("owned" in meta) else False

    # ★ v4.0.7：边车不得跨时间线串扰 —— 同一条 SRT 在别的时间线上用过时，
    #   帧区间对不上、轨索引也没意义，直接当"没有边车"处理。
    try:
        cur_tl = str(tl.GetName() or "")
    except Exception:
        cur_tl = ""
    meta_tl = str(meta.get("tl") or "")
    if meta_tl and cur_tl and meta_tl != cur_tl:
        return None, "other_timeline", False

    # 1) 跨度命中（最强证据：帧区间是落轨产物，不会撞上别的轨）
    if spans:
        for i in range(1, n + 1):
            if _our_items_on_track(tl, i, spans):
                return i, "spans", owned
    # 2) 边车记录的轨且非空
    if rec_valid and _track_items(tl, rec):
        return rec, "meta", owned
    # 3) 旧边车（无跨度）+ 唯一非空字幕轨 → 救回被污染的边车
    if rec_valid and not spans:
        busy = [i for i in range(1, n + 1) if _track_items(tl, i)]
        if len(busy) == 1:
            return busy[0], "unique_busy", True
        # ★ v4.0.7：多条非空轨时，靠边车记的【条数】交叉验证哪条是插件轨
        try:
            cnt = int(meta.get("count") or 0)
        except Exception:
            cnt = 0
        if cnt > 0:
            for i in busy:
                if len(_track_items(tl, i)) == cnt:
                    return i, "count_match", True
    # 4) 认边车（该轨可能已被清空）
    if rec_valid:
        return rec, "meta_empty", owned
    return None, "none", owned


def clear_our_subtitle_items(tl, srt_path):
    """★ v4.0.6 R2 / v4.0.7 修复3：删掉时间线上【本插件落的】旧字幕条目，
    保留用户手工字幕。返回删除条数。

    ★ v4.0.7 修复3：v4.0.6 直接盲信边车的 track 索引。真机上出现过的失败
      落轨会把边车写成 {"track": <空轨>, "spans": []}，于是这里"清 0 条"，
      后续「字幕更新」既不删旧、也不落新（用户看到的重影 / 丢字幕）。
      现在改成多证据定位（_dedicated_info）：
        · 边车跨度（spans）在某轨命中 → 最强证据，按跨度精确删；
        · 边车轨非空 → 认它；
        · 旧边车无跨度 + 时间线上唯一非空字幕轨 → 救回被污染的边车；
        · 认边车（该轨当前为空）。
    """
    n = 0
    meta = _read_landed_meta(srt_path)
    track, why, owned = _dedicated_info(tl, meta)
    if track:
        spans = _meta_spans(meta)
        if spans:
            ours = _our_items_on_track(tl, track, spans)
            if ours:
                try:
                    tl.DeleteClips(ours, False)
                    n += len(ours)
                    print("[land] R2：按落轨跨度清除专用字幕轨 %d 上本插件旧字幕 %d 条"
                          % (track, len(ours)), flush=True)
                    return n
                except Exception as e:
                    print("[land] R2 按跨度清除失败：%s" % e, flush=True)
                    return 0
            # 跨度没命中（字幕被用户手工挪动过）→ 退化到整轨清除（owned 时）
            if owned:
                n += _clear_track(tl, track)
                if n:
                    print("[land] R2：整轨清除专用字幕轨 %d 旧条目 %d 条"
                          % (track, n), flush=True)
                return n
            return 0
        # 无跨度（老边车）：该轨归插件 → 整轨清除
        n += _clear_track(tl, track)
        if n:
            print("[land] R2：整轨清除专用字幕轨 %d 旧条目 %d 条（定位依据 %s）"
                  % (track, n, why), flush=True)
        return n
    # ── 兜底（无边车）：原来的来源 stem 匹配 ──
    try:
        cnt = tl.GetTrackCount("subtitle") or 0
    except Exception:
        cnt = 0
    for i in range(1, cnt + 1):
        try:
            items = tl.GetItemListInTrack("subtitle", i) or []
        except Exception:
            items = []
        mine = [it for it in items if _is_our_subtitle_item(it, srt_path)]
        if not mine:
            continue
        try:
            tl.DeleteClips(mine, False)
            n += len(mine)
        except Exception as e:
            print("[land] 清本插件旧字幕轨 %d 失败：%s" % (i, e), flush=True)
    return n


def relang_subtitles(mp, tl, land_srt, media, fps, work, expected_offset=0,
                     meta_key=None, resolver=None):
    """★ v4.0.5 R2 / v4.0.7：字幕更新后的「闭环重挂」。

    只动本插件自己的字幕，不动视频/音频，也不误禁用用户手工加的其它字幕轨：
      1) 记录当前各字幕轨的启用态；
      2) 删掉本插件【旧】字幕条目（按落轨跨度精确删 / 专用轨整轨清）；
      3) 复用 land_inplace 落【新】字幕（import_to_pool + AppendToTimeline），
         并复用同一条专用轨，绝不新建字幕轨；
      4) 恢复其它字幕轨的启用态，并兜底确保落点轨启用。

    若时间线本就没有本插件的旧字幕（killed==0），说明用户还没在这条时间线
    落过字幕 —— 这时【不擅自新建字幕轨】，只把更新后的 SRT 同步进媒体池
      （import_to_pool），让「字幕更新」只同步池、不改动时间线，符合预期。

    ★ v4.0.7：边车以【原始 SRT 路径】为键（meta_key），不再用偏移后的临时
      副本路径 —— 否则每次偏移量一变，边车就"失联"，定位必然失败。
    """
    meta_key = meta_key or land_srt
    # 1) 记录启用态
    try:
        n = tl.GetTrackCount("subtitle") or 0
    except Exception:
        n = 0
    was_enabled = {}
    for i in range(1, n + 1):
        w = _get_track_enabled(tl, "subtitle", i)
        if w:
            was_enabled[i] = True

    # 2) ★ v4.1.0 API 优先：先试原生 SetName 原地改文本
    #    （不导媒体池、不动轨道、秒级完成）。回读校验不过就整批退回下面的 SRT 往返。
    meta = _read_landed_meta(meta_key)
    track, why, owned = _dedicated_info(tl, meta)
    if track:
        blocks = _parse_srt_blocks(land_srt, fps)
        ok_direct, applied, dwhy = try_apply_texts_direct(tl, track, blocks, owned)
        if ok_direct:
            print("[land] R2：SetName 直改 %d 条（未动轨道/媒体池）" % applied, flush=True)
            for i in was_enabled:
                _set_track_enabled(tl, "subtitle", i, True)
            try:
                tl_name = tl.GetName() or ""
            except Exception:
                tl_name = ""
            _write_landed_meta(meta_key, track, _meta_spans(meta), owned=True,
                               tl_name=tl_name, count=applied)
            return {"ok": True, "placed": applied, "track": track, "aligned": True,
                    "method": "direct", "relang_cleared": 0, "direct": True,
                    "first": 0, "srt_first": 0, "diag": "SetName 直改"}
        print("[land] R2：直改不可用（%s），退回 SRT 往返" % dwhy, flush=True)

    # 3) ★ 顺序铁律：先把新 SRT 导入媒体池，再动时间线（删旧）
    #    反了会触发 Resolve 工程保存 → 紧接着 ImportMedia 偶发返回空列表。
    pre_item, pre_base = import_to_pool(mp, land_srt, resolver=resolver)

    # 4) 删本插件旧字幕
    killed = clear_our_subtitle_items(tl, meta_key)

    if killed == 0:
        # 时间线尚无本插件字幕 → 只同步媒体池（上面已导入），不新建字幕轨
        print("[land] R2：时间线无旧字幕，仅同步媒体池", flush=True)
        for i in was_enabled:
            _set_track_enabled(tl, "subtitle", i, True)
        return {"ok": True, "placed": 0, "track": 0, "aligned": True,
                "method": "relang", "relang_cleared": 0,
                "first": 0, "srt_first": 0, "diag": "时间线尚无本插件字幕，仅同步媒体池"}

    # 3) 落新字幕（land_inplace 会临时禁用其它轨，第 4 步统一恢复）
    d = land_inplace(mp, tl, land_srt, expected_offset, fps, work,
                     meta_key=meta_key, preloaded=(pre_item, pre_base),
                     resolver=resolver)

    # 4) 恢复其它字幕轨启用态
    for i in was_enabled:
        _set_track_enabled(tl, "subtitle", i, True)
    # 兜底：落点轨若是原本禁用的，恢复时会被关掉，这里开回来
    # ★ v4.0.8：落点轨若已被「修复4」回收（自建空轨），绝不能再对它做启用态
    #   操作——否则会误开一条毫不相干的用户字幕轨。
    try:
        tgt = int(d.get("track") or 0)
        reclaimed = int(d.get("reclaimed_track") or 0)
        if tgt and tgt != reclaimed:
            _set_track_enabled(tl, "subtitle", tgt, True)
    except Exception:
        pass
    d["relang_cleared"] = killed
    d["method"] = "relang"
    return d


def main():
    out_lua = ""
    res = {"ok": False, "placed": 0, "total": 0, "track": 0, "replaced": 0,
           "offset": 0, "sidecar": "", "item": "", "err": "",
           "new_timeline": "", "first": 0, "clips": 0,
           "srt_total": 0, "lost": 0, "diag": "", "method": ""}
    try:
        if len(sys.argv) < 2:
            raise RuntimeError("缺少参数：config.json")
        with open(sys.argv[1], encoding="utf-8") as f:
            cfg = json.load(f)

        srt = str(cfg.get("srt") or "")
        media = str(cfg.get("media") or "")
        out_lua = str(cfg.get("out_lua") or "")
        land_tmp = None  # ★ v3.2.9：中文路径临时副本，落轨后清理
        # ★ mode = media  → 只导入媒体池 + 素材旁副本（识别完自动做）
        #   mode = timeline → 落轨：只在当前时间线原地挂字幕轨（点按钮才做，不新建）
        mode = str(cfg.get("mode") or "timeline").lower()

        if not srt or not os.path.exists(srt):
            raise RuntimeError("找不到字幕文件：" + srt)

        # ★ v3.2.7：落轨进度文件（land.lua.progress）—— 证明脚本已启动
        write_progress(out_lua, "start", "落轨脚本已启动，准备连接 Resolve")

        # ── ① 在源素材旁边放一份（失败不影响主流程）──────────────
        # ★ v1.0.1：设置页可关。素材目录是用户的领地（只读盘 / NAS / 同步盘
        #   都常见），以前只能无条件接受；现在默认仍生成，但关掉后只留在
        #   data\output，传进来的 "sidecar": false 生效。
        if cfg.get("sidecar", True) is not False:
            try:
                res["sidecar"] = copy_sidecar(srt, media)
            except Exception as e:
                res["err"] = "旁边副本失败：%s" % e

        # ── ② 连接 Resolve ──────────────────────────────────────
        r = connect()
        write_progress(out_lua, "connected", "已连接 DaVinci Resolve")
        if not r:
            raise RuntimeError(
                "无法连接 DaVinci Resolve。请确认 Resolve 正在运行，且 "
                "首选项 → 系统 → 常规 → 外部脚本使用 = 本地/网络")

        proj = r.GetProjectManager().GetCurrentProject()
        if not proj:
            raise RuntimeError("取不到当前工程")
        mp = proj.GetMediaPool()

        # ── ②b 只导媒体池（识别完成就自动做，不动时间线）──────────
        if mode == "media":
            item, base = import_to_pool(mp, srt, resolver=r)
            res["item"] = (item.GetName() or base).strip()
            res["placed"] = 0
            res["ok"] = True
            write_progress(out_lua, "done", "已导入媒体池")
            write_lua(out_lua, res)
            print(json.dumps(res, ensure_ascii=False))
            sys.exit(0)

        tl = proj.GetCurrentTimeline()
        if not tl:
            raise RuntimeError("当前没有打开的时间线")

        # ── ④ 素材在源时间线上的起始帧 → 字幕整体后移这么多 ──────
        offset, offset_how = find_offset(tl, media)
        res["offset"] = offset
        res["offset_how"] = offset_how

        fps = 60.0
        try:
            fps = float(tl.GetSetting("timelineFrameRate") or 60) or 60.0
        except Exception:
            pass

        # ★ 叠加用户手填的「整体偏移(秒)」：负数会把时间码夹到 0。
        user_off = float(cfg.get("offset_sec") or 0)
        if abs(user_off) > 1e-6:
            offset = offset + int(round(user_off * fps))
            if offset < 0:
                offset = 0
            res["offset"] = offset

        # ── ⑤ 生成偏移版 SRT ────────────────────────────────────
        base = os.path.splitext(os.path.basename(srt))[0]
        work = os.path.dirname(out_lua) or "."
        land_srt = srt
        if offset > 0:
            land_srt = os.path.join(work, base + ".srt")
            shift_srt(srt, land_srt, offset / fps)

        # ★ v3.2.11：停用「复制成 ASCII 临时名」这一步（v4.0.4 该函数已删除）。
        #   当初以为中文/空格路径会让 AppendToTimeline 静默落空，但实测证据是
        #   反的：v3.2.6 用含中文与空格的 output 目录内 SRT 曾连续成功挂上
        #   925 / 1778 条。历史兜底路径（新建时间线）末尾会误删刚建好的时间线，
        #   那条路径 v4.0.4 已整体删除。
        #   副作用还很明显：临时项 _asr_land_tmp 会留在媒体池里，污染
        #   「从媒体池载入字幕」的候选列表。所以直接走原路径。
        land_tmp = None

        # ── ③ 落轨：【只在现有时间线原地落轨】────────────────────────
        #   ★ v4.0.4 架构红线（用户拍板）：所有落轨只在当前打开的时间线上操作，
        #     绝不新建时间线。历史上"原地失败 → fallback 新建一条「X · 字幕」"
        #     的兜底路径已彻底删除：它会把画面/字幕搬到另一条时间线，违背红线，
        #     而且是历次「误删时间线」（包装对象比较不等，建完又把自己删掉）的
        #     最大风险源。
        #   · 粗剪（cut_timeline.py / cut_inplace.py）自 v4.0.0 起也是原地增量剪辑，
        #     同样在当前时间线上直接切，不新建。
        #   · 原地落轨此前被判"不稳定"的两项真因均已修掉，无需兜底：
        #     ① 对齐校验基准从"素材起始帧"改为「SRT 首条时间码」；
        #     ② 删掉误判后的"整轨平移校正"。
        #   万一仍失败，直接把诊断信息返回给用户，不新建、不搬移。
        if mode == "relang":
            # ★ v4.0.5 R2：字幕更新闭环 —— 删掉本插件【旧】字幕，落【新】字幕，
            #   保留用户手工加的其它字幕轨（靠来源 stem 匹配，不误删）。
            #   killed==0（时间线本就没有本插件字幕）时，relang_subtitles 内部
            #   只把更新后的 SRT 同步进媒体池、不新建字幕轨，这里按"正常分支"
            #   处理，避免误报落轨失败。
            write_progress(out_lua, "landing", "开始把更新后的字幕重新挂到当前时间线字幕轨")
            r_in = relang_subtitles(mp, tl, land_srt, media, fps, work,
                                    expected_offset=offset, meta_key=srt,
                                    resolver=r)
            res.update(r_in)
            res["method"] = "relang"
            if r_in.get("relang_cleared") == 0:
                # 时间线尚无本插件字幕：仅同步了媒体池，属预期，不进回读判定。
                res["ok"] = True
                res["placed"] = 0
                res["total"] = 0
                print("[land] R2 relang：时间线无旧字幕，仅同步媒体池", flush=True)
                write_progress(out_lua, "done", "时间线无旧字幕，仅同步媒体池")
                write_lua(out_lua, res)
                print(json.dumps(res, ensure_ascii=False))
                sys.exit(0)
            if not (r_in.get("ok") and r_in.get("aligned")):
                res["err"] = (
                    "重新挂轨未成功（实际首帧 %s，SRT 首条 %s）。"
                    "请确认当前打开的就是目标时间线、字幕与素材对应；"
                    "插件不会新建时间线。"
                    % (r_in.get("first"), r_in.get("srt_first")))
                res["ok"] = False
                raise RuntimeError(res["err"])
        else:
            write_progress(out_lua, "landing", "开始把字幕挂到当前时间线字幕轨")
            r_in = land_inplace(mp, tl, land_srt, offset, fps, work, meta_key=srt,
                            resolver=r)
            res.update(r_in)
            res["method"] = "inplace"
            if not (r_in.get("ok") and r_in.get("aligned")):
                res["err"] = (
                    "原地落轨未成功（实际首帧 %s，SRT 首条 %s）。"
                    "请确认当前打开的就是目标时间线、字幕与素材对应；"
                    "插件不会新建时间线。"
                    % (r_in.get("first"), r_in.get("srt_first")))
                res["ok"] = False
                raise RuntimeError(res["err"])

        # ── ⑧ 回读确认：只在当前时间线上核对（v4.0.4 不再新建时间线）──
        dst = tl
        track = res.get("track") or 1
        after = dst.GetItemListInTrack("subtitle", track) or []
        # 最后兜底：当前时间线（落轨前已钉过播放头，通常就是它）
        if not after:
            try:
                cur = proj.GetCurrentTimeline()
                if cur is not None:
                    alt = cur.GetItemListInTrack("subtitle", track) or []
                    if alt:
                        dst, after = cur, alt
            except Exception:
                pass
        if not after:
            raise RuntimeError("字幕轨上没有出现字幕，落轨失败")
        res["placed"] = len(after)
        res["total"] = len(after)
        if not res.get("first"):
            try:
                res["first"] = int(after[0].GetStart() or 0)
            except Exception:
                res["first"] = 0

        # ★★ SRT 条数 vs 实际落轨条数必须比对。
        #    丢条是 Resolve 自己决定的（批量 AppendToTimeline 不给逐条回执），
        #    我们能做的是：数清楚、把嫌疑条目指出来、明确告诉用户。
        res["srt_total"] = count_srt(land_srt)
        if res["srt_total"] > res["placed"]:
            res["lost"] = res["srt_total"] - res["placed"]
            d = srt_frame_diag(land_srt, fps)
            bits = []
            if d["zero"]:
                bits.append("%d 条时长不足一帧" % d["zero"])
            if d["dup"]:
                bits.append("%d 条与上一条同帧起始" % d["dup"])
            if d["ovl"]:
                bits.append("%d 条与上一条时间重叠" % d["ovl"])
            res["diag"] = "；".join(bits) or "未发现明显的非法时间码"
            if d["items"]:
                res["diag"] += "｜样例：" + " / ".join(d["items"][:3])
        res["ok"] = bool(res.get("placed", 0) > 0 and res.get("aligned", False))

    except Exception as e:
        res["ok"] = False
        res["err"] = "%s: %s" % (type(e).__name__, e)
        res["trace"] = traceback.format_exc()[-600:]
    finally:
        # ★ v3.2.9：无论落轨成功/失败，清理中文路径临时副本
        try:
            tmp = land_tmp
        except NameError:
            tmp = None
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

    write_progress(out_lua, "done", "落轨完成")
    write_lua(out_lua, res)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res.get("ok") else 3)


if __name__ == "__main__":
    main()
