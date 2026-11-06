# -*- coding: utf-8 -*-
"""只读巡检：连接 Resolve 并核对环境与部署一致性，不修改任何时间线内容。

用法：
    python tools\\check_live_readonly.py        （需 DaVinci 在运行且开启外部脚本）

退出码：0 全部通过；1 有失败项。
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

import land_subtitle as L  # noqa: E402

PAIRS = [
    ("AsrSubs.lua", "AsrSubs.lua", None),
    ("land_subtitle.py", "land_subtitle.py", None),
    ("subs_io.py", "subs_io.py", None),
    ("cut_inplace.py", "cut_inplace.py", None),
]
DEPLOYED_LUA = (r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Fusion\Scripts"
                r"\Utility\AsrSubs\AsrSubs.lua")

ok = fail = skip = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print("  [OK]   %s %s" % (name, extra))
    else:
        fail += 1
        print("  [FAIL] %s %s" % (name, extra))


def sk(name, why):
    global skip
    skip += 1
    print("  [SKIP] %s（%s）" % (name, why))


def md5(p):
    if not os.path.exists(p):
        return "MISSING"
    with open(p, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()[:8]


def main():
    r = L.connect()
    chk("连接 Resolve", r is not None,
        "" if r else "（未运行 / 未开启外部脚本）")
    if r:
        pm = r.GetProjectManager()
        proj = pm.GetCurrentProject() if pm else None
        chk("取当前工程", proj is not None, proj.GetName() if proj else "")
        tl = proj.GetCurrentTimeline() if proj else None
        if tl:
            chk("取当前时间线", True, tl.GetName() or "")
            try:
                fps = float(tl.GetSetting("timelineFrameRate") or 0)
            except (TypeError, ValueError):
                fps = 0.0
            chk("帧率可读", fps > 0, "%.3f" % fps)
            nv = tl.GetTrackCount("video") or 0
            ns = tl.GetTrackCount("subtitle") or 0
            print("  · 视频轨 %d / 字幕轨 %d / 帧 %s..%s"
                  % (nv, ns, tl.GetStartFrame(), tl.GetEndFrame()))
            chk("SetTrackEnable 可调用", callable(getattr(tl, "SetTrackEnable", None)))
            chk("GetIsTrackEnabled 可调用", callable(getattr(tl, "GetIsTrackEnabled", None)))
        else:
            sk("时间线相关检查", "当前没有打开的时间线")

    for name, fname, _ in PAIRS:
        a = os.path.join(SRC, fname)
        b = os.path.join(r"D:\AsrSubs\engine", fname)
        same = md5(a) == md5(b)
        if name == "AsrSubs.lua" and os.path.exists(DEPLOYED_LUA):
            same = same and md5(a) == md5(DEPLOYED_LUA)
        chk("部署一致 " + name, same, md5(a))

    print("\n通过 %d / 失败 %d / 跳过 %d" % (ok, fail, skip))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())