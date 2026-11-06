# -*- coding: utf-8 -*-
"""只读探针：验证 R1 / R3 依赖的 DaVinci 原生 API 行为（不改动任何时间线）。

R1 依赖：GetItemListInTrack("subtitle") + GetStartFrame/GetEndFrame
R3 依赖：timeline.Export(path, EXPORT_SRT) 与 EXPORT_SRT 常量取值
"""
import os
import sys
import json

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

import DaVinciResolveScript as dvr

r = dvr.scriptapp("Resolve")
proj = r.GetProjectManager().GetCurrentProject()
tl = proj.GetCurrentTimeline()
out = {"project": proj.GetName() if proj else None,
       "timeline": tl.GetName() if tl else None,
       "fps": None, "sub_tracks": 0, "items": [], "export_api": None,
       "EXPORT_SRT": None}

if tl:
    try:
        out["fps"] = float(tl.GetSetting("timelineFrameRate") or 24)
    except Exception as e:
        out["fps_err"] = str(e)
    try:
        n = tl.GetTrackCount("subtitle") or 0
        out["sub_tracks"] = n
        for i in range(1, n + 1):
            items = tl.GetItemListInTrack("subtitle", i) or []
            for it in items[:3]:
                try:
                    sf = it.GetStartFrame()
                    ef = it.GetEndFrame()
                except Exception:
                    sf = ef = None
                out["items"].append({
                    "track": i,
                    "start_frame": sf, "end_frame": ef,
                    "start": it.GetStart() if hasattr(it, "GetStart") else None,
                    "end": it.GetEnd() if hasattr(it, "GetEnd") else None,
                })
    except Exception as e:
        out["sub_err"] = str(e)
    # R3：探查 Export API 与 EXPORT_SRT
    out["export_api"] = hasattr(tl, "Export")
    for attr in ("EXPORT_SRT", "EXPORT_SRT_XML", "EXPORT_SUBTITLE"):
        if hasattr(tl, attr):
            try:
                out["EXPORT_SRT"] = getattr(tl, attr)
            except Exception:
                pass
            break
    # 也看看 module 里有没有 EXPORT_SRT 常量
    try:
        mod = sys.modules.get("DaVinciResolveScript")
        for k in dir(mod):
            if "SRT" in k or "EXPORT" in k:
                out.setdefault("constants", {})[k] = getattr(mod, k, None)
    except Exception:
        pass

print(json.dumps(out, ensure_ascii=False, indent=2))
