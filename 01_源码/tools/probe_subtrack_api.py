# -*- coding: utf-8 -*-
"""v4.0.9 真机探针：验证 Lua 侧 ReadSubTracks 用到的三个 API 的真实行为。

只读，不改时间线。

验证点：
  1) timeline.GetTrackCount("subtitle")        → 字幕轨条数
  2) timeline.GetIsTrackEnabled("subtitle", i)  → 启用态（true/false/nil?）
  3) timeline.GetTrackName("subtitle", i)       → 轨名（可能抛异常 / 返回空串）
  4) 对照：GetTrackName("video", 1)            → 普通轨名对照
  5) Lua 侧 GetIsTrackEnabled 判 nil 的处理是否合理
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import land_subtitle as LS

resolve = LS.connect()
if not resolve:
    print("NO_CONNECTION（DaVinci 未运行 / 未开工程 / 未开外部脚本）")
    sys.exit(1)
print("connected ok")
try:
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    tl = project.GetCurrentTimeline()
except Exception as e:
    print("NO_TIMELINE:", e)
    sys.exit(1)

print("timeline =", tl.GetName())

print("\n=== 1) 字幕轨条数 ===")
n = 0
try:
    n = tl.GetTrackCount("subtitle") or 0
    print("GetTrackCount('subtitle') =", n, type(n).__name__)
except Exception as e:
    print("GetTrackCount FAILED:", e)

print("\n=== 2/3/4) 每条字幕轨的三个 API ===")
for i in range(1, n + 1):
    cnt = None
    try:
        its = tl.GetItemListInTrack("subtitle", i) or []
        cnt = len(its)
    except Exception as e:
        cnt = "EXC:%s" % e
    print("\n--- subtitle track %d ---" % i)

    raw = "NO_RETURN"
    try:
        raw = tl.GetIsTrackEnabled("subtitle", i)
    except Exception as e:
        raw = "EXC:%s" % e
    print("  GetIsTrackEnabled = %r (%s)" % (raw, type(raw).__name__))
    print("  Lua 判定 en=(raw==true) -> %s" % (raw is True))

    nm = "NO_RETURN"
    try:
        nm = tl.GetTrackName("subtitle", i)
    except Exception as e:
        nm = "EXC:%s" % e
    print("  GetTrackName       = %r (%s)" % (nm, type(nm).__name__))

    print("  item count         = %s" % cnt)

print("\n=== 4) 对照组：video / audio 轨名 ===")
for kind in ("video", "audio"):
    try:
        m = tl.GetTrackCount(kind) or 0
    except Exception as e:
        print("  %s count EXC:%s" % (kind, e))
        continue
    for i in range(1, min(m, 3) + 1):
        try:
            nm = tl.GetTrackName(kind, i)
        except Exception as e:
            nm = "EXC:%s" % e
        try:
            en = tl.GetIsTrackEnabled(kind, i)
        except Exception as e:
            en = "EXC:%s" % e
        print("  %s[%d] name=%r enabled=%r" % (kind, i, nm, en))

print("\n=== 5) 汇总：Lua 侧会拼出的清单文本 ===")
parts = []
for i in range(1, n + 1):
    try:
        its = tl.GetItemListInTrack("subtitle", i) or []
        c = len(its)
    except Exception:
        c = 0
    raw = None
    try:
        raw = tl.GetIsTrackEnabled("subtitle", i)
    except Exception:
        pass
    en = (raw is True)
    nm = ""
    try:
        nm = tl.GetTrackName("subtitle", i) or ""
    except Exception:
        nm = ""
    if nm == "":
        nm = "ST%d" % i
    parts.append("%s(%s·%d条)" % (nm, "启用" if en else "禁用", c))
if parts:
    print("字幕轨 %d 条：%s" % (n, "｜".join(parts)))
else:
    print("字幕轨 0 条（当前时间线没有字幕轨）")

print("\nDONE")
