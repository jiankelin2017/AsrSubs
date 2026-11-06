# -*- coding: utf-8 -*-
"""
AsrSubs · 安装器
════════════════════════════════════════════════════════════════════════
这个 exe 只有十几 MB，里面【只有脚本】，不含 Python 环境和模型。
运行时按你选的版本把它们下载下来，装成能直接在 DaVinci Resolve 里用的样子。

安装后的目录结构（BASE = 安装位置，默认 D:\\AsrSubs）：

    BASE\\_setup\\                     安装器本体（PyInstaller onedir，装完可删）
    BASE\\engine\\                     引擎（Python 脚本 + AsrSubs.lua + paths.json/lua）
    BASE\\engine\\rt\\                  运行期中间文件（args/status/hb/cmd/日志）
    BASE\\runtime\\Scripts\\python.exe  运行环境（Python 3.12 embed + torch 等）
    BASE\\models\\Qwen3-ASR-0.6B-hf\\          识别模型
    BASE\\models\\Qwen3-ForcedAligner-0.6B-hf\\ 时间对齐模型
    BASE\\data\\output\\                字幕产物
    BASE\\data\\logs\\                  错误日志 / 诊断包
    BASE\\data\\cache\\                 音频 / 分段 / HF 下载缓存
    BASE\\docs\\                        使用说明

★ 这套名字是 Lua 主脚本（01_paths.lua 的 LAYOUT）和 asr_engine.py 认的布局，
  改目录名必须同步改这两处，否则插件能找到界面却找不到引擎。
★ 模型必须【直接躺在 models\\ 下】：find_aligner() 只扫描一层，并按名字里的
  "aligner" 匹配。多套一层 models\\asr\\ 就再也找不到对齐模型。
════════════════════════════════════════════════════════════════════════
"""

import hashlib
import os
import re
import sys
import json
import time
import stat
import shutil
import struct
import queue
import zipfile
import tempfile
import threading
import subprocess
import urllib.request
import urllib.error
import socket
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    tk = None

# ── 版本与地址 ────────────────────────────────────────────────────────
APP_NAME = "AsrSubs"
# ★ 版本号由 tools/sync_version.py 从根目录 version.txt 同步，勿手改
APP_VERSION = "1.2.5"
PY_VER = "3.12.9"
PY_TAG = "3.12.9-amd64"

# Python 用 embeddable 版（免安装、10.6MB、不写注册表、不需要管理员权限）。
# ★ 试过完整安装器 + /quiet TargetDir=... ：实测静默安装【不生效】——
#   它会装到 %LOCALAPPDATA%\Programs\Python 而不是指定目录，退出码还是 0，
#   装完一检查目录是空的。embeddable 是纯解压，没这些坑。
# 顺序按实测连通性排：清华镜像对 embed zip 会 403（HEAD 却返回 200，很迷惑），
# 所以 npmmirror 优先，官方兜底。
# ★ 文件名是 python-3.12.9-embed-amd64.zip —— 两处都用 PY_VER。
#   曾经第二个也填了 PY_TAG(=3.12.9-amd64)，拼成
#   python-3.12.9-amd64-embed-amd64.zip → 全部 404，排查了半天。
PY_SOURCES = [
    ("npmmirror", "https://registry.npmmirror.com/-/binary/python/%s/python-%s-embed-amd64.zip" % (PY_VER, PY_VER)),
    ("官方", "https://www.python.org/ftp/python/%s/python-%s-embed-amd64.zip" % (PY_VER, PY_VER)),
    ("清华镜像", "https://mirrors.tuna.tsinghua.edu.cn/python/%s/python-%s-embed-amd64.zip" % (PY_VER, PY_VER)),
]

# torch：CPU 版 126MB，GPU 版 2.5~3 GB
# ★ P1 供应链加固（2026-11-06）：三个 Python 镜像源实测内容完全一致，
#   python-3.12.9-embed-amd64.zip = 11,098,289 字节，SHA-256 见下。
#   Content-Length 只能防截断，防不了源被投毒；这里给固定的小文件配白名单。
#   ★ 改 PY_VER 时必须重测并更新，否则下面会自动降级为「只留痕不强校验」。
PY_EMBED_SHA256 = "615861fb801e8b04c847598db4e1e46e4b046295017caa37cb5486dde72b5865"
PY_EMBED_SHA256_VER = "3.12.9"

TORCH_CPU = [
    ("上海交大", "https://mirror.sjtu.edu.cn/pytorch-wheels/cpu/"),
    ("官方", "https://download.pytorch.org/whl/cpu"),
]

# ★★ v1.0.1：GPU 通道不能写死 cu128。
#   实测镜像里的版本现状（2026-11-01）：
#     cpu  → 2.15.0+cpu      cu126 → 2.14.1+cu126
#     cu128→ 2.11.0+cu128    cu130 → 2.14.1+cu130
#   写死 cu128 = GPU 用户拿到的是【比 CPU 版低四个小版本】的 torch；
#   而 cu126 对 525+ 驱动全兼容、版本还最新，cu130 则要 580+ 驱动。
#   所以按驱动版本选通道（Blackwell / RTX 50 系必须 12.8+，单独处理）。
TORCH_GPU_MIRROR = "https://mirror.sjtu.edu.cn/pytorch-wheels/%s/"
TORCH_GPU_OFFICIAL = "https://download.pytorch.org/whl/%s"


def gpu_sources(channel):
    return [("上海交大", TORCH_GPU_MIRROR % channel),
            ("官方", TORCH_GPU_OFFICIAL % channel)]


def pick_cuda_channel(driver_version, gpu_name):
    """按驱动版本 / 显卡型号选 CUDA 通道 → (通道名, 下载源列表, 人话说明)。

    · 驱动 < 525：连 12.x 都跑不了，建议 CPU 版或先更新驱动
    · Blackwell（RTX 50 系 / B100/B200）：必须 12.8 以上 ——
      驱动 580+ 用 cu130（2.14.x），否则只能 cu128（2.11.x）
    · 其余：驱动 580+ 用 cu130（版本最新），525~579 用 cu126（兼容最广）
    """
    try:
        major = int(str(driver_version or "").split(".")[0])
    except Exception:
        major = 0
    name = str(gpu_name or "")
    blackwell = ("RTX 50" in name) or name.startswith("B1") or name.startswith("B2")

    if major and major < 525:
        return "cu126", gpu_sources("cu126"), \
            "驱动 %s 太旧（需要 525 以上），装完可能用不了 CUDA" % driver_version
    if blackwell:
        if major >= 580:
            return "cu130", gpu_sources("cu130"), \
                "Blackwell 显卡 + 驱动 %s → CUDA 13.0" % driver_version
        return "cu128", gpu_sources("cu128"), \
            "Blackwell 显卡需要 CUDA 12.8+；驱动 %s 不够新（580 以上更好）" \
            % driver_version
    if major >= 580:
        return "cu130", gpu_sources("cu130"), \
            "驱动 %s → CUDA 13.0（版本最新）" % driver_version
    return "cu126", gpu_sources("cu126"), \
        "驱动 %s → CUDA 12.6（兼容最广）" % (driver_version or "未知")

# 其余依赖走 PyPI 镜像
PYPI = [
    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
    ("官方", "https://pypi.org/simple"),
]

# ★ 仓库名必须带 -hf 后缀（2026-11-01 的"识别失败"就是这里写错了）：
#   两个仓库都有非 -hf 的原版（给 qwen_asr / vLLM 用的）。原版落盘后
#   transformers 能加载但【一个张量都对不上】：config 里参数藏在
#   thinker_config、权重键前缀是 thinker.*，而 transformers 要扁平 config +
#   model.*。它不报错，只把几百个张量判成"缺失"然后随机初始化 —— 表现是
#   每个片段都跑成功、字幕 0 条，界面报"时间线可能没有声音"，指错方向。
#   安装器以前用 Qwen/Qwen3-ASR-0.6B 下进 models\Qwen3-ASR-0.6B-hf，正好踩中。
REPO = "Qwen/Qwen3-ASR-0.6B-hf"
# ★ 时间对齐模型：字幕的【词级时间戳】全靠它。以前安装器只下 ASR，全新机器
#   装完 find_aligner() 找到一个不存在的目录 → 加载失败被 except 吞掉 →
#   静默降级成"按字数比例估算"，而安装器还报"完成"。新装用户因此永远拿不到
#   和开发机一样的时间轴精度。现在两个模型一起装，下漏了要显式报出来。
ALIGNER_REPO = "Qwen/Qwen3-ForcedAligner-0.6B-hf"
# 下载源在 Installer._fetch_repo() 里按仓库名现拼（两个仓库共用同一套镜像）。

# 模型文件：先下小的，再下权重。列表里不存在的（404）自动跳过。
MODEL_SMALL = [
    "config.json", "generation_config.json", "tokenizer.json",
    "tokenizer_config.json", "preprocessor_config.json", "processor_config.json",
    "chat_template.json", "chat_template.jinja", "vocab.json", "merges.txt",
    "special_tokens_map.json", "added_tokens.json",
]
MODEL_WEIGHT = "model.safetensors"

# ★ transformers 不再放这里：它必须经过「导入校验 + 回退来源」，
#   由 Installer.install_transformers() 单独安装（见该类里的说明）。
PY_DEPS = ["numpy", "soundfile", "imageio-ffmpeg", "jieba"]

# ★ v1.0.1：torch 的运行依赖。因为 torch 现在用 --no-deps 单独装（避免 PyPI
#   上的 CPU 版 torch 抢位），这些得自己补上。取自 torch 2.15 的 Requires。
TORCH_DEPS = ["filelock", "fsspec", "jinja2", "networkx", "setuptools",
              "sympy", "typing-extensions"]

UA = {"User-Agent": "Mozilla/5.0"}


# ── 基础工具 ──────────────────────────────────────────────────────────
def parse_base_arg():
    """从命令行取安装位置（Inno Setup 用）。

    Inno 的 [Run] 会把 {app} 作为第一个参数传进来 —— 那时安装目录已经
    由安装包选好并建好了，界面里就不该再问一遍。也支持 --base=路径 手动指定。
    """
    skip_next = False
    for i, a in enumerate(sys.argv[1:]):
        if skip_next:
            skip_next = False
            continue
        if a in ("--base", "/base", "-base"):
            skip_next = True
            continue
        if a.startswith(("--base=", "/base=")):
            return a.split("=", 1)[1]
        if not a.startswith(("/", "-")) and os.path.isdir(a):
            return a
    return None


def payload_dir():
    """exe 里内嵌的脚本目录。开发模式下就是 ./payload"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "payload")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload")


# ── 安装位置：默认值 + 三条硬校验 ────────────────────────────────────
# ★ 为什么路径必须纯 ASCII：Lua 在 Windows 上的 io.open / os.execute 走
#   ANSI 接口，中文路径会变成 "AsrSubs瀛楀箷宸ュ叿" 而打不开。01_paths.lua
#   里的 _isAsciiPath() 检测到非 ASCII 会【主动弃用整条路径】，然后按盘符
#   探测也找不到 —— 症状是界面能开、一点"开始生成字幕"就报"任务超时"。
DEFAULT_BASE = r"D:\AsrSubs"

# DaVinci 菜单目录名。
# ★★ 必须保持纯 ASCII ★★ 这里我原本写的是「AsrSubs字幕工具」，改回来的原因：
#    Resolve 加载 Fusion 脚本走的是 luaL_loadfile / fopen 这条 ANSI stdio，
#    和本插件一直绕着的 io.open 是同一套接口 —— 中文目录下脚本会根本加载
#    不出来，菜单里连项都不出现（比"界面能开但跑不动"更难查）。
#    所以中文一律不进安装路径；也不需要中文别名目录（曾经的
#    <安装目录>字幕工具 junction 已取消，老版本留下会被升级顺手摘掉）。
#    安装目录本身也必须是 ASCII（validate_base 拦）。
MENU_DIR = "AsrSubs"
# 2.x 用的菜单目录名：升级时装新目录后必须清掉，否则菜单里会同时出现两项
#
# ★★ 这里曾经也是 "AsrSubs"（去标识化时把 2.x 旧名一起替换掉了），后果是
#    _drop_legacy_menu_dir 把刚写好的新菜单当成老菜单 rmtree 掉 —— 装完
#    Resolve「脚本」菜单里根本没有 AsrSubs，而且脚本自己日志还写着
#    「已清理 2.x 菜单目录」，看着像成功。test_migrate 的「菜单只写一份」
#    用例就是被这个坑挂掉的。
#    2.x 的菜单名是 Qwen ASR，必须与 MENU_DIR 保持不同。
LEGACY_MENU_DIR = "Qwen ASR"


def _is_reparse(p):
    """是不是重解析点（junction / 符号链接）。

    ★ 不能用 os.path.islink()：Windows 的 junction 在 CPython 里
      st_mode 不带 S_IFLNK，islink 返回 False（实测 3.10/3.12 都这样），
      只有 st_file_attributes 里的 FILE_ATTRIBUTE_REPARSE_POINT 才准。
      判错的后果：摘旧版中文门面时会以为"这是用户自己的真实目录"，于是
      永远删不掉；2.x 迁移时也会漏掉"models 是指向真身的 junction"。
    """
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if getattr(st, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        return True
    return os.path.islink(p)


def _drives():
    """C: ～ Z:（迁移探测用；不做任何 I/O，纯字符串）"""
    return [chr(i) + ":" for i in range(ord("C"), ord("Z") + 1)]


def find_legacy_root():
    """找 2.x 的安装根（<盘>\\AsrSubs\\engine\\resolve_bridge.py）。

    ★ 只认"引擎标记文件存在"的目录：光有 legacy 这个文件夹不能说明是
      本工具装的（用户可能自己建了个同名目录）。
    """
    for d in _drives():
        root = os.path.join(d + "\\", "legacy")
        if os.path.isfile(os.path.join(root, "engine", "resolve_bridge.py")):
            return root
    return None


def _has_python(venv_dir):
    return os.path.isfile(os.path.join(venv_dir, "Scripts", "python.exe"))


def _remove_tree(p):
    """删一棵树，但 junction / 符号链接只摘链接，绝不跟进去删目标。"""
    try:
        if _is_reparse(p):
            os.rmdir(p)
        else:
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _tree_stats(p, skip=()):
    """(文件数, 总字节)。迁移前的复制校验用，跳过重解析点避免重复计数。"""
    n = 0
    size = 0
    skip = set(skip or ())
    for root, dirs, files in os.walk(p):
        if os.path.normcase(root) == os.path.normcase(p):
            dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            fp = os.path.join(root, f)
            try:
                if _is_reparse(fp):
                    continue
                size += os.path.getsize(fp)
                n += 1
            except OSError:
                continue
    return n, size


def _ignore_reparse(directory, names):
    """copytree 的 ignore：junction / 符号链接一律不跟（否则会拷目标或死循环）。"""
    out = []
    for n in names:
        if _is_reparse(os.path.join(directory, n)):
            out.append(n)
    return out


def default_base():
    """默认装到「空间最大的本地固定盘」的 \\AsrSubs。

    ★ 与 AsrSubs.iss 的 PickBestDrive() 预填逻辑保持一致（两边都要改）：
      枚举 C:~Z:，只认 GetDriveTypeW == DRIVE_FIXED 的本地固定盘，
      优先剩余空间 >= 7 GB（GPU 版下限）里最大的；一块盘都不够 7 GB
      就取最大的，让 validate_base() 报具体差多少。
    ★ 只是"预填值"：安装包向导和 GUI 都允许改成任意本地固定盘 / 目录，
      真正的布局全部按用户选的那个目录（self.base）拼，见 Installer.__init__。
    """
    big_best, big_free = "", -1
    best, best_free = "", -1
    try:
        import ctypes
        DRIVE_FIXED = 3
        for i in range(ord("C"), ord("Z") + 1):
            drive = chr(i) + ":"
            try:
                if ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") != DRIVE_FIXED:
                    continue
                free = shutil.disk_usage(drive + "\\").free
            except Exception:
                continue
            if free >= 7 * (1 << 30):
                if free > big_free:
                    big_free, big_best = free, drive
            elif free > best_free:
                best_free, best = free, drive
    except Exception:
        pass
    pick = big_best or best
    if pick:
        return pick + "\\AsrSubs"
    return os.path.join(os.path.expanduser("~"), "AsrSubs")


def _find_nvidia_smi():
    """nvidia-smi 的路径：先 PATH，再几个常见位置。

    ★ v1.0.1：老驱动只把它放在
      `C:\\Program Files\\NVIDIA Corporation\\NVSMI\\`，不在 PATH 上 ——
      以前只按名字调，这类机器会被判成"没卡"，于是默认装了 CPU 版。
    """
    try:
        p = shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")
        if p:
            return p
    except Exception:
        pass
    for c in (os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "nvidia-smi.exe"),
              os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                           "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")):
        if os.path.isfile(c):
            return c
    return "nvidia-smi"


def detect_nvidia():
    """探测本机有没有 NVIDIA 显卡 → (bool, 型号, 驱动版本)。

    ★ v4.8.24 新增。安装器原来把「运行版本」默认设成 CPU 版，也不做任何
      探测 —— 有 RTX 显卡的机器装完照样跑 CPU。实测 0.6B 模型在 CPU 上
      实时系数 0.44（116 秒素材要识别 50 秒），而用户根本不知道有更快的选项。
    ★ 探测失败（没装驱动 / 命令不存在 / 超时）一律当作"没卡"，不报错：
      安装流程不该因为探测失败而卡住。
    ★ v1.0.1：一并取驱动版本 —— cu128 的轮子对驱动有下限，驱动太旧会
      "装得上、用不了"（torch 能 import，cuda.is_available() 却是 False）。
    """
    try:
        r = subprocess.run(
            [_find_nvidia_smi(), "--query-gpu=name,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
        if lines:
            parts = [x.strip() for x in lines[0].split(",")]
            name = parts[0] if parts else ""
            drv = parts[1] if len(parts) > 1 else ""
            return True, name, drv
    except Exception:
        pass
    return False, "", ""


def validate_base(path, gpu):
    """返回 (ok, 人话原因)。ok=False 时调用方不该继续下载。"""
    p = (path or "").strip().rstrip("\\/")
    if not p:
        return False, "请填写安装位置"
    # ★ 盘符根目录（D:\）不当安装位置：engine\models\data 会直接摊在盘根，
    #   跟别的软件撞名，卸载时也只敢清自己的子目录。
    if len(p) <= 2:
        return False, "请选择盘里的一个文件夹，例如 %s" % DEFAULT_BASE
    if p != p.rstrip(". ") or p.endswith("."):
        return False, "安装路径的结尾不能是点号或空格。"
    if p.startswith("\\\\"):
        return False, "不能装在网络路径上：venv 启动要读几千个小文件，会超时。"
    bad = sorted({c for c in p if ord(c) > 127})
    if bad:
        return False, ("安装路径只能是英文、数字和常规符号，不能含中文或全角字符。\n"
                       "例如：%s、E:\\Tools\\AsrSubs" % DEFAULT_BASE)
    if any(c in p for c in "&!^%"):
        return False, "安装路径不能包含 & ! ^ % 这四个字符（命令行会解释它们）。"
    drive = os.path.splitdrive(p)[0]
    if not drive:
        return False, "请输入绝对路径，例如 %s" % DEFAULT_BASE
    try:
        import ctypes
        DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE = 2, 3, 4
        DRIVE_CDROM, DRIVE_RAMDISK = 5, 6
        t = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive + "\\"))
        if t == 1:
            return False, ("找不到这个盘：%s\\\n盘符是不是写错了？或者这块盘没接上。"
                           % drive)
        if t == DRIVE_REMOTE:
            return False, "不能装在网络盘上：venv 启动要读几千个小文件，会超时。"
        if t == DRIVE_REMOVABLE:
            return False, "不能装在 U 盘/移动硬盘上：设备一拔插件就坏。"
        if t in (DRIVE_CDROM, DRIVE_RAMDISK):
            return False, "不能装在光驱 / 内存盘上，请选本地硬盘。"
        if t and t != DRIVE_FIXED:
            return False, "只能装在本地固定磁盘（NTFS）上。"
    except Exception:
        pass
    try:
        need = (7 if gpu else 4) * (1 << 30)
        free = shutil.disk_usage(drive + "\\").free
        if free < need:
            return False, ("%s 剩余空间 %s，至少需要 %s。\n请先清理，或换一个盘。"
                           % (drive, human(free), human(need)))
    except Exception:
        pass
    return True, ""


def human(n):
    n = float(n or 0)
    if n >= 2 ** 30:
        return "%.2f GB" % (n / 2 ** 30)
    if n >= 2 ** 20:
        return "%.1f MB" % (n / 2 ** 20)
    if n >= 2 ** 10:
        return "%.0f KB" % (n / 2 ** 10)
    return "%.0f B" % n


def http_size(url, timeout=20):
    try:
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


# ── 模型目录体检 ─────────────────────────────────────────────────────
# ★ 判据与 asr_engine.check_model_layout() 一致（安装器是独立打包的 exe，
#   import 不到引擎代码，只能各写一份；两边都以「transformers 吃不吃得下
#   这份权重」为准）。为什么非要查：仓库下错时 transformers 不报错，只是把
#   全部张量判成"缺失"、随机初始化 —— 装完一切绿灯，识别却输出全空。
def model_layout_problem(model_dir):
    """模型能不能被 transformers 正确加载 → (种类, 描述)，没问题 ("", "")。

    种类只有 "config" / "weights" 两种，调用方据此决定删哪几份重下 ——
    config 坏了没必要把 1.8GB 权重一起扔掉。
    """
    cfg = os.path.join(model_dir, "config.json")
    if not os.path.isfile(cfg):
        return "config", "缺少 config.json"
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            c = json.load(f)
    except (OSError, ValueError) as e:
        return "config", "config.json 读不出来（%s）" % e
    if not isinstance(c, dict) or not ("text_config" in c and "audio_config" in c):
        if isinstance(c, dict) and "thinker_config" in c:
            return "config", ("这是 qwen_asr 原版仓库的结构（参数藏在 thinker_config 里），"
                              "transformers 认不出顶层结构")
        return "config", "config.json 缺顶层 text_config / audio_config"

    w = os.path.join(model_dir, "model.safetensors")
    if not os.path.isfile(w):
        # 允许分片权重：只看第一片的键前缀就能判断命名体系
        shards = sorted(f for f in os.listdir(model_dir) if f.endswith(".safetensors")) \
            if os.path.isdir(model_dir) else []
        if not shards:
            return "weights", "缺少 model.safetensors"
        w = os.path.join(model_dir, shards[0])
    try:
        with open(w, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if not (0 < n <= (64 << 20)):
                return "weights", "safetensors 头部长度异常（%d 字节）" % n
            head = json.loads(f.read(n))
    except (OSError, ValueError) as e:
        return "weights", "safetensors 头部损坏（%s）" % e
    prefixes = {str(k).split(".")[0] for k in head if k != "__metadata__"}
    if "thinker" in prefixes:
        return "weights", "权重是 qwen_asr 原版命名（thinker.*），transformers 要的是 model.*"
    if "model" not in prefixes:
        return "weights", "权重里找不到 model.* 参数"
    return "", ""


def file_sha256(path, chunk=1 << 20):
    """算文件 SHA-256，供下载完整性校验与审计留痕。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def download(url, dst, cb=None, cancel=None, chunk=1 << 20, tries=3, log=None,
             timeout=60, tag=None, dead=None, expected_sha256=None):
    """带断点续传的下载。cb(done, total)；返回 True/False。

    ★ 403/404 这类"源本身拒绝"的错误【不重试】，立刻换下一个源 ——
      重试 3 次只是白白等 4.5 秒。（实测清华镜像对 embed zip 就是 403，
      HEAD 却返回 200，很有迷惑性。）

    ★ v4.8.18 新增 timeout / tag / dead：
      timeout —— 单次连接/读取超时。小文件用 20 秒（本该秒回，卡住就是
        源不可达）；大权重保持 60 秒（慢速传输时两次字节间隔可能很久）。
      tag + dead —— 会话级镜像黑名单。超时 / 连接被拒 / DNS 失败
        （实测 WinError 10060：官方 HF 直连、ModelScope 在国内网络均如此）
        说明【这个源在本网络不可达】，把 tag 记进 dead 集合就够：后续文件
        直接跳过该源，不再每个文件都白等 timeout×tries 秒。
        2026-11-01 实测：5 个镜像站未收录的小文件 × 3 源 × 2 仓库，
        旧逻辑要干等 20~40 分钟，用户看到的就是"下不动"（安装其实早已完成）。
        注意 403/404 不走这里（上面已接住）—— 那种是"源可达但没这个文件"，
        换个文件同一源可能就有，不能拉黑。
    """
    dst = str(dst)
    tmp = dst + ".part"
    for attempt in range(tries):
        if cancel and cancel.is_set():
            return False
        try:
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            req = urllib.request.Request(url, headers=dict(UA))
            if have:
                req.add_header("Range", "bytes=%d-" % have)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                total = int(r.headers.get("Content-Length") or 0)
                if have and r.status == 200:
                    have = 0          # 服务器不支持续传，从头来
                total = have + total if r.status == 206 else total
                mode = "ab" if have and r.status == 206 else "wb"
                done = have if mode == "ab" else 0
                with open(tmp, mode) as f:
                    while True:
                        if cancel and cancel.is_set():
                            return False
                        buf = r.read(chunk)
                        if not buf:
                            break
                        f.write(buf)
                        done += len(buf)
                        if cb:
                            cb(done, total)
            # 完整性：声明了大小就必须对得上
            if total and os.path.getsize(tmp) != total:
                raise IOError("大小不符 %d != %d" % (os.path.getsize(tmp), total))
            # ★ P1：内容级校验。大小对得上不等于内容没被换过；配了白名单
            #   就必须逐字节对上。其余资源也算出 SHA-256 写进安装日志，
            #   便于事后审计（大权重文件会多花几秒，可接受）。
            digest = file_sha256(tmp)
            if log:
                log("    SHA-256 %s" % digest)
            if expected_sha256 and digest.lower() != expected_sha256.lower():
                if log:
                    log("    SHA-256 与白名单不符，丢弃该文件并换源")
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False
            os.replace(tmp, dst)
            return True
        except urllib.error.HTTPError as e:
            # 4xx = 源本身拒绝（403 限流 / 404 没有这个文件），重试没意义
            if 400 <= getattr(e, "code", 0) < 500:
                if log:
                    log("    源返回 HTTP %s，换源" % e.code)
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return False
            if log and attempt == tries - 1:
                log("    HTTP %s" % e.code)
        except Exception as e:
            # 走到这里的是 URLError（HTTPError 已在上面接住）。
            # reason 是 OSError 子类（timeout / 连接被拒 / DNS 失败）=
            # 该源在本网络不可达 → 拉黑，让后续文件不再浪费时间。
            # 个别路径下 socket.timeout 会不经 URLError 包裹直接冒出，
            # 两种形态都认（socket.timeout 即 TimeoutError，3.10+ 起）。
            unreachable = (
                (isinstance(e, urllib.error.URLError)
                 and isinstance(e.reason, OSError))
                or isinstance(e, (socket.timeout, ConnectionError)))
            if unreachable and dead is not None and tag:
                dead.add(tag)
            if log and attempt == tries - 1:
                log("    %s: %s" % (type(e).__name__, e))
        time.sleep(1.5)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return False


def run(cmd, cb=None, cancel=None, cwd=None, env=None, timeout=3600):
    """跑子进程，把输出逐行喂给 cb。返回 returncode。"""
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace",
                             bufsize=1,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        if cb:
            cb("启动失败: %s" % e)
        return -1
    try:
        for line in iter(p.stdout.readline, ""):
            line = line.rstrip()
            if line and cb:
                cb(line)
            if cancel and cancel.is_set():
                p.terminate()
                break
    except Exception:
        pass
    try:
        return p.wait(timeout=timeout)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
        return -1


# ── 安装步骤 ──────────────────────────────────────────────────────────
VERIFY_TF_CODE = (
    "import transformers, json\n"
    "from transformers import (Qwen3ASRForConditionalGeneration,\n"
    "                          Qwen3ASRForTokenClassification)\n"
    "print('TFOK', transformers.__version__)\n"
)


TORCH_PROBE_CODE = (
    "import torch\n"
    "print('VER=' + str(torch.__version__))\n"
    "print('CUDA=' + str(torch.version.cuda))\n"
    "print('AVAIL=' + str(bool(torch.cuda.is_available())))\n"
    "try:\n"
    "    print('DEV=' + (torch.cuda.get_device_name(0)\n"
    "                    if torch.cuda.is_available() else ''))\n"
    "except Exception:\n"
    "    print('DEV=')\n"
)


def probe_torch(py, timeout=180):
    """读目标解释器里的 torch 版本 / 编译的 CUDA 版本 / 能不能用 GPU。

    返回 dict（取不到就是空 dict）。字段：VER、CUDA（None 表示 CPU 版）、
    AVAIL（True/False）、DEV（显卡名）。

    ★ v1.0.1：以前装完根本不看这些 —— "torch 装好了"就等于成功。
      实测有机器 torch 是 CPU 版却装了 GPU 选项、或者驱动太旧导致
      `cuda.is_available()` 为 False，插件一路静默跑 CPU。
    """
    out = []
    rc = run([py, "-c", TORCH_PROBE_CODE],
             cb=lambda l: out.append(l), timeout=timeout)
    if rc != 0:
        return {}
    d = {}
    for line in out:
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if k in ("VER", "CUDA", "AVAIL", "DEV"):
                d[k] = v.strip()
    return d


def verify_transformers(py):
    """在目标解释器里验证 Qwen3ASR 两个类能不能导入。

    返回 (ok, 版本号或失败原因)。★ 必须用目标 venv 里的 python 去试，
    不能在本进程 import —— 本进程用的是打包时的解释器，和装出来的环境无关。
    """
    try:
        r = subprocess.run(
            [py, "-c", VERIFY_TF_CODE],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)

    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "TFOK" in out:
        for line in out.splitlines():
            if line.startswith("TFOK"):
                return True, line.split(None, 1)[1].strip() if " " in line else "?"
        return True, "?"
    # 取最后一行报错，通常是 ImportError: cannot import name ...
    tail = [ln for ln in out.strip().splitlines() if ln.strip()]
    return False, (tail[-1][:180] if tail else "rc=%s" % r.returncode)


class Installer:
    def __init__(self, base, gpu, mirror, log, prog, cancel,
                 driver="", gpu_name=""):
        self.base = base.rstrip("\\/")
        self.gpu = gpu
        # ★ v1.0.1：驱动版本 / 显卡型号决定用哪个 CUDA 通道
        self.driver = driver
        self.gpu_name = gpu_name
        self.mirror = mirror
        self.log = log
        self.prog = prog          # prog(percent, stage_text)
        self.cancel = cancel
        # ★ v1.0.1：选了 GPU 版但 CUDA 实际用不上（驱动太旧 / 独显被禁）
        #   时置 True，冒烟测试和收尾提示据此改口径，不再一律"通过"。
        self.gpu_unavailable = False

        # ── 3.0 安装布局（全部 ASCII、无空格）──────────────────────────
        #   engine  ← 原 engine（无意义的代号，用户会当缓存删掉）
        #   runtime ← 原 "AsrSubs\\.venv"（三层重名，回滚时极易整删）
        #   models  ← 原 "AsrSubs\\models"（提到根目录，大件单独一层）
        #   data    ← 原 "AsrSubs"（含 output/ logs/ cache/）
        #   _setup  ← 原 "安装核心"（PyInstaller onedir 的落地目录）
        #   docs    ← 原 "AsrSubs\\使用说明.txt"
        self.engine = os.path.join(self.base, "engine")
        self.runtime = os.path.join(self.base, "runtime")
        # embeddable 版直接解压到这里：runtime\Scripts\python.exe
        # —— 正好是 Lua 主脚本认的路径，不用再建一层 venv。
        self.runtime_scripts = os.path.join(self.runtime, "Scripts")

        self.data = os.path.join(self.base, "data")
        self.output = os.path.join(self.data, "output")
        self.logs = os.path.join(self.data, "logs")
        self.cache_dir = os.path.join(self.data, "cache")
        self.docs = os.path.join(self.base, "docs")
        # 模型直接放 models\ 下（find_aligner 只扫一层）
        self.models_root = os.path.join(self.base, "models")
        self.models = os.path.join(self.models_root, "Qwen3-ASR-0.6B-hf")
        self.aligner = os.path.join(self.models_root, "Qwen3-ForcedAligner-0.6B-hf")

        # 各步骤占整体进度的 (起点, 跨度)。GPU 版 torch 2.7GB 是大头，
        # CPU 版则是模型 1.8GB 占绝对多数，所以两套权重。
        self.W = (dict(py=(0, 3), pip=(3, 3), torch=(6, 56), deps=(62, 6),
                       model=(68, 24), deploy=(92, 5), smoke=(97, 3))
                  if gpu else
                  dict(py=(0, 6), pip=(6, 4), torch=(10, 6), deps=(16, 10),
                       model=(26, 64), deploy=(90, 6), smoke=(96, 4)))
        self.cache = os.path.join(tempfile.gettempdir(), "qwen3asr_dl")

        self.vpy = os.path.join(self.runtime, "Scripts", "python.exe")
        self.wt = 0.0             # 当前步骤起始进度
        self.wspan = 1.0          # 当前步骤占多少

    # 进度：把 [0,1] 的子进度映射到整体区间
    def step(self, frac, text):
        self.prog(self.wt + self.wspan * max(0.0, min(1.0, frac)), text)

    def begin(self, start, span, text):
        self.wt, self.wspan = start, span
        self.step(0.0, text)

    def die(self, msg):
        raise RuntimeError(msg)

    # ── ① Python ────────────────────────────────────────────────────
    def ensure_python(self):
        self.begin(*self.W["py"], text="准备 Python 运行环境")
        os.makedirs(self.cache, exist_ok=True)
        os.makedirs(self.runtime_scripts, exist_ok=True)

        exe = os.path.join(self.runtime_scripts, "python.exe")
        if os.path.exists(exe):
            self.log("Python 已存在，跳过下载")
            self.step(1, "Python 已就绪")
            return

        zip_path = os.path.join(self.cache, "python-%s-embed-amd64.zip" % PY_TAG)
        srcs = PY_SOURCES if self.mirror else list(reversed(PY_SOURCES))
        ok = False
        for name, url in srcs:
            if self.cancel.is_set():
                return
            self.log("下载 Python %s（%s，免安装版）…" % (PY_VER, name))
            last = [0, 0.0]

            def cb(done, total):
                now = time.time()
                if total and (done - last[0] > 1 << 20 or now - last[1] > 0.4):
                    last[0], last[1] = done, now
                    self.step(done / total * 0.85,
                              "下载 Python %s / %s" % (human(done), human(total)))

            if download(url, zip_path, cb, self.cancel, log=self.log,
                        expected_sha256=(PY_EMBED_SHA256
                                         if PY_VER == PY_EMBED_SHA256_VER else None)):
                self.log("下载完成（%s）" % human(os.path.getsize(zip_path)))
                ok = True
                break
            self.log("  %s 下载失败，换下一个源" % name)
        if not ok:
            self.die("Python 下载失败。请检查网络后重试。")

        self.step(0.9, "解压 Python")
        try:
            with zipfile.ZipFile(zip_path) as z:
                # ★ P1：CPython 的 extractall 本身会剥离 ../ 与绝对路径，
                #   这里再显式挡一层，并限制解压后总体积防 zip bomb。
                total_bytes = 0
                for info in z.infolist():
                    name = info.filename.replace("\\", "/")
                    if (name.startswith("/") or re.match(r"^[A-Za-z]:", name)
                            or ".." in name.split("/")):
                        raise ValueError("压缩包含不安全路径：%s" % info.filename)
                    total_bytes += info.file_size
                if total_bytes > 512 << 20:
                    raise ValueError("解压后体积异常：%d 字节" % total_bytes)
                z.extractall(self.runtime_scripts)
        except Exception as e:
            self.die("Python 解压失败：%s" % e)
        if not os.path.exists(exe):
            self.die("解压后找不到 python.exe")

        # embeddable 版默认关掉 site（不加载 site-packages），pip 装的东西会找不到。
        # 改 _pth 打开它 —— 少了这一步，后面 torch 装完 import 不到。
        for f in os.listdir(self.runtime_scripts):
            if f.endswith("._pth"):
                p = os.path.join(self.runtime_scripts, f)
                try:
                    txt = open(p, encoding="utf-8").read()
                    if "import site" not in txt.replace("#import site", ""):
                        txt = txt.replace("#import site", "import site")
                    if "Lib\\site-packages" not in txt:
                        txt = txt.replace("\nimport site",
                                          "\nLib\\site-packages\nimport site")
                    open(p, "w", encoding="utf-8").write(txt)
                except Exception:
                    pass
        os.makedirs(os.path.join(self.runtime_scripts, "Lib", "site-packages"),
                    exist_ok=True)
        self.log("Python 就绪：%s" % exe)
        self.step(1, "Python 就绪")

    # ── ② pip ───────────────────────────────────────────────────────
    def make_venv(self):
        """embeddable 版没有 pip（也没有 venv 模块），这里把它装上。

        ★ get-pip.py 直接内嵌在 exe 里（2.1MB），这样装 pip 只走 PyPI 镜像，
          不用去连 bootstrap.pypa.io —— 国内网络下少一个可能卡住的环节。
        """
        self.begin(*self.W["pip"], text="安装 pip")
        rc = run([self.vpy, "-m", "pip", "--version"],
                 cb=lambda l: None, cancel=self.cancel, timeout=180)
        if rc == 0:
            self.log("pip 已存在，跳过")
            self.step(1, "pip 已就绪")
            return

        g = os.path.join(payload_dir(), "get-pip.py")
        if not os.path.isfile(g):
            self.die("安装器内部缺少 get-pip.py，请重新下载本安装器。")
        order = PYPI if self.mirror else list(reversed(PYPI))
        for name, idx in order:
            if self.cancel.is_set():
                return
            self.log("安装 pip（%s 源）…" % name)
            rc = run([self.vpy, g, "-i", idx, "--no-warn-script-location"],
                     cb=lambda l: self.log("  " + l[:160]),
                     cancel=self.cancel, timeout=900)
            if rc == 0:
                break
            self.log("  %s 失败，换下一个源" % name)
        rc = run([self.vpy, "-m", "pip", "--version"],
                 cb=lambda l: None, cancel=self.cancel, timeout=180)
        if rc != 0:
            self.die("pip 安装失败。可手动运行：%s %s" % (self.vpy, g))
        self.log("pip 就绪")
        self.step(1, "pip 就绪")

    def _torch_cmd(self, idx, force):
        """拼 torch 的安装命令（抽出来便于离线断言）。

        ★ v1.0.1：两条硬规矩，缺一条就会"选了 GPU 却装成 CPU"：
          ① 只给【一个】源，绝不带 --extra-index-url PyPI —— PyPI 上的
             torch 是 CPU 版且版本更高（实测 2.15.0 vs cu128 的 2.11.0），
             pip 按"版本最高"选它，CUDA 通道等于白设；
          ② 带 --no-deps —— 只从 CUDA 专属源取那一个轮子，torch 的运行
             依赖（filelock/sympy/...）在下一步从 PyPI 装（TORCH_DEPS）。
              Windows 的 torch 轮子自带 CUDA DLL，所以 --no-deps 是安全的。
        """
        cmd = [self.vpy, "-m", "pip", "install", "torch",
               "--index-url", idx, "--no-deps"]
        if force:
            cmd.append("--force-reinstall")
        return cmd

    # ── ③ 依赖 ──────────────────────────────────────────────────────
    def pip_install(self):
        want_cuda = bool(self.gpu)
        self.begin(*self.W["torch"],
                   text="安装 torch（%s）" % ("GPU 版" if want_cuda else "CPU 版"))
        if want_cuda:
            _ch, srcs, _why = pick_cuda_channel(self.driver, self.gpu_name)
            self.log("CUDA 通道：%s（%s）" % (_ch, _why))
        else:
            srcs = TORCH_CPU
        srcs = srcs if self.mirror else list(reversed(srcs))

        # ★★ v1.0.1：先看已经装的是什么版本 —— 这一步是"换版本"能不能生效的关键。
        #   pip 对 `pip install torch`（不限版本）在已装 torch 时只会回
        #   "Requirement already satisfied" 然后什么都不做：实测本机
        #   torch 是 +cpu，用 cu128 源 dry-run 依然回 "already satisfied"。
        #   所以以前"装过 CPU 版→重跑安装器选 GPU 版"根本不会换掉 torch，
        #   说明书里那句"重跑安装器即可"是假的。现在：版本不一致就
        #   --force-reinstall（pip 不会自动降级，CPU↔GPU 两个方向都得强制）。
        cur = probe_torch(self.vpy)
        have = ""
        if cur:
            have = "cpu" if str(cur.get("CUDA", "None")) in ("None", "", "False") else "cuda"
        want = "cuda" if want_cuda else "cpu"
        if have == want:
            self.log("torch 已经是 %s 版（%s），跳过下载"
                     % ("GPU" if want_cuda else "CPU", cur.get("VER", "")))
            self.step(1, "torch 已是 %s 版" % ("GPU" if want_cuda else "CPU"))
        else:
            if have:
                self.log("原有 torch 是 %s 版，本次要换成 %s 版，先卸载重装…"
                         % ("GPU" if have == "cuda" else "CPU",
                            "GPU" if want_cuda else "CPU"))
            ok = False
            for name, idx in srcs:
                if self.cancel.is_set():
                    return
                self.log("安装 torch（%s，%s）…%s" % (
                    "GPU" if want_cuda else "CPU", name,
                    " 体积较大，请耐心" if want_cuda else ""))
                # ★ v1.0.1：两条硬规矩，缺一条就会"选了 GPU 却装成 CPU"：
                #   ① 绝不带 --extra-index-url PyPI —— PyPI 上的 torch 是
                #      CPU 版且版本更高（实测 2.15.0 vs cu128 的 2.11.0），
                #      pip 按"版本最高"选它，GPU 通道等于白设。
                #   ② 带 --no-deps —— 只从 CUDA 专属源取那一个轮子，
                #      torch 的运行依赖（filelock/sympy/...）在下一步从 PyPI 装。
                rc = run(self._torch_cmd(idx, bool(have)),
                         cb=lambda l: self.log("  " + l[:200]),
                         cancel=self.cancel, timeout=5400)
                if rc == 0:
                    ok = True
                    break
                self.log("  %s 安装失败，换下一个源" % name)
            if not ok:
                self.die("torch 安装失败。可稍后重试，安装器支持断点续传的缓存。")
            self.log("torch 装好了")
            self.step(1, "torch 装好了")

        # ── 装完必须验一遍"到底是哪个版本、CUDA 能不能用" ────────────
        d = probe_torch(self.vpy)
        if want_cuda:
            if d.get("AVAIL") == "True":
                self.log("✓ CUDA 可用：torch %s / CUDA %s / %s"
                         % (d.get("VER", "?"), d.get("CUDA", "?"),
                            d.get("DEV") or "GPU"))
            else:
                self.gpu_unavailable = True
                self.log("⚠ torch 装的是 GPU 版，但 CUDA 用不了"
                         "（torch %s / 编译 CUDA %s）。"
                         % (d.get("VER", "?"), d.get("CUDA", "?")))
                self.log("  常见原因：NVIDIA 驱动太旧（CUDA 12.8 需要 570 以上）、"
                         "笔记本独显被禁用、或显卡不在支持列表。")
                self.log("  现在装下去也能用，但会【跑 CPU】（慢 5~10 倍）。")
                self.log("  处理：更新 NVIDIA 驱动后重跑安装器；"
                         "或改选 CPU 版，省下 2.7 GB 下载。")
        else:
            if d.get("AVAIL") == "True":
                self.log("提示：本机 CUDA 可用，但本次装的是 CPU 版 —— 识别会慢 5~10 倍。")
                self.log("  想要更快：重跑安装器并选 GPU 版（现在换版本会真正替换 torch）。")
            else:
                self.log("torch %s（CPU 版）就绪" % d.get("VER", ""))

        self.begin(*self.W["deps"], text="安装其余依赖（transformers / numpy 等）")
        pypi = PYPI[0][1] if self.mirror else PYPI[-1][1]
        # ★ v1.0.1：torch 用 --no-deps 装的，它的依赖在这里补（TORCH_DEPS）。
        _deps = PY_DEPS + TORCH_DEPS
        rc = run([self.vpy, "-m", "pip", "install", "-i", pypi] + _deps,
                 cb=lambda l: self.log("  " + l[:200]),
                 cancel=self.cancel, timeout=2400)
        if rc != 0:
            # 换下一个源再试一次
            rc = run([self.vpy, "-m", "pip", "install", "-i", PYPI[1][1]] + _deps,
                     cb=lambda l: self.log("  " + l[:200]),
                     cancel=self.cancel, timeout=2400)
        if rc != 0:
            self.die("依赖安装失败（numpy / soundfile / imageio-ffmpeg / torch 运行依赖）")
        self.log("基础依赖装好了")
        self.step(1, "基础依赖装好了")

        # ── transformers 单独处理：必须能 import 到 Qwen3ASR 两个类 ──
        self.install_transformers()

    def install_transformers(self):
        """装 transformers，并且【验证 Qwen3ASR 两个类真的存在】。

        ★★ 为什么不能像 v1.28 那样把 transformers 塞进 PY_DEPS 裸装：
          识别引擎依赖 Qwen3ASRForConditionalGeneration 与
          Qwen3ASRForTokenClassification，这两个类当时只存在于
          transformers 的开发构建里。裸装 PyPI 最新稳定版时，如果那个版本
          还没收录 Qwen3-ASR，装完一跑就是 ImportError —— 安装过程全绿，
          用户却在点「开始」的瞬间失败，且错误信息完全看不出是版本问题。
           开发机之所以能用，只是因为 venv 里恰好躺着 5.15.0.dev0。

        策略：先试稳定区间（走镜像、体积小），导入校验不过再退到
        已知可用的固定提交。两条都失败就明确报错，不再静默继续。
        """
        pypi = PYPI[0][1] if self.mirror else PYPI[-1][1]

        # 已知可用：v1.28 实测通过的 transformers 提交
        KNOWN_GOOD_COMMIT = (
            "https://codeload.github.com/huggingface/transformers/tar.gz/"
            "c7f9c8815610d27e41a6b0b0cc9e2d3c49468d1d")
        specs = [
            ("稳定版 5.x", ["transformers>=5.14,<6"]),
            ("固定提交（含 Qwen3-ASR）", [KNOWN_GOOD_COMMIT]),
        ]

        for label, args in specs:
            if self.cancel.is_set():
                return
            self.log("安装 transformers（%s）…" % label)
            rc = run([self.vpy, "-m", "pip", "install", "-i", pypi] + args,
                     cb=lambda l: self.log("  " + l[:200]),
                     cancel=self.cancel, timeout=2400)
            if rc != 0:
                self.log("  %s 安装失败" % label)
                continue
            ok, info = verify_transformers(self.vpy)
            if ok:
                self.log("transformers %s 校验通过（Qwen3ASR 两个类都在）" % info)
                self.step(1, "transformers 就绪（%s）" % info)
                return
            self.log("  %s 里没有 Qwen3ASR 类（%s），换下一个来源" % (label, info))

        self.die(
            "transformers 安装后仍找不到 Qwen3ASR 模型类，无法继续。\n"
            "可在「%s」里手动执行：\n"
            "  python -m pip install \"%s\"\n"
            "装完重新运行本安装器（已装好的部分会跳过）。"
            % (self.vpy, KNOWN_GOOD_COMMIT))

    # ── ④ 模型 ──────────────────────────────────────────────────────
    def fetch_model(self):
        self.begin(*self.W["model"], text="下载识别模型")
        os.makedirs(self.models_root, exist_ok=True)
        if self.cancel.is_set():
            return

        # ASR 占 72%，对齐模型占 28% —— 对齐全下 1.5GB，比 ASR 的 1.8GB 略小。
        def prog_asr(frac, text):
            self.step(frac * 0.72, text)

        def prog_align(frac, text):
            self.step(0.72 + frac * 0.28, text)

        try:
            self._fetch_repo(self.models, REPO, "识别模型", prog_asr)
        except RuntimeError as e:
            self.die(str(e))

        # ★ 对齐模型缺失不阻断安装（插件会自动降级），但必须【说出来】，
        #   并且在 smoke 里如实回报"时间轴精度降级"，不能假装一切正常。
        self.aligner_ok = False
        try:
            self._fetch_repo(self.aligner, ALIGNER_REPO, "对齐模型", prog_align)
            self.aligner_ok = True
        except RuntimeError as e:
            self.log("⚠ 对齐模型没装上：%s" % e)
            self.log("  插件仍可用，但时间戳会退回「按字数比例估算」，"
                     "词级精度没有保障。")
        self.step(1, "模型已就绪")

    def _fetch_repo(self, dst_dir, repo, label, prog):
        """把一个 HF 仓库（小文件 + model.safetensors）下到 dst_dir。

        逐文件多源重试：各镜像收录的文件并不一致（hf-mirror 上就没有
        tokenizer.json），"一个源试完所有文件再换源"会因个别文件缺失把
        整个源判死。权重下完校验 safetensors 头部，损坏的当场报错。

        ★ v4.8.18：会话级黑名单 dead + 小文件 20 秒短超时。
          超时/连接失败的源记一次就够（详见 download() 说明）——
          2026-11-01 实测：旧逻辑下 5 个镜像站未收录的小文件 × 3 源 ×
          2 仓库要干等 20~40 分钟，而模型早已下完，用户看到的就是
          "下不动"。各源都拿不到某个文件时打一行跳过日志
          （这些文件本就是"仓库里没有就跳过"的可选项）。
        """
        os.makedirs(dst_dir, exist_ok=True)

        # ★ 先体检【已存在】的文件，再决定跳不跳。
        #   以前"存在且非空就跳过"：仓库名错的时候，坏掉的 config.json 和
        #   1.8GB 权重会被当成"已装好"原样留下 —— 用户重跑安装器也修不好
        #   （2026-11-01 就是这样把 qwen_asr 原版的权重留在了 -hf 目录里）。
        #   这里把不对的那几份删掉重下。chat_template.json 是旧仓库的文件名，
        #   新仓库用 chat_template.jinja，两份同时存在有被旧文件盖住的风险。
        if os.path.isdir(dst_dir) and os.listdir(dst_dir):
            kind, bad = model_layout_problem(dst_dir)
            if bad:
                self.log("⚠ %s 目录里已有模型文件，但不对：%s" % (label, bad))
                self.log("  清理这几份后重下（不清理的话「重装」等于没装）")
                drop = ["chat_template.json", "chat_template.jinja"]
                if kind == "config":
                    drop.append("config.json")
                elif kind == "weights":
                    drop.append(MODEL_WEIGHT)
                for f in drop:
                    p = os.path.join(dst_dir, f)
                    try:
                        if os.path.isfile(p):
                            os.remove(p)
                    except OSError:
                        pass

        dead = set()          # 本网络不可达的源（会话级；每个仓库各一份）
        bakes = [
            ("hf-mirror", "https://hf-mirror.com/%s/resolve/main/" % repo),
            ("ModelScope", "https://www.modelscope.cn/models/%s/resolve/master/" % repo),
            ("官方", "https://huggingface.co/%s/resolve/main/" % repo),
        ]
        if not self.mirror:
            bakes.reverse()

        self.log("取%s元信息…（%s）" % (label, repo))
        got_cfg = False
        for f in MODEL_SMALL:
            if self.cancel.is_set():
                return
            dst = os.path.join(dst_dir, f)
            if os.path.exists(dst) and os.path.getsize(dst) > 0:
                got_cfg = got_cfg or f == "config.json"
                continue
            for name, base in bakes:
                if self.cancel.is_set():
                    return
                if name in dead:
                    continue          # 该源在本网络不可达，不再每个文件重试
                if download(base + f, dst, None, self.cancel, tries=2,
                            log=self.log, timeout=20, tag=name, dead=dead):
                    if f == "config.json":
                        got_cfg = True
                    self.log("  ✓ %s（%s）" % (f, name))
                    break
            else:
                self.log("  ⊘ %s：各源均未收录或不可达，跳过" % f)
        if not got_cfg:
            raise RuntimeError("%s 的配置文件下载失败（config.json）。请检查网络后重试。" % label)

        # 权重（大头）
        dst = os.path.join(dst_dir, MODEL_WEIGHT)
        if os.path.exists(dst) and os.path.getsize(dst) > 100 << 20:
            self.log("%s权重已存在（%s），跳过下载" % (label, human(os.path.getsize(dst))))
            self._verify_layout(dst_dir, repo, label)
            return
        for name, base in bakes:
            if self.cancel.is_set():
                return
            if name in dead:
                continue          # 小文件阶段已探明不可达的源，不重复试探
            url = base + MODEL_WEIGHT
            total = http_size(url) or 1790 * 2 ** 20
            self.log("下载%s权重 %s（%s）…这是最大的一份，慢一点正常"
                     % (label, human(total), name))
            last = [0, 0.0]

            def cb(done, tot):
                now = time.time()
                if done - last[0] > 4 << 20 or now - last[1] > 0.5:
                    last[0], last[1] = done, now
                    pct = done / max(1, tot or total)
                    prog(pct, "下载%s %s / %s（%.0f%%）"
                         % (label, human(done), human(tot or total), pct * 100))

            if download(url, dst, cb, self.cancel, tries=4, log=self.log,
                        tag=name, dead=dead):
                self.log("%s下载完成（%s）" % (label, human(os.path.getsize(dst))))
                break
            self.log("  %s 下载失败，换下一个源" % name)
        else:
            raise RuntimeError("%s权重下载失败。若断点续传卡住，可删掉 %s 后重试。"
                               % (label, dst))

        # 校验：safetensors 头部必须是合法 JSON
        try:
            with open(dst, "rb") as f:
                n = struct.unpack("<Q", f.read(8))[0]
                json.loads(f.read(n))
        except Exception as e:
            raise RuntimeError("%s文件损坏（%s）。请删除后重新运行安装器。"
                               % (label, e))

        # ★ 下完再按 transformers 的口径验一次布局。镜像把 -hf 重定向到原版
        #   仓库这种事必须在这里拦下 —— 放过去就是"装完全绿、识别全空"。
        self._verify_layout(dst_dir, repo, label)

    def _verify_layout(self, dst_dir, repo, label):
        """按 transformers 的口径验模型目录布局，不对就报能照做的错。"""
        _kind, bad = model_layout_problem(dst_dir)
        if not bad:
            return
        raise RuntimeError(
            "%s的模型文件对不上：%s\n"
            "多半是镜像站把 -hf 仓库重定向到了原版仓库（或只回了一半文件）。\n"
            "换网络/镜像重试，或手动下载 %s 的内容放到：%s"
            % (label, bad, repo, dst_dir))

    # ── ⑤ 部署脚本 ──────────────────────────────────────────────────
    def write_paths(self):
        """把安装布局落成两份清单：paths.json（Python）＋ paths.lua（Lua）。

        ★ 为什么要有它：engine / AsrSubs / AsrSubs 这套名字以前散落在
          9 个文件、60 多处，改一次目录名就得全局搜索替换，漏一处就是
          "界面能开、一跑就超时"。现在目录名只在本类里定义一次，运行时
          双方都读这份清单。
        ★ Lua 侧用 loadfile 读 .lua 而不是解析 .json —— Windows 上
          io.open 打不开含中文的路径，loadfile 走 Resolve 的 Unicode 通道。
        """
        info = {
            "schema": 1,
            "root": self.base,
            "engine": self.engine,
            "runtime": self.runtime,
            "models": self.models_root,
            "data": self.data,
            "output": self.output,
            "logs": self.logs,
            "cache": self.cache_dir,
            "docs": self.docs,
        }
        os.makedirs(self.engine, exist_ok=True)
        with open(os.path.join(self.engine, "paths.json"), "w",
                  encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)

        def q(p):
            return '"%s"' % p.replace("\\", "\\\\")

        order = ("root", "engine", "runtime", "models",
                 "data", "output", "logs", "cache", "docs")
        lines = ["-- 由安装器生成（installer.py: write_paths）。请勿手改。",
                 "-- 改了目录名就重跑一次安装器的「部署」步骤。",
                 "return {"]
        for k in order:
            lines.append("  %s = %s," % (k, q(info[k])))
        lines.append("}")
        with open(os.path.join(self.engine, "paths.lua"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

        self.write_model_manifest()

    def write_model_manifest(self):
        """把 models\\ 下已有的模型写成 models.lua / models.txt。

        ★ v4.8.24：这两份清单以前只有"跑过一次识别"才由
          resolve_bridge.refresh_manifest() 生成。于是全新安装后打开插件、
          进「设置」看模型下拉是空的 —— 用户以为模型没装上，实际模型就在
          `models\\` 里。安装器既然知道模型目录，顺手写一份就行。
        ★ 格式必须与 refresh_manifest 完全一致：
            models.txt  每行一个模型名
            models.lua  return { { name = "…", path = "…" }, … }
          否则 Lua 的 readModels() 读不到（它读 .lua，失败再退回 .txt）。
        """
        try:
            root = self.models_root
            names = []
            if os.path.isdir(root):
                for n in sorted(os.listdir(root)):
                    p = os.path.join(root, n)
                    if os.path.isdir(p) and os.path.isfile(
                            os.path.join(p, "config.json")):
                        names.append(n)
            if not names:
                self.log("模型清单：models 目录里暂时没有可用模型，跳过生成")
                return
            with open(os.path.join(self.engine, "models.txt"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write("\n".join(names) + "\n")

            def _q(s):
                return '"%s"' % str(s).replace("\\", "\\\\").replace('"', '\\"')

            body = ", ".join(
                "{ name = %s, path = %s }" % (_q(n), _q(os.path.join(root, n)))
                for n in names)
            with open(os.path.join(self.engine, "models.lua"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write("return { " + body + " }\n")
            self.log("模型清单：已写入 %d 个模型（models.lua / models.txt）"
                     % len(names))
        except Exception as e:
            # 清单只是给下拉框用的，写不了不该让安装失败
            self.log("模型清单写入失败（不影响识别）：%s" % e)

    def drop_entry_junction(self):
        """摘掉老版本留下的中文门面 <BASE>字幕工具，**不再新建**。

        ★ 为什么取消：用户只想看到 `D:\\AsrSubs` 一个目录。多一个 0 字节的
          中文别名没有任何功能收益（程序从不读它），却让人反复怀疑
          "哪个才是真的、哪个能删" —— 那就别留着。
        ★ 为什么还要"摘"：2.0.10 及更早的安装器建过这个 junction，升级时
          不摘掉它就永远挂在盘上（看起来像还有个副本）。
        ★ 只能用 os.rmdir：Windows 的 RemoveDirectory 对 junction 只删除
          重解析点、不动目标。换成 shutil.rmtree / DelTree 会跟进去把整个
          安装目录 9 GB 删掉 —— 这是卸载/升级最不该发生的事。
        ★ 同名真实目录一律不碰：里面可能是用户自己放的东西。

        返回一句人话（没动任何东西时返回空串），失败不抛异常。
        """
        link = self.base + "字幕工具"
        if not os.path.isdir(link):
            return ""
        if not _is_reparse(link):
            return "同名目录不是链接，保留原样（可能装着用户自己的文件）：%s" % link
        try:
            os.rmdir(link)
            return "已移除旧版中文门面：%s" % link
        except OSError as e:
            return "旧版中文门面没移除（不影响使用）：%s（%s）" % (link, e)

    def _drop_legacy_menu_dir(self, scripts_dir):
        """升级时清掉 2.x 留在菜单里的 `AsrSubs` 目录。

        ★ 不清理的后果：DaVinci 的「脚本」菜单里会同时出现
          「AsrSubs」和「AsrSubs」两项，用户点旧的那个就跑到老锚点去了
          （或干脆报超时）。
        ★ 只在目录内容【全部是本工具写的两个文件】时才删 —— 用户可能往里
          放过自己的 lua，那种情况宁可留着多一个菜单项，也不能删人家东西。
        """
        old = os.path.join(scripts_dir, LEGACY_MENU_DIR)
        if not os.path.isdir(old):
            return
        try:
            names = set(os.listdir(old))
        except OSError:
            return
        if names <= {"AsrSubs.lua", "render_to_wav.xml"}:
            try:
                shutil.rmtree(old)
                self.log("  已清理 2.x 菜单目录：%s" % old)
            except OSError as e:
                self.log("  清理 2.x 菜单目录失败（不影响使用）：%s" % e)
        else:
            self.log("  保留 2.x 菜单目录（里面有别的文件）：%s" % old)

    def drop_duplicate_menu_dirs(self, keep):
        """清掉其它脚本根目录里本工具写的同一份菜单（只留 keep 那一份）。

        ★ 现象：Resolve「脚本」菜单里出现两个「AsrSubs」。原因是
          %ProgramData%\\...\\Fusion\\Scripts\\Utility 和
          %APPDATA%\\...\\Support\\Fusion\\Scripts\\Utility 各有一份，
          Resolve 两个根目录都会扫进菜单。老版本安装器"哪个存在就都写"，
          换台机器看就是两个子菜单，点哪个都"能用"，但改了代码只生效一份。

        ★ 删除条件很保守：目录里【只有】本工具的那两个文件才删。
          用户往里放过自己的 lua / 备份，就保留并记日志，宁可多一个菜单项。
        """
        if not keep:
            return
        keep_target = os.path.normcase(
            os.path.abspath(os.path.join(keep, MENU_DIR)))
        for d in resolve_script_dirs():
            target = os.path.join(d, MENU_DIR)
            if os.path.normcase(os.path.abspath(target)) == keep_target:
                continue
            if not os.path.isdir(target):
                continue
            try:
                names = set(os.listdir(target))
            except OSError:
                continue
            if not names <= {"AsrSubs.lua", "render_to_wav.xml"}:
                self.log("  保留另一处菜单目录（里面有别的文件）：%s" % target)
                continue
            try:
                shutil.rmtree(target)
                self.log("  已清理重复的菜单目录：%s" % target)
            except OSError as e:
                self.log("  清理重复菜单目录失败（不影响使用）：%s" % e)

    # ── 老布局迁移（2.x → 3.0）────────────────────────────────────────
    def migrate_legacy(self):
        """把 2.x 的 C:\\legacy 布局就地/跨盘迁成 3.0 布局。

        返回 "none" / "same" / "cross"。任何一步失败都只记日志、不抛异常：
        迁移失败时还有"全新下载"这条路，不能把安装卡死。
        """
        old = find_legacy_root()
        if not old:
            return "none"
        same = os.path.normcase(os.path.abspath(old)) == \
            os.path.normcase(os.path.abspath(self.base))
        mode = "same" if same else "cross"
        self.log("发现 2.x 老布局：%s → 迁移到 %s（%s）"
                 % (old, self.base, "原地改名" if same else "跨盘搬运"))
        self.prog(0, "迁移老版本安装")
        try:
            # ① 运行环境：优先用安装器自带的 embed（AsrSubs\.venv），
            #    没有就退回开发机的 runtime。只搬"真的带 python.exe"的那份。
            for cand, tag in ((os.path.join(old, "AsrSubs", ".venv"), "安装器自带"),
                              (os.path.join(old, "engine", ".venv"), "开发机")):
                if _has_python(cand):
                    self._move_tree(cand, self.runtime, "运行环境（%s）" % tag)
                    break
            # 另一份 venv 已纯属累赘（新版只用 runtime）：先删掉再搬引擎，
            # 否则 3 GB 会被一起搬进 engine。只在 runtime 真有 python.exe 时删。
            if _has_python(self.runtime):
                for p in (os.path.join(old, "engine", ".venv"),
                          os.path.join(old, "AsrSubs", ".venv")):
                    if os.path.isdir(p):
                        _remove_tree(p)
                        self.log("  已清理多余的虚拟环境：%s" % p)
            # ② 引擎：engine → engine（跳过 .venv）
            self._move_tree(os.path.join(old, "engine"), self.engine, "引擎",
                            skip=(".venv",))
            # ③ 模型：老布局里 <工具>\models 常常是【指向 <根>\models 的
            #    junction】（2.x 安装器就这么建的）。★ 必须在搬 <工具> 之前
            #    处理：copytree 会跳过重解析点，先把 <工具> 搬走的话，模型
            #    真身会留在老根里 —— 新装看着"少了模型"，白白重下 3.3 GB。
            legacy_tool = os.path.join(old, "AsrSubs")
            tool_models = os.path.join(legacy_tool, "models")
            if _is_reparse(tool_models):
                real_models = os.path.realpath(tool_models)
                self.log("  模型目录是 junction → 真身 %s" % real_models)
                self._move_tree(real_models, self.models_root, "模型（junction 真身）")
                try:
                    os.rmdir(tool_models)      # 只摘链接，不动目标
                except OSError:
                    pass
            # ④ 数据目录：AsrSubs → data（models / 字幕输出 随后各归各位）
            self._move_tree(legacy_tool, self.data, "数据目录")
            # ⑤ 模型提到根目录（老布局里 models 是普通子目录时走这条；
            #    junction 的情况上面已经搬完，这里会因目标存在而跳过）
            self._move_tree(os.path.join(self.data, "models"), self.models_root,
                            "模型")
            # ⑥ 字幕产物：字幕输出 → data\output（用户文件，绝不丢）
            self._move_tree(os.path.join(self.data, "字幕输出"), self.output,
                            "字幕产物")
            for d in (self.models_root, self.data, self.output, self.logs,
                      self.cache_dir, self.docs):
                os.makedirs(d, exist_ok=True)
            self.write_paths()
        except Exception as e:
            self.log("⚠ 迁移中断：%s: %s（已搬过去的文件可以继续用）"
                     % (type(e).__name__, e))

        if not same:
            # 旧根整棵改名保留：绝不直接删，用户自己确认没问题再删
            leftover = old + "_migrated_" + time.strftime("%Y%m%d-%H%M%S")
            try:
                if os.path.isdir(old) and not os.path.exists(leftover):
                    os.rename(old, leftover)
                    self.log("旧目录已改名保留：%s（确认新版本正常后可手动删除）"
                             % leftover)
            except OSError as e:
                self.log("旧目录改名失败（可手动删除）：%s" % e)
        # 搬空的中间目录（老布局的 "AsrSubs\"）顺手收掉，别留一堆空壳
        for p in (os.path.join(old, "AsrSubs"),):
            try:
                os.rmdir(p)
            except OSError:
                pass
        self.log("迁移完成：%s" % mode)
        return mode

    def _move_tree(self, src, dst, label, skip=()):
        """把 src 搬到 dst。同卷直接 rename（秒级），跨卷复制+校验+删源。

        ★ 三条硬规则：
          1) dst 已存在一律跳过 —— 重跑安装不能覆盖已经装好的环境；
          2) src 是 junction 时先解析到真身，搬完只摘掉链接（绝不跟进去删目标）；
          3) 跨卷复制后比对「文件数 + 总字节」，不一致就保留源、报错退出。
        skip 里的子目录名不参与复制（调用方已经把它们搬到别处了）。
        """
        if not src or not os.path.isdir(src):
            return False
        if os.path.isdir(dst):
            self.log("  %s：目标已存在，跳过（%s）" % (label, dst))
            return True

        link = _is_reparse(src)
        real = os.path.realpath(src) if link else src
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)

        try:
            os.rename(real, dst)          # 同卷：瞬间完成
            if link:
                try:
                    os.rmdir(src)
                except OSError:
                    pass
            self.log("  %s：%s → %s（重命名）" % (label, real, dst))
            return True
        except OSError:
            pass                          # 跨卷，落到下面复制

        self.log("  %s：跨盘复制 %s → %s（大文件，请稍候）" % (label, real, dst))
        skip = tuple(skip or ())

        def _ignore(d, names):
            out = _ignore_reparse(d, names)
            for n in names:
                if n in skip and n not in out:
                    out.append(n)
            return out

        n_src, sz_src = _tree_stats(real, skip)
        try:
            shutil.copytree(real, dst, symlinks=False,
                            ignore=_ignore, dirs_exist_ok=True)
        except Exception as e:
            self.log("  %s：复制失败 %s: %s（源文件保留）"
                     % (label, type(e).__name__, e))
            return False
        n_dst, sz_dst = _tree_stats(dst)
        if (n_dst, sz_dst) != (n_src, sz_src):
            self.log("  %s：复制校验不一致（%d/%d 文件，%d/%d 字节），保留源文件"
                     % (label, n_dst, n_src, sz_dst, sz_src))
            return False
        # ★ 跳过项如果还在源里（调用方没搬走），就不能删源 —— 那是唯一副本
        left = [n for n in skip if os.path.exists(os.path.join(real, n))]
        if left:
            self.log("  %s：已复制，但源里的 %s 未搬走，源目录保留不删"
                     % (label, "、".join(left)))
            return True
        # 校验通过才删源
        if link:
            try:
                os.rmdir(src)
            except OSError:
                pass
        shutil.rmtree(real, ignore_errors=True)
        self.log("  %s：已完成（%d 个文件，%s）" % (label, n_dst, human(sz_dst)))
        return True

    def deploy(self):
        self.begin(*self.W["deploy"], text="部署脚本")
        src = payload_dir()
        if not os.path.isdir(src):
            self.die("安装器内部缺少脚本文件（payload），请重新下载本安装器。")

        # 六个一级目录一次建齐：engine 给脚本、data 给用户数据、
        # docs 给说明、runtime/models 由前面几步填内容。
        for d in (self.engine, os.path.join(self.engine, "rt"), self.runtime,
                  self.models_root, self.data, self.output, self.logs,
                  self.cache_dir, self.docs):
            os.makedirs(d, exist_ok=True)

        for f in os.listdir(src):
            # ★ 排除 get-pip.py：它是安装时用来装 pip 的（已内嵌进 payload），
            #   不该被当成引擎脚本一起拷进 engine。
            if f.endswith(".py") and f != "get-pip.py":
                shutil.copy2(os.path.join(src, f), os.path.join(self.engine, f))
        shutil.copy2(os.path.join(src, "AsrSubs.lua"),
                     os.path.join(self.engine, "AsrSubs.lua"))
        # WAV 渲染预设：渲染导出靠它设格式（中文版 Resolve 上内置
        # 'Audio Only' 预设名被本地化，LoadRenderPreset 必失败）。
        # Lua 按「脚本同级 → 引擎目录」的顺序找它，两处都要放。
        if os.path.isfile(os.path.join(src, "render_to_wav.xml")):
            shutil.copy2(os.path.join(src, "render_to_wav.xml"),
                         os.path.join(self.engine, "render_to_wav.xml"))
        else:
            self.log("  ⚠ payload 里没有 render_to_wav.xml，渲染导出将走格式探测")
        self.log("引擎已部署到 %s" % self.engine)

        # ★★ 路径单一来源：把真实路径写成两份，谁都不再猜目录名。
        #   paths.json → Python（asr_engine / worker 启动解释器时读）
        #   paths.lua  → Lua（loadfile 读，绕开 io.open 打不开中文路径的问题）
        self.write_paths()
        self.log("路径清单：%s\\paths.json / paths.lua" % self.engine)

        # ★ 中文门面已取消：只保留 <BASE> 一个目录（用户明确要求）。
        #   老版本建过的 <BASE>字幕工具 在这里顺手摘掉 —— 它只是链接，
        #   摘掉不影响任何数据；不摘的话升级后盘上永远多一个 0 字节目录。
        entry_note = self.drop_entry_junction()
        if entry_note:
            self.log("  " + entry_note)

        # Lua 主脚本要放进 DaVinci 的脚本目录，菜单里才会出现。
        # 同时把它认的引擎路径改成实际安装位置（支持装到任意盘/目录）。
        lua_src = open(os.path.join(src, "AsrSubs.lua"), encoding="utf-8").read()
        escaped = self.engine.replace("\\", "\\\\")   # C:\a\b -> C:\\a\\b（Lua 字符串要转义）
        if "--<<INSTALL_ROOT>>" in lua_src:
            # ★★ repl 必须用 lambda：re.sub 会把替换串里的 `\\` 当转义吃掉，
            #    直接传字符串的话 `C:\\Users` 会被写成 `C:\Users` —— `\U` 在
            #    Lua 里是非法转义，脚本一加载就报 token recognition error。
            #    （实测踩到：临时目录含 \U 时直接语法错误。）
            lua_src = re.sub(
                r'local INSTALLED_ROOT = "[^"]*"\s*--<<INSTALL_ROOT>>',
                lambda _m: 'local INSTALLED_ROOT = "%s"' % escaped,
                lua_src, count=1)
        else:
            self.log("  ⚠ Lua 里没找到 INSTALL_ROOT 标记，跳过路径改写")
        self.log("引擎路径写入 Lua：%s" % self.engine)
        # ★ 顺手把【改写过的】Lua 也写回 engine 一份：万一安装时没找到 DaVinci
        #   脚本目录，下面会提示用户手动复制 {engine}\AsrSubs.lua —— 若那份还留着
        #   未改写的 INSTALL_ROOT，自定义安装目录时脚本按盘符也探不到引擎，
        #   复制过去照样打不开。这里直接覆盖成正确版本。
        try:
            with open(os.path.join(self.engine, "AsrSubs.lua"), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(lua_src)
        except Exception:
            pass

        placed = []
        chosen = pick_script_dir()
        if chosen:
            try:
                target = os.path.join(chosen, MENU_DIR)
                os.makedirs(target, exist_ok=True)
                with open(os.path.join(target, "AsrSubs.lua"), "w",
                          encoding="utf-8", newline="\n") as f:
                    f.write(lua_src)
                # 渲染预设必须和 Lua 放同一目录（Lua 用 debug.getinfo
                # 拿脚本目录，第一候选就是这里）
                xml_src = os.path.join(src, "render_to_wav.xml")
                if os.path.isfile(xml_src):
                    shutil.copy2(xml_src,
                                 os.path.join(target, "render_to_wav.xml"))
                placed.append(target)
            except Exception as e:
                self.log("  写入 %s 失败：%s" % (chosen, type(e).__name__))
        # 每个脚本根目录都清一遍 2.x 留下的「AsrSubs」目录（升级场景）
        for d in resolve_script_dirs():
            self._drop_legacy_menu_dir(d)
        # ★ 只留一份菜单：老版本会把同一份 lua 写进 ProgramData 和 AppData，
        #   Resolve 两个根都扫 → 菜单里两个 AsrSubs。这里把多余那份清掉。
        self.drop_duplicate_menu_dirs(chosen)
        if placed:
            for p in placed:
                self.log("已安装到 DaVinci 脚本目录：%s" % p)
        else:
            self.log("⚠ 没找到 DaVinci 脚本目录（DaVinci 可能没装过）。")
            self.log("  装好 DaVinci 后，把 %s\\AsrSubs.lua 复制到：" % self.engine)
            self.log("  %%ProgramData%%\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Utility\\%s\\"
                     % MENU_DIR)

        # 使用说明
        try:
            shutil.copy2(os.path.join(src, "使用说明.txt"),
                         os.path.join(self.docs, "使用说明.txt"))
        except Exception:
            pass

        # 卸载清单：Inno 卸载时按它精确清理 DaVinci 脚本目录 ——
        # 只删我们真的写进去的，没部署过就一个都不碰。
        # 路径统一成正斜杠：JSON 会把反斜杠转义成 \\，Inno 的 Pascal 代码
        # 做字符串查找时对不上。
        if getattr(sys, "frozen", False):
            try:
                man = os.path.join(os.path.dirname(sys.executable), "_deployed.json")
                with open(man, "w", encoding="utf-8") as f:
                    json.dump({"resolve_dirs": [p.replace("\\", "/") for p in placed],
                               "root": self.base.replace("\\", "/"),
                               "engine": self.engine.replace("\\", "/"),
                               "runtime": self.runtime.replace("\\", "/"),
                               "models": self.models_root.replace("\\", "/"),
                               "data": self.data.replace("\\", "/"),
                               "docs": self.docs.replace("\\", "/")},
                              f, ensure_ascii=False, indent=1)
            except Exception:
                pass
        self.step(1, "脚本部署完成")

    # ── ⑥ 冒烟测试 ──────────────────────────────────────────────────
    def smoke(self):
        """真跑一次识别，不只是加载模型。

        ★ 两个坑都在这里踩过：
          ① 以前没传 asr_model，Engine 回退到【本机】的默认模型路径，
             于是"测试通过"其实测的是旧模型，新装的那份根本没被验证。
          ② 只 load() 不 generate()，dtype/算子那类问题暴露不出来
             （CPU 上 bf16 的坑就是这么藏住的）。
        """
        self.begin(*self.W["smoke"], text="验证能不能跑起来")
        code = (
            "import sys, math, struct, wave, tempfile, os\n"
            "sys.path.insert(0, " + repr(self.engine) + ")\n"
            "from asr_engine import Engine\n"
            "e = Engine(log=print, asr_model=" + repr(self.models) + ")\n"
            "e.load()\n"
            "print('MODEL_DIR=' + str(e.asr_path))\n"
            "print('ALIGNER_DIR=' + str(getattr(e, 'aligner_path', '')))\n"
            "print('ALIGNER_OK=' + ('1' if getattr(e, 'align_model', None) is not None else '0'))\n"
            "sr = 16000\n"
            "p = os.path.join(tempfile.gettempdir(), 'asr_smoke.wav')\n"
            "w = wave.open(p, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)\n"
            "w.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(2 * math.pi * 440 * i / sr)))\n"
            "                       for i in range(sr * 3)))\n"
            "w.close()\n"
            "blocks, stats = e.run(p, lang='Chinese', max_chars=12)\n"
            "print('SMOKE_OK device=' + str(e.device) + ' blocks=' + str(len(blocks)))\n"
        )
        out = []

        def cb(l):
            out.append(l)
            self.log("  " + l[:200])

        rc = run([self.vpy, "-c", code], cb=cb, cancel=self.cancel, timeout=900)
        joined = "\n".join(out)
        if rc != 0 or "SMOKE_OK" not in joined:
            self.log("⚠ 冒烟测试没通过（返回码 %d）。上面几行是原因。" % rc)
            self.log("  torch / transformers 装了，但模型或算子跑不起来。")
            self.log("  先看上面报错，或把这段日志发我。")
        elif self.gpu and "device=cuda" not in joined.lower():
            # ★ v1.0.1：选了 GPU 版就必须真的跑到 CUDA 上。
            #   以前这里只认 SMOKE_OK —— 装的是 GPU 版、实际跑在 CPU 上，
            #   也照样打"✓ 冒烟测试通过"，用户永远发现不了。
            self.log("⚠ 冒烟测试跑通了，但用的是【CPU】—— 装的是 GPU 版却没启用 CUDA。")
            self.log("  先更新 NVIDIA 驱动（CUDA 12.8 需 570 以上），再重跑安装器。")
            self.log("  急用可以先这么跑，但识别会慢 5~10 倍。")
        elif self.models.replace("/", "\\").lower() not in joined.lower():
            self.log("⚠ 冒烟测试跑的是别处的模型，不是刚装的那份：")
            self.log("  " + (joined.split("MODEL_DIR=") + [""])[1][:160])
        elif "ALIGNER_OK=1" not in joined:
            # ★ 对齐模型没加载 ≠ 装失败，但绝不能报"一切正常"：
            #   这时的字幕时间戳是按字数比例估算的，精度明显差一档。
            self.log("⚠ 冒烟测试通过，但对齐模型没加载 —— 时间轴精度降级为「按字数估算」。")
            self.log("  对齐模型目录：%s" % self.aligner)
            self.log("  可重新运行安装器补下（已装好的部分会跳过）。")
        else:
            self.log("✓ 冒烟测试通过：识别模型 + 对齐模型都能正常加载")
        self.step(1, "完成")

    # ── ⑦ 收尾：清掉下载缓存 ────────────────────────────────────────
    def clean_cache(self):
        """安装成功后清掉下载缓存。

        Python embed zip + torch wheel（GPU 版 2.7 GB）+ 模型分片都堆在
        %TEMP%\\qwen3asr_dl 里，装完就没用了 —— 不清的话一台机器白占几个 GB。
        这个目录在 {app} 之外，Inno 卸载器不管它（[UninstallDelete] 只管
        {app} 下的几棵子树），所以只能在这里收尾。

        ★ 只在全部步骤成功后才调：中途失败还要靠缓存续传，不能提前清。
        """
        try:
            if not os.path.isdir(self.cache):
                return
            shutil.rmtree(self.cache, ignore_errors=True)
            if os.path.isdir(self.cache):
                self.log("下载缓存没能完全清掉：%s（可手动删除）" % self.cache)
            else:
                self.log("已清理下载缓存：%s" % self.cache)
        except Exception as e:
            self.log("清理下载缓存失败（不影响使用）：%s" % e)


def resolve_script_dirs():
    """DaVinci 会扫描的脚本目录（按优先级）。返回存在的那些。

    全新机器上 Utility 目录要用户手动建过才存在；DaVinci 装着的时候就
    直接认 ProgramData 那处 —— 部署时 makedirs 会把它建出来（Inno 安装包
    是管理员权限，建得动；单独跑 exe 建不动时会记日志走手动兜底）。
    """
    out = []
    env = os.environ
    pd = env.get("ProgramData", r"C:\ProgramData")
    ad = env.get("APPDATA", os.path.expanduser("~"))
    cands = [
        os.path.join(pd, "Blackmagic Design", "DaVinci Resolve", "Fusion", "Scripts", "Utility"),
        os.path.join(ad, "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility"),
        os.path.join(env.get("ProgramFiles", r"C:\Program Files"), "Blackmagic Design",
                     "DaVinci Resolve", "Fusion", "Scripts", "Utility"),
    ]
    for c in cands:
        if os.path.isdir(c) and c not in out:
            out.append(c)
    if not out:
        dv = os.path.join(env.get("ProgramFiles", r"C:\Program Files"),
                          "Blackmagic Design", "DaVinci Resolve")
        if os.path.isdir(dv) and cands[0] not in out:
            out.append(cands[0])
    return out


def pick_script_dir():
    """只在【一个】DaVinci 脚本目录里写菜单。返回该目录，没有就 None。

    ★★ 为什么不能"哪个存在就都写一份"（2.x/3.0 初版就是这么写的）★★
      Resolve 把 ProgramData、AppData\\...\\Support、Program Files 这几处的
      Fusion\\Scripts 全都扫一遍并进「脚本」菜单 —— 同一个 lua 写两份，
      菜单里就出现两个一模一样的 AsrSubs 子菜单（实测截图就是这个）。
      多发一份没有任何好处，还会让"我改的是哪份"变成猜谜。
      所以：只写优先级最高的那一处，其余的只做清理（见 drop_duplicate_menu_dirs）。
    """
    dirs = resolve_script_dirs()
    return dirs[0] if dirs else None


# ── GUI ───────────────────────────────────────────────────────────────
class App:
    def __init__(self, root, base=None, auto=False):
        self.root = root
        self.base_arg = base      # Inno 传来的安装目录；None = 手动选
        self.q = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.done = False
        # ★ v1.1.0：一键安装开关。安装包用 --auto 拉起本程序时，界面直接
        #   开始下载部署，不用再点一次「开始安装」；位置由安装包给定、
        #   CPU/GPU 用检测出来的默认值。用户仍可随时按「取消」。
        self.auto = bool(auto)

        root.title("%s · 安装器" % APP_NAME)
        root.geometry("720x560")
        root.resizable(False, False)

        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(root)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="%s 安装器" % APP_NAME,
                  font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", **pad)
        ttk.Label(frm, text="本安装器只含脚本。运行环境与模型在下面选择后下载。",
                  foreground="#666").pack(anchor="w", padx=12)

        # 安装位置
        row = ttk.Frame(frm)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="安装位置", width=10).pack(side="left")
        self.var_base = tk.StringVar(value=base or default_base())
        ent = ttk.Entry(row, textvariable=self.var_base)
        ent.pack(side="left", fill="x", expand=True, padx=6)
        if base:
            # 安装包已经选好位置并建好目录，锁掉不让改（改了会和卸载清单对不上）
            ent.config(state="readonly")
            ttk.Label(row, text="由安装包指定", foreground="#888").pack(side="left")
        else:
            ttk.Button(row, text="浏览…", width=8,
                       command=self.pick_dir).pack(side="left")
            ttk.Label(frm, text="可以装在任意本地硬盘的任意目录"
                                "（路径只能是英文/数字，不能有中文）",
                      foreground="#666").pack(anchor="w", padx=12)

        # 版本
        # ★ v4.8.24：先探测显卡再决定默认值 —— 有 NVIDIA 卡就默认 GPU 版，
        #   否则默认 CPU 版。以前一律默认 CPU，两张机器装完都在跑 CPU。
        row2 = ttk.Frame(frm)
        row2.pack(fill="x", **pad)
        ttk.Label(row2, text="运行版本", width=10).pack(side="left")
        has_gpu, gpu_name, drv_ver = detect_nvidia()
        # ★ v1.0.1：驱动与显卡型号传给安装线程，用来挑 CUDA 通道
        self.gpu_name = gpu_name
        self.drv = drv_ver
        self.var_gpu = tk.BooleanVar(value=bool(has_gpu))
        ttk.Radiobutton(row2, text="CPU 版（下载约 1.8 GB，普通电脑都能用）",
                        variable=self.var_gpu, value=False,
                        command=self.refresh_tip).pack(anchor="w")
        ttk.Radiobutton(
            row2,
            text="GPU 版（下载约 4.4 GB，需 NVIDIA 显卡，识别快数倍）"
                 + ("　← 检测到 %s，已默认选中" % gpu_name if has_gpu else ""),
            variable=self.var_gpu, value=True,
            command=self.refresh_tip).pack(anchor="w")
        if not has_gpu:
            ttk.Label(row2, text="　未检测到 NVIDIA 显卡（nvidia-smi 无输出），"
                                 "建议保持 CPU 版",
                      foreground="#666").pack(anchor="w", padx=12)
        else:
            # ★ v1.0.1：把驱动版本摆出来。cu128 的轮子要求较新的驱动，
            #   驱动太旧会"装得上、用不了"——先提醒一句，省 2.7 GB 白下。
            try:
                major = int(str(drv_ver).split(".")[0])
            except Exception:
                major = 0
            if major and major < 570:
                ttk.Label(row2,
                          text="　驱动 %s 偏旧：CUDA 12.8 一般要 570 以上。"
                               "装完会验证；跑不起来就更新驱动或改选 CPU 版"
                               % drv_ver,
                          foreground="#B35C00").pack(anchor="w", padx=12)
            elif drv_ver:
                ttk.Label(row2, text="　驱动版本 %s" % drv_ver,
                          foreground="#666").pack(anchor="w", padx=12)

        # 下载源
        row3 = ttk.Frame(frm)
        row3.pack(fill="x", **pad)
        ttk.Label(row3, text="下载源", width=10).pack(side="left")
        self.var_mirror = tk.BooleanVar(value=True)
        ttk.Radiobutton(row3, text="国内镜像（推荐）", variable=self.var_mirror,
                        value=True).pack(side="left")
        ttk.Radiobutton(row3, text="官方源", variable=self.var_mirror,
                        value=False).pack(side="left", padx=12)

        self.lbl_tip = ttk.Label(frm, text="", foreground="#1D9E75")
        self.lbl_tip.pack(anchor="w", padx=12, pady=2)

        # 进度
        pf = ttk.Frame(frm)
        pf.pack(fill="x", padx=12, pady=(10, 2))
        self.var_stage = tk.StringVar(value="就绪")
        ttk.Label(pf, textvariable=self.var_stage).pack(anchor="w")
        self.bar = ttk.Progressbar(pf, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=2)

        # 日志
        lf = ttk.Frame(frm)
        lf.pack(fill="both", expand=True, padx=12, pady=6)
        self.txt = tk.Text(lf, height=12, wrap="word", font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)

        # 按钮
        bf = ttk.Frame(frm)
        bf.pack(fill="x", padx=12, pady=(0, 10))
        self.btn_go = ttk.Button(bf, text="开始安装", command=self.go)
        self.btn_go.pack(side="left")
        self.btn_cancel = ttk.Button(bf, text="取消", command=self.do_cancel,
                                     state="disabled")
        self.btn_cancel.pack(side="left", padx=8)
        ttk.Button(bf, text="退出", command=root.destroy).pack(side="right")

        self.refresh_tip()
        self.root.after(80, self.pump)
        if self.auto:
            self.root.after(700, self._auto_go)

    def _auto_go(self):
        """一键安装：界面起来后自动开始（见 __init__ 的说明）。"""
        if self.done:
            return
        self.say("一键安装：按检测结果自动开始（位置 %s / 版本 %s）…"
                 % (self.var_base.get().strip() or default_base(),
                    "GPU" if self.var_gpu.get() else "CPU"))
        self.go()

    def refresh_tip(self):
        # ★ 两个模型：ASR 1.8GB + ForcedAligner 1.5GB。以前只算 ASR 一份，
        #   装完才发现少了对齐模型 —— 现在提示按真实体积说。
        if self.var_gpu.get():
            self.lbl_tip.config(text="将下载约 6 GB：torch GPU 版 2.7 GB + 两个模型 3.3 GB + Python 与其它 0.2 GB")
        else:
            self.lbl_tip.config(text="将下载约 3.4 GB：两个模型 3.3 GB + torch CPU 版 0.1 GB + Python 与其它 0.2 GB")

    def pick_dir(self):
        d = filedialog.askdirectory(initialdir=self.var_base.get() or default_base())
        if d:
            self.var_base.set(d.replace("/", "\\"))

    def say(self, msg):
        self.q.put(("log", msg))

    def prog(self, pct, text):
        self.q.put(("prog", (max(0, min(100, pct)), text)))

    def pump(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "log":
                    self.txt.insert("end", val + "\n")
                    self.txt.see("end")
                elif kind == "prog":
                    self.bar["value"] = val[0]
                    self.var_stage.set(val[1])
                elif kind == "end":
                    self.finish(val)
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    def finish(self, ok_msg):
        self.btn_go.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.done = True
        if ok_msg is True:
            self.bar["value"] = 100
            self.var_stage.set("安装完成")
            if getattr(self, "auto", False):
                # ★ v1.1.0：一键模式不再弹「完成」框（那又是一次点击），
                #   窗口 6 秒后自己关；日志留在界面上，关之前能看。
                self.say("✓ 安装完成，本窗口 6 秒后自动关闭。")
                self.root.after(6000, self.root.destroy)
            else:
                messagebox.showinfo("完成",
                                    "安装完成。\n\n打开 DaVinci Resolve → 顶部菜单「工作区」→「脚本」→"
                                    "「AsrSubs」→「AsrSubs」。\n\n如果菜单里没有，重启一下 Resolve。")
        elif ok_msg == "cancelled":
            self.var_stage.set("已取消")
        else:
            self.var_stage.set("安装失败")
            messagebox.showerror("安装失败", str(ok_msg)[:400])

    def go(self):
        # ★ 归一化：末尾的空格 / 反斜杠会让 <base>\engine 变成 <base>\\engine，
        #   mklink 和 os.rename 都会因此多一级/少一级。
        base = self.var_base.get().strip().rstrip("\\/")
        if not base:
            messagebox.showwarning("缺少信息", "请选择安装位置")
            return
        gpu = self.var_gpu.get()
        # ★ 三条硬校验（ASCII / 本地固定盘 / 剩余空间）：宁可现在拦下，
        #   也别让用户等 20 分钟下载完，才发现插件一跑就"任务超时"。
        okv, why = validate_base(base, gpu)
        if not okv:
            messagebox.showerror("安装位置不可用", why)
            return
        # 提前探一下能不能写，避免下载完了才发现装不进去
        try:
            os.makedirs(base, exist_ok=True)
            probe = os.path.join(base, ".write_test")
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
        except Exception as e:
            messagebox.showerror("装不了", "无法写入 %s\n%s\n\n请换一个位置（如 %s），"
                                           "或以管理员身份运行。" % (base, e, DEFAULT_BASE))
            return

        self.txt.delete("1.0", "end")
        self.btn_go.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.cancel.clear()
        mirror = self.var_mirror.get()

        def work():
            try:
                inst = Installer(base, gpu, mirror,
                                 self.say, self.prog, self.cancel,
                                 driver=getattr(self, "drv", ""),
                                 gpu_name=getattr(self, "gpu_name", ""))
                self.say("安装位置：%s" % base)
                self.say("版本：%s　下载源：%s" % ("GPU" if gpu else "CPU",
                                                  "国内镜像" if mirror else "官方"))
                # ★ v1.0.1：下载缓存落在系统临时目录，和安装目录不是同一块盘。
                #   validate_base 只校验了安装盘 —— C 盘紧张的机器会在装到
                #   一半（torch 轮子 2.5 GB 正在写）时才失败，前面白等 20 分钟。
                try:
                    tmp_free = shutil.disk_usage(tempfile.gettempdir()).free
                    need_tmp = (4 if gpu else 3) << 30
                    if tmp_free < need_tmp:
                        self.say("⚠ 系统临时盘只剩 %.1f GB：下载缓存还要 %.0f GB，"
                                 "可能装到一半失败。建议先清理 C 盘，"
                                 "或把 TEMP 指到大一点的盘。" % (
                                     tmp_free / float(1 << 30),
                                     need_tmp / float(1 << 30)))
                except Exception:
                    pass
                # ★ 先看有没有 2.x 老布局：有就搬过来，装好的环境和模型
                #   直接复用（不重下 6GB）。失败也只是退回全新下载，不阻断。
                inst.migrate_legacy()
                if self.cancel.is_set():
                    self.q.put(("end", "cancelled")); return
                inst.ensure_python()
                if self.cancel.is_set():
                    self.q.put(("end", "cancelled")); return
                inst.make_venv()
                if self.cancel.is_set():
                    self.q.put(("end", "cancelled")); return
                inst.pip_install()
                if self.cancel.is_set():
                    self.q.put(("end", "cancelled")); return
                inst.fetch_model()
                if self.cancel.is_set():
                    self.q.put(("end", "cancelled")); return
                inst.deploy()
                inst.smoke()
                inst.clean_cache()
                self.say("全部完成。")
                self.q.put(("end", True))
            except Exception as e:
                self.say("出错：%s: %s" % (type(e).__name__, e))
                self.q.put(("end", str(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def do_cancel(self):
        self.cancel.set()
        self.say("正在取消（当前步骤结束后停止）…")
        self.btn_cancel.config(state="disabled")


def main():
    if tk is None:
        print("缺少 tkinter，无法启动界面"); return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    base = parse_base_arg()
    # ★ v1.1.0：一键安装。Inno 的 [Run] 传 --auto，界面起来后自动开始。
    auto = any(str(a).lower() in ("--auto", "/auto", "-auto", "--oneclick")
               for a in sys.argv[1:])
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App(root, base=base, auto=auto)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        p = os.path.join(tempfile.gettempdir(), "qwen3asr_installer_crash.txt")
        try:
            open(p, "w", encoding="utf-8").write(traceback.format_exc())
        except Exception:
            pass
        raise
