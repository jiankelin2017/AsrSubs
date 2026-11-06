# -*- coding: utf-8 -*-
"""字幕存取桥 —— 给 Lua 侧的字幕编辑器读写 SRT。

为什么需要它：
  Lua 的 io.open 在 Windows 上走 ANSI 接口，打不开含中文的路径
  （本工程的 SRT 就放在 C:\\legacy\\AsrSubs\\字幕输出\\）。所以
  字幕文件一律由本脚本读写，Lua 只碰纯 ASCII 的中间文件。

两种模式（都从命令行传一个 JSON 配置文件的路径）：
  {"mode": "dump", "srt": "<srt>", "out": "<subs.lua>"}
      SRT → Lua 表，供 Lua 用 loadfile 直接读
  {"mode": "save", "srt": "<srt>", "in": "<subs.json>"}
      Lua 写出的 JSON → 覆写回 SRT

★ 用「Lua 表」而不是 JSON 下发（Lua 读回结果），是因为 Lua 没有内置
  JSON 解析；反过来上行用 JSON，是因为 Lua 拼 JSON 只要转义引号即可。
  两个方向各取对方最容易解析的格式。
"""
import json
import os
import sys
import re
import shutil
import time

# ★★ v4.8.20：必须自己把【本文件所在目录】塞进 sys.path，不能指望
#   "以脚本方式运行时自动在 sys.path"。
#   装机版的运行环境是【嵌入式发行版】：runtime\Scripts\python312._pth 一存在，
#   解释器就固定在 isolated 模式（sys.flags.isolated / safe_path 都是 1），
#   sys.path 只认 ._pth 里列的几项 —— 脚本自己所在的目录【不在里面】。
#   本文件下面有 from txt_out / import hotwords / import land_subtitle 三处
#   同目录导入，于是运行时报
#       ModuleNotFoundError: No module named 'txt_out'
#   界面上的表现却是「✕ 错误：读取字幕超时（字幕文件仍在：…）」+
#   「subs_io.py 异常退出（退出码 1）」—— 字幕明明已经生成好了，
#   用户完全看不出是导入路径的问题。
#   同目录的 resolve_bridge / cut_timeline / cut_inplace / land_subtitle /
#   diag 都显式加了这一行，只有本文件漏了。被别的脚本 import 时这行无害。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

TS_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def ts_to_sec(h, m, s, ms):
    # ★ ms 可能是 1~3 位：",50" 是 50 毫秒，不是 500。
    #   原来的 ljust(3,"0") 会把它放大 10 倍，字幕整体提前半秒。
    #   按位数补右：1 位 → ×100，2 位 → ×10，3 位 → ×1
    ms = str(ms)
    scale = 10 ** (3 - len(ms)) if len(ms) < 3 else 1
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) * scale / 1000.0


def sec_to_ts(x):
    # 按总毫秒算：先拆时/分/秒再各自进位会在 59.9997 这类值上
    # 产出 "HH:MM:60,000"（非法时间码）。总毫秒法除到位，不会出现 60。
    total = int(round(max(0.0, float(x)) * 1000))
    h, rem = divmod(total, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def parse_srt(text):
    """解析 SRT → [{"s": 开始秒, "e": 结束秒, "t": 文本}, ...]"""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        # 首行可能是序号，也可能直接是时间行
        ti = 0
        if not TS_RE.search(lines[0]) and len(lines) > 1:
            ti = 1
        if ti >= len(lines):
            continue
        m = TS_RE.search(lines[ti])
        if not m:
            continue
        g = m.groups()
        st = ts_to_sec(g[0], g[1], g[2], g[3])
        en = ts_to_sec(g[4], g[5], g[6], g[7])
        body = "\n".join(lines[ti + 1:]).strip()
        if body:
            out.append({"s": st, "e": en, "t": body})
    return out


def lua_quote(s):
    out = []
    for ch in str(s):
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
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def write_lua_table(blocks, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    parts = ["return {\n"]
    for b in blocks:
        parts.append("  { s = %.3f, e = %.3f, t = %s },\n"
                     % (b["s"], b["e"], lua_quote(b["t"])))
    parts.append("}\n")
    tmp = path + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(parts))
    os.replace(tmp, path)


def write_hotwords_lua(blocks, out_path):
    """顺手统计全文高频热词 → 与 subs.lua 同目录的 hotwords.lua。

    ★ v2.0.8。放在 dump 分支里是因为【所有】字幕入口都会走这条路
      （识别完成、媒体池载入、选择窗载入），一处覆盖全部，不用新增 IPC。
    ★ 顺序很重要：必须先写 hotwords.lua、最后写 subs.lua。
      Lua 侧是「看见 subs.lua 出现就开始填表格」，如果 hotwords 后写，
      会读到上一次的旧数据。所以本函数在 write_lua_table 之前调用。
    ★ 失败只打 WARN，绝不影响字幕本身 —— 它只是个参考信息。
    """
    try:
        import hotwords
        hp = os.path.join(os.path.dirname(out_path) or ".", "hotwords.lua")
        total, thr, n = hotwords.write_lua(blocks, hp)
        print("OK hotwords %d words (>=%d) -> %d items" % (total, thr, n))
        return True
    except Exception as e:
        print("WARN hotwords failed: %s: %s" % (type(e).__name__, e))
        return False


def backup_srt(path, keep=5):
    """★ v4.0.2：写回前先把【原 SRT】备份一份，防止「字幕更新」覆盖后不可逆。

    场景：字幕可能是用户自己拖进媒体池的 SRT（非本插件识别产物）。
    SaveSubs 会把编辑结果直接写回那个本体 —— 一旦改错就找不回原件。
    这里在覆写前按时间戳复制一份，最多保留最近 keep 份，避免无限堆积。

    ★ 备份失败只打 WARN，绝不阻断正常写回：备份是"保命"而非"门禁"，
      磁盘满 / 权限问题不该让用户连字幕都存不了。
    """
    try:
        if not path or not os.path.isfile(path):
            return None                      # 新文件（首次写回）没有可备份的原件
        d = os.path.dirname(path) or "."
        base = os.path.basename(path)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        # 同一秒内多次写回可能撞名，加 pid + 序号保证唯一
        bak = os.path.join(d, "%s.%s.bak" % (base, stamp))
        seq = 0
        while os.path.exists(bak):
            seq += 1
            bak = os.path.join(d, "%s.%s-%d.bak" % (base, stamp, seq))
        shutil.copy2(path, bak)
        # 清理旧备份：只认同 base 前缀、以 .bak 结尾的，按文件名（=时间序）排序
        olds = sorted(
            (f for f in os.listdir(d)
             if f.startswith(base + ".") and f.endswith(".bak")),
            reverse=True)
        for f in olds[keep:]:
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass
        print("OK backup -> %s" % bak)
        return bak
    except Exception as e:
        print("WARN backup failed: %s: %s" % (type(e).__name__, e))
        return None


def write_srt(blocks, path, backup=True):
    # ★ v4.0.2：写回入口（dump/totxt 不碰 SRT，只有 save 分支到这里）。
    #   顺序改为「先构造 → 再比对 → 变了才备份+覆写」：
    #   ① 内容完全一致就跳过 —— 用户无脑连点「字幕更新」时不会反复产生
    #      备份文件、也不会无谓重写磁盘（媒体池同步的 1 秒延迟也省了）。
    #   ② 内容真的变了，才在覆写【之前】留一份原文件备份（数据安全）。
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    parts = []
    n = 0
    for b in blocks:
        text = str(b["t"]).strip()
        # ★ 文本为空的行直接跳过：编辑时把整行删空是常见动作，
        #   不能让它变成一条"有时间轴、没内容"的空字幕写进 SRT。
        #   条目编号也跟着重排，保证 SRT 序号连续。
        if not text:
            continue
        n += 1
        parts.append("%d\n%s --> %s\n%s\n\n"
                     % (n, sec_to_ts(b["s"]), sec_to_ts(b["e"]), text))
    content = "".join(parts)

    # 与磁盘现有内容比对（忽略 BOM / 换行的细微差异）：相同就不动。
    if os.path.isfile(path):
        try:
            old = read_text_any(path).replace("\r\n", "\n").replace("\r", "\n")
            if old.replace("\ufeff", "") == content:
                print("OK save: 内容未变化，跳过写入与备份")
                return
        except Exception:
            pass        # 读不出来就照常写，别因为比对失败卡住

    if backup:
        backup_srt(path)
    tmp = path + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(content)
    os.replace(tmp, path)


# ── TXT 文稿 ────────────────────────────────────────────────────────
# ★ v2.0.0：实现已抽到同目录的 txt_out.py，与 resolve_bridge 共用同一份。
#   本文件原来那份在「全文没有句末标点」时同样会踩空列表分支，一并收敛。
# ★ v4.8.20：同目录导入靠文件顶部那段 sys.path.insert 撑着（嵌入式解释器
#   不会自动加脚本目录），不能删。
from txt_out import write_txt  # noqa: E402


def read_text_any(path):
    """按常见编码读文本 —— 先看 BOM，再用「解出来像不像 SRT」做自检。

    ★★ v2.0.1 修复：v2.0.0 抽走 write_txt 时把这个函数一起删了，
       dump 分支调用它 → 读任何 SRT 都抛 NameError。按 v1.28 原文恢复。

    ★★ v4.8.25：原来的顺序是 utf-8-sig → utf-8 → gbk → latin-1，有两个洞：
      ① UTF-16（记事本「另存为 → Unicode」）的字节流在 GBK 下"恰好合法"，
         被解成乱码后时间码正则全部失配 → 载入 0 条字幕，且退出码是 0，
         界面上看不出任何异常；
      ② latin-1 对任何字节都成功，一旦走到它就是乱码，同样静默。
      现在：先按 BOM 定编码（UTF-16 LE/BE、UTF-8 BOM），再用
      「含不含义时间码箭头 -->」做自检，只有通过才采用；全都不像就
      返回最后一种解法的结果，由上层报"格式无法识别"。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        head = b""

    # ① BOM 优先：UTF-16 的 BOM 是 \xff\xfe / \xfe\xff，必须先于 GBK 识别
    if head[:2] in (b"\xff\xfe", b"\xfe\xff"):
        for enc in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
    if head[:3] == b"\xef\xbb\xbf":
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return f.read()
        except UnicodeDecodeError:
            pass

    # ② 无 BOM 的 UTF-16：ASCII 部分每隔一个字节就是 \x00，拿它当判据。
    #    （有用工具导出 UTF-16BE 且不带 BOM，只靠 BOM 判断会漏）
    if head.count(b"\x00") >= 2:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                with open(path, "r", encoding=enc) as f:
                    txt = f.read()
            except (UnicodeDecodeError, UnicodeError):
                continue
            if "-->" in txt:
                return txt

    # ③ 依次尝试，用"像不像 SRT"过滤 —— 空文件也算通过（它就是没内容）
    fallback = ""
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as f:
                txt = f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            break
        fallback = fallback or txt
        if not txt.strip() or "-->" in txt:
            return txt

    if fallback:
        return fallback
    # ③ 实在不行按 utf-8 容错读，交给上层判"解析失败"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_err_mark(out_path, err):
    """在 subs.lua 旁边写一份解析结果标记（Lua 侧读它提示用户）。

    ★ v4.8.25：dump 出 0 块时，用户看到的是"空表格"，分不清
      「文件本来就是空的」和「格式/编码坏了」。这个文件把原因带过去。
    ★ v1.2.0：没有 err 就【不写】。原来每次 dump 都无条件落一个文件，
      而 out_path 为空时 dirname 退化成 "."，会把 subs_err.lua 写到
      进程当前目录（运行目录）里去 —— 既无用又脏。Lua 侧每轮 dump 前
      本来就会先删这个标记，不写不会留下旧值。
    """
    if not str(err or ""):
        return
    try:
        p = os.path.join(os.path.dirname(out_path) or ".", "subs_err.lua")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("return { err = %s }\n" % lua_quote(str(err or "")))
    except OSError:
        pass


def main():
    if len(sys.argv) < 2:
        print("usage: subs_io.py <config.json>")
        return 2
    with open(sys.argv[1], "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    mode = cfg.get("mode") or "dump"
    srt = cfg.get("srt") or ""

    if mode == "dump":
        if not srt or not os.path.isfile(srt):
            print("ERR srt not found: %s" % srt)
            # ★ 顺序：先热词后表格（见 write_hotwords_lua 的说明）
            write_hotwords_lua([], cfg.get("out") or "")
            write_lua_table([], cfg.get("out") or "")
            return 1
        text = read_text_any(srt)
        blocks = parse_srt(text)
        # ★ 和 save 分支一样修正 e<s：编辑器收到负时长条目时
        #   时间列会算出负数，用户看到 "-1.20 → 3.40" 这种鬼东西。
        for b in blocks:
            try:
                if float(b.get("e") or 0.0) < float(b.get("s") or 0.0):
                    b["e"] = b["s"]
            except (TypeError, ValueError):
                b["e"] = b.get("s") or 0.0
        # ★ v4.8.25：文件有内容却解析出 0 块 —— 必须告诉用户为什么。
        #   以前这里只打 "OK dump 0 blocks"，界面就是空表格，
        #   用户分不清「文件本来就是空的」和「编码/时间码坏了」。
        out = cfg.get("out") or ""
        warn = ""
        if not blocks and text.strip():
            warn = ("SRT 格式无法识别：文件有内容，但解析不出任何字幕。"
                    "常见原因是时间码不规范（要 HH:MM:SS,mmm --> HH:MM:SS,mmm），"
                    "或编码既不是 UTF-8 也不是 GBK。文件：" + srt)
            print("WARN " + warn)
        write_err_mark(out, warn)
        # ★ v2.0.8：先算热词，再写 subs.lua —— 后者是 Lua 侧的"就绪信号"
        write_hotwords_lua(blocks, out)
        write_lua_table(blocks, out)
        print("OK dump %d blocks" % len(blocks))
        return 0

    if mode == "dump_track":
        # ★ v4.2.0（B1）：从【时间线字幕轨】只读载入编辑区。
        #   只读：不 Set*、不 Delete*、不碰媒体池、不写边车。
        #   选轨口径与落轨/翻译一致：第一条「启用且非空」的字幕轨。
        #   产物与 dump 完全相同（subs.lua + hotwords.lua），外加一个
        #   subs_src.lua 记录来源轨号/条数，供 Lua 侧显示"来源：STn"。
        out = cfg.get("out") or ""
        src_mark = cfg.get("src") or ""
        blocks, track, tl_name, err = [], 0, "", ""
        try:
            import land_subtitle as L
            r = L.connect()
            if not r:
                err = "连不上 DaVinci（未运行 / 未开工程 / 未开启外部脚本）"
            else:
                proj = r.GetProjectManager().GetCurrentProject()
                tl = proj.GetCurrentTimeline() if proj else None
                if tl is None:
                    err = "当前没有打开的时间线"
                else:
                    tl_name = tl.GetName() or ""
                    try:
                        fps = float(tl.GetSetting("timelineFrameRate") or 24) or 24.0
                    except (TypeError, ValueError):
                        fps = 24.0
                    for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
                        if tl.GetIsTrackEnabled("subtitle", i) is False:
                            continue
                        items = tl.GetItemListInTrack("subtitle", i) or []
                        if not items:
                            continue
                        track = i
                        for it in items:
                            try:
                                st = float(it.GetStart() or 0)
                                en = float(it.GetEnd() or st)
                                txt = it.GetName() or ""
                            except Exception:
                                continue
                            if en < st:
                                en = st
                            blocks.append({"s": st / fps, "e": en / fps, "t": txt})
                        break
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
        blocks.sort(key=lambda b: (b["s"], b["e"]))
        # 与 dump 同序：先热词，再表格（后者是 Lua 侧的就绪信号）
        write_hotwords_lua(blocks, out)
        write_lua_table(blocks, out)
        if src_mark:
            try:
                with open(src_mark, "w", encoding="utf-8", newline="\n") as f:
                    f.write("return { track = %d, count = %d, timeline = %s, err = %s }\n"
                            % (track, len(blocks), lua_quote(tl_name), lua_quote(err)))
            except Exception:
                pass
        print("OK dump_track %d blocks (ST%s)%s"
              % (len(blocks), track or "-", (" ERR:" + err) if err else ""))
        return 0

    if mode == "save":
        src = cfg.get("in") or ""
        if not src or not os.path.isfile(src):
            print("ERR json not found: %s" % src)
            return 1
        with open(src, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        blocks = []
        for b in data.get("blocks") or []:
            t = str(b.get("t") or "").strip()
            if not t:
                continue
            try:
                s = float(b.get("s") or 0.0)
                e = float(b.get("e") or 0.0)
            except (TypeError, ValueError):
                continue
            if e < s:
                e = s
            blocks.append({"s": s, "e": e, "t": t})
        # ★ v4.8.25：写回失败要说人话。
        #   实测把 SRT 设成只读后点「字幕更新」，界面只看到
        #   "PermissionError: [WinError 5] 拒绝访问" 一段英文堆栈。
        #   真实触发场景很常见：SRT 正被记事本 / PR / 播放器打开着。
        #   ★ 顺序上把"原文件未改动"放在最后一行 —— Lua 取的是最后一行日志。
        try:
            write_srt(blocks, srt)
        except PermissionError:
            print("ERR 字幕文件被占用或为只读：%s" % srt)
            print("    原文件未改动，可放心；请关闭正在打开它的程序后重试。")
            return 4
        except OSError as e:
            print("ERR 写入字幕失败（%s）：%s" % (type(e).__name__, e))
            print("    原文件未改动，可放心；磁盘满或权限不足时请处理后重试。")
            return 4
        # ★ 文稿必须和字幕同步重生成，否则「导出 TXT」给出的是修改前的旧稿。
        #   给了 txt 路径就写；写失败不影响字幕本身。
        n_txt = 0
        txt = cfg.get("txt") or ""
        if txt:
            try:
                n_txt = write_txt(blocks, txt)
            except Exception as e:
                print("WARN txt not written: %s" % e)
        print("OK save %d blocks -> %s (txt=%d)" % (len(blocks), srt, n_txt))
        return 0

    if mode == "totxt":
        # 只按当前字幕重新生成文稿，不碰 SRT。
        # 「导出 TXT」在复制之前会先跑这一步，保证导出的是修改过的最新版本。
        src = cfg.get("in") or ""
        txt = cfg.get("txt") or ""
        if not src or not os.path.isfile(src):
            print("ERR json not found: %s" % src)
            return 1
        if not txt:
            print("ERR txt path empty")
            return 1
        with open(src, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        blocks = []
        for b in data.get("blocks") or []:
            t = str(b.get("t") or "").strip()
            if not t:
                continue
            # ★ 和 save 分支保持一致：脏 JSON 不该让整个脚本崩掉，
            #   坏条目跳过、能用的照常用。
            try:
                s = float(b.get("s") or 0.0)
                e = float(b.get("e") or 0.0)
            except (TypeError, ValueError):
                continue
            blocks.append({"s": s, "e": e, "t": t})
        n = write_txt(blocks, txt)
        print("OK totxt %d lines -> %s" % (n, txt))
        return 0

    print("ERR unknown mode: %s" % mode)
    return 2


if __name__ == "__main__":
    sys.exit(main())
