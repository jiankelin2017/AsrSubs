# -*- coding: utf-8 -*-
"""v4.0.9：字幕轨清单逻辑的离线单测（不需要 DaVinci 运行）。

做法：直接从 AsrSubs.lua 源码里【抽取】App:ReadSubTracks / App:RefreshSubTrackInfo
两个函数的真实文本（不是抄一份副本），塞进 lupa 的 Lua 运行时里跑，
用假 timeline 对象喂各种边界情况。

这样测的就是线上真正跑的那段代码，改坏了单测会红。

运行：python test_subtrack_info.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ v4.0.9：本脚本现位于 01_源码\tools\，而被测的 AsrSubs.lua 在 01_源码\。
#   原来脚本在 01_源码\ 根目录时，"脚本所在目录"恰好就是 Lua 源码所在目录；
#   搬进 tools\ 后必须显式回退一级，否则找不到 AsrSubs.lua。
SRC_ROOT = os.path.dirname(HERE)
LUA_SRC = os.path.join(SRC_ROOT, "AsrSubs.lua")

try:
    import lupa
except ImportError:
    print("需要 lupa：pip install lupa")
    sys.exit(2)


def extract_func(src, header):
    """从 Lua 源码里抠出以 header 开头、以顶层 end 结束的函数定义文本。"""
    i = src.find(header)
    if i < 0:
        raise AssertionError("源码里找不到：%s" % header)
    # 找到该 function 的顶层 end（行首的 "end"）
    rest = src[i:]
    m = re.search(r"^end\s*$", rest, re.M)
    if not m:
        raise AssertionError("找不到 %s 的结尾 end" % header)
    return rest[:m.end()]


FAILURES = []


def check(name, got, want):
    if got == want:
        print("  [OK] %s" % name)
        print("       %s" % got)
    else:
        print("  [FAIL] %s" % name)
        print("       期望: %r" % (want,))
        print("       实得: %r" % (got,))
        FAILURES.append(name)


class FakeItems(object):
    """模拟 Fusion 的 win:GetItems() 返回值。"""

    def __init__(self):
        self.SubTrackInfo = FakeLabel()
        self.LogEdit = FakeLog()


class FakeLabel(object):
    def __init__(self):
        self.Text = "<初始>"


class FakeLog(object):
    def __init__(self):
        self.lines = []

    def PlainText(self):
        return "\n".join(self.lines)


class FakeTL(object):
    """假 timeline。行为由 ctor 参数驱动，可模拟抛异常/返回 nil。"""

    def __init__(self, lua, tracks=None, count=None, name=""):
        self._lua = lua
        self._tracks = tracks or []      # [{count=, enabled=, name=}]
        self._count = count
        self._name = name

    def GetTrackCount(self, kind):
        if kind != "subtitle":
            return 0
        if self._count is not None:
            return self._count
        return len(self._tracks)

    def GetItemListInTrack(self, kind, idx):
        t = self._tracks[idx - 1]
        if isinstance(t.get("count"), Exception):
            raise t["count"]
        if t["count"] is None:
            raise RuntimeError("boom")
        # ★ 必须返回 Lua 原生 table：lupa 不把 Python list 的 __len__
        #   暴露给 Lua 的 # 运算符，直接返回 Python list 会让条数恒为 0。
        return self._lua.table_from(["item"] * t["count"])

    def GetIsTrackEnabled(self, kind, idx):
        return self._tracks[idx - 1].get("enabled")

    def GetTrackName(self, kind, idx):
        v = self._tracks[idx - 1].get("name")
        if isinstance(v, Exception):
            raise v
        return v


def make_lua():
    src = io.open(LUA_SRC, encoding="utf-8").read()
    f1 = extract_func(src, "function App:ReadSubTracks()")
    f2 = extract_func(src, "function App:RefreshSubTrackInfo(showLog)")
    print("  已从 AsrSubs.lua 抽取真实函数：")
    print("    ReadSubTracks       %d 字符" % len(f1))
    print("    RefreshSubTrackInfo %d 字符" % len(f2))
    lua = lupa.LuaRuntime(unpack_returned_tuples=False)
    lua.execute("""
        App = {}
        function App:Log(m) end
    """)
    lua.execute(f1)
    lua.execute(f2)
    return lua


def run_case(lua, tracks, count=None, tlname=""):
    """跑一次 RefreshSubTrackInfo，返回 (标签文本, 标签对象)。"""
    items = FakeItems()
    lua.globals().App.S = lua.table_from({
        "items": items,
    })
    fake = FakeTL(lua, tracks, count=count, name=tlname)
    # 用 python 对象做 resolve 桥：App.resolve:GetProjectManager():GetCurrentProject():GetCurrentTimeline()
    class PM(object):
        def GetCurrentProject(self):
            return self

        def GetCurrentTimeline(self):
            return fake

    class RS(object):
        def GetProjectManager(self):
            return PM()

    lua.globals().App.resolve = RS()
    lua.globals().App.timeline = fake
    lua.globals().App._itemsRef = items
    txt = lua.globals().App.RefreshSubTrackInfo(lua.globals().App, False)
    return txt, items.SubTrackInfo.Text


def main():
    print("=" * 62)
    print(" v4.0.9 字幕轨清单 · 离线单测")
    print("=" * 62)
    lua = make_lua()
    print()

    print("[1] 三条轨：启用/禁用/启用，字幕轨名返回空串")
    txt, lbl = run_case(lua, [
        {"count": 5, "enabled": True,  "name": ""},
        {"count": 0, "enabled": False, "name": ""},
        {"count": 2, "enabled": True,  "name": ""},
    ])
    want = "字幕轨 3 条：ST1(启用·5条)｜ST2(禁用·0条)｜ST3(启用·2条)"
    check("清单文本", txt, want)
    check("标签同步", lbl, want)

    print()
    print("[2] GetIsTrackEnabled 返回 nil → 必须判为【禁用】（不能虚报启用）")
    txt, _ = run_case(lua, [
        {"count": 3, "enabled": None, "name": ""},
    ])
    check("nil → 禁用", txt, "字幕轨 1 条：ST1(禁用·3条)")

    print()
    print("[3] GetTrackName 抛异常 → 回退成 ST编号，不炸")
    txt, _ = run_case(lua, [
        {"count": 1, "enabled": True, "name": Exception("no name api")},
    ])
    check("异常回退", txt, "字幕轨 1 条：ST1(启用·1条)")

    print()
    print("[4] GetTrackName 返回真实名字 → 用名字（不显示 ST1）")
    txt, _ = run_case(lua, [
        {"count": 53, "enabled": True, "name": "字幕 1"},
    ])
    check("用真实名字", txt, "字幕轨 1 条：字幕 1(启用·53条)")

    print()
    print("[5] 时间线没有字幕轨 → 明确说 0 条")
    txt, _ = run_case(lua, [])
    check("0 条", txt, "字幕轨 0 条")

    print()
    print("[6] GetItemListInTrack 抛异常 → 条数按 0，不崩")
    txt, _ = run_case(lua, [
        {"count": None, "enabled": True, "name": ""},
    ])
    check("读条目异常", txt, "字幕轨 1 条：ST1(启用·0条)")

    print()
    print("[7] GetTrackCount 返回 0 但轨数组非空 → 以计数为准")
    txt, _ = run_case(lua, [
        {"count": 9, "enabled": True, "name": ""},
    ], count=0)
    check("计数为 0", txt, "字幕轨 0 条")

    print()
    print("=" * 62)
    if FAILURES:
        print("失败 %d 项：%s" % (len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("全部通过（7 项）")
    print("=" * 62)


if __name__ == "__main__":
    main()
