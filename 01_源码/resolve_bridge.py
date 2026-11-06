"""
Resolve 桥接入口
================
被 AsrSubs.lua 调用：

  python resolve_bridge.py <参数文件.json>

★ 为什么用参数文件而不是命令行参数？

  **Windows 命令行按 GBK 解读参数，而 Lua 传的是 UTF-8。**
  中文路径（如 C:\\Users\\...\\直播用\\...）会被解析成乱码，
  Python 报 "can't open file"。而且 .bat 文件本身是 ANSI 编码，chcp 也救不了。

  所以：**命令行只传纯 ASCII 的参数文件路径，其余值全部从文件读。**
  JSON 用 UTF-8 写，Python 读出来完全正确（Python 内部是 Unicode）。

参数文件格式（UTF-8 JSON）：
  {
    "audio": "C:/Users/.../123.mp4",   ← 也可写 "文件:路径" 从文件读
    "audio_file": "C:\\AsrSubs\\engine\\rt\\audio_path.txt",  ← 优先，从文件读路径
    "srt":    "C:/legacy/AsrSubs/字幕输出/Timeline 1.srt",
    "lang":   "Chinese",
    "max_chars": 12,
    "no_gap": false,
    "no_punct": false,
    "prompt": "",
    "corrections": "人参=人生,人叁\n重庆=崇庆",
    "status": "C:\\AsrSubs\\engine\\rt\\st.txt",
    "model":  "",
    "log":    "C:\\AsrSubs\\engine\\rt\\engine.log"
  }

  ★ v2.0.7："prompt" 永远传空串 —— 用户实测提示词会被模型当正文吐进字幕，
    位置还不对。热词功能改由 "corrections"（权重词纠错表）承担：识别完、
    写 SRT 之前做确定性替换，见 corrections.py。
"""
import json
import os
import re as _re
import subprocess
import sys
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from asr_engine import write_srt, list_asr_models, DEFAULT_ASR, _find_model
# ★ `_find_model` 虽然带下划线，但 refresh_manifest 里"一个模型都没扫到"时
#   要用它兜底出默认模型。以前漏了导入 → 那条兜底路径抛 NameError →
#   又被 `except Exception: pass` 吞掉 → models.txt/models.lua 静默不更新，
#   恰好在"没扫到模型"这个最需要兜底的场景失效。
# ★ Engine 故意不在这里 import：它 load() 时才去拿 torch，而 torch 一旦
#   进来就占一大块显存 / 系统页面文件。走常驻服务时本进程根本用不到它，
#   顶层 import 会在不需要的时候平白多占一份资源（见 main 里的延迟导入）。

# ── TXT 文稿 ────────────────────────────────────────────────────────
# ★ v2.0.0：实现已抽到同目录的 txt_out.py，与 subs_io 共用同一份。
#   原先这里自带的副本在「全文没有句末标点」时会抛 IndexError（勾选
#   「去除标点符号」必现），根因是两份实现分裂、只修了其中一份。
from txt_out import write_txt  # noqa: E402  （同目录模块，脚本方式运行时自动在 sys.path）


# ── 音频预处理（抽帧/转码 + 缓存）────────────────────────────────────
# ★★ v2.0.1 修复：v2.0.0 把 write_txt 抽到 txt_out.py 时，连下面这一整块
#    函数一并删掉了，只留下一行 import。结果 main() 里对 _prepare_audio 的
#    调用变成 NameError（源素材是视频时必崩），_re 也一起丢了（卡死判定
#    读 status.lua 时必崩）。这里按 v1.28 原文原样恢复，逻辑一字未改。
def _ffmpeg_to_wav(src, dst, ss=None, dur=None):
    """ffmpeg 转 16k 单声道 WAV；给了 ss/dur 就只抽这一段。

    ★ -ss 放在 -i 前面（输入侧 seek）：几分钟处的片段不用从头解码，
      速度快一个数量级；ffmpeg 默认 -accurate_seek，会解码到精确位置，
      不会因关键帧对齐而偏移（字幕时间精度靠这个）。
    """
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [exe, "-y"]
        if ss is not None and ss > 0:
            cmd += ["-ss", "%.3f" % float(ss)]
        cmd += ["-i", str(src)]
        if dur is not None and dur > 0:
            cmd += ["-t", "%.3f" % float(dur)]
        cmd += ["-vn", "-ac", "1", "-ar", "16000", str(dst)]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and os.path.isfile(dst) \
                and os.path.getsize(dst) > 1024:
            return True
        tail = (r.stderr or b"")[-300:]
        if isinstance(tail, bytes):
            tail = tail.decode("utf-8", "replace")
        print("ffmpeg 转码失败：%s" % tail.strip(), file=sys.stderr)
    except Exception as e:
        print("ffmpeg 不可用：%s: %s" % (type(e).__name__, e),
              file=sys.stderr)
    return False


def _audio_fingerprint(path):
    """音频指纹：绝对路径 + 大小 + mtime。文件被替换/重导都算新的。"""
    import hashlib
    st = os.stat(path)
    h = hashlib.md5()
    h.update(os.path.abspath(path).encode("utf-8", "replace"))
    h.update(("%d|%d" % (st.st_size, int(st.st_mtime))).encode("ascii"))
    return h.hexdigest()[:16]


def _prune_cache(cache_dir, log, keep=12, max_bytes=4 << 30):
    """缓存上限：最多 keep 个 / max_bytes 字节，超了删最旧的。"""
    try:
        files = []
        total = 0
        for n in os.listdir(cache_dir):
            p = os.path.join(cache_dir, n)
            try:
                if not os.path.isfile(p):
                    continue
                sz = os.path.getsize(p)
                files.append((os.path.getmtime(p), sz, p))
                total += sz
            except OSError:
                continue
        files.sort()
        while (len(files) > keep or total > max_bytes) and files:
            _, sz, p = files.pop(0)
            try:
                os.remove(p)
                total -= sz
            except OSError:
                break
    except OSError:
        pass


def _prepare_audio(src, cfg, work, log):
    """按需把音频料理成 16k 单声道 WAV，并缓存复用。

    · 源是 wav → 原样返回（soundfile 直读，没有 ffmpeg 开销）
    · 源是视频 → ffmpeg 出 16k WAV，按指纹缓存：
      重复识别同一素材（改改每行字数再跑一次）不再重复抽音频。
    """
    # ★ v1.1.3：时间线上只有一个片段、但被掐头/裁尾/从中间取用时，
    #   只抽「用到的这一段」去识别：既省掉整条素材的解码时间，也让 SRT
    #   天然从 0 起（落轨时按片段起始帧整体偏移，声画对齐）。
    t_from, t_dur = 0.0, 0.0
    _tr = cfg.get("audio_trim") or []
    try:
        if isinstance(_tr, (list, tuple)) and len(_tr) >= 2:
            t_from, t_dur = float(_tr[0]), float(_tr[1])
    except (TypeError, ValueError):
        t_from, t_dur = 0.0, 0.0
    if t_dur <= 0.2 or t_from < 0:
        t_from, t_dur = 0.0, 0.0          # 不合理就按整段处理

    if t_dur <= 0 and os.path.splitext(src)[1].lower() == ".wav":
        return src

    try:
        fp = _audio_fingerprint(src)
    except OSError:
        return src
    # 缓存根：3.0 用 paths.json 的 data\cache（音频缓存单独一层 audio\），
    # 老布局/开发机仍然是引擎下的 rt\cache —— 与分段缓存保持同一套规则。
    cache_dir = os.path.join(work, "cache")
    try:
        from asr_engine import PATHS as _P
        if _P.get("cache"):
            cache_dir = os.path.join(_P["cache"], "audio")
    except Exception:
        pass
    if t_dur > 0:
        # 缓存键带上裁剪区间，掐头 10 秒和掐头 20 秒是两份不同的音频
        fp = "%s_t%d_%d" % (fp, int(round(t_from * 1000)), int(round(t_dur * 1000)))
    dst = os.path.join(cache_dir, fp + ".wav")
    if os.path.isfile(dst) and os.path.getsize(dst) > 1024:
        log("音频缓存命中：%s" % os.path.basename(dst))
        return dst
    try:
        os.makedirs(cache_dir, exist_ok=True)
    except OSError:
        return src
    # ★ 临时名必须保留 .wav 后缀：ffmpeg 按扩展名猜封装格式，
    #   "<name>.wav.123.tmp" 会被当成未知格式直接报 Invalid argument。
    tmp = dst[:-4] + ".%d.tmp.wav" % os.getpid()
    ok = _ffmpeg_to_wav(src, tmp, ss=t_from or None, dur=t_dur or None)
    if not ok:
        try:
            os.remove(tmp)
        except OSError:
            pass
        log("音频预处理失败，按原文件识别")
        return src
    try:
        os.replace(tmp, dst)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return src
    _prune_cache(cache_dir, log)
    log("音频已缓存：%s" % os.path.basename(dst))
    return dst



LANG_MAP = {
    "Chinese": "Chinese", "English": "English", "Cantonese": "Cantonese",
    "Japanese": "Japanese", "Korean": "Korean", "Auto": "Auto",
}

# 模型清单文件：给 Lua 界面读取用（Lua 无法列目录，避免弹 CMD 窗口）
# ★★ 必须放在【纯 ASCII】目录（_HERE = D:\AsrSubs）。
#    原来 Lua 读的是工具安装目录（D:\AsrSubs\engine\models.txt）：
#    ① 那不是 Python 写清单的地方（Python 写 _HERE）→ 两边根本不是同一个文件
#    ② 就算放对了，Windows 的 Lua io.open 走 ANSI 接口，中文路径打不开
#    → 结果就是模型下拉框永远只有硬编码的默认值，用户装了新模型也看不到。
MANIFEST = os.path.join(_HERE, "models.txt")
# ★ 再写一份 Lua chunk：Lua 侧优先 loadfile 它，连 io.open 都不用。
MANIFEST_LUA = os.path.join(_HERE, "models.lua")


def refresh_manifest():
    """把当前可用的模型写成清单文件，供 Lua 读取。

    ★ 必须带【完整路径】：模型可能装在 _MODEL_DIRS 里的任意一个目录，
      只给名字的话 Lua 只能拿它去拼 TOOL_ROOT\\models\\<名字>，拼错就
      静默回退默认模型（用户以为换了模型，其实没换）。
    """
    try:
        items = list_asr_models()
        if not items:
            items = [_find_model(DEFAULT_ASR)]
        names = [p.name for p in items]
        with open(MANIFEST, "w", encoding="utf-8") as f:
            f.write("\n".join(names) + "\n")

        def _q(s):
            return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')

        # return { { name = "X", path = "C:\\...\\X" }, ... }
        body = ", ".join('{ name = %s, path = %s }' % (_q(p.name), _q(p))
                         for p in items)
        _write_lua_chunk(MANIFEST_LUA, body)
    except Exception:
        pass


def _read_path_from_file(p):
    """从 UTF-8 文本文件读第一行作为路径（避开命令行编码问题）。"""
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.readline().replace("\r", "").replace("\n", "").strip()
    except OSError:
        return ""


# 实际跑起来的模型说明（由 main 填，report 回带到界面）。
# 自定义路径无效时会变成"⚠ …已改用默认 XXX"。
MODEL_NOTE = ""

# ★ v1.0.1：环境告警（由 main 填，report 回带到界面状态栏）。
#   目前只有一种：「本机有 NVIDIA 显卡，但 torch 是 CPU 版」——
#   实测这台 RTX 5060 的机器上 torch 是 2.15.0+cpu，engine 静默选了 cpu，
#   面板上完全看不出来，用户只会觉得"这插件就是慢"。
RUN_WARN = ""


def _has_nvidia_gpu():
    """本机有没有 NVIDIA 显卡。只查命令/文件存在，绝不初始化 CUDA。"""
    import shutil
    for n in ("nvidia-smi", "nvidia-smi.exe"):
        try:
            if shutil.which(n):
                return True
        except Exception:
            pass
    cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                        "System32", "nvidia-smi.exe")
    return os.path.isfile(cand)


def _lua_str(s):
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


def _write_lua_chunk(path, body):
    """把 `return { <body> }` 写成 Lua chunk，原子替换。

    ★ 为什么不用纯文本 / JSON？
      Lua 侧读文件只有 loadfile 是可靠的：
        · io.open 在 Windows 上走 ANSI 接口，中文路径打不开；
          而且 Resolve 的 Lua 沙箱里 io 可能是 nil。
        · loadfile 实测一定可用（本工具也用此"文件邮箱"传数据）。
      格式交给 Lua 引擎自己解析，中文/转义全部不用管。

    ★ 为什么要原子替换（先写 .tmp 再 rename）？
      否则 Lua 可能正好读到写了一半的文件 → loadfile 失败。
    """
    if not path:
        return
    try:
        tmp = path + ".tmp.%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("return {" + body + "}\n")
        os.replace(tmp, path)
    except OSError:
        pass


def load_args(argv):
    """支持两种模式：
       ① python resolve_bridge.py <参数.json>       ← 推荐（Lua 用这个）
       ② python resolve_bridge.py <音频> <srt> ...  ← 兼容旧的命令行方式
    """
    if len(argv) >= 2 and argv[1].lower().endswith(".json"):
        with open(argv[1], "r", encoding="utf-8") as f:
            cfg = json.load(f)
    elif len(argv) >= 9:
        cfg = {
            "audio": argv[1], "srt": argv[2], "lang": argv[3],
            "max_chars": int(argv[4]) if argv[4].isdigit() else 12,
            "no_gap": argv[5] == "1", "no_punct": argv[6] == "1",
            "prompt": argv[7], "status": argv[8],
            "model": argv[9] if len(argv) > 9 else "",
        }
    else:
        raise SystemExit("参数不足：需要 <参数.json> 或旧的 9 参数命令行方式")

    # audio_file 优先（路径从文件读，绕开命令行编码）
    af = cfg.get("audio_file")
    if af and os.path.exists(af):
        real = _read_path_from_file(af)
        if real:
            cfg["audio"] = real
    return cfg


# ══════════════════════════════════════════════════════════════════
# 常驻 ASR 服务（asr_server.py）的客户端
#
# ★ 为什么：模型加载（ASR + Aligner）每次要 6~7 秒。服务常驻后复用，
#   第二次起直接省掉这笔开销。
#
# ★★ 这里只把它当【加速器】：服务不在 / 没就绪 / 中途崩 / 超时，
#    一律返回 None，main() 会照原样在本地加载模型 —— 功能完全不受影响。
# ══════════════════════════════════════════════════════════════════
SRV_WAIT_MAX = 1800      # 单个任务最多等多久（秒）
SRV_HB_FRESH = 25.0      # 心跳超过这么久没更新 = 服务不在
SRV_HB_STALE = 120.0     # 跑到一半心跳断了这么久 = 判定服务已死，回退本地
SRV_ACCEPT_SEC = 90.0    # 下发后超过这么久还没开始干活 = 服务没接住活
SRV_BUSY_WAIT = 120.0    # 服务正忙时最多等它多久（秒），等不到就本地加载
SRV_STUCK_SEC = 600.0    # 干活中但这么久没有一次进度更新 = 卡死

import subprocess as _sp


def srv_state(hb_path):
    """读服务心跳，返回 (ready, model, pid, busy)。

    当前格式（asr_server.write_hb 写的，JSON）：
        {"t": <unix时间戳>, "ready": 0|1, "model": "<路径>",
         "pid": <进程号>, "busy": 0|1}
    ★ 为什么改成 JSON：旧格式是空格分隔的
        <unix时间戳> <ready 0|1> <模型路径> <pid> <busy 0|1>
      而模型路径 ① 可能含空格（用户自定义路径）② 加载前后会被写成空串。
      两种情况都会让 split() 出来的字段整体错位 → int(parts[3]) 失败 →
      pid 变成 0 → 下面 try_server 里的 srv_pid_alive(0) 判"进程已死" →
      删心跳 → 常驻服务被静默废掉，每次都退化成本地加载。
    这里保留旧格式的解析分支，方便新老版本混跑时平滑过渡。
    服务不在（文件没有 / 心跳过期）时返回 (False, None, 0, False)。
    """
    try:
        with open(hb_path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if raw.startswith("{"):
            d = json.loads(raw)
            t = int(d.get("t") or 0)
            ready = bool(d.get("ready"))
            model = str(d.get("model") or "")
            pid = int(d.get("pid") or 0)
            busy = bool(d.get("busy"))
        else:
            # ── 旧格式（空格分隔），仅用于兼容过渡 ──
            parts = raw.split(None, 4)
            t = int(parts[0])
            ready = len(parts) > 1 and parts[1] == "1"
            model = parts[2].strip() if len(parts) > 2 else ""
            try:
                pid = int(parts[3]) if len(parts) > 3 else 0
            except ValueError:
                pid = 0
            busy = len(parts) > 4 and parts[4].strip().startswith("1")
        if time.time() - t > SRV_HB_FRESH:
            return (False, None, pid, False)
        return (ready, model, pid, busy)
    except Exception:
        return (False, None, 0, False)


def srv_pid_alive(pid, timeout=6.0):
    """进程还在不在。

    ★ 只在【心跳已经停了】之后才调用。正常路径一律不碰它 ——
      tasklist 要启一个子进程，放在每 0.25 秒一轮的轮询里太重了。
    """
    if not pid:
        return False
    try:
        out = _sp.run(["tasklist", "/FI", "PID eq %d" % pid],
                      stdout=_sp.PIPE, stderr=_sp.DEVNULL, timeout=timeout)
        return str(pid) in (out.stdout or b"").decode("utf-8", "replace")
    except Exception:
        return False


def srv_kill(pid, log, wait=12.0):
    """把常驻服务杀掉，并等它真的退出。

    ★★ 这是【必须】的一步，不是可选优化：
        服务的 torch/CUDA 上下文占着一大块显存和系统页面文件。
        不先把它杀干净就回退本地加载，本地这份 torch 连 DLL 都加载不了：
          OSError: [WinError 1455] 页面文件太小 ... cublas64_12.dll
        实测在这种机器上 100% 复现。所以回退 = 先杀服 + 等它释放，再加载。
    """
    if not pid:
        return False
    dead = False
    try:
        _sp.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, timeout=15)
    except Exception:
        pass
    end = time.time() + wait
    while time.time() < end:
        alive = False
        try:
            out = _sp.run(["tasklist", "/FI", "PID eq %d" % pid],
                          stdout=_sp.PIPE, stderr=_sp.DEVNULL, timeout=10)
            alive = str(pid) in (out.stdout or b"").decode("utf-8", "replace")
        except Exception:
            alive = False
        if not alive:
            dead = True
            break
        time.sleep(0.3)
    log("（常驻服务）已停止 pid=%s%s" % (pid, "" if dead else "（未能确认退出）"))
    return dead


def try_server(work, cfg, status_lua, status_file, log):
    """把识别交给常驻服务。成功返回 (blocks, stats)；不可用/失败返回 None。"""
    cmd_path = os.path.join(work, "asr_cmd.json")
    res_path = os.path.join(work, "asr_res.json")
    hb_path = os.path.join(work, "asr_hb.txt")

    # ★★ 上一次的 pid 必须留着：等会儿万一要回退本地加载，
    #    得先用它把服务杀掉释放显存，否则两份 torch 会打架（见 srv_kill）。
    ready, _model, srv_pid, busy = srv_state(hb_path)
    if _model is None and not ready and not srv_pid:
        return None          # 服务压根不在（心跳没有或已过期）

    if not ready:
        # 服务在，但模型还在加载（多半是刚预热完就立刻开始了下一次）。
        # 等它一下 —— 否则本地也要装一份模型，两份一起占显存，
        # 小显存的卡可能直接 OOM。等不到就照旧本地加载。
        dl = time.time() + 15
        while time.time() < dl:
            time.sleep(0.3)
            ready, _m, p, b = srv_state(hb_path)
            if p:
                srv_pid = p
            if ready:
                break
            if not ready and not p:
                break        # 服务没了
        if not ready:
            return None

    # ★★ 进程必须真的活着才能交任务。
    #    服务被 terminate / 崩溃强杀时来不及删心跳文件，会留下一条
    #    「时间戳很新但进程已经没了」的假心跳 —— 认了它就是把任务发给
    #    一个死进程，要干等 SRV_ACCEPT_SEC(90s) 才被超时兜住（实测
    #    多花 90 秒）。一次 tasklist 的代价远比这 90 秒便宜。
    if not srv_pid_alive(srv_pid):
        log("（常驻服务）心跳是旧的（pid=%s 已不存在），改用本地加载"
            % srv_pid)
        try:
            os.remove(hb_path)
        except OSError:
            pass
        return None

    # ★ 服务正忙着（上一次的任务还没跑完）时，【不能】直接覆盖
    #   asr_cmd.json：服务会把新命令当成下一次任务，而旧任务还在跑，
    #   表现为"我点了停止，它还在识别"。
    #   这里等它闲下来（上限 SRV_BUSY_WAIT 秒），等不到就本地加载 ——
    #   本地会另装一份模型占显存，但总比把正在跑的任务顶掉好。
    if busy:
        log("（常驻服务）正忙，等它跑完上一个任务…")
        dl = time.time() + SRV_BUSY_WAIT
        while time.time() < dl:
            time.sleep(0.5)
            r2, _m2, p2, b2 = srv_state(hb_path)
            if not p2 or not srv_pid_alive(p2):
                break                      # 服务没了，走本地
            if not b2:
                break                      # 闲下来了
        else:
            return None
        r3, _m3, p3, b3 = srv_state(hb_path)
        if b3 or not p3:
            log("（常驻服务）等了 %d 秒还在忙，改用本地加载" % SRV_BUSY_WAIT)
            return None

    jid = int(time.time() * 1000) % 1000000000
    job = {
        "id": jid,
        "audio": cfg.get("audio", ""),
        "lang": cfg.get("lang", "Chinese"),
        "prompt": cfg.get("prompt", ""),
        "max_chars": cfg.get("max_chars", 12),
        "no_gap": cfg.get("no_gap", False),
        "no_punct": cfg.get("no_punct", False),
        "strip_all_punct": cfg.get("strip_all_punct", False),
        "model": cfg.get("model", ""),
        # ★ v4.8.23：这两个参数以前没随任务下发 —— 走常驻服务（预热好的那条
        #   路，也是日常走的那条）时，「最短显示」「长停顿断行」会被悄悄忽略，
        #   只有本地加载才生效。界面上看着一样，结果不一样，最难查。
        "min_dur": cfg.get("min_dur", 0.04),
        "split_pause": bool(cfg.get("split_pause", False)),
        "status_lua": status_lua,
        "status_file": status_file,
    }
    try:
        # 先清掉上一次的结果，否则可能读到旧数据直接"秒完成"
        if os.path.exists(res_path):
            os.remove(res_path)
        tmp = cmd_path + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False)
        os.replace(tmp, cmd_path)
    except OSError as e:
        log("（常驻服务）下发任务失败，改用本地加载：%s" % e)
        return None

    log("（常驻服务）已下发任务，复用已加载的模型")
    deadline = time.time() + SRV_WAIT_MAX
    last_hb = time.time()
    sent_at = time.time()
    accepted = False        # 服务有没有真的开始干活（busy=1）
    last_progress = None    # 上一次看到的进度时间戳（status.lua 里的 t）
    last_progress_at = sent_at

    while time.time() < deadline:
        try:
            if os.path.exists(res_path):
                with open(res_path, "r", encoding="utf-8") as f:
                    res = json.load(f)
                if int(res.get("id") or 0) != jid:
                    # 上一个任务的残留，继续等
                    time.sleep(0.2)
                    continue
                if res.get("ok"):
                    global MODEL_NOTE
                    _mn = str(res.get("model_note") or "")
                    if _mn:
                        MODEL_NOTE = _mn
                    blocks = [(float(a), float(b), str(t))
                              for a, b, t in res.get("blocks") or []]
                    return blocks, res.get("stats") or {}
                log("（常驻服务）返回失败：%s —— 改用本地加载"
                    % res.get("err", ""))
                # 服务端自己报错（多半是模型没装好）也要先把它杀掉：
                # 它虽然没干活，但 torch 已经 import 过，显存照样占着。
                srv_kill(srv_pid, log)
                return None
        except (OSError, ValueError, TypeError):
            # 可能正被写（半截 JSON）—— 下轮再读
            pass

        rd, _, p, busy = srv_state(hb_path)
        if p:
            srv_pid = p
        if rd:
            last_hb = time.time()
        elif time.time() - last_hb > SRV_HB_STALE:
            # 心跳彻底停了 —— 先用 pid 分清「真死了」和「活着但停摆」：
            #   进程没了 → 崩了，直接回退
            #   进程还在 → 卡死/换页中，【必须】先杀掉它再回退，
            #              否则本地这份 torch 抢不到显存，注定二次失败
            #              （WinError 1455 就是这么来的）
            if srv_pid_alive(srv_pid):
                log("（常驻服务）进程还在（pid=%s）但心跳已停 %.0fs，判定卡死"
                    % (srv_pid, SRV_HB_STALE))
                srv_kill(srv_pid, log)
            else:
                log("（常驻服务）进程已退出，改用本地加载")
            return None

        if busy:
            accepted = True

        # ── 卡死判定 ─────────────────────────────────────────────
        #  服务的 heartbeat 是独立线程写的，哪怕识别跑飞了它也照跳，
        #  所以【心跳活着 ≠ 任务在推进】。真正要看的是它写进 status.lua
        #  的进度时间戳 t 有没有往前走。
        try:
            if status_lua and os.path.exists(status_lua):
                _m = _re.search(r"\bt\s*=\s*(\d+)",
                                open(status_lua, encoding="utf-8").read())
                if _m and _m.group(1) != last_progress:
                    last_progress = _m.group(1)
                    last_progress_at = time.time()
                    accepted = True
        except OSError:
            pass

        _stall = time.time() - last_progress_at
        if not accepted and _stall > SRV_ACCEPT_SEC:
            log("（常驻服务）下发后 %.0fs 没有开始干活，改用本地加载" % _stall)
            srv_kill(srv_pid, log)
            return None
        if accepted and _stall > SRV_STUCK_SEC:
            log("（常驻服务）%.0fs 没有任何进度更新（疑似卡死），改用本地加载"
                % _stall)
            srv_kill(srv_pid, log)
            return None

        time.sleep(0.25)

    log("（常驻服务）等待超时，改用本地加载")
    srv_kill(srv_pid, log)
    return None


def friendly_error(e):
    """把用户看不懂的底层异常翻成能照着做的中文提示。

    ★ 界面上只有一行状态文字，直接甩 NVIDIA CUDA driver to be installed
      这类原文，用户除了重启电脑没别的事可做。这两类错是实测真出现过的。
    """
    name = type(e).__name__
    raw = "%s: %s" % (name, e)
    msg = str(e)

    if "1455" in msg or "页面文件太小" in msg:
        return ("系统虚拟内存不足，CUDA 加载失败（WinError 1455）。\n"
                "① 关掉占内存的软件后重试；\n"
                "② 或增大虚拟内存：此电脑→属性→高级系统设置→性能【设置】"
                "→高级→虚拟内存【更改】，改到 16GB 以上\n"
                "③ 如装了更小的模型，可在「设置 → 识别模型」里换过去")
    if "out of memory" in msg.lower() and "cuda" in msg.lower():
        return ("显存不足（CUDA out of memory）。\n"
                "先关掉其它占显存的程序，或改用更小的模型后重试")
    if "no cuda" in msg.lower() or "driver" in msg.lower():
        return ("CUDA 不可用：%s\n"
                "请更新 NVIDIA 驱动，或在设置里改用 CPU 模式" % msg)
    if isinstance(e, MemoryError):
        return "系统内存不足，请关掉其它程序后重试"
    # ★ v4.8.19：模型下错仓库时，引擎会在 load() 直接拦下（见
    #   asr_engine.check_model_layout）。这里给一句不用读技术细节的结论，
    #   因为界面上只有一行字，而"0 条字幕"最容易被误判成音频没声音。
    if "识别模型目录和当前 transformers 对不上" in msg:
        return ("模型文件不对（不是音频的问题）：装成了 qwen_asr 原版仓库，"
                "当前 transformers 载不进去。\n"
                "重跑一次安装器即可修好 —— 不对的模型文件会自动删掉重下。")
    return raw


def main():
    # ★ v4.8.23：从"点开始"到"出字幕"的真实总时长（含导入/加载）
    _T0 = time.time()
    if len(sys.argv) < 2:
        print("usage: resolve_bridge.py <args.json>")
        sys.exit(2)

    cfg = load_args(sys.argv)

    audio = str(cfg.get("audio", ""))
    out_srt = str(cfg.get("srt", ""))
    # TXT 文稿路径：不给或为空就不产出（老配置兼容）
    out_txt = str(cfg.get("txt", "") or "").strip()
    if not out_txt and out_srt:
        out_txt = os.path.splitext(out_srt)[0] + ".txt"
    lang = LANG_MAP.get(cfg.get("lang", "Chinese"), "Chinese")
    try:
        max_chars = int(cfg.get("max_chars", 12))
    except (TypeError, ValueError):
        max_chars = 12
    no_gap = bool(cfg.get("no_gap", False))
    # ★ v4.8.21：设置页「长停顿断行」。True/False 原样往下传，由引擎换算成秒
    #   （PAUSE_SPLIT_SEC）；老配置里没有这一项 = 关，行为与 4.8.20 完全一致。
    split_pause = bool(cfg.get("split_pause", False))
    no_punct = bool(cfg.get("no_punct", False))
    all_punct = bool(cfg.get("strip_all_punct", False))
    # ★ v2.0.0：最短显示时长（秒），界面可调；缺省 0.04 = v1.28 行为
    try:
        min_dur = float(cfg.get("min_dur", 0.04) or 0.04)
    except (TypeError, ValueError):
        min_dur = 0.04
    if min_dur < 0.04:
        min_dur = 0.04
    elif min_dur > 10:
        min_dur = 10.0
    prompt = str(cfg.get("prompt", "") or "")
    # ★ v2.0.7：提示词整项废弃（会被模型当正文吐进字幕）。
    #   这里硬性置空，即使旧版 Lua 还在传 prompt 也不会再传进模型。
    #   （警告延到 log 定义之后再打 —— 上面还没有 log 可用）
    _prompt_dropped = bool(prompt.strip())
    prompt = ""
    corr_text = str(cfg.get("corrections", "") or "")
    status_file = str(cfg.get("status", ""))
    model_path = str(cfg.get("model", "") or "").strip()
    log_file = str(cfg.get("log", "") or "")

    # 进度 / 心跳的 Lua chunk 路径（Lua 侧用 loadfile 读）
    _work = os.path.dirname(status_file) or _HERE
    status_lua = str(cfg.get("status_lua", "") or "") or os.path.join(_work, "status.lua")
    hb_lua = str(cfg.get("hb", "") or "") or os.path.join(_work, "hb.lua")

    def report(pct, count=0, err="", msg=""):
        """写进度供 Lua 轮询。

        ★ 两个通道同时写：
          · status.lua → `return {pct=.., count=.., t=.., msg=..}`，Lua 用 loadfile 读
          · status.txt → "百分比%|条数"，兜底 + 人肉排查用
        ★ 条数也由这里写 —— Lua 打不开中文路径的 srt，没法自己数行。

        ★ v4.8.24：新增 msg 字段（阶段说明）。原因：抽音频和加载模型这两段
          没有任何进度可报，界面只能一直显示"识别中... 3%"，实测模型加载
          要停 11~12 秒，用户判断不出是在干活还是卡死。

        ★★★ 血泪教训（务必保持）：
          **识别【中途】的进度绝对不能报 100！**
          原来进度回调是 report(cur*100/tot)，cur 到 tot 时就等于 100，
          而此时 count 还是 0 → Lua 看到 pct=100 就调 Finish()，
          count=0 → 界面显示"未生成字幕"，后续挂字幕轨也永远不会执行。
          所以：
            · 进度一律钳到 99，只有真正写完 SRT 才报 100 + 条数
            · 失败时写 err，让 Lua 能区分"失败"和"没识别到内容"
        """
        pct = int(pct)
        count = int(count)
        body = f"pct = {pct}, count = {count}, t = {int(time.time())}"
        if msg:
            body += ", msg = " + _lua_str(str(msg)[:80])
        if err:
            body += ", err = " + _lua_str(str(err)[:300])
        # ★ 实际跑的模型（可能和用户填的不一样——路径无效会回退），
        #   带回界面让用户一眼看到，别出现"填了 A 实际跑 B"还蒙在鼓里。
        if MODEL_NOTE:
            body += ", model = " + _lua_str(MODEL_NOTE[:300])
        # ★ v1.0.1：环境告警（"CPU 模式"）常驻状态栏，界面上能直接看到
        if RUN_WARN:
            body += ", warn = " + _lua_str(RUN_WARN[:40])
        _write_lua_chunk(status_lua, body)
        if not status_file:
            return
        try:
            with open(status_file, "w", encoding="utf-8") as f:
                f.write(f"{pct}%|{count}")
        except OSError:
            pass

    # ── 心跳：独立线程每 2 秒写一次时间戳 ──
    #   ★ 为什么需要：Lua 侧要判断"引擎是否还活着"。
    #     原来用 io.popen('tasklist') 查进程 —— 既阻塞界面，
    #     又因为脚本是 pythonw 启动的而永远查不到 → 误报"进程已退出"。
    #     心跳是独立线程，模型加载/识别的任何阶段都在跳。
    _hb_stop = threading.Event()

    def _heartbeat():
        while not _hb_stop.is_set():
            _write_lua_chunk(hb_lua, f"t = {int(time.time())}")
            _hb_stop.wait(2.0)

    _write_lua_chunk(hb_lua, f"t = {int(time.time())}")
    threading.Thread(target=_heartbeat, daemon=True).start()

    # 日志同时写文件（Lua 侧看不到 stdout）
    logf = None
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            logf = open(log_file, "a", encoding="utf-8")
        except OSError:
            logf = None

    def log(msg):
        line = str(msg)
        print(line, flush=True)
        if logf:
            try:
                logf.write(line + "\n")
                logf.flush()
            except OSError:
                pass

    try:
        if not audio or not os.path.isfile(audio):
            log(f"错误：找不到音频 {audio}")
            report(100, 0, "找不到音频文件：" + audio)
            sys.exit(3)

        if _prompt_dropped:
            log("提示词已废弃（实测会被模型当正文吐进字幕、位置也不对），"
                "本次忽略；需要纠正用词请填「权重词纠错」")

        # ── 音频缓存（统一入口）──────────────────────────────────
        #   渲染路径产出的本来就是 wav → 原样进引擎；快速路径的视频素材
        #   在这里抽成 16k WAV 并按指纹缓存，重复识别不再抽。
        # ★ v4.8.24：抽音频之前先报一句阶段文案。长素材（1 小时 1.4 GB 的
        #   mp4）这一步要几十秒，以前这期间 status.lua 根本没有，
        #   界面就停在"识别中..."一动不动。
        if os.path.splitext(str(audio))[1].lower() != ".wav":
            report(0, msg="正在从素材抽取音频…")
        audio = _prepare_audio(audio, cfg, _work, log)

        log(f"音频: {audio}")
        # ★ 参数回显：选项"看着没效果"时，先看这行确认值有没有传到引擎。
        log(f"参数: 语言={lang} 每行最多 {max_chars} 字 "
            f"无间隙={'开' if no_gap else '关'} "
            f"去标点={'全部' if all_punct else ('句末' if no_punct else '关')} "
            f"长停顿断行={'开' if split_pause else '关'} "
            f"最短显示 {min_dur:.2f}s")
        report(0, msg="音频就绪，正在准备识别…")
        refresh_manifest()

        # ── ① 优先走常驻服务（模型已加载 → 省掉 6~7 秒）──────────────
        #    不在线 / 没就绪 / 崩了 / 超时 → try_server 返回 None，
        #    自动退回下面的本地加载，结果完全一样，只是慢一点。
        got = None
        try:
            got = try_server(_work, {
                "audio": audio, "lang": lang, "prompt": prompt,
                "max_chars": max_chars, "no_gap": no_gap,
                "no_punct": no_punct, "strip_all_punct": all_punct,
                "min_dur": min_dur, "model": model_path,
                "split_pause": split_pause,
            }, status_lua, status_file, log)
        except Exception as e:
            log(f"（常驻服务）不可用，改用本地加载：{type(e).__name__}: {e}")

        if got is not None:
            blocks, stats = got
        else:
            # ── ② 本地加载（老路径，永远可用）─────────────────────────
            # ★ 延迟 import：torch 只有在这里才需要。走常驻服务时本进程
            #   完全不碰 CUDA —— 少占一份显存，也少一次 DLL 加载失败的机会。
            try:
                from asr_engine import Engine as _Engine
            except Exception as _e:
                log("导入识别引擎失败：%s: %s" % (type(_e).__name__, _e))
                report(100, 0, "导入识别引擎失败：%s" % _e)
                sys.exit(6)

            # ★ v4.8.23：模型加载/启动要 20~30 秒，这段时间以前进度条一直停在
            #   0%，看着像卡死。先给 3%，加载完再给 6%，让用户知道在动。
            # ★ v4.8.24：再补一句文案 —— 实测 CPU 版 0.6B 加载要 11~12 秒，
            #   期间界面上只有"识别中... 3%"，用户分不清是在加载还是卡死。
            report(3, msg="正在加载识别模型（首次较慢，之后会复用常驻服务）…")
            _t_load = time.time()
            engine = _Engine(log=log, asr_model=(model_path or None))
            engine.load()
            _load_s = time.time() - _t_load
            report(6, msg="模型已就绪，正在检测语音片段…")
            # ★ v1.0.1：有 NVIDIA 显卡却在跑 CPU —— 必须让用户在【面板上】看见，
            #   而不是只能翻诊断包。engine 是静默 fallback（torch.cuda 不可用就 cpu），
            #   实测 RTX 5060 的机器上 torch 装成了 +cpu，实时系数 0.77。
            global MODEL_NOTE, RUN_WARN
            if (not RUN_WARN
                    and str(getattr(engine, "device", "") or "") == "cpu"
                    and _has_nvidia_gpu()):
                RUN_WARN = "CPU 模式"
                log("⚠ 检测到 NVIDIA 显卡，但当前 torch 是 CPU 版 —— 本次仍用 CPU 识别，"
                    "实测慢 5~10 倍。重跑安装器并选 GPU 版即可提速。")
            # 回传实际模型（自定义路径无效时这里会带上 ⚠ 说明）
            if getattr(engine, "model_note", ""):
                MODEL_NOTE = engine.model_note

            blocks, stats = engine.run(
                audio, lang=lang, prompt=prompt, max_chars=max_chars,
                remove_gaps=no_gap, trim_punct=no_punct,
                strip_all_punct=all_punct, min_dur=min_dur,
                split_pause=split_pause,
                # ★ 钳到 99：中途绝不允许报 100（否则 Lua 会在 SRT 写出来之前
                #   就判"完成"，count 还是 0 → 显示"未生成字幕"，也不会去挂字幕轨）
                progress=lambda cur, tot: report(
                    min(99, 6 + int(cur * 93 / max(1, tot))) if tot else 6,
                    msg=("正在识别第 %d/%d 段…" % (cur, tot)) if tot else "")
            )

        if not blocks:
            # ★ 0 条字幕有两种完全不同的原因，必须分开说：
            #   ① 片段真的没文本（静音/纯噪声）→ "时间线可能没有声音"成立；
            #   ② 片段全抛异常（模板不对、模型不对、显存炸）→ 那句话就是误导，
            #      用户会去查音频。2026-11-01 的"识别失败"正是 ②：13 个片段
            #      全挂在 chat 模板上，界面却提示"时间线可能没有声音"。
            fails = int((stats or {}).get("failed") or 0)
            first = str((stats or {}).get("first_error") or "").strip()
            if fails:
                log("识别失败：%d 个片段报错，识别链路没跑通（不是音频的问题）" % fails)
                if first:
                    log("  首个错误：" + first)
                report(100, 0, "识别失败：%d 个片段报错（%s）"
                       % (fails, (first or "详见日志")[:110]))
            elif int((stats or {}).get("segments") or 0) == 0:
                # ★ v4.8.23：一个语音段都没检测到，跟"检测到但没有文本"是
                #   两回事，措辞要分开，否则用户会去翻音频找"漏掉的话"。
                log("未识别到语音内容：整段没有检测到说话（电平低于阈值）")
                report(100, 0, "未识别到语音内容：整段没有检测到说话")
            else:
                log("未识别到语音内容（所有片段都没有可用文本）")
                report(100, 0, "未识别到语音内容（时间线可能没有声音）")
            sys.exit(0)

        # ── v2.0.7：权重词纠错（识别完、写 SRT 之前）──────────────────
        #   ★ 位置很关键：写 SRT / 写 TXT / 回传给界面的表格，全都从 blocks
        #     出去。在这一处改，三个产物天然一致；放到 Lua 侧改就只会改界面，
        #     落轨的 SRT 还是错的。
        if corr_text.strip():
            try:
                from corrections import parse_rules, apply_to_blocks, describe
                _rules, _probs = parse_rules(corr_text)
                for _p in _probs:
                    log("纠错表 " + _p)
                if _rules:
                    _hits = apply_to_blocks(blocks, _rules)
                    log("权重词纠错：%s，命中 %d 处"
                        % (describe(_rules), _hits))
                else:
                    log("权重词纠错：表里没有可用的规则，跳过")
            except Exception as _e:
                # 纠错失败绝不能影响出字幕 —— 它只是锦上添花
                log("权重词纠错失败（不影响出字幕）：%s: %s"
                    % (type(_e).__name__, _e))

        write_srt(blocks, out_srt)

        # ★ 同时产出一份【纯文本文稿】（带标点的完整短句，一句一行）
        #   失败不影响主流程 —— 字幕是主产物，文稿是附赠。
        n_lines = 0
        if out_txt:
            try:
                n_lines = write_txt(blocks, out_txt)
                log(f"文稿：{n_lines} 句 -> {out_txt}")
            except Exception as e:
                log(f"文稿写出失败（不影响字幕）：{type(e).__name__}: {e}")

        report(100, len(blocks))
        log(f"完成：{len(blocks)} 条字幕-> {out_srt}")
        # 常驻服务返回的 stats 可能缺字段，用 .get 兜住，别让日志本身抛异常
        _g = (stats or {}).get
        # ★ v4.8.23：引擎自报的 elapsed 不含模型加载/导入，用户体感差 30 秒
        #   （实测 48.1s vs 78.7s）。这里报【从点开始到出字幕】的总时间。
        _total_s = time.time() - _T0
        _dur_s = float(_g("duration") or 0)
        log("总耗时 %.1fs（引擎识别 %.1fs，其余为启动/加载）｜音频 %.1fs（实际实时系数 %.2f，%s）"
            % (_total_s, float(_g("elapsed") or 0), _dur_s,
               (_total_s / _dur_s) if _dur_s else 0.0, _g("device") or "-"))

        # ── 诊断快照（rt\last_run.lua，供「关于」弹窗展示/复制）────
        #   排查"识别怎么又慢了/掉字了"时，先看这里，不用去翻日志文件。
        try:
            _diag = (
                "t = %d, blocks = %d, duration = %.1f, elapsed = %.1f, "
                "rtf = %.2f, device = %s, model = %s, lang = %s, "
                "max_chars = %d, cached = %s"
                % (int(time.time()), len(blocks),
                   float(_g("duration") or 0), float(_g("elapsed") or 0),
                   float(_g("rtf") or 0), _lua_str(str(_g("device") or "-")),
                   _lua_str(MODEL_NOTE or model_path or "默认模型"),
                   _lua_str(str(lang)), int(max_chars),
                   "true" if os.path.dirname(audio) == os.path.join(
                       _work, "cache") else "false"))
            _write_lua_chunk(os.path.join(_work, "last_run.lua"), _diag)
        except Exception:
            pass
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as e:
        log("异常：")
        log(traceback.format_exc())
        try:
            report(100, 0, "识别异常：%s" % friendly_error(e))
        except Exception:
            pass
        sys.exit(1)
    finally:
        try:
            _hb_stop.set()
        except Exception:
            pass
        if logf:
            try:
                logf.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()