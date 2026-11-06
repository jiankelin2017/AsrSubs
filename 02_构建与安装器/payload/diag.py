# -*- coding: utf-8 -*-
"""一键打包诊断信息（v2.0.0 新增）。

用法:
    diag.py <config.json>

config (UTF-8 JSON):
{
  "rt":      "C:\\...\\engine\\rt",              -- 运行时目录（日志/状态/缓存都在里面）
  "base":    "C:\\legacy",                    -- AsrSubs_error.log 所在目录
  "out_dir": "C:\\Users\\...\\Documents\\AsrSubs 诊断",
  "out_lua": "C:\\...\\rt\\diag.lua"
}

产出：
    <out_dir>\\AsrSubs诊断_20261027-1104.zip
    ├─ 环境信息.txt     版本 / Python / torch / transformers / 系统
    ├─ 配置\\*.lua        settings.lua、env.lua、last_run.lua（用户设置与上次运行统计）
    ├─ 日志\\*.log        rt\\logs 下的子进程日志 + engine.log + 界面错误日志
    └─ 状态\\*.json       当轮的 args/cmd/status 等（不含音频，只有路径字符串）

★ 为什么要有它：出问题时用户只会说"不动了"，而排查需要的
  （版本、torch 版本、日志、设置、上次运行统计）散在四五个地方，
  来回问一轮就是半小时。现在点一下给一个 zip。
★ 隐私：只收文本配置与日志，不收音频、不收模型、不联网；
  写入前统一脱敏（用户名 / 环境变量 / 识别文本片段）。
  ★ 日志仍可能含业务文本，外发前请人工复核。
"""
import json
import os
import re
import platform
import shutil
import sys
import time
import zipfile

MAX_LOG_BYTES = 4 << 20      # 单个日志最多收 4MB（够看，又不至于把包撑爆）


def scrub_text(s):
    """诊断包文本脱敏：用户名、环境变量、常见识别文本片段。

    ★ 只打码，不改行结构 —— 排障要能看清「第几行出了什么事」。
    ★ 这是尽力而为的兜底，不是内容审查；业务文本仍需人工复核。
    """
    s = str(s if s is not None else "")
    # C:\Users\Alice\... → C:\Users\<用户>\...
    s = re.sub(r"([A-Za-z]:\\Users\\)[^\\\r\n]+", r"\1<用户>", s, flags=re.I)
    # %USERPROFILE% / %TEMP% 之类
    s = re.sub(r"%[A-Za-z_][A-Za-z0-9_]*%", "<环境变量>", s)
    # 引擎日志里常见的识别文本片段输出（key: 内容 / key= 内容）
    s = re.sub(r"(raw|片段|识别文本|文本片段|transcript)\s*[:=]\s*[^\r\n]+",
               r"\1=<内容已脱敏>", s, flags=re.I)
    return s


def lua_str(s):
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
        else:
            out.append(ch if o >= 0x20 else "\\%d" % o)
    return '"' + "".join(out) + '"'


def write_lua(path, d):
    if not path:
        return
    text = ("return {\n"
            "  ok = %s,\n" % ("true" if d.get("ok") else "false")
            + "  zip = %s,\n" % lua_str(d.get("zip"))
            + "  files = %d,\n" % int(d.get("files") or 0)
            + "  err = %s,\n" % lua_str(d.get("err"))
            + "}\n")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp.%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        pass


def pkg_versions():
    """取包版本。用 importlib.metadata，绝不 import torch ——
    import torch 要几秒且会占显存，诊断包不该有这种副作用。"""
    out = {}
    try:
        from importlib.metadata import version, PackageNotFoundError
    except Exception:
        return out
    for name in ("torch", "torchaudio", "transformers", "numpy",
                 "soundfile", "imageio-ffmpeg", "silero-vad", "huggingface_hub"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = "（未安装）"
        except Exception as e:
            out[name] = "（读取失败：%s）" % type(e).__name__
    return out


def _torch_variant():
    """读 torch/version.py 拿完整版本串（含 +cpu / +cu128）。

    ★ 不 import torch（那要几秒还会占显存），只读纯文本文件；
      importlib.metadata 的 version() 在这台机器上只返回 "2.15.0"，
      丢掉了 +cpu 这个最关键的信息。
    """
    try:
        import importlib.util
        import re as _re
        spec = importlib.util.find_spec("torch")
        if not spec or not spec.origin:
            return ""
        vp = os.path.join(os.path.dirname(spec.origin), "version.py")
        with open(vp, encoding="utf-8", errors="replace") as f:
            head = f.read(4000)
        m = _re.search(r"__version__\s*=\s*['\"]([^'\"]+)", head)
        return m.group(1) if m else ""
    except Exception:
        return ""


def env_text(cfg):
    lines = []
    lines.append("AsrSubs 诊断信息")
    lines.append("=" * 60)
    lines.append("生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    ver = "?"
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from qwen_version import VERSION
        ver = VERSION
    except Exception:
        pass
    lines.append("插件版本：%s" % ver)
    lines.append("")

    lines.append("【运行环境】")
    lines.append("  Python      ：%s" % sys.version.replace("\n", " "))
    lines.append("  解释器路径  ：%s" % scrub_text(sys.executable))
    lines.append("  可执行文件  ：%s" % (scrub_text(sys.executable) if getattr(sys, "frozen", False)
                                        else "（脚本方式运行）"))
    for k, v in pkg_versions().items():
        lines.append("  %-12s：%s" % (k, v))
    # ★ v4.8.24：直接写清"到底跑在什么设备上"。
    #   原来只列 torch 版本号，而 importlib.metadata 拿到的是 "2.15.0"，
    #   看不出这是 +cpu 版；用户配着 RTX 显卡却跑 CPU，诊断包里完全看不出来。
    variant = _torch_variant()
    if variant:
        dev = "CUDA（torch 为 GPU 版）" if "+cu" in variant else \
              "CPU（torch 为 CPU 版，未启用 CUDA）"
        lines.append("  推理设备    ：%s" % dev)
        lines.append("  torch 变体  ：%s" % variant)
    lines.append("")

    lines.append("【系统】")
    lines.append("  平台        ：%s" % platform.platform())
    lines.append("  架构        ：%s" % platform.machine())
    lines.append("  CPU 数      ：%s" % os.cpu_count())
    lines.append("")

    lines.append("【关键路径】")
    for key in ("rt", "base"):
        lines.append("  %-12s：%s" % (key, scrub_text(cfg.get(key)) or "（未提供）"))
    lines.append("")

    # GPU：只读 nvidia-smi，不 import torch
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        gpu = (r.stdout or "").strip()
        lines.append("【显卡】")
        lines.append("  " + (gpu.replace("\n", "\n  ") if gpu else "（nvidia-smi 无输出）"))
        lines.append("")
    except Exception as e:
        lines.append("【显卡】")
        lines.append("  （无法读取：%s）" % type(e).__name__)
        lines.append("")

    return "\n".join(lines)


def collect(zf, src_dir, arc_prefix, patterns, limit=MAX_LOG_BYTES):
    """把 src_dir 下匹配的文件塞进 zip。返回收集数量。"""
    n = 0
    if not src_dir or not os.path.isdir(src_dir):
        return 0
    for name in sorted(os.listdir(src_dir)):
        low = name.lower()
        if not any(low.endswith(p) for p in patterns):
            continue
        p = os.path.join(src_dir, name)
        if not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
            if size > limit:
                continue          # 超大日志跳过，别把诊断包撑成几百 MB
            # ★ P1：文本类统一脱敏后写入；读不出来就跳过，
            #   绝不把原始文件直接塞进诊断包。
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fp:
                    body = scrub_text(fp.read())
                zf.writestr(arc_prefix + "/" + name, body)
            except (OSError, UnicodeError):
                continue
            n += 1
        except OSError:
            continue
    return n


def main():
    res = {"ok": False, "zip": "", "files": 0, "err": ""}
    cfg = {}          # ★ 先给默认值：解析 config 失败时下面写结果文件还要用它
    try:
        if len(sys.argv) < 2:
            raise RuntimeError("缺少参数：config.json")
        with open(sys.argv[1], encoding="utf-8") as f:
            cfg = json.load(f)

        rt = str(cfg.get("rt") or "")
        base = str(cfg.get("base") or "")
        out_dir = str(cfg.get("out_dir") or "")
        out_lua = str(cfg.get("out_lua") or "")
        if not out_dir:
            raise RuntimeError("没有指定输出目录")

        os.makedirs(out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        zip_path = os.path.join(out_dir, "AsrSubs诊断_%s.zip" % stamp)

        n = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("环境信息.txt", env_text(cfg))
            n += 1

            # 配置（settings.lua 里有用户的设置，排查"设置没生效"必备）
            n += collect(zf, rt, "配置",
                         (".lua", ".json", ".txt"), limit=1 << 20)
            # 日志
            n += collect(zf, rt, "日志", (".log",))
            n += collect(zf, os.path.join(rt, "logs"), "日志/子进程", (".log",))
            if base:
                n += collect(zf, base, "日志", ("qwen asr_error.log",))
            # 缓存只收清单，不收内容（内容可能几十 MB）
            try:
                cdir = os.path.join(rt, "cache")
                if os.path.isdir(cdir):
                    items = []
                    for name in sorted(os.listdir(cdir)):
                        p = os.path.join(cdir, name)
                        if os.path.isfile(p):
                            items.append("%s  %.1f MB"
                                         % (name, os.path.getsize(p) / 1048576.0))
                    zf.writestr("缓存清单.txt",
                                "音频/分段缓存文件（未打包，仅列名）：\n"
                                + ("\n".join(items) if items else "（空）"))
                    n += 1
            except OSError:
                pass

        res["ok"] = True
        res["zip"] = zip_path
        res["files"] = n

        # 顺手打开所在目录（失败无所谓）
        try:
            if cfg.get("open", True):
                os.startfile(out_dir)  # noqa: S606  Windows only
        except Exception:
            pass

    except Exception as e:
        res["ok"] = False
        res["err"] = "%s: %s" % (type(e).__name__, e)

    write_lua(str(cfg.get("out_lua") or ""), res)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res.get("ok") else 3)


if __name__ == "__main__":
    main()
