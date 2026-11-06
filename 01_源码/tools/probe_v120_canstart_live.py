# -*- coding: utf-8 -*-
r"""v1.2.0 待验证项 ① 的只读真机探针 —— 不修改任何时间线内容。

背景
----
App:CanStart()（AsrSubs.lua:4858-4898）靠 tl:GetItemListInTrack(kind, i)
判断时间线上有没有片段。离线测试 tools/test_can_start.py 用 lupa 假对象
覆盖了 9 个用例（含「返回代理对象时 fail-open」分支），但真实 Resolve
在空时间线 / 只有字幕轨的时间线上究竟返回 Lua 空表、nil 还是拿不到 #
的代理对象，只能真机回答。

本脚本做两件事：
  1. 连 Resolve 的 Python 脚本 API，逐轨记录 GetItemListInTrack 的返回
     类型与条目数。只读，绝不新建时间线、不增删轨、不动片段。
  2. --lua 打印一段 Lua 片段，贴进 Resolve 控制台执行，直接取 Lua 侧
     type() / # 的真值。CanStart 跑在 Lua 侧，这一步才是 ① 的直接证据；
     Python 侧结果只作旁证，不能代替它。

用法（在 01_源码 目录下）：
    py -3.12 tools\probe_v120_canstart_live.py
    py -3.12 tools\probe_v120_canstart_live.py --json
    py -3.12 tools\probe_v120_canstart_live.py --lua

退出码：0 探测完成；1 连不上 Resolve；2 当前没有打开的时间线。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)

import land_subtitle as L  # noqa: E402

LUA_SNIPPET = r'''-- AsrSubs v1.2.0 待验证项 ①：Lua 侧 GetItemListInTrack 返回类型取证
-- 只读：不新建时间线、不增删轨、不改任何片段。整段贴进 Resolve 控制台执行。
local function probe()
  local r = Resolve()
  local pm = r and r:GetProjectManager()
  local proj = pm and pm:GetCurrentProject()
  local tl = proj and proj:GetCurrentTimeline()
  if not tl then print("[probe] NO TIMELINE"); return end
  print("[probe] timeline=" .. tostring(tl:GetName()))
  for _, kind in ipairs({"video", "audio", "subtitle"}) do
    local okc, cnt = pcall(function() return tonumber(tl:GetTrackCount(kind)) or 0 end)
    if not okc then
      print("[probe] " .. kind .. " GetTrackCount THREW " .. tostring(cnt))
    else
      for i = 1, cnt do
        local ok2, its = pcall(function() return tl:GetItemListInTrack(kind, i) end)
        if not ok2 then
          print(("[probe] %s[%d] THREW %s"):format(kind, i, tostring(its)))
        else
          local t = type(its)
          local okn, n = pcall(function() return #its end)
          print(("[probe] %s[%d] type=%s len_ok=%s len=%s"):format(
            kind, i, t, tostring(okn), okn and tostring(n) or "N/A"))
        end
      end
    end
  end
  print("[probe] DONE")
end
probe()'''


def connect():
    try:
        return L.connect()
    except Exception as exc:
        print("[probe] connect raised %s: %s" % (type(exc).__name__, exc))
        return None


def probe_python():
    r = connect()
    if r is None:
        return {"connected": False,
                "reason": "Resolve 未运行 / 外部脚本未开启 / connect() 抛错"}, 1
    out = {"connected": True, "timeline": None, "tracks": [], "scenario": None}
    pm = r.GetProjectManager()
    proj = pm.GetCurrentProject() if pm else None
    tl = proj.GetCurrentTimeline() if proj else None
    if tl is None:
        out["reason"] = "当前没有打开的时间线"
        return out, 2
    try:
        out["timeline"] = tl.GetName()
    except Exception as exc:
        out["timeline"] = "<GetName 失败: %s>" % exc

    for kind in ("video", "audio", "subtitle"):
        try:
            cnt = int(tl.GetTrackCount(kind) or 0)
        except Exception as exc:
            out["tracks"].append({"kind": kind, "index": 0,
                                  "error": "GetTrackCount: %s" % exc})
            continue
        for i in range(1, cnt + 1):
            rec = {"kind": kind, "index": i}
            try:
                its = tl.GetItemListInTrack(kind, i)
                rec["py_type"] = type(its).__name__
                rec["is_none"] = its is None
                try:
                    rec["len"] = len(its)
                except Exception as exc:
                    rec["len_error"] = "%s: %s" % (type(exc).__name__, exc)
            except Exception as exc:
                rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            out["tracks"].append(rec)

    va = [t for t in out["tracks"] if t["kind"] in ("video", "audio")]
    sub = [t for t in out["tracks"] if t["kind"] == "subtitle"]
    va_items = sum(int(t.get("len") or 0) for t in va)
    sub_items = sum(int(t.get("len") or 0) for t in sub)
    out["video_audio_items"] = va_items
    out["subtitle_items"] = sub_items
    if not va:
        out["scenario"] = "A 无任何视频/音频轨（空时间线形态）"
    elif va_items == 0 and sub_items > 0:
        out["scenario"] = "B 只有字幕轨有内容（① 的关键场景）"
    elif va_items == 0:
        out["scenario"] = "A' 有视频/音频轨但全空（空时间线形态）"
    else:
        out["scenario"] = "C 正常有片段（对照组，按钮应可点）"
    out["expected_can_start"] = va_items > 0
    return out, 0


def show_text(out):
    print("连接 Resolve: %s" % ("OK" if out.get("connected") else "FAIL"))
    if not out.get("connected"):
        print("原因: %s" % out.get("reason", ""))
        return
    print("当前时间线: %s" % out.get("timeline") or out.get("reason", ""))
    for t in out.get("tracks", []):
        print("  %-8s [%s] type=%s len=%s%s" % (
            t["kind"], t["index"], t.get("py_type", "-"),
            t.get("len", "N/A"),
            " ERROR=%s" % t["error"] if t.get("error") else
            (" LEN_ERROR=%s" % t["len_error"] if t.get("len_error") else "")))
    print("场景判定: %s" % out.get("scenario"))
    print("按 CanStart 判据，按钮预期: %s" % (
        "可点" if out.get("expected_can_start") else "变灰"))
    print("※ Python API 面只作旁证；① 的直接证据请用 --lua 取 Lua 侧 type()/#")


def main():
    ap = argparse.ArgumentParser(description="v1.2.0 CanStart 只读真机探针")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--lua", action="store_true", help="打印贴进 Resolve 控制台的 Lua 片段")
    args = ap.parse_args()
    if args.lua:
        print(LUA_SNIPPET)
        return 0
    out, code = probe_python()
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        show_text(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
