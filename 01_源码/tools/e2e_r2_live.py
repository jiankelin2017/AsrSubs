# -*- coding: utf-8 -*-
"""★ v4.0.5 受控端到端真机测试（方案 A）—— 验证 R2 闭环重挂 + R1 读取路径。

⚠ 安全约定：
  · 只在【当前打开的时间线】上做最小侵入：临时落一份测试 SRT 字幕 → 触发
    relang（删旧+重挂）→ 末尾用 clear_our_subtitle_items 把测试字幕删干净。
  · 绝不调用任何会改动视频/音频的 API（不粗剪、不重排）；R1 只在真实字幕
    位置上「试算偏移」而不实际切割。
  · try/finally 保证无论中间是否异常，最后都会清理测试字幕并尽量还原。
"""
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
#   与 test_r1/r2 同一修法（见开发文档 §9.5.4「测试搬到 tools 后路径假设失效」）。
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
for _p in (_HERE, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import DaVinciResolveScript as dvr
import land_subtitle as L
import cut_inplace as C

r = dvr.scriptapp("Resolve")
proj = r.GetProjectManager().GetCurrentProject()
mp = proj.GetMediaPool()
tl = proj.GetCurrentTimeline()
fps = float(tl.GetSetting("timelineFrameRate") or 24)

tmp = tempfile.mkdtemp(prefix="asrsubs_e2e_")
SRT = os.path.join(tmp, "e2e_test_subs.srt")

SRT_BODY = """1
00:00:00,000 --> 00:00:01,500
测试字幕第一行

2
00:00:02,000 --> 00:00:03,500
测试字幕第二行

3
00:00:04,000 --> 00:00:05,500
测试字幕第三行

"""

def read_sub_items():
    out = []
    n = tl.GetTrackCount("subtitle") or 0
    for i in range(1, n + 1):
        its = tl.GetItemListInTrack("subtitle", i) or []
        for it in its:
            sf = ef = None
            try:
                sf = int(it.GetStart()); ef = int(it.GetEnd())
            except Exception:
                pass
            out.append({"track": i, "sf": sf, "ef": ef})
    return out

def snapshot():
    return {
        "sub_tracks": tl.GetTrackCount("subtitle") or 0,
        "items": read_sub_items(),
    }

summary = {"fps": fps, "project": proj.GetName(), "timeline": tl.GetName(),
           "steps": [], "final": None, "cleanup_ok": None, "errors": []}

try:
    # ── 0) 初始快照 ──
    before = snapshot()
    summary["before"] = before

    # ── 1) 落测试字幕（模拟「适配字幕」首次落轨）──
    with open(SRT, "w", encoding="utf-8") as f:
        f.write(SRT_BODY)
    land1 = L.land_inplace(mp, tl, SRT, 0, fps, tmp)
    summary["steps"].append({"step": "land_inplace", "res": land1})
    after_land = read_sub_items()
    summary["steps"].append({"step": "read_after_land", "items": after_land})
    # 边车：落轨后应记录专用轨索引
    try:
        with open(SRT + ".landed.json", encoding="utf-8") as f:
            meta = json.load(f)
        summary["steps"].append({"step": "landed_sidecar", "meta": meta})
    except Exception as e:
        summary["steps"].append({"step": "landed_sidecar", "err": str(e)})

    # ── 2) R1 读取路径：在真实字幕位置上试算偏移（不切割）──
    # 用真实落点构造一组「切区间」，验证 _subtitle_offset_frames 能读出 0 偏移
    cuts = []
    for it in after_land:
        if it["sf"] is not None and it["ef"] is not None and it["ef"] > it["sf"]:
            cuts.append((it["sf"] / fps, it["ef"] / fps))
    off = C._subtitle_offset_frames(tl, cuts, fps, 0, 10**9)
    summary["steps"].append({"step": "r1_offset_probe", "cuts_count": len(cuts),
                              "detected_offset": off})

    # ── 3) 改 SRT（模拟「字幕更新」）→ relang 删旧+重挂 ──
    SRT_BODY2 = SRT_BODY + """4
00:00:06,000 --> 00:00:06,800
测试字幕第四行（更新后新增）

"""
    with open(SRT, "w", encoding="utf-8") as f:
        f.write(SRT_BODY2)
    relang = L.relang_subtitles(mp, tl, SRT, "", fps, tmp, expected_offset=0)
    summary["steps"].append({"step": "relang_subtitles", "res": relang})
    after_relang = read_sub_items()
    summary["steps"].append({"step": "read_after_relang", "items": after_relang,
                             "item_count": len(after_relang)})
    # 关键断言：relang 必须【删旧+重挂】，末尾条目数 == 新 SRT 条数(4)，而非 3+4=7
    # ★ v4.1.1：只统计【本插件落点轨】上的条目。真实时间线上可能还有用户手工
    #   字幕轨（本机实测 41 条），旧口径把全时间线都算进去会误判为失败。
    _pt = relang.get("track")
    plugin_items = [x for x in after_relang if x["track"] == _pt]
    summary["steps"].append({
        "step": "relang_dedup_check",
        "expect_new_count": 4,
        "plugin_track": _pt,
        "actual_count": len(plugin_items),
        "all_tracks_count": len(after_relang),
        "relang_cleared": relang.get("relang_cleared"),
        "pass": (len(plugin_items) == 4 and relang.get("relang_cleared") == 3),
    })

finally:
    # ── 4) 清理：删掉本插件测试字幕，还原到入侵前 ──
    try:
        killed = L.clear_our_subtitle_items(tl, SRT)
        # 兜底：把仍被禁用的字幕轨重新启用（本测试前无字幕轨，正常不触发）
        for i in range(1, (tl.GetTrackCount("subtitle") or 0) + 1):
            L._set_track_enabled(tl, "subtitle", i, True)
        summary["cleanup_ok"] = True
        summary["cleanup_killed"] = killed
        # ★ v4.1.0：本测试会让时间线多出一条字幕轨（land_inplace 自建），
        #   清理字幕条目后还要把【本次新增的】空轨删掉，否则测试前后轨道数不一致，
        #   下一次跑就变成"已经有 ST1"，掩盖"自建轨"路径。
        #   只删 index > 测试前轨数的空轨，绝不碰用户原有轨道。
        try:
            before_n = int((summary.get("before") or {}).get("sub_tracks") or 0)
            n_now = tl.GetTrackCount("subtitle") or 0
            for i in range(n_now, before_n, -1):
                if not (tl.GetItemListInTrack("subtitle", i) or []):
                    tl.DeleteTrack("subtitle", i)
        except Exception as e:
            summary["errors"].append("cleanup track: %s" % e)
    except Exception as e:
        summary["cleanup_ok"] = False
        summary["errors"].append("cleanup: %s" % e)
    # 末快照
    try:
        summary["final"] = snapshot()
    except Exception as e:
        summary["errors"].append("final snapshot: %s" % e)
    shutil.rmtree(tmp, ignore_errors=True)

print(json.dumps(summary, ensure_ascii=False, indent=2))
