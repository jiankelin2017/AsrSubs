# -*- coding: utf-8 -*-
"""★ v4.0.7 专项真机复验探针 —— 验证「修复1/2/3」在真实 DaVinci 上确实成立。

背景：v4.0.6 在用户时间线上凭空多出一条字幕轨 ST2（用户反馈"ST2 是直接出现的"）。
v4.0.7 修了三处：
  修复1  land_inplace 优先【复用插件专用轨】，不再"找不到空轨就 AddTrack"
  修复2  边车【只在落轨成功后】写；失败时保留旧边车不动
  修复3  多证据定位（spans > 边车轨 > 唯一非空轨 > 条数吻合 > 认边车）

⚠ 安全约定（沿用方案 A 的受控端到端约定）：
  · 只在【当前打开的时间线】上做最小侵入：临时落一份测试 SRT → 重复落第二次
    → 跑一次 relang → 末尾把测试字幕删干净。
  · 绝不调用任何会改动视频/音频的 API（不粗剪、不重排）。
  · try/finally 保证无论中间是否异常，最后都会清理测试字幕并还原轨道启用态。
  · 只增不减：绝不调用 DeleteTrack（空轨清理另见 cleanup_empty_sub_tracks.py）。

用法：
  python verify_v407_live.py            # 受控端到端（会临时落字幕，结束自动清理）
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
if not proj:
    print(json.dumps({"ok": False, "err": "没有打开的工程"}, ensure_ascii=False))
    sys.exit(2)
mp = proj.GetMediaPool()
tl = proj.GetCurrentTimeline()
if not tl:
    print(json.dumps({"ok": False, "err": "没有打开的时间线"}, ensure_ascii=False))
    sys.exit(2)

fps = float(tl.GetSetting("timelineFrameRate") or 24)

tmp = tempfile.mkdtemp(prefix="asrsubs_v407_")
SRT = os.path.join(tmp, "v407_verify.srt")
SIDECAR = SRT + ".landed.json"

SRT_BODY = """1
00:00:00,000 --> 00:00:01,500
V407 验证字幕第一行

2
00:00:02,000 --> 00:00:03,500
V407 验证字幕第二行

3
00:00:04,000 --> 00:00:05,500
V407 验证字幕第三行

"""

SRT_BODY2 = SRT_BODY + """4
00:00:06,000 --> 00:00:06,800
V407 验证字幕第四行（更新后新增）

"""


def sub_track_count():
    try:
        return tl.GetTrackCount("subtitle") or 0
    except Exception:
        return 0


def track_items(track):
    try:
        return tl.GetItemListInTrack("subtitle", track) or []
    except Exception:
        return []


def track_fingerprint():
    """返回 {轨索引: [(唯一ID, 起点, 终点), ...]} —— 用来判断"落到了哪条轨"。"""
    fp = {}
    for i in range(1, sub_track_count() + 1):
        row = []
        for it in track_items(i):
            try:
                uid = it.GetUniqueId()
            except Exception:
                uid = None
            try:
                sf = int(it.GetStart())
            except Exception:
                sf = None
            try:
                ef = int(it.GetEnd())
            except Exception:
                ef = None
            row.append((uid, sf, ef))
        fp[i] = row
    return fp


def nonempty_tracks():
    return [i for i in range(1, sub_track_count() + 1) if track_items(i)]


def read_sidecar():
    try:
        with open(SIDECAR, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def diff_fp(a, b):
    """返回 {轨: 新增条目数}"""
    out = {}
    for i in set(a) | set(b):
        ids_a = set(x[0] for x in a.get(i, []))
        ids_b = set(x[0] for x in b.get(i, []))
        add = len(ids_b - ids_a)
        if add:
            out[i] = add
    return out


out = {
    "ok": False,
    "project": proj.GetName(),
    "timeline": tl.GetName(),
    "fps": fps,
    "steps": [],
    "checks": {},
    "cleanup": None,
    "err": "",
}

try:
    n0 = sub_track_count()
    out["steps"].append({"step": "0_initial", "sub_tracks": n0,
                         "nonempty_tracks": nonempty_tracks()})
    out["checks"]["baseline_tracks"] = n0

    # ── 步骤 1：首次落轨 ──
    with open(SRT, "w", encoding="utf-8") as f:
        f.write(SRT_BODY)
    fp_before = track_fingerprint()
    res1 = L.land_inplace(mp, tl, SRT, 0, fps, tmp, meta_key=SRT)
    n1 = sub_track_count()
    fp1 = track_fingerprint()
    sc1 = read_sidecar()
    out["steps"].append({
        "step": "1_first_land",
        "result": res1,
        "sub_tracks_after": n1,
        "new_items_per_track": diff_fp(fp_before, fp1),
        "sidecar": sc1,
    })
    out["checks"]["land1_track"] = res1.get("track")
    out["checks"]["land1_placed"] = res1.get("placed")
    out["checks"]["land1_tracks_growth"] = n1 - n0

    # ── 步骤 2：重复落轨（模拟用户重复点「适配字幕」）──
    # 关键断言：v4.0.6 会在这里凭空 AddTrack 多出一条 ST2；v4.0.7 必须复用。
    fp2_before = track_fingerprint()
    res2 = L.land_inplace(mp, tl, SRT, 0, fps, tmp, meta_key=SRT)
    n2 = sub_track_count()
    fp2 = track_fingerprint()
    sc2 = read_sidecar()
    grew = n2 - n1
    out["steps"].append({
        "step": "2_repeat_land",
        "result": res2,
        "sub_tracks_after": n2,
        "tracks_growth_vs_step1": grew,
        "new_items_per_track": diff_fp(fp2_before, fp2),
        "sidecar": sc2,
    })
    out["checks"]["repeat_land_growth"] = grew
    out["checks"]["repeat_land_same_track"] = (res1.get("track") == res2.get("track"))

    # ── 步骤 3：修复2 —— 失败落轨不得污染边车 ──
    # 构造一个空 SRT（0 条字幕）→ 落轨必然 0 条 → 边车必须保持步骤2的值不变
    sc_before_fail = read_sidecar()
    bad = os.path.join(tmp, "v407_empty.srt")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("")            # 空文件 = 0 条字幕
    try:
        res3 = L.land_inplace(mp, tl, bad, 0, fps, tmp, meta_key=bad)
    except Exception as e:
        res3 = {"ok": False, "err": str(e)}
    sc_after_fail = read_sidecar()   # 注意：bad 的边车是 bad.srt.landed.json，和 SRT 边车不同
    sc_bad = None
    try:
        with open(bad + ".landed.json", encoding="utf-8") as f:
            sc_bad = json.load(f)
    except Exception:
        sc_bad = None
    out["steps"].append({
        "step": "3_failed_land_sidecar",
        "result": res3,
        "bad_sidecar_after_failure": sc_bad,
        "main_sidecar_unchanged": (sc_before_fail == sc_after_fail),
    })
    out["checks"]["failed_land_no_sidecar"] = (sc_bad is None)
    out["checks"]["main_sidecar_intact"] = (sc_before_fail == sc_after_fail)

    # ── 步骤 4：修复3 —— relang 多证据定位 + relang_cleared 正确导出 ──
    with open(SRT, "w", encoding="utf-8") as f:
        f.write(SRT_BODY2)
    fp4_before = track_fingerprint()
    res4 = L.relang_subtitles(mp, tl, SRT, "", fps, tmp, expected_offset=0, meta_key=SRT)
    fp4 = track_fingerprint()
    n4 = sub_track_count()
    sc4 = read_sidecar()
    out["steps"].append({
        "step": "4_relang",
        "result": res4,
        "sub_tracks_after": n4,
        "new_items_per_track": diff_fp(fp4_before, fp4),
        "sidecar": sc4,
        "relang_cleared": res4.get("relang_cleared"),
        "placed": res4.get("placed"),
        "track": res4.get("track"),
    })
    out["checks"]["relang_cleared"] = res4.get("relang_cleared")
    out["checks"]["relang_placed"] = res4.get("placed")
    out["checks"]["relang_no_new_track"] = (n4 == n1)
    # relang 后该轨条目数应恰好等于新 SRT 条数（4），而不是 3+4=7
    final_items = []
    for i in range(1, n4 + 1):
        final_items += track_items(i)
    out["checks"]["relang_total_items"] = len(final_items)

    # ── 汇总判定 ──
    c = out["checks"]
    out["ok"] = (
        c.get("land1_tracks_growth", 99) <= 1 and   # 首次落轨最多新增 1 条（专用轨）
        c.get("repeat_land_growth", 99) == 0 and    # ★ 重复落轨【不得】新增轨
        c.get("repeat_land_same_track") is True and
        c.get("failed_land_no_sidecar") is True and
        c.get("main_sidecar_intact") is True and
        c.get("relang_no_new_track") is True
    )

except Exception as e:
    import traceback
    out["err"] = "%s\n%s" % (e, traceback.format_exc())

finally:
    # ── 清理：删掉本插件测试字幕，还原轨道启用态 ──
    try:
        killed = L.clear_our_subtitle_items(tl, SRT)
        for i in range(1, sub_track_count() + 1):
            L._set_track_enabled(tl, "subtitle", i, True)
        try:
            os.remove(SIDECAR)
        except Exception:
            pass
        # ★ v4.8.15：还原到【进入时】的轨数。
        #   原规则是"只增不减、绝不 DeleteTrack"，于是每跑一次留一条空轨，
        #   连跑多次会堆一串。安全边界仍是"不碰用户已有的轨"：
        #   只删索引 > n0（本次新增）且【条目为 0】的轨，从后往前删避免位移。
        try:
            for _idx in range(sub_track_count(), n0, -1):
                if not (tl.GetItemListInTrack("subtitle", _idx) or []):
                    tl.DeleteTrack("subtitle", _idx)
        except Exception as _e:
            out.setdefault("cleanup_notes", []).append("还原轨数失败: %s" % _e)

        out["cleanup"] = {"ok": True, "killed": killed,
                          "sub_tracks_left": sub_track_count(),
                          "nonempty_left": nonempty_tracks()}

    except Exception as e:
        out["cleanup"] = {"ok": False, "err": str(e)}
    shutil.rmtree(tmp, ignore_errors=True)

print(json.dumps(out, ensure_ascii=False, indent=2))
