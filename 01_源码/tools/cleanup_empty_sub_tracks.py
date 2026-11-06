# -*- coding: utf-8 -*-
"""★ v4.0.7 辅助工具：清理【历史遗留的空字幕轨】。

背景：v4.0.6 的 land_inplace 在"找不到空轨就 AddTrack"，曾在你当前时间线上
凭空留下一条空的字幕轨 2（ST2）。v4.0.7 已修掉根因（不再新建），但**已存在的
空轨不会被自动删除**（保守原则：插件不擅自删除用户时间线的轨道）。

本脚本：
  · 默认【只读体检】：列出所有字幕轨、各轨条目数、各轨启用态，指出哪些是空轨。
  · 加 --delete N 参数才真正删除第 N 条【空】字幕轨（条目数必须为 0）。
    · 拒绝删除非空轨（防止误删用户字幕）。
    · 删除从大到小排序执行，避免索引位移。
  · 全程记录操作前后状态，方便核对。

用法：
  python cleanup_empty_sub_tracks.py                 # 只读体检（默认，安全）
  python cleanup_empty_sub_tracks.py --delete 2      # 删除第 2 条空字幕轨
  python cleanup_empty_sub_tracks.py --delete-empty  # 删除所有空字幕轨
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
# ★ v4.1.0：本脚本在 tools\ 下，被测模块在上一级（01_源码\）。与 test_r1/r2 同一修法。
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
for _p in (_HERE, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import DaVinciResolveScript as dvr
import land_subtitle as L

argv = sys.argv[1:]
want_delete = []
delete_all_empty = False
i = 0
while i < len(argv):
    a = argv[i]
    if a == "--delete-empty":
        delete_all_empty = True
    elif a == "--delete":
        # 形式一：--delete N（值在下一个参数）
        if i + 1 < len(argv):
            try:
                want_delete.append(int(argv[i + 1]))
                i += 1
            except Exception:
                print("无法解析 --delete 的参数：%s" % argv[i + 1])
                sys.exit(2)
    elif a.startswith("--delete="):
        try:
            want_delete.append(int(a.split("=", 1)[1]))
        except Exception:
            print("无法解析参数：%s" % a)
            sys.exit(2)
    i += 1


def snapshot(tl):
    rows = []
    n = tl.GetTrackCount("subtitle") or 0
    for i in range(1, n + 1):
        items = tl.GetItemListInTrack("subtitle", i) or []
        rows.append({
            "track": i,
            "name": (tl.GetTrackName("subtitle", i) if hasattr(tl, "GetTrackName") else "") or "",
            "items": len(items),
            "enabled": L._get_track_enabled(tl, "subtitle", i),
        })
    return rows


r = dvr.scriptapp("Resolve")
if not r:
    print(json.dumps({"ok": False, "err": "连不上 DaVinci（请确认已打开）"},
                     ensure_ascii=False))
    sys.exit(2)
proj = r.GetProjectManager().GetCurrentProject()
tl = proj.GetCurrentTimeline() if proj else None
if not tl:
    print(json.dumps({"ok": False, "err": "没有打开的时间线"}, ensure_ascii=False))
    sys.exit(2)

out = {
    "ok": True,
    "project": proj.GetName(),
    "timeline": tl.GetName(),
    "before": snapshot(tl),
    "targets": [],
    "deleted": [],
    "skipped": [],
    "after": None,
}

empty = [x["track"] for x in out["before"] if x["items"] == 0]
out["empty_tracks"] = empty

if delete_all_empty:
    want_delete = list(empty)

if not want_delete:
    out["mode"] = "readonly"
    out["hint"] = ("当前为只读体检，未改动任何轨道。"
                   "确认要删的轨号后加 --delete N，或加 --delete-empty 删除全部空轨。")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0)

out["mode"] = "delete"
for t in want_delete:
    rows = {x["track"]: x for x in out["before"]}
    if t not in rows:
        out["skipped"].append({"track": t, "reason": "不存在"})
        continue
    if rows[t]["items"] != 0:
        out["skipped"].append({"track": t, "reason": "非空轨（%d 条条目），拒绝删除"
                                % rows[t]["items"]})
        continue
    out["targets"].append(t)

# 从大到小删，避免索引位移
for t in sorted(out["targets"], reverse=True):
    try:
        ok = tl.DeleteTrack("subtitle", t)
        out["deleted"].append({"track": t, "result": bool(ok)})
    except Exception as e:
        out["deleted"].append({"track": t, "result": False, "err": str(e)})

out["after"] = snapshot(tl)
print(json.dumps(out, ensure_ascii=False, indent=2))
