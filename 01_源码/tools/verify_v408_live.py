# -*- coding: utf-8 -*-
"""★ v4.0.8 专项真机探针：验证「落轨失败 → 回收本次自建的空字幕轨」。

背景：v4.0.8 真机复验暴露——`land_inplace` 在「没有可用空轨」时会 AddTrack
新建一条字幕轨，落不上就把它留在时间线上 → 用户看到「ST2 凭空出现」的空轨。
这正是 v4.0.6 真机日志「整轨清除专用字幕轨 1 旧条目 53 条 → 已挂到 0 条
→ 边车写成 track=2」的那条路径。

v4.0.8 的修复：
  修复4   落轨失败 / 实际落点≠自建轨 且该轨为空 → DeleteTrack 回收
  修复4a  SRT 一条字幕都没有 → 直接早退，连轨都不碰

⚠ 安全约定：
  · 只在【当前打开的时间线】上做最小侵入。
  · 绝不调用任何会改动视频/音频的 API（不粗剪、不重排）。
  · try/finally 保证最后把测试字幕删干净。

用法：
  python verify_v408_live.py
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
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
for _p in (_HERE, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import DaVinciResolveScript as dvr
import land_subtitle as L

r = dvr.scriptapp("Resolve")
if not r:
    print(json.dumps({"ok": False, "err": "连不上 DaVinci（请确认已打开）"},
                     ensure_ascii=False))
    sys.exit(2)
proj = r.GetProjectManager().GetCurrentProject()
mp = proj.GetMediaPool()
tl = proj.GetCurrentTimeline() if proj else None
if not tl:
    print(json.dumps({"ok": False, "err": "没有打开的时间线"}, ensure_ascii=False))
    sys.exit(2)

fps = float(tl.GetSetting("timelineFrameRate") or 24)

tmp = tempfile.mkdtemp(prefix="asrsubs_v408_")


def sub_count():
    try:
        return tl.GetTrackCount("subtitle") or 0
    except Exception:
        return 0


def track_items(i):
    try:
        return tl.GetItemListInTrack("subtitle", i) or []
    except Exception:
        return []


def nonempty():
    return [i for i in range(1, sub_count() + 1) if track_items(i)]


def write_srt(path, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


SRT_BODY = """1
00:00:00,000 --> 00:00:01,500
V408 验证字幕第一行

2
00:00:02,000 --> 00:00:03,500
V408 验证字幕第二行

"""

out = {"ok": False, "project": proj.GetName(), "timeline": tl.GetName(),
       "fps": fps, "steps": [], "checks": {}, "cleanup": None, "err": ""}

srt_path = os.path.join(tmp, "v408_probe.srt")

try:
    # ── 准备：先在时间线上占一条【非空】字幕轨，逼迫 land_inplace 走 AddTrack ──
    #   用真实的空 SRT 落一份字幕到 ST1，让它变成"没有可用空轨"的状态。
    n0 = sub_count()
    out["steps"].append({"step": "0_initial", "sub_tracks": n0,
                         "nonempty_tracks": nonempty()})

    # 落一份正常字幕占位（用 relang 路径不合适，这里直接 land_inplace）
    write_srt(srt_path, SRT_BODY)
    r0 = L.land_inplace(mp, tl, srt_path, 0, fps, tmp, meta_key=srt_path)
    out["steps"].append({"step": "1_seed_land", "result": r0,
                          "sub_tracks": sub_count(),
                          "nonempty_tracks": nonempty()})

    # 再落一条【也占用 ST1】——它会复用 ST1，不新增轨。
    # 现在时间线上的状态：ST1 非空，且**没有别的空轨**。
    #   （若 ST1 之外还有空轨，需要先把它们占掉或删掉。）
    #   为了保证"没有可用空轨"，先把其它空轨删掉。
    for i in range(1, sub_count() + 1):
        if not track_items(i):
            try:
                tl.DeleteTrack("subtitle", i)
                print("[probe] 删掉多余空轨 %d" % i, flush=True)
            except Exception as e:
                print("[probe] 删空轨 %d 失败：%s" % (i, e), flush=True)

    n_before = sub_count()
    busy = nonempty()
    out["steps"].append({"step": "2_precondition", "sub_tracks": n_before,
                         "nonempty_tracks": busy})

    if not busy:
        out["err"] = "前置条件不满足：时间线上没有非空字幕轨，无法触发 AddTrack 分支"
    elif len(busy) < n_before:
        out["err"] = "前置条件不满足：仍有空轨可复用，不会走 AddTrack 分支"
    else:
        # ── 核心：构造"落轨失败" ──
        #   手法：给 land_inplace 一个【存在但内容非法】的 SRT ——
        #   文件存在（绕过修复4a 的早退），但字幕条目全非法
        #   （时间码倒挂 / 时长为负），Resolve 会拒绝落 → placed=0。
        bad = write_srt(os.path.join(tmp, "v408_bad.srt"), """1
00:00:05,000 --> 00:00:01,000
倒挂时间码

2
00:00:09,000 --> 00:00:08,000
也是倒挂

""")
        n_pre = sub_count()
        res = L.land_inplace(mp, tl, bad, 0, fps, tmp, meta_key=bad)
        n_post = sub_count()
        out["steps"].append({
            "step": "3_failed_land",
            "result": res,
            "sub_tracks_before": n_pre,
            "sub_tracks_after": n_post,
            "growth": n_post - n_pre,
            "nonempty_after": nonempty(),
        })
        out["checks"]["failed_land_growth"] = n_post - n_pre
        out["checks"]["reclaimed_track"] = res.get("reclaimed_track")
        out["checks"]["placed"] = res.get("placed")

        # ── 修复4a：空 SRT 早退（完全不碰轨道）──
        empty = write_srt(os.path.join(tmp, "v408_empty.srt"), "")
        n_pre2 = sub_count()
        res2 = L.land_inplace(mp, tl, empty, 0, fps, tmp, meta_key=empty)
        n_post2 = sub_count()
        out["steps"].append({
            "step": "4_empty_srt",
            "result": res2,
            "sub_tracks_before": n_pre2,
            "sub_tracks_after": n_post2,
            "growth": n_post2 - n_pre2,
        })
        out["checks"]["empty_srt_growth"] = n_post2 - n_pre2
        out["checks"]["empty_srt_diag"] = res2.get("diag")

        c = out["checks"]
        out["ok"] = (
            c.get("failed_land_growth") == 0 and       # ★ 失败落轨不得新增轨
            c.get("empty_srt_growth") == 0 and        # ★ 空 SRT 不得动轨
            (c.get("reclaimed_track") or 0) > 0      # ★ 确实回收了自建轨
        )

except Exception as e:
    import traceback
    out["err"] = "%s\n%s" % (e, traceback.format_exc())

finally:
    # 清理：删掉本插件测试字幕，还原轨道启用态
    try:
        killed = L.clear_our_subtitle_items(tl, srt_path)
        for i in range(1, sub_count() + 1):
            L._set_track_enabled(tl, "subtitle", i, True)
        # ★ v4.8.15：还原到【进入时】的轨数（只删本次新增且为空的轨）
        try:
            for _idx in range(sub_count(), n0, -1):
                if not (tl.GetItemListInTrack("subtitle", _idx) or []):
                    tl.DeleteTrack("subtitle", _idx)
        except Exception as _e:
            out.setdefault("cleanup_notes", []).append("还原轨数失败: %s" % _e)

        out["cleanup"] = {"ok": True, "killed": killed,
                          "sub_tracks_left": sub_count(),
                          "nonempty_left": nonempty()}

    except Exception as e:
        out["cleanup"] = {"ok": False, "err": str(e)}
    shutil.rmtree(tmp, ignore_errors=True)

print(json.dumps(out, ensure_ascii=False, indent=2))
