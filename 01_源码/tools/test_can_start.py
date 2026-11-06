# -*- coding: utf-8 -*-
"""「开始生成」前置条件（v1.2.0 回归）

要锁住的行为：
    条件不满足时按钮必须是灰的（不可点），条件恢复后 3 秒内自己变亮。
    改前 StartBtn 在开窗和 EnableButtons 里都被无条件置 true，
    用户点了才知道"没时间线 / 没片段 / 环境不对 / 上一轮还在跑"。

判据三条（见 App:CanStart）：
    ① 相位不在 BUSY_PHASES 里
    ② 环境自检没有缺失项（App.S.pathBad 为空）
    ③ 拿得到当前时间线，且音轨或视频轨上至少有一个片段

不连 DaVinci：用 lupa 载入 AsrSubs.lua（摘掉入口块），把 resolve/project/timeline
换成"记账"的假对象，直接调 App:CanStart / App:RefreshStartBtn。
"""
import io
import os
import re
import sys

import lupa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUA = os.path.join(ROOT, "AsrSubs.lua")


def load_app():
    src = io.open(LUA, encoding="utf-8").read()
    # 摘掉最后的入口块（与 tools/test_close_page.py 一致）
    src = re.sub(r'if resolve == nil and type\(Resolve\) == "function" then'
                 r'[\s\S]*$', "", src)
    src += "\nreturn App\n"
    rt = lupa.LuaRuntime(unpack_returned_tuples=False)
    return rt, rt.execute(src)


class FakeTimeline:
    """tracks = {"audio": [3, 0], "video": [0]}，数字是那条轨上的片段数。

    ★ 必须返回【真正的 Lua 表】而不是 Python list：Resolve 的
      GetItemListInTrack 给的是 Lua 表，而 lupa 把 Python list 暴露成
      POBJECT，Lua 侧取 # 会抛 "attempt to get length of a POBJECT value"。
      用 list 假扮，测的就不是真实返回类型了（CanStart 有一条 fail-open
      分支专门吃这种情况，见 case_proxy_object）。
    """

    def __init__(self, tracks, calls, rt):
        self.tracks = tracks
        self.calls = calls
        self.rt = rt

    def GetName(self):
        return "Timeline 1"

    def GetTrackCount(self, kind):
        self.calls.append(("GetTrackCount", kind))
        return len(self.tracks.get(kind, []))

    def GetItemListInTrack(self, kind, idx):
        self.calls.append(("GetItemListInTrack", kind, idx))
        n = self.tracks.get(kind, [])[idx - 1]
        if n <= 0:
            return self.rt.table()
        return self.rt.table(*[self.rt.table() for _ in range(n)])


class FakeProject:
    def __init__(self, tl):
        self.tl = tl

    def GetCurrentTimeline(self):
        return self.tl


class FakePM:
    def __init__(self, proj):
        self.proj = proj

    def GetCurrentProject(self):
        return self.proj


class FakeResolve:
    def __init__(self, proj):
        self.proj = proj

    def GetProjectManager(self):
        if self.proj is None:
            return None
        return FakePM(self.proj)


def fresh(tracks=None, phase="idle", path_bad=None, has_tl=True, proxy_items=False):
    """载入一份新的 App，接上假对象。tracks=None 表示"没有时间线"。

    proxy_items=True 时让 GetItemListInTrack 返回 Python list（= 拿不到 # 的
    代理对象），用来锁 CanStart 的 fail-open 分支。
    """
    rt, App = load_app()
    calls = []
    tl = FakeTimeline(tracks, calls, rt) if tracks is not None else None
    if tl is not None and proxy_items:
        def _proxy(kind, idx, _tl=tl):
            n = _tl.tracks.get(kind, [])[idx - 1]
            return [object()] * n
        tl.GetItemListInTrack = _proxy
    proj = FakeProject(tl) if has_tl else None
    App.resolve = FakeResolve(proj)
    App.timeline = tl
    App.S.phase = phase
    App.S.pathBad = path_bad if path_bad is not None else rt.table()
    App.S.items = rt.table(StartBtn=rt.table())
    return rt, App, calls


def case_empty_timeline():
    _rt, App, _c = fresh(tracks={"audio": [], "video": []})
    ok, why = App.CanStart(App)
    assert ok is False, "空时间线不该允许开始"
    assert "没有片段" in why, "提示词不对：%r" % (why,)
    print("PASS 时间线没有片段 → 灰（%s）" % why)


def case_tracks_without_items():
    """有音轨但轨上一条都没有 —— 这是"导入了空轨"的真实形态。"""
    _rt, App, _c = fresh(tracks={"audio": [0, 0], "video": [0]})
    ok, why = App.CanStart(App)
    assert ok is False, "有轨没片段不该允许开始"
    print("PASS 有轨但没片段 → 灰（%s）" % why)


def case_has_item():
    _rt, App, calls = fresh(tracks={"audio": [2, 1], "video": [0]})
    ok, why = App.CanStart(App)
    assert ok is True, "有片段却判成不可点：%r" % (why,)
    assert why == "", "允许时不该带原因：%r" % (why,)
    # 找到第一个片段就该停：只查了第 1 条音轨的头一个片段
    n = [c for c in calls if c[0] == "GetItemListInTrack"]
    assert len(n) == 1, "没有提前返回，查了 %d 条轨：%r" % (len(n), n)
    print("PASS 有片段 → 可点（且只查了 %d 次轨）" % len(n))


def case_busy_phase():
    _rt, App, _c = fresh(tracks={"audio": [1]}, phase="recognizing")
    ok, why = App.CanStart(App)
    assert ok is False, "任务进行中不该允许再点"
    assert "进行中" in why, "提示词不对：%r" % (why,)
    print("PASS 识别中 → 灰（%s）" % why)


def case_env_bad():
    rt, App, _c = fresh(tracks={"audio": [1]})
    bad = rt.table("Python 环境缺失：D:\\AsrSubs\\runtime\\python.exe")
    App.S.pathBad = bad
    ok, why = App.CanStart(App)
    assert ok is False, "环境自检没过不该允许开始"
    assert "环境自检" in why, "提示词不对：%r" % (why,)
    print("PASS 环境自检未过 → 灰（%s）" % why)


def case_no_timeline():
    _rt, App, _c = fresh(tracks=None)
    ok, why = App.CanStart(App)
    assert ok is False, "没有时间线不该允许开始"
    assert "时间线" in why, "提示词不对：%r" % (why,)
    print("PASS 取不到时间线 → 灰（%s）" % why)


def case_refresh_writes_enabled():
    """RefreshStartBtn 必须真的把 Enabled 写成 true/false，而不是只算不写。"""
    _rt, App, _c = fresh(tracks={"audio": [1]})
    App.RefreshStartBtn(App)
    # ★ 取 Lua 表字段要用下标，不能用属性：lupa 把 App.S.items.StartBtn
    #   当"表方法"解析，属性访问会拿回一个 cython 函数。
    assert App.S["items"]["StartBtn"]["Enabled"] is True, "有片段时按钮没变亮"
    App.S.phase = "rendering"
    App.RefreshStartBtn(App)
    assert App.S["items"]["StartBtn"]["Enabled"] is False, "渲染中按钮没变灰"
    assert App.S["startWhy"] and "进行中" in App.S["startWhy"], \
        "灰掉的原因没记录：%r" % (App.S["startWhy"],)
    print("PASS RefreshStartBtn 会真的改写 Enabled（含原因记录）")


def case_done_phase_enables():
    """一轮跑完（phase=done）后必须恢复可点，别把按钮永久锁死。"""
    _rt, App, _c = fresh(tracks={"audio": [1]}, phase="done")
    ok, _why = App.CanStart(App)
    assert ok is True, "done 相位却仍判为不可点"
    print("PASS done 相位 → 可点")


def case_proxy_object_fails_open():
    """返回的不是 Lua 表（拿不到 #）时不许把用户锁死 —— 这是 fail-open 契约。"""
    _rt, App, _c = fresh(tracks={"audio": [1]}, proxy_items=True)
    ok, _why = App.CanStart(App)
    assert ok is True, "读不出片段数时不该拦（宁可让用户点一下看到真错误）"
    print("PASS 返回非表对象 → 不拦（fail-open）")


def main():
    case_empty_timeline()
    case_tracks_without_items()
    case_has_item()
    case_busy_phase()
    case_env_bad()
    case_no_timeline()
    case_proxy_object_fails_open()
    case_refresh_writes_enabled()
    case_done_phase_enables()
    print("ALL CAN-START TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
