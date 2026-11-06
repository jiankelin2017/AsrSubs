"""
Qwen3-ASR 字幕生成工具（独立版）
=================================
基于 Qwen3-ASR-0.6B + Qwen3-ForcedAligner-0.6B 的本地字幕生成工具。

特点：
- 不依赖 DaVinci Resolve，任何软件都能用
- 图形界面，拖拽文件即可
- GPU 加速（RTX 50 系列 / 任意 CUDA 卡）
- 全本地运行，音频不上传

技术栈：
- 语音识别：Qwen3ASRForConditionalGeneration
- 时间对齐：Qwen3ASRForTokenClassification（与 ASR 同架构）
- 语音活动检测：silero-vad（装了就用）或自适应噪声底的能量阈值
"""

import os
import re
import sys
import json
import math
import time
import threading
import subprocess
import unicodedata
import queue
import tempfile
from pathlib import Path

# ── 路径配置（3.0 起 paths.json 是唯一事实来源）─────────────────────────
# ★ 3.0 安装布局：<根>\engine、<根>\models、<根>\data
#   2.x 安装布局：<根>\engine、     <根>\AsrSubs\models
# ★ 安装器会把真实路径写进 engine\paths.json；没有它时按相对位置推导，
#   所以开发机（.py 和引擎同目录）和老部署都能直接跑。
# ★ 绝对路径 D:\AsrSubs\... 只作为开发机的历史兜底，新装机永远走不到。
_HERE_DIR = Path(__file__).resolve().parent


def _load_paths():
    try:
        with open(_HERE_DIR / "paths.json", "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


PATHS = _load_paths()
_ROOT_DIR = Path(PATHS["root"]) if PATHS.get("root") else _HERE_DIR.parent


def _model_dirs():
    out = []
    if PATHS.get("models"):
        out.append(Path(PATHS["models"]))
    out.append(_ROOT_DIR / "models")                     # 3.0 安装布局
    out.append(_ROOT_DIR / "AsrSubs" / "models")     # 2.x 安装布局
    out.append(_HERE_DIR / "models")                     # 工具自身目录
    out.append(Path(r"D:\AsrSubs\models"))                 # 开发机历史位置
    out.append(Path(r"D:\AsrSubs\engine\models"))     # 2.x 开发机位置
    seen, uniq = set(), []
    for d in out:
        k = str(d).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(d)
    return uniq


_MODEL_DIRS = _model_dirs()

# ── 是否启用 ForcedAligner 词语级时间对齐 ──────────────────────────────
# ★ 开启后，字幕时间戳由 Qwen3-ForcedAligner 做「词语级」对齐，取代原先
#   按字数比例估算的做法（distribute_timing）。
#   代价：加载多占约 2.5GB 显存、首次多 6~7 秒。
#   兼容性：只有 ALIGNER_LANGS 里列的语言能做对齐；遇到不支持的语种、
#   模型缺失、加载失败，或对齐质量不达标且重试无效时，一律自动降级回
#   按字数比例估算 —— 功能不会中断，只是精度回到老水平。
USE_ALIGNER = True

# ForcedAligner 支持的语言（对应 transformers 的 FORCED_ALIGNER_LANGUAGES）
ALIGNER_LANGS = frozenset({
    "Chinese", "English", "Cantonese", "French", "German",
    "Italian", "Japanese", "Korean", "Portuguese", "Russian", "Spanish",
})

# 界面上的「自动识别」。★ 必须规范化为 None：transformers 的
# resolve_language() 只接受语言代码或全名，传字符串 "Auto" 会直接抛
# ValueError（"Unsupported language: 'Auto'"）。
_AUTO_LANGS = frozenset({"auto", "自动", "自动识别", "自动检测"})


def normalize_language(lang):
    """把界面传来的语言值规范化；「自动识别」返回 None，交给模型自行检测。"""
    if lang is None:
        return None
    s = str(lang).strip()
    if not s or s.lower() in _AUTO_LANGS:
        return None
    return s


def _find_model(name):
    for d in _MODEL_DIRS:
        p = d / name
        if p.exists():
            return p
    return _MODEL_DIRS[0] / name


DEFAULT_ASR = "Qwen3-ASR-0.6B-hf"
DEFAULT_ALIGNER = "Qwen3-ForcedAligner-0.6B-hf"


def list_asr_models(base_dir=None):
    """列出可用的 ASR 模型目录（排除 Aligner）"""
    out = []
    dirs = [Path(base_dir)] if base_dir else _MODEL_DIRS
    seen = set()
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if not p.is_dir() or p.name in seen:
                continue
            if not (p / "config.json").exists():
                continue
            low = p.name.lower()
            if "aligner" in low:
                continue
            seen.add(p.name)
            out.append(p)
    return out


def find_aligner(asr_path=None):
    """找配对的时间对齐模型：优先同目录，其次全局搜索"""
    candidates = []
    if asr_path:
        candidates.append(Path(asr_path).parent)
    candidates.extend(_MODEL_DIRS)
    for d in candidates:
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.is_dir() and "aligner" in p.name.lower() and (p / "config.json").exists():
                return p
    return _MODEL_DIRS[0] / DEFAULT_ALIGNER


def check_asr_dir(path):
    """校验「自定义模型路径」能不能用，返回 (ok, 人话原因)。

    ★ 为什么单独抽出来：以前路径不对时 resolve_models 直接【静默回退】到
      默认模型，日志一个字都不说。用户填了 D:\\models\\Qwen3-ASR-1.7B-hf，
      界面回显也显示这个路径，结果跑的其实还是 0.6B —— 跟「字数限制永远
      12」是同一类"看着生效其实没有"的坑。现在把原因说清楚。
    """
    p = (path or "").strip()
    if not p:
        return (False, "留空")
    try:
        cand = Path(p)
    except Exception:
        return (False, "路径格式不对：%s" % p)
    if not cand.exists():
        return (False, "路径不存在：%s" % p)
    if not cand.is_dir():
        return (False, "不是文件夹（要给模型目录，不是文件）：%s" % p)
    if not (cand / "config.json").exists():
        return (False, "目录里没有 config.json，不是有效的模型目录：%s" % p)
    return (True, "")


def resolve_models(asr_override=None):
    """解析出实际使用的 (ASR 模型, Aligner 模型)

    自定义路径无效时**回退默认模型**。是否回退由 check_asr_dir() 判定，
    调用方（Engine / bridge）负责把它讲给用户听。
    """
    asr = None
    if asr_override:
        ok, _why = check_asr_dir(asr_override)
        if ok:
            asr = Path(asr_override)
    if asr is None:
        asr = _find_model(DEFAULT_ASR)
    return asr, find_aligner(asr)


ASR_MODEL = _find_model(DEFAULT_ASR)
ALIGNER_MODEL = _find_model(DEFAULT_ALIGNER)

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".flv", ".wmv", ".mpg", ".mpeg", ".webm", ".ts"}

LANGUAGES = {
    "Chinese": "中文", "English": "English", "Auto": "自动识别",
    "Cantonese": "粤语", "Japanese": "日语", "Korean": "韩语",
    "French": "Français", "German": "Deutsch", "Spanish": "Español",
    "Russian": "Русский", "Italian": "Italiano", "Portuguese": "Português",
}


# ── 音频加载 ────────────────────────────────────────────────────────────
def _tmp_wav_path():
    """提取音频时用的临时 wav 放哪。

    ★ 优先放 <根>\\data\\cache\\tmp，不要只放 %TEMP%：
      两小时的素材转出来是几百 MB 的 wav，用户中途点取消 / 进程被任务管理器
      杀掉时 finally 不会执行，文件就永远留在 C 盘用户临时目录里。攒几次
      C 盘就红了（这正是"插件在我 C 盘留垃圾"的典型来源）。
      放进 data\\cache 后，它跟别的缓存同进同退：用户清缓存、重装、卸载都
      能干净带走，而且和 C 盘容量无关。
    ★ 没有 paths.json 的开发机退回引擎\\rt\\cache\\tmp；连这个都建不出来
      （只读目录）才退回 %TEMP%，保证功能不会因为清理策略而失败。
    """
    name = "qwen3asr_%d.wav" % os.getpid()
    cands = []
    if PATHS.get("cache"):
        cands.append(Path(PATHS["cache"]) / "tmp")
    cands.append(_HERE_DIR / "rt" / "cache" / "tmp")
    for d in cands:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        # 顺手扫掉上次被杀掉的进程留下的同名前缀文件（超过 1 小时的）
        try:
            import time as _t
            for old in d.glob("qwen3asr_*.wav"):
                if old.name == name:
                    continue
                try:
                    if _t.time() - old.stat().st_mtime > 3600:
                        old.unlink()
                except OSError:
                    pass
        except Exception:
            pass
        return d / name
    return Path(tempfile.gettempdir()) / name


def extract_audio(path, sr=16000):
    """从视频/音频提取单声道 16k 波形"""
    import soundfile as sf
    import numpy as np

    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT or ext not in {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus"}:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            tmp = _tmp_wav_path()
            subprocess.run(
                [exe, "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), str(tmp)],
                capture_output=True, check=True
            )
            # ★ try/finally：sf.read 失败时（文件损坏/磁盘满）
            #   以前会把临时 wav 留在 %TEMP% 里，每次失败漏一个，
            #   几十 GB 的音频漏几次就把临时目录撑满。
            try:
                data, _ = sf.read(str(tmp), dtype="float32")
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            return data, sr
        except subprocess.CalledProcessError as e:
            # ★ 原来是 except: pass，然后 fallthrough 去 sf.read(视频文件)，
            #   报出来的是"soundfile 打不开 .mp4"这种误导信息。真实原因
            #   （ffmpeg 不在 / 解码失败 / 磁盘满）必须带到上层。
            tail = (e.stderr or b"")[-400:]
            if isinstance(tail, bytes):
                tail = tail.decode("utf-8", "replace")
            raise RuntimeError("ffmpeg 提取音频失败：%s\n%s" % (path, tail.strip()))
        except Exception as e:
            # imageio_ffmpeg 缺失之类：也别静默，明确失败原因
            raise RuntimeError("音频提取失败（%s）：%s: %s"
                               % (path, type(e).__name__, e))

    data, file_sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if file_sr != sr:
        import numpy as np
        n = int(round(len(data) * sr / file_sr))
        data = np.interp(
            np.linspace(0, len(data) - 1, n, dtype=np.float64),
            np.arange(len(data), dtype=np.float64),
            data
        ).astype("float32")
    return data, sr


# ── 语音活动检测（能量 + 过零率，无需额外依赖）────────────────────────────
def _valley_cut(energy, voiced, lo, hi, ideal, win, search=3.0, min_seg=2.0):
    """在 ideal ± search 秒内找「谷点」下刀，返回切点秒数；找不到返回 ideal

    为什么不在 ideal 处硬切：理想切点往往落在词语中间，硬切会把一个词
    拦腰截断，后半窗识别出来是个残词。谷点（能量局部最小、且最好落在
    非语音帧上）基本都在词间停顿里，切过去前后两窗都能识别完整。
    """
    import numpy as np

    a = max(int((lo + min_seg) / win), int((ideal - search) / win), 0)
    b = min(int((hi - min_seg) / win), int((ideal + search) / win),
            len(energy) - 1)
    if b <= a:
        return ideal
    seg_e = energy[a:b + 1]
    unvoiced = np.where(~voiced[a:b + 1])[0]
    # 非语音帧里挑能量最低的；一个都没有（整窗都在说话）就挑全局最低
    cand = unvoiced if len(unvoiced) else np.arange(len(seg_e))
    best = cand[int(np.argmin(seg_e[cand]))]
    pos = (a + best) * win
    return min(max(pos, lo + min_seg), hi - min_seg)


def _median3(x):
    """3 点中值平滑：去掉能量序列里的单帧毛刺（爆音、点击声）。

    只用来算阈值和判语音，不改动原始波形，所以不会影响识别。
    """
    import numpy as np

    if len(x) < 3:
        return x
    p = np.pad(x, 1, mode="edge")
    return np.median(np.stack([p[:-2], p[1:-1], p[2:]]), axis=0)


# silero-vad 模型只加载一次（首次约 0.3~1 秒），失败也记下来不再重试
_SILERO = {"model": None, "tried": False}


def silero_available():
    return _SILERO["model"] is not None


def _silero_segments(audio, sr, max_len, min_gap, pad, min_speech):
    """用 silero-vad 切分；不可用/出错返回 None（调用方自动回退能量法）。

    ★ 为什么是"可选"而不是"必须"：
      silero-vad 是个独立 pip 包（自带模型文件，不联网）。安装器默认不装它
      —— 它是几十 MB 的额外体积，且老用户升级时不会自动补装。所以做成
      「装了就用、没装就回退」，两条路都不影响主流程。
    """
    if not _SILERO["tried"]:
        _SILERO["tried"] = True
        try:
            from silero_vad import load_silero_vad
            _SILERO["model"] = load_silero_vad()
        except Exception:
            _SILERO["model"] = None
    model = _SILERO["model"]
    if model is None:
        return None
    try:
        import numpy as np
        import torch
        from silero_vad import get_speech_timestamps

        wav = torch.from_numpy(np.asarray(audio, dtype="float32"))
        ts = get_speech_timestamps(
            wav, model, sampling_rate=sr, return_seconds=True,
            max_speech_duration_s=max_len,
            min_silence_duration_ms=int(min_gap * 1000),
            speech_pad_ms=int(pad * 1000),
        )
        out = [(float(x["start"]), float(x["end"])) for x in (ts or [])
               if float(x["end"]) - float(x["start"]) >= min_speech]
        return out or None
    except Exception:
        return None


def detect_speech(audio, sr, win_ms=30, thresh_ratio=0.08, min_speech=0.6,
                  min_gap=0.6, pad=0.5, max_len=35.0, hang=0.20,
                  use_silero=True):
    """语音活动检测，返回 [(start_sec, end_sec), ...]

    ★ v2.0.0 起两条路径，优先 silero：
      ① silero-vad（如果环境里装了 silero-vad 包）—— 神经网络 VAD，
         对背景音乐、空调声、键盘声这类非平稳噪声远好于能量阈值。
         包自带模型文件，不联网，符合「音频不上传」的定位。
         没装就静默回退，不影响任何功能。
      ② 能量阈值（兜底，零依赖）—— v2.0.0 重写为【自适应噪声底】：
         · 噪声底 = 能量的 15 分位，而不是拿 95 分位当"峰值"再乘固定比例。
           老写法在噪声大的素材上阈值偏低（把噪声当语音，切出大量碎片），
           在安静素材里又偏高（漏掉轻声字）。
         · 阈值 = 噪声底 + max(噪声底 × 1.5, (语音参考 − 噪声底) × 0.12)，
           两头都夹住，噪声再大也不会把整段判成语音。
         · 能量先做 3 点中值平滑，去掉单帧毛刺。
         · 语音段向后延 hang 秒（尾音保持），避免一个词被中间的弱音节切断。

    参数偏保守：宁可把静音也包进来，也不要把一句话切断
    （Qwen3-ASR 需要足够上下文，断得太碎识别质量会下降）。

    ★ 超长片段不按 max_len 硬切，而是在理想切点附近找【谷点】下刀
      （见 _valley_cut）；pad 0.5s、上限 35s，给模型更多上下文。
    """
    import numpy as np

    if use_silero:
        segs = _silero_segments(audio, sr, max_len, min_gap, pad, min_speech)
        if segs:
            return segs

    win = int(sr * win_ms / 1000)
    if len(audio) < win:
        return [(0.0, len(audio) / sr)]

    n = len(audio) // win
    frames = audio[: n * win].reshape(n, win)
    energy = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)

    peak = float(np.percentile(energy, 95))
    if peak <= 1e-6:
        # ★ v4.8.23：整段数字静音【不要】交给模型 —— 它会对静音硬编出
        #   "嗯。"这类字幕（实测 10 秒全零音频 → 2 条"嗯。"）。
        #   这里返回空 = 上游报"未识别到语音内容"，而不是伪造字幕。
        return []

    # ── 自适应噪声底 ──
    energy_s = _median3(energy)
    noise = float(np.percentile(energy_s, 15))
    ref = float(np.percentile(energy_s, 90))
    # 阈值 = clamp(语音参考 × 0.12, 噪声底 × 1.6, 语音参考 × 0.5)
    #   · 下界 噪声底×1.6：噪声再大，阈值也要压住噪声，否则整段都算语音
    #   · 上界 语音参考×0.5：再高就会把轻声说话整段吞掉
    lo = noise * 1.6
    hi = ref * 0.5
    if hi <= lo:
        # 信噪比低于约 1.6:1，能量法已经分不开语音和噪声。
        # 这时候不要硬切（切出来的全是碎片），整段交给模型更稳。
        # ★ v4.8.23 但先卡一道绝对电平：连 -45 dBFS 都不到的"声音"是
        #   底噪/空调声，交给模型只会换来幻觉字幕。
        if 20.0 * np.log10(max(ref, 1e-12)) < -45.0:
            return []
        return [(0.0, len(audio) / sr)]
    thresh = min(max(ref * 0.12, lo), hi)
    thresh = max(thresh, 1e-4)
    voiced = energy_s > thresh

    # ── 尾音保持：把每个语音段向后延 hang 秒 ──
    hang_frames = max(1, int(hang * sr / win))
    if hang_frames > 1:
        idx = np.where(voiced)[0]
        for i in idx:
            voiced[i: i + hang_frames] = True

    segs, i = [], 0
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            segs.append([i * win / sr, j * win / sr])
            i = j
        else:
            i += 1

    merged = []
    for s in segs:
        if merged and s[0] - merged[-1][1] < min_gap:
            merged[-1][1] = s[1]
        else:
            merged.append(s)

    # 超长片段再切开：谷点切分（保留 pad 重叠上下文，避免边界截词）
    final = []
    for s, e in merged:
        if e - s <= max_len:
            final.append([s, e])
            continue
        cur = s
        while cur < e:
            nxt = min(e, cur + max_len)
            if nxt < e:   # 尾巴上不足一个窗长时剩下的全拿走，不再找谷点
                nxt = _valley_cut(energy_s, voiced, cur, e, nxt, win)
            final.append([cur, nxt])
            cur = nxt

    # ── padding：前后各扩 pad 秒，但【绝不允许和相邻片段重叠】──
    # ★★ v1.28 的坑：min_gap=0.6、pad=0.5，两段静音间隔只要在 0.6~1.0 秒之间
    #    （正常说话的停顿大量落在这个区间），各扩 0.5 秒后就会重叠 0.1~0.4 秒。
    #    重叠区的话被两个片段各识别一次 → 字幕里出现重复句子。
    #    现在把扩展量夹在「中点」以内，天然不会重叠，重叠区的话只属于一段。
    dur = len(audio) / sr
    padded = []
    for k, (s, e) in enumerate(final):
        left = s - pad
        right = e + pad
        if k > 0:
            left = max(left, (final[k - 1][1] + s) / 2.0 + 0.02)
        if k + 1 < len(final):
            right = min(right, (e + final[k + 1][0]) / 2.0 - 0.02)
        left = max(0.0, left)
        right = min(dur, max(right, e))
        padded.append((left, right))

    out = [(s, e) for s, e in padded if e - s >= min_speech]
    return out or [(0.0, len(audio) / sr)]


# ── 断句（按标点和长度切分文本）─────────────────────────────────────────
END_PUNCT = "。！？!?；;…"
PAUSE_PUNCT = "，,、：:"
# 收尾引号 / 括号 —— 口语里常用引号或书名号把整句包起来，
# 句末标点之后往往还剩一个收尾符号，去标点时要连带吃掉。
CLOSERS = "”’」』）】》〉」\"']"
TRAIL_PUNCT = END_PUNCT + PAUSE_PUNCT + CLOSERS


def _dw(s):
    """显示宽度：CJK 全角字符算 2 列，其余算 1 列

    字幕行宽要看【占多少列】而不是【多少个字符】：同样 12 个单位，
    汉字占 24 列、英文只占 12 列。界面上的「每行最多 12 字」按
    12 个汉字的视觉宽度理解，英文因此能放约 24 个字符。
    """
    w = 0
    for ch in s:
        w += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return w


def _is_wide(ch):
    """单个字符是不是全角（CJK 等）。用于判断合并两段时要不要补空格。"""
    return unicodedata.east_asian_width(ch) in ("F", "W")


# 数字 + 常见量词视为整体，断行时不允许拆开（"3.14" 不能变成 "3." + "14"，
# "12 个" 不能变成 "12" + "个"）。单位表覆盖字幕口语里 95% 的场合。
_NUM_UNIT_RE = re.compile(
    r"\d+(?:[.,]\d+)+"                      # 3.14 / 1,000.50
    r"|\d+\s*(?:个|种|条|只|份|秒|分钟|小时|点|次|位|名|块|片|句|字|行"
    r"|年|月|日|天|号|版|集|章|节|段|台|部|首|幅|张|把|件|套|双|对"
    r"|群|批|堆|串|滴|帧|万|亿|元|块|米|公里|斤|公斤|升|度|折)"
    r"|\d+")


def _spans(s):
    """把字符串切成原子：标点 / 数字单位整体 / 其余单字。

    只有原子【边界】才是合法断点，原子内部不断 —— 这是数字保护的实现。
    """
    spans, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        if ch in END_PUNCT or ch in PAUSE_PUNCT or ch in CLOSERS:
            spans.append((i, i + 1))
            i += 1
            continue
        m = _NUM_UNIT_RE.match(s, i)
        if m:
            spans.append((i, m.end()))
            i = m.end()
            continue
        spans.append((i, i + 1))
        i += 1
    return spans


def _allowed_cuts(s):
    """合法断点集合：每个原子边界 + 每个标点之后"""
    spans = _spans(s)
    cuts = set()
    for k, (a, b) in enumerate(spans):
        if s[a] in END_PUNCT or s[a] in PAUSE_PUNCT or s[a] in CLOSERS:
            cuts.add(b)
        if k + 1 < len(spans):
            cuts.add(spans[k + 1][0])
    return cuts


# ★ v4.8.23：折行时宁可提前几格，也要落在标点后 —— 这是中文断行最影响
#   观感的一条（实测 28 行里有 8 行切在词中间："大家晚 / 上好。"）。
_WRAP_PUNCT_WINDOW = 10
# 这些字出现在行首最难看（它们是词尾/虚词）
_WRAP_NO_LINE_START = "的了着过们吗呢吧啊呀嘛哦然的"


def _punct_cut_before(s, i, floor):
    """[floor, i) 里最后一个标点之后的位置；没有就 None"""
    for q in range(min(i, len(s)), floor, -1):
        ch = s[q - 1]
        if ch in END_PUNCT or ch in PAUSE_PUNCT or ch in CLOSERS:
            return q
    return None


def _avoid_bad_break(s, p, start, cuts):
    """别把"的/了/们"甩到行首：往回退一格（必须仍是合法断点）"""
    while p < len(s) and s[p] in _WRAP_NO_LINE_START and p - 1 > start and (p - 1) in cuts:
        p -= 1
    return p


def _wrap_line(s, limit, word_cuts=None):
    """按显示宽度把一行折到 limit 列以内，优先在【词边界】断，其次标点。

    ★ v4.8.24：加词边界（word_cuts）。断点优先落在强制对齐给出的词边界上，
      实测能消掉「我代 / 表云岭」「中秋祝 / 福。」这类词内断开 —— 原来的
      候选断点只有「标点之后」和「原子边界」，而中文的原子边界就是每个单字，
      等于没有任何词语概念。
      词边界由调用方从 words 换算成【本行内的字符下标】传入，算不出来就传 None，
      行为与 4.8.23 完全一致。
    """
    if _dw(s) <= limit:
        return [s]
    cuts = _allowed_cuts(s)
    wcuts = set()
    for c in (word_cuts or ()):
        try:
            c = int(c)
        except (TypeError, ValueError):
            continue
        if 0 < c < len(s):
            wcuts.add(c)
    if wcuts:
        cuts = cuts | wcuts          # 词与词之间天然是合法断点
    lines, start, w, i, n = [], 0, 0, 0, len(s)
    while i < n:
        cw = _dw(s[i])
        if w + cw > limit:
            p = None
            # ① 标点优先（v1.0.1）：中文断行观感上「句读」永远比「词」重要。
            #   实测 24 字档切出「…大家晚 / 上好。」，根因是对齐器把
            #   「大家晚上」当成一个词、词边界排在标点前面；v4.8.23 的注释
            #   写着"宁可提前几格也要落在标点后"，代码顺序却正好相反。
            p = _punct_cut_before(s, i, max(start + 1, i - _WRAP_PUNCT_WINDOW))
            # ② 窗口内没有标点，再退到词边界（窗口内最靠后的那个）
            if p is None and wcuts:
                cand = [c for c in wcuts if start < c <= i]
                if cand:
                    p = max(cand)
            if p is None:
                # ③ 退回原子边界
                p = i
                while p > start and p not in cuts:
                    p -= 1
                # 词边界不做「行首难看字」回退：回退会把词拆开，正是要避免的事
                p = _avoid_bad_break(s, p, start, cuts)
            # ★ v1.0.1：收尾引号/括号不能落在行首（实测切出过
            #   「…扣一波“一」+「”，好不好？」这种断法）。往后挪一格，
            #   让引号留在上一行末尾 —— 宁可这行多占两列，也不要行首一个「”」。
            while p < n and s[p] in CLOSERS:
                p += 1
            if p <= start:            # 一个断点都没有 → 硬切
                # ★ 必须保证 p > start：否则 start 原地不动、s[i] 也不消费，
                #   下一轮状态完全一样 → 死循环、lines 无限增长。
                #   limit >= 2 时 i 必然 > start（w 累加过才会超限），
                #   但 limit <= 0 时第一轮就 i == start，这里兜一下。
                p = max(i, start + 1)
            # ★ v4.8.24：切完只剩标点时，断点前移。
            #   典型场景：8 字档「各位朋友，大家好。」—— 词边界把断点放在「。」前，
            #   于是这行切成「各位朋友，大家好」+「。」，句号单独成行。
            #   前移到「，」之后 → 「各位朋友，」+「大家好。」，两行都不超宽。
            if p < len(s) and not _has_word_char(s[p:]):
                q = _punct_cut_before(s, p, max(start + 1, start))
                if q and start < q < p:
                    p = q
                else:
                    prev = [c for c in cuts if start < c < p]
                    if prev:
                        p = max(prev)
            lines.append(s[start:p].strip())
            # ★ v4.8.23：断点被"粘到标点"后会退到 i 前面，i 必须跟着退，
            #   否则 p..i 之间的字没进宽度统计 —— 实测会切出 29 字/58 列的行。
            start, i, w = p, p, 0
            continue                 # 从新起点重新计宽
        w += cw
        i += 1
    if start < n:
        lines.append(s[start:].strip())
    return [x for x in lines if x]


def _has_word_char(s):
    """串里有没有"真字"（汉字/字母/数字）。只有标点的串是脏数据。"""
    return any(unicodedata.category(c)[0] in ("L", "N") for c in str(s or ""))


def _flatten_ws(s):
    """把文本里的换行/制表压平。

    ★ 全角字符之间直接去掉（中文换行不留空格），
      两侧都是半角时才补一个空格（英文换行不粘词）。
    """
    s = str(s or "")
    if "\n" not in s and "\r" not in s and "\t" not in s:
        return s
    out = []
    for i, ch in enumerate(s):
        if ch in "\r\n\t":
            prev = s[i - 1] if i else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if prev and nxt and not _is_wide(prev) and not _is_wide(nxt):
                out.append(" ")
            continue
        out.append(ch)
    return "".join(out)


def _word_char_cuts(text, words):
    """把对齐器的词序列换算成 text 里的字符下标断点。

    ★ v4.8.24：折行时优先断在词边界，靠的就是这张表。
      词序列和原文经常差一点（模型改标点、去空格），一旦对不上就
      返回 None —— 宁可退回 4.8.23 的"标点/原子边界"行为，也不要
      拿一串错位的下标去切文本。
    """
    if not words or not text:
        return None
    cuts, pos = set(), 0
    for w in words:
        try:
            wt = str(w[0] or "")
        except (IndexError, TypeError):
            return None
        if not wt:
            continue
        idx = text.find(wt, pos)
        if idx < 0:
            return None
        pos = idx + len(wt)
        cuts.add(pos)
    return cuts or None


def _word_char_cuts_loose(text, words):
    """宽松版：词在 text 里找不到就跳过，不整份放弃。

    给「长条再切」用 —— 那里传进来的 text 只是整句的一个子串，
    严格版（_word_char_cuts）第一个词就匹配不上，只能返回 None。
    """
    if not words or not text:
        return None
    cuts, pos = set(), 0
    for w in words:
        try:
            wt = str(w[0] or "")
        except (IndexError, TypeError):
            continue
        if not wt:
            continue
        idx = text.find(wt, pos)
        if idx < 0:
            continue
        pos = idx + len(wt)
        cuts.add(pos)
    return cuts or None


def split_sentences(text, max_chars=12, min_chars=None, word_cuts=None):
    """把文本切成字幕行，按【显示宽度】严格不超过 max_chars×2 列

    策略：
      1. 按句末标点断句
      2. 超宽的行按显示宽度折行（优先词边界，其次标点/原子边界，数字单位不拆）
      3. 过窄的行与下一行合并 —— 合并后仍不得超宽
      4. 去掉行首标点
      5. 纯标点片段并回前一条（v4.8.24）
    """
    # ★ 先夹住 max_chars：limit = max_chars*2 一旦 <= 0，_wrap_line 里
    #   每个字符的宽度都 > limit，会一路走进"一个断点都没有"的硬切分支，
    #   而那个分支原来不保证指针前进 → 死循环 + lines 无限增长（内存爆掉）。
    #   Lua 的「开始」路径已把字数夹在 4~200，但命令行参数
    #   （`int(argv[4]) if argv[4].isdigit()` 会把 "0" 传成 0）和
    #   args.json 等入口没有夹，这里做最后一道防线。
    try:
        max_chars = max(1, int(max_chars))
    except (TypeError, ValueError):
        max_chars = 12
    if min_chars is None:
        min_chars = max(4, max_chars // 2)
    limit = max_chars * 2
    min_w = min_chars * 2

    # 1) 按句末标点初步断句（超长行也先粗断，交给第 2 步细折）
    #    ★ v4.8.24：同时记下每段在原文里的起始下标，供词边界映射用。
    #      strip 掉的前导空格要算进偏移，否则英文文本的词边界会整体错位。
    raw, buf, buf_start = [], "", 0

    def _push(seg_buf, start_idx):
        seg = seg_buf.strip()
        if seg:
            raw.append((seg, start_idx + (len(seg_buf) - len(seg_buf.lstrip()))))

    for i, ch in enumerate(text):
        if not buf:
            buf_start = i
        buf += ch
        if ch in END_PUNCT:
            _push(buf, buf_start)
            buf = ""
        elif _dw(buf) >= limit * 2:
            _push(buf, buf_start)
            buf = ""
    _push(buf, buf_start)

    # 2) 超宽行按显示宽度折行（有词边界就传给 _wrap_line 优先用）
    chunks = []
    for seg, off in raw:
        if not seg:
            continue
        local = None
        if word_cuts:
            lo, hi = off, off + len(seg)
            local = [c - off for c in word_cuts if lo <= c <= hi]
        chunks.extend(_wrap_line(seg, limit, local))

    # 3) 过窄的合并（合并后仍不得超过 limit）
    merged = []
    for s in chunks:
        if merged:
            prev = merged[-1]
            # ★ 空格分词语言相接时要补一个空格：前面各步都做过 .strip()，
            #   直接相加会把词间空格吃掉 —— "OK." + "Go now." 变成
            #   "OK.Go now."。中文/日文之间不需要空格，所以只在【两侧
            #   都不是全角字符】时才补（全角判定与 _dw 同一套标准）。
            sep = " " if (prev and s
                          and not _is_wide(prev[-1])
                          and not _is_wide(s[0])) else ""
            if _dw(prev) < min_w and _dw(prev) + _dw(sep) + _dw(s) <= limit:
                merged[-1] = prev + sep + s
                continue
        merged.append(s)

    # 4) 去掉行首标点（硬切容易把逗号甩到下一行开头）
    #    ★ v4.8.24：顺手把文本里的换行/制表压平 —— 模型有时把句号吐在
    #      下一行（"...记在心里\n。"），不压平就会带着换行进 SRT，
    #      第 5 步也认不出它是纯标点。
    cleaned = []
    for s in merged:
        s = _flatten_ws(s).lstrip(PAUSE_PUNCT)
        s = s.strip()
        if s:
            cleaned.append(s)

    # 5) ★ v4.8.24：纯标点片段并回前一条。
    #    实测 24 字档 38 条里有 1 条整条只有一个「。」（0.64 秒），
    #    画面上会闪一下空字幕框，导出的 TXT 还会变成「皓月当空，。」双标点。
    #    模型有时把句号单独吐成一个 token，前面第 3 步的合并条件
    #    （前一条宽度 < min_w 才并）拦不住它。
    final = []
    for s in cleaned:
        if final and not _has_word_char(s):
            final[-1] = final[-1] + s
            continue
        final.append(s)
    return final


def strip_tail_punct(sents):
    """把每句【行尾】的标点连同收尾引号一起去掉，空句直接丢弃。"""
    out = []
    for s in sents:
        s = s.rstrip(TRAIL_PUNCT).strip()
        if s:
            out.append(s)
    return out


# 去标点用的完整集合：句末 + 停顿 + 收尾引号，再加上成对符号与连接符。
# 只在"去除标点符号"开关打开时使用（会把行内标点也删掉）。
# ★ 引号必须【左右都收】。CLOSERS 里只有右引号（” ’ 等），左引号 “ ‘ 不在
#   任何一张表里 —— 实测结果就是「去除标点符号」勾上以后，SRT 里仍残留
#   `“我用人参能吃吗` 这种以左引号开头的行（右引号被删掉了，只剩半边）。
ALL_PUNCT = (TRAIL_PUNCT
             + "“”‘’〝〞﹁﹂｢｣"          # 中英文引号：左右都要
             + "（）()「」『』《》〈〉【】〔〕［］[]{}"
             + "—－–~～·・…、／/\\|`^&*#@$%+=<>￥$")
_DROP_TABLE = str.maketrans("", "", ALL_PUNCT)


def drop_all_punct(sents):
    """把行内行尾的标点全部删掉（保留空格与字母数字）。

    ★ 断句仍然在标点上做（split_sentences 先切好），这里只是最后把标点抹掉，
    所以不会把一整段话糊成一行。
    """
    out = []
    for s in sents:
        s = s.translate(_DROP_TABLE).strip()
        if s:
            out.append(s)
    return out


# 无间隙时，最多把一条字幕往后延长多少秒。
# 遇到超长静音（比如中场休息 30 秒）就不硬撑 —— 否则一个字幕会在屏幕上
# 挂很久。正常口播的句间空档都远小于这个值，所以效果依然是"没有空隙"。
GAP_MAX_EXTEND = 6.0


def fill_gaps(blocks, min_dur=0.04, max_extend=GAP_MAX_EXTEND):
    """字幕无间隙：把每条的【结束时间】延长到下一条开始之前。

    ★★★ 绝对不能移动【开始时间】！早先的实现是把整条字幕往前挤，
    总时长被压缩（实测 158 条字幕从 212.6 秒被压到 185.9 秒），
    于是越往后偏得越多 —— 用户看到的就是"字幕和音频完全对不上"。
    现在只动结束时间：字幕始终跟着音频走，只是中途不消失。
    """
    out = []
    n = len(blocks)
    for i, (st, en, tx) in enumerate(blocks):
        if i + 1 < n:
            nxt = blocks[i + 1][0]
            # 留 0.04 秒的极短间隔：两段完全首尾相接（尤其是时间戳相同）
            # 在某些播放器/轨道上会被当成重叠处理。
            if nxt - 0.04 > en and (nxt - en) <= max_extend:
                en = nxt - 0.04
        if en < st + min_dur:
            en = st + min_dur
        out.append((st, en, tx))
    return out


# ── 分段结果缓存（断点续跑，v2.0.0 新增）─────────────────────────────
# ★ 解决什么问题：84 分钟的素材识别要 25 分钟（RTF 0.30）。中途断电、
#   显存炸了、用户手滑点了停止 —— 以前只能从头再来一遍。
# ★ 怎么解决：每识别完一个语音片段就把「原文 + 词级对齐结果」落到
#   <根>\data\cache\seg\seg_<音频指纹>.json（老布局/开发机在引擎\rt\cache）。
#   下次跑同一素材，片段直接命中缓存，
#   只重做没跑过的部分。改「每行字数」「去标点」这类纯排版选项不会
#   让缓存失效（缓存的是原文，不是切好的字幕行）。
# ★ 失效条件：音频文件变了（大小/mtime）、换模型、换语言、改提示词 ——
#   签名里都带着，任一不符就整份作废，绝不拿旧结果凑数。
SEG_CACHE_VER = 2
SEG_CACHE_MAX_FILES = 20


def _seg_cache_dir():
    """分段缓存目录：3.0 在 <根>\\data\\cache\\seg，2.x/开发机在引擎\\rt\\cache。

    ★ 放 data 下而不是引擎下：缓存可能几百 MB～几 GB，用户重装/升级时
      不该被当成程序文件一起删；rt 只留运行期的小文件（状态/心跳/日志）。
    """
    if PATHS.get("cache"):
        d = Path(PATHS["cache"]) / "seg"
    else:
        d = _HERE_DIR / "rt" / "cache"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return d


def _audio_sig(path):
    """音频指纹：绝对路径 + 大小 + mtime。"""
    import hashlib
    st = os.stat(path)
    h = hashlib.md5()
    h.update(os.path.abspath(path).encode("utf-8", "replace"))
    h.update(("%d|%d" % (st.st_size, int(st.st_mtime))).encode("ascii"))
    return h.hexdigest()[:16]


def _seg_sig(model_path, lang, prompt):
    import hashlib
    h = hashlib.md5()
    for part in (str(model_path), str(lang), str(prompt)):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _seg_cache_prune(keep=SEG_CACHE_MAX_FILES):
    d = _seg_cache_dir()
    if d is None:
        return
    try:
        files = []
        for p in d.iterdir():
            if p.suffix == ".json" and p.name.startswith("seg_"):
                files.append((p.stat().st_mtime, p))
        files.sort()
        while len(files) > keep:
            _, p = files.pop(0)
            try:
                p.unlink()
            except OSError:
                break
    except OSError:
        pass


def _load_seg_cache(audio_path, model_path, lang, prompt):
    """返回 (缓存字典 或 None, 缓存文件路径)。签名不符一律返回 None。"""
    d = _seg_cache_dir()
    if d is None:
        return None, None
    try:
        fp = _audio_sig(audio_path)
    except OSError:
        return None, None
    path = d / ("seg_%s.json" % fp)
    if not path.exists():
        return None, path
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, path
    if (data.get("v") != SEG_CACHE_VER
            or data.get("sig") != _seg_sig(model_path, lang, prompt)):
        return None, path
    segs = data.get("segs")
    if not isinstance(segs, dict):
        return None, path
    return segs, path


def _save_seg_cache(path, audio_path, model_path, lang, prompt, segs, dur=0.0):
    if not path or segs is None:
        return
    payload = {
        "v": SEG_CACHE_VER,
        "sig": _seg_sig(model_path, lang, prompt),
        "audio": str(audio_path),
        "dur": round(float(dur or 0), 2),
        "t": int(time.time()),
        "segs": segs,
    }
    tmp = "%s.%d.tmp" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ── 时间戳估算（按字数比例分配）─────────────────────────────────────────
def distribute_timing(sentences, seg_start, seg_end, gap=0.08):
    """在片段内按字数比例分配时间戳（降级路径）

    ★ ForcedAligner 可用且对齐合格时不会走这里；只有语种不支持、模型
      缺失、加载失败或对齐质量不达标时才退回本函数。
    """
    if not sentences:
        return []
    weights = [max(1, len(re.sub(r"[^\w\u4e00-\u9fff]", "", s))) for s in sentences]
    total = sum(weights)
    span = max(0.1, seg_end - seg_start - gap * (len(sentences) - 1))

    out, t = [], seg_start
    for s, w in zip(sentences, weights):
        dur = span * w / total
        out.append((t, t + dur, s))
        t += dur + gap
    return out


# ── 词语级对齐（ForcedAligner）───────────────────────────────────────────
# 对齐质量判定的两个阈值：覆盖率下限、允许的最大词间空洞（秒）
ALIGN_MIN_COVERAGE = 0.35
ALIGN_MAX_GAP = 3.0


def parse_asr_output(processor, raw):
    """解析识别原始输出 → (纯文本, 检测到的语言或 None)

    Qwen3-ASR 的输出格式是 `language <LANG><asr_text>转写文本`：
      · 手动指定语言时，语言前缀由 processor 预填，模型只生成转写文本；
      · 自动检测时（language=None），前缀由模型自己生成，必须剥掉 ——
        否则会把 "language Chinese" 这类内容原样写进字幕。
    这里直接调用 processor 自带的官方解析，避免自己造轮子出错。
    """
    if not raw:
        return "", None
    try:
        parsed = processor.parse_output(raw)
    except Exception:
        return raw.strip(), None
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    if not isinstance(parsed, dict):
        return raw.strip(), None
    text = (parsed.get("transcription") or "").strip()
    lang = parsed.get("language") or None
    # ★ 解析成功就以解析结果为准。模型遇到空音频会输出
    #   `language None<asr_text>`，官方解析给出的是空转写；此处绝不能
    #   回退成原文，否则这句标记会被当成字幕写进 SRT。
    return text, lang


def check_alignment(words, dur):
    """检查对齐质量 → (合格?, 原因)

    三类问题判为不合格，触发「拆分重试」，重试仍不合格才降级为比例估算：
      · 时间戳异常（非有限值 / 结束早于开始）—— 模型偶发乱序
      · 覆盖率过低 —— 说明整体漂移，开头或结尾没对上
      · 出现过大空洞 —— 说明中间一段没对齐上
    """
    import math

    if not words:
        return False, "没有对齐结果"
    for _t, s, e in words:
        if not (math.isfinite(s) and math.isfinite(e)) or e < s:
            return False, "时间戳异常"
    covered = words[-1][2] - words[0][1]
    if dur > 0 and covered < dur * ALIGN_MIN_COVERAGE:
        return False, "覆盖率仅 %.0f%%" % (covered / dur * 100)
    worst = 0.0
    for i in range(len(words) - 1):
        worst = max(worst, words[i + 1][1] - words[i][2])
    if worst > ALIGN_MAX_GAP:
        return False, "存在 %.1f 秒空洞" % worst
    return True, ""


def enforce_monotonic(blocks, min_gap=0.0):
    """保证时间戳单调不减，避免相邻字幕行时间倒退。"""
    out, prev_end = [], -1.0
    for st, en, tx in blocks:
        if st < prev_end + min_gap:
            st = prev_end + min_gap
        if en < st + 0.04:
            en = st + 0.04
        out.append((st, en, tx))
        prev_end = en
    return out


def enforce_min_duration(blocks, min_dur=0.04, gap=0.04):
    """保证每条字幕至少停留 min_dur 秒（v2.0.0 新增，界面上可调）。

    ★ 为什么需要：太短的字幕一闪而过根本看不清；而且在 Resolve 里，
      不足一帧的条目会被合并甚至丢掉（v1.28 实测 2169 条只落上 2168 条）。
      把最短时长抬到 0.5~1 秒能同时改善观感和落轨成功率。

    ★ 只延长结束时间，不动开始时间 —— 和 fill_gaps 同一原则：
      动开始时间会让字幕整体和音频错位，越往后偏得越多。
      延长到会撞上下一条时，退让到下一条开始前 gap 秒。
    ★ v4.8.24：被下一条挤住、退让后仍然极短（< TINY_SHOW_SEC）的条目，
      由 merge_tiny_blocks 提前并进前一条，不会再留下 0.12 秒的单字。
    """
    if not blocks:
        return blocks
    min_dur = max(0.04, float(min_dur or 0.04))
    out = []
    n = len(blocks)
    for i, (st, en, tx) in enumerate(blocks):
        if en < st + min_dur:
            en = st + min_dur
        if i + 1 < n:
            limit = blocks[i + 1][0] - gap
            if en > limit:
                en = max(st + 0.04, limit)
        out.append((st, en, tx))
    return out


# ══════════════════════════════════════════════════════════════════
# v4.8.24：三条「观感修补」—— 都是实测出来的具体毛病
#   · 碎片：8 字档 72 条里 9 条不到 0.79 秒，最短 0.12 秒（"心"）
#   · 长条：24 字档有一行占 6.20 秒（"…送上一份诚挚的中秋祝"），
#     下一条才是"福。"，等于一句话被拆成 6 秒 + 0.8 秒
#   · 纯标点：整条只有一个「。」，画面上闪一下，还污染导出的 TXT
# ══════════════════════════════════════════════════════════════════
TINY_SHOW_SEC = 0.40        # 短于这个值就并进前一条
MAX_SHOW_SEC = 4.00         # 单条最长显示，超过就再切一刀


def merge_tiny_blocks(blocks, min_show=TINY_SHOW_SEC, max_width=0):
    """把显示时长不足 min_show 的碎片并进前一条。

    ★ 为什么要在 enforce_min_duration 之前做：那条函数只能"延长到不撞下一条"，
      撞上了就只能退让，于是留下 0.12 秒的字幕。与其让它闪一下，不如把
      这几个字并到前一条里 —— 观众看到的是完整一句话。
    ★ 开头的碎片没有前一条可并，原样保留（不能往前并）。
    ★ v4.8.24 补：合并后不得超宽（max_width 是列宽，0=不限）。
      不检查的话会把行拼回 9~12 字（8 字档实测 8 条超字数），
      等于用"超字数"换"不闪"，得不偿失 —— 超宽就放弃这次合并。
    返回 (新块表, 被并掉的条数)。
    """
    if not blocks:
        return blocks, 0
    out, merged = [], 0
    for st, en, tx in blocks:
        if out and (en - st) < min_show:
            pst, pen, ptx = out[-1]
            sep = ""
            if ptx and tx and not _is_wide(ptx[-1]) and not _is_wide(tx[0]):
                sep = " "                     # 英文/数字之间补一个空格
            cand = ptx + sep + tx
            if max_width and _dw(cand) > max_width:
                out.append((st, en, tx))      # 并上去会超宽 → 留着这条短的
                continue
            out[-1] = (pst, max(pen, en), cand)
            merged += 1
            continue
        out.append((st, en, tx))
    return out, merged


def merge_punct_only_blocks(blocks, max_width=0):
    """整条只有标点的块并进前一条（跨语音片段也管）。

    ★ 为什么单独一个函数：split_sentences 只能在【一个片段内】并条，
      而实测的「。」多数来自片段边界 —— 上一个片段收尾吐一个句号，
      它自成一个片段，split 里没有前一条可并。
    ★ v4.8.24：同样不允许拼超宽。
    返回 (新块表, 并掉的条数)。开头就是纯标点且没有前一条时直接丢弃。
    """
    if not blocks:
        return blocks, 0
    out, n = [], 0
    for st, en, tx in blocks:
        tx = str(tx or "")
        if not _has_word_char(tx):
            if out and (not max_width
                        or _dw(out[-1][2] + tx.strip()) <= max_width):
                pst, pen, ptx = out[-1]
                out[-1] = (pst, max(pen, en), ptx + tx.strip())
                n += 1
                continue
            if not out:
                n += 1               # 首条就是纯标点：没有前一条可并，直接丢
                continue
            # 前一条放不下：宁可丢掉这个标点，也别把行撑宽
            n += 1
            continue
        out.append((st, en, tx))
    return out, n


def resplit_overlong_blocks(blocks, words, seg_start, max_show=MAX_SHOW_SEC,
                            min_chars=6):
    """单条显示超过 max_show 秒就再切一刀（优先切在标点/词边界）。

    ★ 只在「字数够多」时才切：一句话只有 6 个字却占了 5 秒，
      那是说话人拖长音，切开反而更碎。
    返回 (新块表, 新增条数)。
    """
    if not blocks:
        return blocks, 0
    bounds = []
    if words:
        try:
            bounds = sorted({seg_start + float(w[1]) for w in words}
                            | {seg_start + float(w[2]) for w in words})
        except (IndexError, TypeError, ValueError):
            bounds = []
    out, added = [], 0
    for st, en, tx in blocks:
        dur = en - st
        chars = len(tx)
        if max_show <= 0 or dur <= max_show or chars < min_chars:
            out.append((st, en, tx))
            continue
        k = min(int(math.ceil(dur / max_show)), max(1, chars // 3))
        if k <= 1:
            out.append((st, en, tx))
            continue
        pieces = _split_text_evenly(tx, k, _word_char_cuts_loose(tx, words))
        if len(pieces) <= 1:
            out.append((st, en, tx))
            continue
        total_c = sum(len(p) for p in pieces) or 1
        acc, cuts, prev_t = 0, [], st
        for p in pieces[:-1]:
            acc += len(p)
            t = st + dur * acc / total_c
            t = _snap_to_bound(t, bounds)
            t = max(prev_t + 0.04, min(t, en - 0.04))
            cuts.append(t)
            prev_t = t
        starts = [st] + cuts
        ends = cuts + [en]
        for a, b, p in zip(starts, ends, pieces):
            out.append((a, b, p))
        added += len(pieces) - 1
    return out, added


def _split_text_evenly(text, k, word_cuts=None):
    """把一段文本切成 k 份，优先切在标点之后，其次词边界。"""
    text = str(text or "")
    if k <= 1 or len(text) < 2:
        return [text]
    # 候选切点：标点之后优先，词边界其次（词边界不该排在硬均分后面）
    cuts = [i + 1 for i, ch in enumerate(text)
            if ch in END_PUNCT or ch in PAUSE_PUNCT]
    cuts = [c for c in cuts if 1 <= c < len(text)]
    wcuts = sorted({int(c) for c in (word_cuts or ())
                    if 1 <= int(c) < len(text)})
    target = len(text) / float(k)
    picked = []
    for j in range(1, k):
        want = target * j
        pool = cuts if cuts else wcuts
        if pool:
            best = min(pool, key=lambda c: abs(c - want))
            if abs(best - want) <= max(2.0, target * 0.5) and best not in picked:
                picked.append(best)
                continue
        if wcuts:
            best = min(wcuts, key=lambda c: abs(c - want))
            if abs(best - want) <= max(2.0, target * 0.5) and best not in picked:
                picked.append(best)
                continue
        picked.append(int(round(want)))
    picked = sorted({max(1, min(len(text) - 1, p)) for p in picked})
    if not picked:
        return [text]
    out, prev = [], 0
    for p in picked:
        seg = text[prev:p].strip()
        if seg:
            out.append(seg)
        prev = p
    tail = text[prev:].strip()
    if tail:
        out.append(tail)
    return out or [text]


def _snap_to_bound(t, bounds, tol=0.35):
    """把切点时间吸附到最近的词边界（差得太远就不动）。"""
    if not bounds:
        return t
    best = min(bounds, key=lambda b: abs(b - t))
    return best if abs(best - t) <= tol else t


def refold_overlong_text(blocks, max_chars):
    """最后一道闸：写 SRT 前把任何超宽的块重新折行。

    ★ 为什么还要这一道：折行 / 长停顿断行 / 并条三条路各自都有宽度判断，
      但实测 8 字档仍漏下 2 条 18 列的行（"月当空，万家团圆。"）。
      与其在上游逐个堵，不如在出口统一兜一次 —— 只对超宽的块生效，
      时间按字符数比例摊（这类块极少，精度损失可以接受）。
    返回 (新块表, 被折开的块数)。
    """
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        return blocks, 0
    if max_chars < 1 or not blocks:
        return blocks, 0
    max_w = max_chars * 2
    out, n = [], 0
    for st, en, tx in blocks:
        tx = str(tx or "")
        if _dw(tx) <= max_w:
            out.append((st, en, tx))
            continue
        parts = [p for p in _wrap_line(tx, max_w) if p]
        if len(parts) <= 1:
            out.append((st, en, tx))
            continue
        dur = en - st
        total = sum(len(p) for p in parts) or 1
        acc = 0
        for p in parts:
            a = st + dur * acc / total
            acc += len(p)
            b = st + dur * acc / total
            if b < a + 0.04:
                b = a + 0.04
            out.append((a, b, p))
        n += 1
    return out, n


def assign_times_by_words(sentences, words, seg_start):
    """把词语级时间戳分配到字幕行

    sentences 是已经切好行的文本，words 是
    [(词, 相对开始秒, 相对结束秒), ...]（时间相对本片段）。
    做法：把每行的字符区间按比例映射到词序列上，取覆盖该行的第一个词
    的开始时间与最后一个词的结束时间。用「按比例映射」而不是精确字符
    对齐，是因为切行时会去掉标点/空格，行长与词长不可能完全一致；比例
    映射对这点差异是容忍的，而得到的时间戳仍来自真实的音频对齐。
    """
    if not sentences:
        return []
    if not words:
        return []

    line_lens = [max(1, len(s)) for s in sentences]
    word_lens = [max(1, len(w[0])) for w in words]
    total_line = float(sum(line_lens))
    total_word = float(sum(word_lens))

    bounds, acc = [], 0.0
    for L in word_lens:
        bounds.append((acc, acc + L))
        acc += L

    def index_at(ratio):
        target = ratio * total_word
        for i, (_a, b) in enumerate(bounds):
            if target <= b or i == len(bounds) - 1:
                return i
        return len(bounds) - 1

    out, cum = [], 0.0
    for s, L in zip(sentences, line_lens):
        a = cum / total_line
        cum += L
        b = cum / total_line
        i0, i1 = index_at(a), index_at(b)
        if i1 < i0:
            i1 = i0
        st = seg_start + words[i0][1]
        en = seg_start + words[i1][2]
        if en < st + 0.04:
            en = st + 0.04
        out.append((st, en, s))
    return enforce_monotonic(out)


# ── 长停顿断行（设置页开关，默认关）────────────────────────────────────
# ★ 为什么要它：对齐器把一个片段里的词按真实发音摊到整段音频上，一句话
#   中间要是停了 0.5 秒以上，字幕会连着把那段静音一起盖住。实测 99 秒素材
#   里字幕合计 72.4s、话音 61.9s，多出的 10.6s 基本都是这种"停在静音上"。
#   断行后：两半各自压在说话的那一段上，粗剪时删掉这一行也不会连带切掉静音。
PAUSE_SPLIT_SEC = 0.5


_PUNCT_BREAK = "。！？!?；;，,、"


def _snap_to_punct(text, idx, window=8):
    """把断点吸到最近的句读后面（窗口内），切出来才像一句话。"""
    best, bestd = None, window + 1
    for i, ch in enumerate(text):
        if ch in _PUNCT_BREAK:
            d = abs(i + 1 - idx)
            if d <= window and d < bestd:
                best, bestd = i + 1, d
    return best if best is not None else idx


def _char_pos_of_n_words(text, n_chars):
    """text 里走过 n_chars 个【实字】（跳过标点/空白）后的字符位置"""
    seen = 0
    for i, ch in enumerate(text):
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            seen += 1
        if seen >= n_chars:
            return i + 1
    return len(text)


def split_at_long_pauses(blocks, words, seg_start, limit, max_chars=0):
    """块内长静音处断成两行 → (新 blocks, 断了几处)

    ★ 在【时间已经算好】之后再切：这些时间是 ForcedAligner 给的真实词边界，
      按词边界切，两半各自落在有声的那一段；先切文本再对齐会重新按比例摊
      时间，等于把刚拿到的精度扔掉。
    ★ words 是【相对本片段】的 (词, 起, 止)，blocks 是绝对时间。
    ★ 每个块最多切一刀：一行里出现两处长静音极罕见，切一刀已足够，
      少切一刀比切乱文本安全。
    """
    if not words or limit <= 0 or not blocks:
        return blocks, 0
    gaps = []
    for i in range(len(words) - 1):
        gs = seg_start + float(words[i][2])
        ge = seg_start + float(words[i + 1][1])
        if ge - gs >= limit:
            gaps.append((gs, ge, i + 1))
    if not gaps:
        return blocks, 0

    total_chars = float(sum(len(b[2]) for b in blocks)) or 1.0
    total_word = float(sum(max(1, len(w[0])) for w in words)) or 1.0
    word_cum, acc = [], 0
    for w in words:
        acc += max(1, len(w[0]))
        word_cum.append(acc)

    def word_at(frac):
        """字符比例 → 词下标（与 assign_times_by_words 同一套映射）"""
        target = frac * total_word
        for i, c in enumerate(word_cum):
            if target <= c or i == len(word_cum) - 1:
                return i
        return len(word_cum) - 1

    out, n_cut, done = [], 0, 0
    for st, en, text in blocks:
        a = done / total_chars
        done += len(text)
        b = done / total_chars
        inner = [g for g in gaps if st < g[0] and g[1] < en]
        if not inner or len(text) < 4:
            out.append((st, en, text))
            continue
        gs, ge, wi = inner[0]
        # ★ 断点必须落在【词边界】上：按比例映射找到本块起点对应的词，
        #   再数到停顿前那个词为止有几个实字 —— 这样不会把"聊一聊"切成
        #   "聊一 / 聊"。（第一版按字符比例直接切，实测就切在词中间。）
        wi0 = word_at(a)
        local = word_cum[wi - 1] - (word_cum[wi0 - 1] if wi0 > 0 else 0)
        idx = _char_pos_of_n_words(text, local)
        idx = _snap_to_punct(text, idx)
        if idx <= 0 or idx >= len(text):
            out.append((st, en, text))
            continue
        left, right = text[:idx].strip(), text[idx:].strip()
        # 任何一半短到看不见（<0.2s）就不切，免得制造一闪而过的行
        if not left or not right or gs - st < 0.2 or en - ge < 0.2:
            out.append((st, en, text))
            continue
        # ★ v4.8.24：切完反而超宽就不切。
        #   实测 8 字档切出「时值中秋佳节，皓」+「月当空，万家团圆。」——
        #   两行都是 18 列，既超宽又把「皓月」拆开。断点算偏时宁可不切，
        #   让原块（合规的 8 字行）保持原样。
        if max_chars and max_chars > 0 and (
                _dw(left) > max_chars * 2 or _dw(right) > max_chars * 2):
            out.append((st, en, text))
            continue
        out.append((st, gs, left))
        out.append((ge, en, right))
        n_cut += 1
    if not n_cut:
        return blocks, 0
    return enforce_monotonic(out), n_cut


# ── SRT 输出 ────────────────────────────────────────────────────────────
def fmt_ts(x):
    """秒 → SRT 时间码 HH:MM:SS,mmm

    ★ 按【总毫秒】算，不要先拆时/分/秒再各自进位：
      x=59.9997 时拆法会得到 s=59, ms=1000 → 只把秒+1 变成 60，
      输出 "HH:MM:60,000" —— 非法时间码，部分播放器和 Resolve 直接解析失败。
      总毫秒法一次除到位，天然不会出现 60。
    """
    total = int(round(max(0.0, float(x)) * 1000))
    h, rem = divmod(total, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(blocks, path):
    p = Path(path)
    if p.parent and not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (st, en, tx) in enumerate(blocks, 1):
        lines.append(f"{i}\n{fmt_ts(st)} --> {fmt_ts(en)}\n{tx}\n")
    # ★ 必须原子写：识别还在写 SRT 时，subs_io / land_subtitle 可能已经在读
    #   （worker 用 Popen 拉起脚本，彼此不等待）—— 直接 write_text 会让
    #   对方读到半截文件，导入一堆残句。和 subs_io / worker 的写法保持一致。
    tmp = p.with_name(p.name + ".%d.tmp" % os.getpid())
    tmp.write_text("\n".join(lines), encoding="utf-8-sig")
    os.replace(tmp, p)


# ── 复读 / 异常输出防御 ────────────────────────────────────────────────
class _RepeatStopper:
    """transformers stopping_criteria：盯生成 token 的「周期重复」

    复读是 Qwen3-ASR 在长静音/纯噪声段的典型崩溃模式（"呃呃呃呃…" 一路
    生成到 1024 token 上限，白白烧掉几十秒）。每 32 个新 token 检查一次：
    最近 2p 个 token 若与更早的 p 个完全相同（p∈[8,64]），判定复读，
    立即停生成。纯 Python，不依赖 transformers 的 StoppingCriteria 基类，
    老版本 transformers 也能用（callable 即可）。
    """

    def __init__(self, min_new=96, check_every=32,
                 min_period=8, max_period=64):
        self.min_new = min_new
        self.check_every = check_every
        self.min_period = min_period
        self.max_period = max_period
        self._base = None
        self._ticks = 0

    def __call__(self, input_ids, scores, **kw):
        n = input_ids.shape[-1]
        if self._base is None:
            self._base = n
        self._ticks += 1
        if n - self._base < self.min_new or self._ticks % self.check_every:
            return False
        seq = input_ids[0].tolist()
        for p in range(self.min_period, min(self.max_period, n // 2) + 1):
            if seq[-2 * p:-p] == seq[-p:]:
                return True
        return False


def _looks_anomalous(text):
    """识别结果异常检测：复读残渣 / 协议标记泄漏 / 单字符统治

    transcribe_segment 解码后先过这一关；命中就作废本段（返回空），
    由 _process_segment 走拆分重试，而不是把 "啊啊啊啊啊" 写进字幕。
    """
    t = text.strip()
    if not t:
        return True
    if "asr_text" in t or "<|" in t or "|>" in t:
        return True
    body = t.replace(" ", "").replace("　", "")
    if len(body) < 2:
        return True
    from collections import Counter
    top, cnt = Counter(body).most_common(1)[0]
    if cnt / len(body) >= 0.9:
        return True
    for i in range(len(body) - 9):
        unit = body[i:i + 2]
        if body[i:i + 10] == unit * 5:   # 同一 2 字串连续 5 遍
            return True
    return False


# ── 加载前体检：模型目录对不对、chat 模板接不接得住 ──────────────────────
# ★ 为什么必须在 load() 之前体检（2026-11-01 实测的坑）：
#   安装器的 REPO 写成了 qwen_asr 原版仓库 Qwen/Qwen3-ASR-0.6B，落盘目录却叫
#   Qwen3-ASR-0.6B-hf。原版仓库的 config.json 把 text_config/audio_config 藏在
#   thinker_config 里、权重键前缀是 thinker.*，而 transformers 要的是扁平 config
#   + model.*。结果 from_pretrained【一声不响】：几百个张量全判 MISSING、按默认
#   结构随机初始化，每个片段都"跑成功"却输出全空，界面最后报
#   「未识别到语音内容（时间线可能没有声音）」—— 排查方向被指到音频上，
#   而实测那段音频峰值 0 dBFS、RMS -24 dBFS，根本没毛病。
#   下面两个函数把「布局不对」和「模板不对」这两种"不报错但全空"的情况
#   在开始识别之前拦下来，并且只说人话：该重装哪个东西。
_WEIGHT_PREFIX = "model."          # transformers 侧 Qwen3ASR 的参数键前缀

# 内置官方模板：与 Qwen3-ASR-0.6B-hf / Qwen3-ForcedAligner-0.6B-hf 仓库里的
# chat_template.jinja 逐字一致。只在模型目录缺模板、或模板接不住 transformers
# 的「assistant 预填」请求时兜底 —— 不替代模型自带的模板。
_FALLBACK_ASR_TEMPLATE = (
    "{%- set ns = namespace(system_text='') -%}{%- for m in messages -%}{%- if m.role == 'system' -%}{%- if m.content is string -%}{%- set ns.system_text = ns.system_text + m.content -%}{%- else -%}{%- for c in m.content -%}{%- if c.type == 'text' and (c.text is defined) -%}{%- set ns.system_text = ns.system_text + c.text -%}{%- endif -%}{%- endfor -%}{%- endif -%}{%- endif -%}{%- endfor -%}{%- set ns2 = namespace(audio_tokens='') -%}{%- for m in messages -%}{%- if m.content is not string -%}{%- for c in m.content -%}{%- if c.type == 'audio' or ('audio' in c) or ('audio_url' in c) -%}{%- set ns2.audio_tokens = ns2.audio_tokens + '<|audio_start|><|audio_pad|><|audio_end|>' -%}{%- endif -%}{%- endfor -%}{%- endif -%}{%- endfor -%}{{- '<|im_start|>system\n' + ns.system_text + '<|im_end|>\n' -}}{{- '<|im_start|>user\n' + ns2.audio_tokens + '<|im_end|>\n' -}}{%- for m in messages -%}{%- if m.role == 'assistant' -%}{%- set ns3 = namespace(assistant_text='') -%}{%- if m.content is string -%}{%- set ns3.assistant_text = m.content -%}{%- else -%}{%- for c in m.content -%}{%- if c.type == 'text' and (c.text is defined) -%}{%- set ns3.assistant_text = ns3.assistant_text + c.text -%}{%- endif -%}{%- endfor -%}{%- endif -%}{{- '<|im_start|>assistant\n' -}}{% generation %}{{- ns3.assistant_text + '<|im_end|>\n' -}}{% endgeneration %}{%- endif -%}{%- endfor -%}{%- if add_generation_prompt -%}{{- '<|im_start|>assistant\n' -}}{%- endif -%}"
)

_FALLBACK_ALIGN_TEMPLATE = (
    "{%- set ns = namespace(audio_tokens='', words=[]) -%}{%- for m in messages -%}{%- if m.content is not string -%}{%- for c in m.content -%}{%- if c.type == 'audio' or ('audio' in c) or ('audio_url' in c) -%}{%- set ns.audio_tokens = ns.audio_tokens + '<|audio_start|><|audio_pad|><|audio_end|>' -%}{%- endif -%}{%- if c.type == 'text' and (c.text is defined) -%}{%- set ns.words = ns.words + [c.text] -%}{%- endif -%}{%- endfor -%}{%- endif -%}{%- endfor -%}{{- ns.audio_tokens + ns.words | join('<timestamp><timestamp>') + '<timestamp><timestamp>' -}}"
)


def _safetensors_header(path):
    """只读 safetensors 的 JSON 头（不碰几个 GB 的权重本体）。"""
    import struct
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if not (0 < n <= (64 << 20)):
            raise ValueError("头部长度异常（%d 字节）" % n)
        return json.loads(f.read(n))


def check_model_layout(model_dir):
    """模型目录能不能被当前 transformers 正确加载 → (ok, 原因)

    两条都必须成立，缺一条就是「加载成功、识别全空」：
      ① config.json 的 text_config / audio_config 在【顶层】。原版仓库塞在
         thinker_config 下，transformers 认不出来 → 子模型全走默认结构。
      ② 权重键前缀是 model.*。原版仓库是 thinker.*，一个都对不上 → 全部
         张量按"缺失"处理，等于随机初始化。
    """
    d = Path(model_dir)
    cfg = d / "config.json"
    if not cfg.is_file():
        return False, "缺少 config.json"
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        return False, "config.json 读不出来（%s）" % e
    if not isinstance(c, dict) or not ("text_config" in c and "audio_config" in c):
        if isinstance(c, dict) and "thinker_config" in c:
            return False, ("这是 qwen_asr 原版仓库的结构（参数藏在 thinker_config 里），"
                           "transformers 认不出顶层结构")
        return False, "config.json 里没有顶层的 text_config / audio_config"

    w = d / "model.safetensors"
    if not w.is_file():
        # 允许分片权重（model-00001-of-00002.safetensors 这类）：只看第一片
        # 的键前缀就够判断命名体系。安装器下的是单文件，这里是为自定义模型目录。
        shards = sorted(d.glob("*.safetensors")) if d.is_dir() else []
        if not shards:
            return False, "缺少 model.safetensors"
        w = shards[0]
    try:
        head = _safetensors_header(w)
    except (OSError, ValueError) as e:
        return False, "model.safetensors 头部损坏（%s）" % e
    prefixes = {str(k).split(".")[0] for k in head if k != "__metadata__"}
    if "thinker" in prefixes:
        return False, ("权重是 qwen_asr 原版命名（thinker.*），当前 transformers 要的是 "
                       "model.* —— 一个张量都载不进去")
    if "model" not in prefixes:
        return False, "权重里找不到 model.* 参数（见到的是 %s）" % "/".join(sorted(prefixes)[:4])
    return True, ""


def _template_probe(proc):
    """模板接不接得住「assistant 预填」的转录请求 → (ok, 原因)

    ★ transformers 的 apply_transcription_request 把 language <LANG><asr_text>
      当成最后一条 assistant 消息、用 continue_final_message=True 渲染。模板若
      只渲染 system/user 两条（原版仓库那份 chat_template.json 就是），每个
      片段都会抛 ValueError: continue_final_message is set but the final
      message does not appear... —— 外层再逐个 except 掉，最后只剩"0 条字幕"。
      用 1 秒静音探一次最省事：只走模板和特征提取，不跑模型。
    """
    import numpy as np
    try:
        proc.apply_transcription_request(
            audio=np.zeros(16000, dtype=np.float32), language=["Chinese"], prompt=[""])
        return True, ""
    except Exception as e:      # noqa: BLE001 —— 任何异常都说明这条路走不通
        return False, "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:160])


def ensure_transcription_template(proc, log):
    """模板不能用就换成内置官方版 → 是否换过"""
    ok, why = _template_probe(proc)
    if ok:
        return False
    log("⚠ 模型目录里的 chat 模板接不住 transformers 的转录请求（%s）" % why)
    log("  改用内置官方模板（与 -hf 仓库的 chat_template.jinja 一致）")
    proc.chat_template = _FALLBACK_ASR_TEMPLATE
    ok2, why2 = _template_probe(proc)
    if not ok2:
        raise RuntimeError("换成内置模板后仍然不行：%s" % why2)
    return True


def ensure_aligner_template(proc, log):
    """对齐模板必须把待对齐的字词渲染进去 → 是否换过

    ★ 探针只用【一个字】：中文是按字切词的，官方模板用 <timestamp><timestamp>
      把逐字连起来，用"测试"这种词去查会永远查不到（那是模板正确时的正常
      样子），白白把好模板判成坏的。
    """
    import numpy as np

    def _probe():
        try:
            inp, _w = proc.prepare_forced_aligner_inputs(
                audio=np.zeros(16000, dtype=np.float32),
                transcript="测", language="Chinese")
        except Exception as e:  # noqa: BLE001
            return False, "%s: %s" % (type(e).__name__, str(e).splitlines()[0][:160])
        if "测" not in proc.tokenizer.decode(inp["input_ids"][0]):
            return False, "模板把待对齐的字词丢了"
        return True, ""

    ok, why = _probe()
    if ok:
        return False
    log("⚠ 对齐模型的 chat 模板不对（%s）" % why)
    log("  改用内置官方模板（与 Qwen3-ForcedAligner-0.6B-hf 的 chat_template.jinja 一致）")
    proc.chat_template = _FALLBACK_ALIGN_TEMPLATE
    ok2, why2 = _probe()
    if not ok2:
        raise RuntimeError("对齐模板换成内置版后仍然不行：%s" % why2)
    return True


# ── 核心引擎 ────────────────────────────────────────────────────────────
class Engine:
    def __init__(self, log=print, asr_model=None):
        self.log = log
        self.asr_proc = None
        self.asr_model = None
        self.align_proc = None
        self.align_model = None
        self.align_timestamp_token_id = 0
        self.device = "cpu"
        self.dtype = None          # load() 里按 cuda/cpu 定；float32 兜底
        self.asr_path, self.aligner_path = resolve_models(asr_model)

        # 对齐统计：跑完在日志里报一次，便于判断对齐到底有没有生效
        self.aligned_segments = 0   # 成功走词语级对齐的片段数
        self.retried_segments = 0   # 因质量不合格触发拆分重试的片段数
        self.fallback_segments = 0  # 最终降级为按字数比例估算的片段数
        # ★ v4.8.21：长停顿断行实际切出的行数（设置页开关；0 = 没开或没切）
        self._split_lines = 0
        # 字幕挂在"没有人发声"的时间上的累计秒数（用对齐词表算，跨片段累加）
        self._overhang = 0.0
        # ★ 逐片段失败统计。以前 except 只打一行日志就把片段丢掉，13 个片段
        #   全挂时外层只知道"0 条字幕"，于是报成"时间线可能没有声音"——
        #   真正的原因（模板/模型不对）被埋在日志中段。现在把失败数和首个
        #   错误带回 stats，bridge 才能如实报错。
        self.failed_segments = 0
        self.first_error = ""

        # 断点续跑缓存（run() 里按音频指纹 + 模型/语言/提示词签名装载）
        self._seg_cache = None
        self._cache_path = None
        self._cache_dirty = False
        self.cached_segments = 0    # 本次命中缓存的片段数

        # ★ 实际用的是哪个模型、以及为什么 —— 一路带回给界面。
        #   自定义路径无效时这里会记下原因，bridge 把它写进状态文件，
        #   用户就能看见"我填的路径没用上，实际跑的是 XXX"，而不是干瞪眼。
        self.model_note = ""
        if (asr_model or "").strip():
            ok, why = check_asr_dir(asr_model)
            if ok:
                self.model_note = str(self.asr_path)
            else:
                self.model_note = ("⚠ 自定义模型路径无效（%s），已改用默认 %s"
                                   % (why, self.asr_path.name))

    def load(self):
        # ★ 先体检模型目录：布局不对时下面的 from_pretrained 不会报错，只会
        #   悄悄加载成随机权重（见 check_model_layout 的说明）。宁可在开始
        #   识别之前失败，也不要让用户拿"0 条字幕"去怀疑音频。
        ok, why = check_model_layout(self.asr_path)
        if not ok:
            raise RuntimeError(
                "识别模型目录和当前 transformers 对不上：%s\n"
                "目录：%s\n"
                "这份权重不会报错，只会把每个片段识别成空 —— "
                "请用安装器重装模型（装对的文件会跳过，只补不对的）。"
                % (why, self.asr_path))
        import torch
        # ★ v2.0.0：Qwen3ASR 两个类不是每个 transformers 版本都有。装错版本时
        #   原来只会甩一个裸 ImportError，用户完全看不出是版本问题（安装器
        #   那边全绿）。这里换成能照做的说明。
        try:
            from transformers import (
                AutoProcessor,
                Qwen3ASRForConditionalGeneration,
                Qwen3ASRForTokenClassification,
            )
        except ImportError as e:
            import transformers as _tf
            raise RuntimeError(
                "当前 transformers %s 里没有 Qwen3ASR 模型类（%s）。\n"
                "需要含 Qwen3-ASR 的版本，请执行（用同一个解释器）：\n"
                "  \"%s\" -m pip install -U "
                "\"transformers>=5.14\"\n"
                "仍不行就用安装器重装（安装器会自动校验并回退到可用版本）。"
                % (getattr(_tf, "__version__", "?"), e, sys.executable))

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # ★ dtype 必须存下来给 _to_device 用。以前那里【硬编码 bfloat16】，
        #   CPU 上大量算子不支持 bf16，generate() 直接 RuntimeError ——
        #   实测 CPU 模式 5 个片段全挂、0 条字幕。CUDA 才用 bf16。
        self.dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        dtype = self.dtype
        self.log(f"设备: {self.device.upper()}" +
                 (f"  {torch.cuda.get_device_name(0)}" if self.device == "cuda" else ""))

        self.log("识别模型: %s" % self.asr_path.name)
        self.log("模型目录: %s" % self.asr_path)
        if self.model_note:
            self.log(self.model_note)
        t0 = time.time()
        self.asr_proc = AutoProcessor.from_pretrained(str(self.asr_path))
        # ★ 模板不对同样"不报错、全空"：见 ensure_transcription_template。
        #   模型自带模板能用就一个字节都不动。
        if ensure_transcription_template(self.asr_proc, self.log):
            self.log("  （识别模板已回落到内置官方版，识别结果不受影响）")
        self.asr_model = Qwen3ASRForConditionalGeneration.from_pretrained(
            str(self.asr_path), dtype=dtype).to(self.device).eval()
        self.log(f"  完成 ({time.time() - t0:.1f}s)")

        # ★ 时间对齐模型：字幕时间戳由它做词语级对齐（见文件顶部 USE_ALIGNER）。
        #   加载失败会自动跳过，run() 里退回按字数比例估算，功能不中断。
        self.align_model = None
        if USE_ALIGNER:
            self.log(f"对齐模型: {self.aligner_path.name}")
            t0 = time.time()
            try:
                aok, awhY = check_model_layout(self.aligner_path)
                if not aok:
                    raise RuntimeError("目录对不上：%s" % awhY)
                self.align_proc = AutoProcessor.from_pretrained(str(self.aligner_path))
                if ensure_aligner_template(self.align_proc, self.log):
                    self.log("  （对齐模板已回落到内置官方版）")
                self.align_model = Qwen3ASRForTokenClassification.from_pretrained(
                    str(self.aligner_path), dtype=dtype).to(self.device).eval()
                # 解时间戳要用到；不同版本可能取不到该字段，取不到就留 0
                self.align_timestamp_token_id = int(
                    getattr(self.align_model.config, "timestamp_token_id", 0) or 0)
                self.log(f"  完成 ({time.time() - t0:.1f}s)")
            except Exception as e:
                self.align_model = None
                # ★ 以前只打 type(e).__name__，"RuntimeError" 这种根本没法排查
                self.log(f"  跳过（将用比例估算时间轴）: {type(e).__name__}: {str(e)[:160]}")
        else:
            self.log("对齐模型: 已关闭（按字数比例估算时间轴）")

    def _to_device(self, inputs):
        import torch
        # ★ 用 load() 定下的 dtype，不能写死 bfloat16（CPU 上会 RuntimeError）
        dtype = getattr(self, "dtype", None) or torch.float32
        out = {}
        for k, v in inputs.items():
            if hasattr(v, "to"):
                out[k] = v.to(self.device, dtype) if v.is_floating_point() else v.to(self.device)
            else:
                out[k] = v
        return out

    def transcribe_segment(self, audio, sr, lang, prompt=""):
        """识别一个片段 → (纯文本, 检测到的语言或 None)

        ★ lang 先过 normalize_language()：界面上的「自动识别」在这里变成
          None —— 这才是 transformers 认可的自动检测写法（传 "Auto" 会抛
          ValueError: Unsupported language: 'Auto'）。
        ★ 自动检测时模型会自己输出 `language XXX<asr_text>` 前缀，用官方
          parse_output() 剥掉，否则前缀会被原样写进字幕。
        """
        language = normalize_language(lang)
        inputs = self.asr_proc.apply_transcription_request(
            audio=audio, language=[language], prompt=[prompt])
        inputs = self._to_device(inputs)
        stopper = _RepeatStopper()
        try:
            out = self.asr_model.generate(**inputs, max_new_tokens=1024,
                                          stopping_criteria=[stopper])
        except TypeError:
            # 老版本 transformers 不认 stopping_criteria 关键字时的退路
            out = self.asr_model.generate(**inputs, max_new_tokens=1024)
        gen = out[:, inputs["input_ids"].shape[1]:]
        texts = self.asr_proc.tokenizer.batch_decode(gen, skip_special_tokens=True)
        raw = (texts[0] if texts else "").strip()
        if _looks_anomalous(raw):
            self.log("  检测到复读/异常输出（%s…），本段作废" % raw[:24])
            return "", None
        return parse_asr_output(self.asr_proc, raw)

    def align_segment(self, audio, text, lang):
        """对本片段做词语级对齐 → [(词, 开始秒, 结束秒), ...]；失败返回 None

        走官方三步：prepare_forced_aligner_inputs → 模型 forward →
        decode_forced_alignment。返回的时间是【相对本片段】的秒数。
        """
        if self.align_model is None or self.align_proc is None:
            return None
        if not text.strip() or lang not in ALIGNER_LANGS:
            return None
        import torch
        try:
            with torch.inference_mode():
                inputs, word_lists = self.align_proc.prepare_forced_aligner_inputs(
                    audio=audio, transcript=text, language=lang)
                inputs = self._to_device(inputs)
                out = self.align_model(**inputs)
            logits = out.logits if hasattr(out, "logits") else out[0]
            items = self.align_proc.decode_forced_alignment(
                logits, inputs["input_ids"], word_lists,
                timestamp_token_id=self.align_timestamp_token_id)
            words = items[0] if items else []
            return [(w.get("text", ""), float(w["start_time"]), float(w["end_time"]))
                    for w in words
                    if "start_time" in w and "end_time" in w]
        except Exception as e:
            self.log("  对齐失败（本段改用比例估算）: %s: %s"
                     % (type(e).__name__, str(e)[:160]))
            return None

    def _timed(self, sents, words, st, en, split_pause=0.0, max_chars=0):
        """切好的行 → 带时间戳的块（词级对齐优先，没有就按字数比例摊）。

        ★ 缓存命中和新识别都走这里，长停顿断行也在这里做一次，两条路结果一致。
        ★ v4.8.24：在断行之后补两道观感修补 —— 先给过长的条目再切一刀，
          再把不足 0.4 秒的碎片并进前一条。两刀都放在这里，缓存命中同样生效。
          max_chars 用来算列宽上限，避免"并条把行拼回 9~12 字"（8 字档实测踩过）。
        """
        max_width = max_chars * 2 if max_chars and max_chars > 0 else 0
        if words:
            blocks = assign_times_by_words(sents, words, st)
            if split_pause and split_pause > 0:
                blocks, n = split_at_long_pauses(blocks, words, st, split_pause,
                                                max_chars)
                self._split_lines += n
            blocks, n2 = resplit_overlong_blocks(blocks, words, st)
            self._resplit_lines = getattr(self, "_resplit_lines", 0) + n2
            blocks, n3 = merge_tiny_blocks(blocks, max_width=max_width)
            self._merged_lines = getattr(self, "_merged_lines", 0) + n3
            # ★ 必须在断行【之后】统计，否则开了开关数字也不动
            self._overhang += self._word_overhang(blocks, words, st)
            return blocks
        blocks = distribute_timing(sents, st, en)
        blocks, n2 = resplit_overlong_blocks(blocks, None, st)
        self._resplit_lines = getattr(self, "_resplit_lines", 0) + n2
        blocks, n3 = merge_tiny_blocks(blocks, max_width=max_width)
        self._merged_lines = getattr(self, "_merged_lines", 0) + n3
        return blocks

    def _word_overhang(self, blocks, words, seg_start):
        """块里有多少秒完全没有词覆盖 —— 字幕挂在静音上的量。

        ★ 用对齐器给的词时间算，不用 VAD：VAD 段本身就把停顿算进去了，
          拿它当分母会算出负值（实测 -10.8s），反而误导。
        """
        iv = [(seg_start + float(w[1]), seg_start + float(w[2])) for w in words]
        total = 0.0
        for st, en, _t in blocks:
            covered = 0.0
            for a, b in iv:
                lo, hi = max(a, st), min(b, en)
                if hi > lo:
                    covered += hi - lo
            total += max(0.0, (en - st) - covered)
        return total

    def _blocks_from_text(self, text, words, st, en, align_lang, max_chars,
                          strip_all_punct, trim_punct, split_pause=0.0):
        """原文 → 字幕块（切句 + 分配时间）。

        缓存命中和新识别共用这一段：缓存里存的是【原文 + 词级对齐】，
        所以用户改「每行字数」「去标点」这些纯排版选项时，重跑会命中缓存
        但仍然按新选项重新切行 —— 既省时间又不会拿旧排版凑数。
        ★ v4.8.24：缓存里带着词级对齐，就把词边界传给切句，折行断在词上。
        """
        sents = split_sentences(text, max_chars,
                                word_cuts=_word_char_cuts(text, words))
        if strip_all_punct:
            sents = drop_all_punct(sents)
        elif trim_punct:
            sents = strip_tail_punct(sents)
        if not sents:
            return []
        return self._timed(sents, words, st, en, split_pause, max_chars)

    def _cache_put(self, key, text, align_lang, words):
        if not key or self._seg_cache is None or not text:
            return
        self._seg_cache[key] = {
            "t": text,
            "l": align_lang or "",
            "w": [[str(w[0]), round(float(w[1]), 3), round(float(w[2]), 3)]
                  for w in (words or [])],
        }
        self._cache_dirty = True

    def _process_segment(self, audio, sr, st, en, lang, prompt, max_chars,
                         strip_all_punct, trim_punct, depth=0, split_pause=0.0):
        """处理单个语音片段 → 字幕块列表

        优先走 ForcedAligner 词语级对齐。对齐质量不合格时，把片段
        【一分为二】重新识别并对齐（即「空洞检测重试」，只重试一层），
        两半都拿不到合格结果才降级为按字数比例估算。

        ★ v2.0.0：只在 depth == 0（真正的顶层片段）读写分段缓存。
          拆分出来的子片段不单独缓存 —— 它们的区间是临时算的，
          下次运行未必还是这个切法，缓存了也用不上。
        """
        import numpy as np

        seg = audio[int(st * sr): int(en * sr)]
        if len(seg) < sr * 0.1:
            return []
        seg_dur = len(seg) / sr

        # ★ v4.8.23：电平极低的片段不进模型。VAD 已经尽量挡了，但音乐、
        #   电流声这类"有能量没人声"的段仍会漏进来，模型就会编字。
        seg_rms = float((seg.astype("float64") ** 2).mean()) ** 0.5 if len(seg) else 0.0
        if seg_rms > 0 and 20.0 * np.log10(max(seg_rms, 1e-12)) < -52.0:
            self.log("  片段 %.1f~%.1fs 电平 %.0f dBFS，判为无语音（跳过）"
                     % (st, en, 20.0 * np.log10(max(seg_rms, 1e-12))))
            return []

        # ── 断点续跑：命中上次跑过的同一片段 ──
        key = None
        if depth == 0 and self._seg_cache is not None:
            key = "%.2f-%.2f" % (round(st, 2), round(en, 2))
            hit = self._seg_cache.get(key)
            if isinstance(hit, dict) and hit.get("t"):
                self.cached_segments += 1
                words = [tuple(w) for w in (hit.get("w") or [])
                         if isinstance(w, (list, tuple)) and len(w) == 3]
                return self._blocks_from_text(
                    hit["t"], words, st, en, hit.get("l"), max_chars,
                    strip_all_punct, trim_punct, split_pause)

        text, detected = self.transcribe_segment(seg, sr, lang, prompt)
        if not text:
            # 空结果（含复读/异常作废）：也走一次拆分重试 —— 两半各自
            # 有机会出正常结果；重试仍空才真正放弃这个片段。
            if depth == 0 and (en - st) >= 1.0:
                self.retried_segments += 1
                self.log("  片段 %.1f~%.1fs 无有效文本，拆分重试" % (st, en))
                mid = (st + en) / 2.0
                parts = []
                for a, b in ((st, mid), (mid, en)):
                    parts.extend(self._process_segment(
                        audio, sr, a, b, lang, prompt, max_chars,
                        strip_all_punct, trim_punct, depth + 1, split_pause))
                return parts
            return []

        # 对齐用的语言：自动检测时以模型报出来的为准（它才准）
        align_lang = normalize_language(lang)
        if detected in ALIGNER_LANGS:
            align_lang = detected

        sents = split_sentences(text, max_chars)
        if strip_all_punct:
            sents = drop_all_punct(sents)
        elif trim_punct:
            sents = strip_tail_punct(sents)
        if not sents:
            return []

        if USE_ALIGNER and align_lang in ALIGNER_LANGS:
            words = self.align_segment(seg, text, align_lang)
            ok, why = check_alignment(words, seg_dur) if words else (False, "没有结果")
            if ok:
                self.aligned_segments += 1
                self._cache_put(key, text, align_lang, words)
                # ★ v4.8.24：拿到词边界后按词边界【重切一次】句子。
                #   上面那次切句发生在对齐之前，只能用"标点/原子边界"，
                #   于是出现「我代 / 表云岭」「中秋祝 / 福。」这类词内断开。
                #   重切只是纯文本运算，代价可以忽略。
                sents2 = split_sentences(text, max_chars,
                                         word_cuts=_word_char_cuts(text, words))
                if strip_all_punct:
                    sents2 = drop_all_punct(sents2)
                elif trim_punct:
                    sents2 = strip_tail_punct(sents2)
                return self._timed(sents2 or sents, words, st, en, split_pause,
                                    max_chars)

            # ── 空洞检测重试：一分为二，各自重新识别 + 对齐 ──
            if depth == 0 and (en - st) >= 1.0:
                self.retried_segments += 1
                self.log("  片段 %.1f~%.1fs 对齐不合格（%s），拆分重试" % (st, en, why))
                mid = (st + en) / 2.0
                parts = []
                for a, b in ((st, mid), (mid, en)):
                    parts.extend(self._process_segment(
                        audio, sr, a, b, lang, prompt, max_chars,
                        strip_all_punct, trim_punct, depth + 1, split_pause))
                if parts:
                    return parts
            else:
                self.log("  片段 %.1f~%.1fs 对齐不合格（%s），改用比例估算" % (st, en, why))

        self.fallback_segments += 1
        self._cache_put(key, text, align_lang, None)
        return self._timed(sents, None, st, en, split_pause, max_chars)

    def run(self, path, lang="Chinese", prompt="", max_chars=12,
            remove_gaps=False, trim_punct=False, strip_all_punct=False,
            progress=None, min_dur=0.04, split_pause=0.0):
        import torch

        # ★ v4.8.21：split_pause 允许两种写法 —— True/False（设置页开关）
        #   或秒数（0 = 关）。统一成秒，后面只用 limit。
        limit = PAUSE_SPLIT_SEC if split_pause is True else float(split_pause or 0.0)

        t_start = time.time()
        audio, sr = extract_audio(path)
        dur = len(audio) / sr
        self.log(f"音频时长 {dur:.1f} 秒")

        segs = detect_speech(audio, sr)
        self.log("检测到 %d 个语音片段（%s）"
                 % (len(segs), "silero-vad" if silero_available() else "能量阈值"))

        # ── 断点续跑：装载上次的分段缓存 ──
        #   签名 = 音频指纹 + 模型 + 语言 + 提示词。任一项变了就整份作废，
        #   绝不允许拿旧模型的识别结果冒充新模型的。
        self._seg_cache, self._cache_path = _load_seg_cache(
            path, self.asr_path, lang, prompt)
        if self._seg_cache:
            self.log("发现上次的分段缓存（%d 段），可续跑" % len(self._seg_cache))
        self._cache_dirty = False
        self.cached_segments = 0
        self.failed_segments = 0
        self.first_error = ""
        self._split_lines = 0
        self._resplit_lines = 0     # v4.8.24：长条再切了一刀的次数
        self._merged_lines = 0      # v4.8.24：碎片并进前一条的次数
        self._overhang = 0.0
        # ★ 同一实例连跑两次时，这三个统计以前会累加（日志里出现"24 段对齐"）
        self.aligned_segments = 0
        self.retried_segments = 0
        self.fallback_segments = 0

        blocks = []
        ctx = ""          # 跨窗上下文粘滞：前一窗已接受文本的尾部
        # ★★ v2.0.7：CTX_CHARS 由 120 改成 0 —— 关掉跨窗上下文。
        #    这个"上下文"本质就是 prompt（拼进 seg_prompt 传给模型），而用户
        #    实测 prompt 会被模型当正文吐出来：每一窗开头重复上一窗的尾巴，
        #    表现出来正是「提示词落在字幕里、位置还不对」。
        #    关掉的代价：跨窗的人名/数字写法可能不一致（同一名字两窗两种写法）。
        #    真要恢复，把这里改回 120 —— 但注意下面的切片必须走 `CTX_CHARS > 0`
        #    的判断，因为 Python 里 `s[-0:]` 等于 `s[0:]`（整个字符串），
        #    直接把 0 塞进切片会变成"每窗都把上一窗全文当 prompt"。
        CTX_CHARS = 0
        for idx, (st, en) in enumerate(segs, 1):
            if progress:
                progress(idx, len(segs))
            # 把上一窗的文本尾部拼进 prompt：模型看到前文，跨窗处的用词、
            # 人名、数字写法才能保持一致（否则同一名字在两窗里两种写法）。
            seg_prompt = prompt
            if ctx:
                seg_prompt = (prompt + " " if prompt else "") + ctx
            try:
                blocks.extend(self._process_segment(
                    audio, sr, st, en, lang, seg_prompt, max_chars,
                    strip_all_punct, trim_punct, split_pause=limit))
            except Exception as e:
                # ★ 只打 type(e).__name__ 会把真正的错误信息吞掉。
                #   CPU 上 5 个片段全挂时只显示"RuntimeError"，根本没法排查。
                self.failed_segments += 1
                if not self.first_error:
                    self.first_error = "%s: %s" % (type(e).__name__, e)
                self.log(f"  片段 {idx} 失败: {type(e).__name__}: {e}"[:300])
                continue
            # 更新粘滞上下文（失败片段沿用上一窗的，不更新）
            # ★ v2.0.7：必须判 CTX_CHARS > 0 —— Python 的 s[-0:] 是 s[0:]
            #   （整个字符串），写成无条件切片会在 CTX_CHARS=0 时把上一窗
            #   全文当 prompt 塞进去，正好是我们要避免的事。
            if blocks and CTX_CHARS > 0:
                ctx = str(blocks[-1][2]).strip()[-CTX_CHARS:]
            # 断点续跑：每 10 段落一次盘。不能等全部跑完才写 ——
            # 中途断电/显存炸了正是最需要缓存的时候。
            if self._cache_dirty and idx % 10 == 0:
                _save_seg_cache(self._cache_path, path, self.asr_path,
                                lang, prompt, self._seg_cache, dur)
                self._cache_dirty = False

        if self._cache_dirty:
            _save_seg_cache(self._cache_path, path, self.asr_path,
                            lang, prompt, self._seg_cache, dur)
            self._cache_dirty = False
        if self._cache_path:
            _seg_cache_prune()
        if self.cached_segments:
            self.log("断点续跑：%d 段直接复用上次结果（共 %d 段）"
                     % (self.cached_segments, len(segs)))

        if USE_ALIGNER:
            self.log("对齐结果：%d 段词语级对齐，%d 段拆分重试，%d 段降级为比例估算"
                     % (self.aligned_segments, self.retried_segments,
                        self.fallback_segments))

        # ★ 有片段抛异常就必须说出来。"片段失败"N 行 + "完成 0 条字幕" 之间
        #   隔着几十行对齐日志，用户最先看到的是最后那句"没有声音"。
        if self._split_lines:
            self.log("长停顿断行：多切出 %d 行（块内静音 ≥%.2fs）"
                     % (self._split_lines, limit))

        if self.failed_segments:
            self.log("⚠ %d/%d 个片段识别失败，首个错误：%s"
                     % (self.failed_segments, len(segs), self.first_error[:160]))

        # ★ 兜底：绝不能出现空字幕块（Resolve 里会显示一个空的字幕 Box）。
        #   极短片段偶尔会被识别成一串纯标点，切完句就被清掉了，但时间戳已经
        #   排好 —— 实测 121 条里就有 2 条这种空块，一起写进 SRT 会造成脏数据。
        # ★ v4.8.24：整条只有一个「。」的块也要处理（它 strip 后不是空串，
        #   原来的过滤拦不住），跨片段的那种在 split_sentences 里管不到，
        #   这里统一并进前一条。
        blocks, n_punct = merge_punct_only_blocks(blocks, max_width=max_chars * 2)
        self._merged_lines = getattr(self, "_merged_lines", 0) + n_punct

        # ★ v4.8.25：跨片段再并一次碎片。
        #   merge_tiny_blocks 在 _timed 里只处理【片段内部】，而实测那批
        #   0.30 秒的「好，可以。」正好是每个 VAD 片段末尾的降级段，
        #   自成一个片段 → 片段内没有"前一条"可并，于是每条都留了下来
        #   （10 分钟素材里出现了 5 次）。这里在片段之间再收一次。
        blocks, n_tiny = merge_tiny_blocks(blocks, max_width=max_chars * 2)
        self._merged_lines = getattr(self, "_merged_lines", 0) + n_tiny

        # ★ v4.8.24：出口统一兜一次宽度 —— 上游三条路各自判断过，
        #   实测 8 字档仍会漏 2 条 18 列的行。
        blocks, n_refold = refold_overlong_text(blocks, max_chars)
        if n_refold:
            self.log("超宽再折：%d 条超过 %d 字的行在写盘前重新折开"
                     % (n_refold, max_chars))

        # ★★ v1.2.0：行尾标点的【最后一道收口】。
        #   为什么必须有：strip_tail_punct 只在切句之后执行一次（见
        #   _blocks_from_text / _process_segment），而它后面还有三条会重新
        #   切分文本的路径 —— 长条再切（resplit_overlong_blocks）、长停顿
        #   断行（split_at_long_pauses）、超宽再折（refold_overlong_text）。
        #   三条都【优先切在标点之后】，于是「时值中秋佳节，」这种行尾逗号
        #   又被造了出来，而且最后一条正好在写盘前，必然进 SRT。
        #   实测复现：输入「我们今天在这里欢聚一堂，共同庆祝…」8 字档，
        #   上面三条分别产出「…欢聚一堂，」「聚一堂，」「…欢聚一堂，」。
        #   这里不管前面几条路径动过文本，写盘前统一抹一遍，出口一律干净。
        #   与「去除标点符号」不冲突：那条路早把标点删光了，这里只是空转。
        if trim_punct or strip_all_punct:
            swept, n_tail = [], 0
            for st, en, tx in blocks:
                t = _flatten_ws(str(tx or ""))
                t2 = t.rstrip(TRAIL_PUNCT).strip()
                if t2 != t.strip():
                    n_tail += 1
                if t2:
                    swept.append((st, en, t2))
                else:
                    self.log("  丢弃 1 个只剩标点的块（%.2f~%.2fs）" % (st, en))
            blocks = swept
            if n_tail:
                self.log("句末去标点：%d 条行尾标点已抹掉" % n_tail)

        bad = [b for b in blocks if not str(b[2]).strip()]
        if bad:
            self.log("丢弃 %d 个空字幕块（原始文本只剩标点）" % len(bad))
            blocks = [b for b in blocks if str(b[2]).strip()]

        if getattr(self, "_merged_lines", 0):
            self.log("碎片并条：%d 条过短/纯标点的字幕并入前一条"
                     % self._merged_lines)
        if getattr(self, "_resplit_lines", 0):
            self.log("长条再切：%d 条超过 %.1f 秒的字幕按词边界又切了一刀"
                     % (self._resplit_lines, MAX_SHOW_SEC))

        if remove_gaps and blocks:
            # ★★ 以前这里写成 `for b in blocks: b = (...)` —— 循环变量 b 只是
            #    元组的一个副本，改完丢掉，blocks 一行没动 ⇒ "字幕无间隙"
            #    勾了等于没勾。改成真正重建列表。
            # ★★★ 但"重建"必须是【延长结束时间】，不能是【前移开始时间】：
            #    后者把总时长压短（212.6s → 185.9s），越往后偏得越多。
            before = blocks[-1][1]
            shown = sum(e - s for s, e, _ in blocks)
            blocks = fill_gaps(blocks, min_dur=min_dur)
            self.log(
                "无间隙：显示区间 %.1fs → %.1fs，末尾 %.1fs 未变"
                "（只拉长结束时间，开始时间一律不动 → 始终与音频同步）"
                % (shown, sum(e - s for s, e, _ in blocks), before))
        elif min_dur and min_dur > 0.04:
            # 没勾「无间隙」时也要尊重最短显示时长（v2.0.0）
            blocks = enforce_min_duration(blocks, min_dur)

        elapsed = time.time() - t_start
        rtf = elapsed / dur if dur > 0 else 0
        self.log(f"完成 {len(blocks)} 条字幕，耗时 {elapsed:.1f}s（实时系数 {rtf:.2f}）")
        return blocks, {"duration": dur, "elapsed": elapsed, "rtf": rtf,
                        "device": self.device, "segments": len(segs),
                        # ★ 给 bridge 报实话用：0 条字幕到底是"没声音"还是"全挂了"
                        "failed": self.failed_segments,
                        "first_error": self.first_error[:200],
                        # ★ v4.8.21：给界面/诊断看的两个量
                        "split_lines": self._split_lines,
                        # ★ v4.8.24：长条再切 / 碎片并条 的次数
                        "resplit_lines": getattr(self, "_resplit_lines", 0),
                        "merged_lines": getattr(self, "_merged_lines", 0),
                        # 字幕挂在"没有人发声"的时间上的总量（用对齐词表算）
                        "overhang_s": round(self._overhang, 2)}
