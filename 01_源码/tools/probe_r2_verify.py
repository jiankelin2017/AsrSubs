# -*- coding: utf-8 -*-
"""R1/R2 只读验证探针：连上后台 DaVinci，只读地确认两条重构依赖的 API 真能拿到数据。

★ 绝不调用 DeleteClips / AppendToTimeline / ImportMedia / AddTrack，
  不会改动用户任何时间线或媒体池 —— 纯只读体检。

验证点：
  R1：GetItemListInTrack("subtitle") + GetStartFrame/GetEndFrame 能读到真实字幕位置。
  R2：① 每个字幕条目能取到来源媒体池项（_is_our_subtitle_item 据此判 stem）；
      ② 字幕轨启用态接口（_get_track_enabled）可读，闭环重挂时能正确恢复。
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
# 直接复用 land_subtitle 里的 R2 判据/读启用态函数，确认它们在真机上不报错
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_subtitle as L

r = dvr.scriptapp("Resolve")
proj = r.GetProjectManager().GetCurrentProject()
tl = proj.GetCurrentTimeline() if proj else None

out = {"connected": True,
       "project": proj.GetName() if proj else None,
       "timeline": tl.GetName() if tl else None,
       "fps": None, "sub_tracks": 0,
       "video_tracks": 0, "audio_tracks": 0,
       "video_items": 0, "audio_items": 0,
       "items": [], "enabled_states": [], "stem_probe_ok": None,
       "enable_api_ok": None, "helper_bindings_ok": None,
       "ready_for_e2e": False, "errors": []}

if not tl:
    out["connected"] = bool(r)
    out["errors"].append("无打开的时间线（R1/R2 需要时间线才有意义，但连接本身 OK）")
else:
    try:
        out["fps"] = float(tl.GetSetting("timelineFrameRate") or 24)
    except Exception as e:
        out["errors"].append("GetSetting: %s" % e)
    try:
        # 就绪度：video/audio 轨是否有内容（落轨需要时间线上有素材）
        nv = tl.GetTrackCount("video") or 0
        na = tl.GetTrackCount("audio") or 0
        out["video_tracks"] = nv
        out["audio_tracks"] = na
        for i in range(1, nv + 1):
            out["video_items"] += len(tl.GetItemListInTrack("video", i) or [])
        for i in range(1, na + 1):
            out["audio_items"] += len(tl.GetItemListInTrack("audio", i) or [])
    except Exception as e:
        out["errors"].append("video/audio enum: %s" % e)
    try:
        n = tl.GetTrackCount("subtitle") or 0
        out["sub_tracks"] = n
        for i in range(1, n + 1):
            # R2：读启用态接口
            try:
                en = L._get_track_enabled(tl, "subtitle", i)
                out["enabled_states"].append({"track": i, "enabled": en})
                out["enable_api_ok"] = True
            except Exception as e:
                out["enable_api_ok"] = False
                out["errors"].append("GetIsTrackEnabled track %d: %s" % (i, e))
            items = tl.GetItemListInTrack("subtitle", i) or []
            for it in items[:5]:
                try:
                    sf = it.GetStartFrame()
                    ef = it.GetEndFrame()
                except Exception:
                    sf = ef = None
                # R2：来源媒体项（_is_our_subtitle_item 据此判 stem）
                src_name = src_path = None
                try:
                    mi = it.GetMediaPoolItem()
                    if mi:
                        src_name = mi.GetName()
                        try:
                            cp = mi.GetClipProperty() or {}
                            src_path = str(cp.get("File Path") or cp.get("Original File Path") or "")
                        except Exception:
                            src_path = ""
                except Exception as e:
                    out["errors"].append("GetMediaPoolItem: %s" % e)
                # 实跑判据函数（不删不改，只是读），确认真机不抛
                try:
                    is_ours = L._is_our_subtitle_item(it, "C:\\fake\\我的直播.srt")
                    out["stem_probe_ok"] = True
                except Exception as e:
                    out["stem_probe_ok"] = False
                    out["errors"].append("_is_our_subtitle_item: %s" % e)
                out["items"].append({
                    "track": i, "start_frame": sf, "end_frame": ef,
                    "src_name": src_name, "src_path": src_path,
                    "is_ours_probe": is_ours,
                })
    except Exception as e:
        out["errors"].append("subtitle enum: %s" % e)
    # 受保护地实跑辅助函数绑定（不删不改，只读路径）：即便无条目也确认导入/调用不抛
    try:
        L._get_track_enabled(tl, "subtitle", 1)   # 轨不存在时返回 None，不抛
        L._is_our_subtitle_item(lambda: None, "C:\\fake\\x.srt")  # 用最小桩对象确认逻辑路径
        out["helper_bindings_ok"] = True
    except Exception as e:
        out["helper_bindings_ok"] = False
        out["errors"].append("helper binding: %s" % e)
    # 端到端就绪判定：有时间线 + 有素材 + 有真实字幕条目（R1/R2 才有真实数据可练）
    out["ready_for_e2e"] = bool(tl) and (out["video_items"] + out["audio_items"]) > 0 and out["sub_tracks"] > 0 and out["stem_probe_ok"] is True

print(json.dumps(out, ensure_ascii=False, indent=2))
