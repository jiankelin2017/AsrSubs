# -*- coding: utf-8 -*-
"""★ 诊断 + 强制清理：查清 R1 读取方式 与 R2 stem 匹配 在真机上的真实表现，
   并把上轮遗留的测试字幕彻底删掉，还原时间线。"""
import os
import sys
import json
import tempfile
import shutil

API = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
LIBS = [
    r"D:\DaVinci Resolve\fusionscript.dll",
    r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
]
os.environ["RESOLVE_SCRIPT_API"] = API
for p in LIBS:
    if os.path.exists(p):
        os.environ["RESOLVE_SCRIPT_LIB"] = p
        break
sys.path.insert(0, os.path.join(API, "Modules"))
# ★ v4.1.0：本脚本在 tools\ 下，被测模块在上一级（01_源码\）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
for _p in (_HERE, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import DaVinciResolveScript as dvr
import land_subtitle as L

r = dvr.scriptapp("Resolve")
proj = r.GetProjectManager().GetCurrentProject()
mp = proj.GetMediaPool()
tl = proj.GetCurrentTimeline()
fps = float(tl.GetSetting("timelineFrameRate") or 24)

out = {"fps": fps, "diagnostics": [], "clear_by_stem": None,
       "force_delete": None, "remaining": None, "errors": []}

# 测试 SRT 的 stem（与 e2e 用的一致），即便文件已删，stem 仍用于匹配判据
TEST_SRT = r"C:\fake\e2e_test_subs.srt"
stem = os.path.splitext(os.path.basename(TEST_SRT))[0].lower()

# 1) 遍历当前所有字幕条，打印真实属性
n = tl.GetTrackCount("subtitle") or 0
items_all = []
for i in range(1, n + 1):
    its = tl.GetItemListInTrack("subtitle", i) or []
    for it in its:
        d = {"track": i}
        for fn in ("GetStart", "GetEnd", "GetStartFrame", "GetEndFrame",
                   "GetDuration"):
            try:
                v = getattr(it, fn, None)
                d[fn] = (v() if callable(v) else None)
            except Exception as e:
                d[fn] = "ERR:%s" % e
        # 来源媒体池项
        try:
            mi = it.GetMediaPoolItem()
            if mi:
                d["mi_name"] = mi.GetName()
                try:
                    cp = mi.GetClipProperty() or {}
                    d["mi_path"] = str(cp.get("File Path") or cp.get("Original File Path") or "")
                except Exception as e:
                    d["mi_path"] = "ERR:%s" % e
            else:
                d["mi_name"] = None
        except Exception as e:
            d["mi_name"] = "ERR:%s" % e
        # 直接跑 R2 判据
        try:
            d["is_ours(stem)"] = L._is_our_subtitle_item(it, TEST_SRT)
        except Exception as e:
            d["is_ours(stem)"] = "ERR:%s" % e
        items_all.append(d)

out["diagnostics"] = items_all
out["stem_under_test"] = stem

# 2) 先试 R2 的 stem 清理（预期应能删掉）
try:
    killed = L.clear_our_subtitle_items(tl, TEST_SRT)
    out["clear_by_stem"] = killed
except Exception as e:
    out["clear_by_stem"] = "ERR:%s" % e

# 3) 强制清理：把剩余字幕条全删（本时间线无用户真实字幕，安全）
remaining_items = []
try:
    for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
        remaining_items += (tl.GetItemListInTrack("subtitle", i) or [])
    if remaining_items:
        tl.DeleteClips(remaining_items, False)
        out["force_delete"] = len(remaining_items)
    else:
        out["force_delete"] = 0
except Exception as e:
    out["errors"].append("force_delete: %s" % e)

# 4) 末检
try:
    rem = 0
    for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
        rem += len(tl.GetItemListInTrack("subtitle", i) or [])
    out["remaining"] = rem
except Exception as e:
    out["errors"].append("remaining: %s" % e)

print(json.dumps(out, ensure_ascii=False, indent=2))
