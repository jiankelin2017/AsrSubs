# -*- coding: utf-8 -*-
"""关窗不得切页（v1.1.1 回归）

用户实测的 bug：
    在 DaVinci 里打开 AsrSubs，什么都不做，直接把窗口关掉 ——
    DaVinci 会自己跳到「交付」页。

根因（两层，缺一不可）：
    ① v1.0.1 把 Cleanup 改成也调 CleanupRender（为了"停止不再改坏交付页"），
       而 CleanupRender 无条件执行 DeleteRenderPreset("render_to_wav")；
       Resolve 删渲染预设时会把界面切到交付页。
    ② ReturnToPrevPage 只看 App.S.prevPage 有没有值 —— 那是 StartRender 留下的，
       关窗时若还留着旧值，就把界面拽回上一轮那页；没渲染过则什么也不做，
       于是停在 ① 造成的交付页上。

现在：
    · CleanupRender 在没有快照、没有作业时【直接返回】，一个交付页 API 都不碰；
    · ReturnToPrevPage 只在 pageMoved（StartRender 真的切过页）时执行一次。

不连 DaVinci：用 lupa 载入 AsrSubs.lua（摘掉入口），把 App.resolve / App.project
换成"记录调用"的假对象，直接调 App:Cleanup / App:CleanupRender。
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
    # 摘掉最后的入口块（与 tools/test_logic_v402.js 的做法一致）
    src = re.sub(r'if resolve == nil and type\(Resolve\) == "function" then'
                 r'[\s\S]*$', "", src)
    src += "\nreturn App\n"
    rt = lupa.LuaRuntime(unpack_returned_tuples=False)
    return rt, rt.execute(src)


class FakeProject:
    def __init__(self, calls):
        self.calls = calls

    def DeleteRenderJob(self, jid):
        self.calls.append(("DeleteRenderJob", jid)); return True

    def DeleteRenderPreset(self, name):
        self.calls.append(("DeleteRenderPreset", name)); return True

    def LoadRenderPreset(self, name):
        self.calls.append(("LoadRenderPreset", name)); return True

    def SetCurrentRenderFormatAndCodec(self, f, c):
        self.calls.append(("SetCurrentRenderFormatAndCodec", f, c)); return True

    def SetCurrentRenderMode(self, m):
        self.calls.append(("SetCurrentRenderMode", m)); return True

    def SetRenderSettings(self, s):
        self.calls.append(("SetRenderSettings",)); return True


class FakeResolve:
    def __init__(self, calls, state):
        self.calls = calls
        self.state = state

    def GetCurrentPage(self):
        return self.state["page"]

    def OpenPage(self, name):
        self.calls.append(("OpenPage", name))
        self.state["page"] = name
        return True


def fresh(page="edit"):
    """载入一份新的 App，接上假对象，清空上一轮状态。"""
    rt, App = load_app()
    calls, state = [], {"page": page}
    App.resolve = FakeResolve(calls, state)
    App.project = FakeProject(calls)
    App.KillTasks = lambda *a: None       # Cleanup 里的副作用，测页面不关心
    App.EnableButtons = lambda *a: None
    App.S.renderSnap = None
    App.S.jobId = None
    App.S.prevPage = None
    App.S.pageMoved = None
    App.S.openPage = "edit"
    return rt, App, calls, state


def case_close_without_render():
    """★ 主用例：没渲染过就关窗 —— 不碰任何交付页 API，也不切页。"""
    _rt, App, calls, state = fresh(page="edit")
    App.Cleanup(App, True)
    assert calls == [], "关窗时不该有任何页面/渲染调用，实际：%r" % (calls,)
    assert state["page"] == "edit", "页面被切走了：%r" % (state,)
    print("PASS 没渲染就关窗：0 次页面/渲染调用")


def case_cleanup_render_noop():
    """直接调 CleanupRender（无快照无作业）也不许碰交付页。"""
    _rt, App, calls, _ = fresh(page="edit")
    App.CleanupRender(App)
    assert calls == [], "无渲染状态时不该调任何 API，实际：%r" % (calls,)
    print("PASS 无渲染状态调用 CleanupRender：0 次调用")


def case_restore_after_render():
    """真渲染过（pageMoved）→ 关窗要回到进入前那页。"""
    _rt, App, calls, state = fresh(page="deliver")
    App.S.pageMoved = True
    App.S.prevPage = "edit"
    App.Cleanup(App, True)
    assert ("OpenPage", "edit") in calls, "应当切回 edit，实际：%r" % (calls,)
    assert state["page"] == "edit", "实际停在 %r" % (state,)
    print("PASS 渲染过：关窗切回 edit（且只切一次）")


def case_restore_skips_when_already_there():
    """已经在目标页时不要再调 OpenPage（多余一次切页会闪）。"""
    _rt, App, calls, _ = fresh(page="edit")
    App.S.pageMoved = True
    App.S.prevPage = "edit"
    App.Cleanup(App, True)
    assert not any(c[0] == "OpenPage" for c in calls), \
        "已经在 edit 还切页：%r" % (calls,)
    print("PASS 已在目标页不重复切页")


def case_snapshot_still_restored():
    """有快照时仍要正常还原预设（别把 v1.0.1 的修复改坏）。"""
    rt, App, calls, _ = fresh(page="deliver")
    App.S.pageMoved = True
    App.S.prevPage = "edit"
    App.S.renderSnap = rt.eval('{ presetName = "AsrSubsBackup-1" }')
    App.S.jobId = "job-1"
    App.Cleanup(App, True)
    kinds = [c[0] for c in calls]
    assert ("DeleteRenderJob", "job-1") in calls, "作业没删：%r" % (calls,)
    assert ("LoadRenderPreset", "AsrSubsBackup-1") in calls, \
        "用户预设没还原：%r" % (calls,)
    assert ("DeleteRenderPreset", "AsrSubsBackup-1") in calls, \
        "临时预设没清掉：%r" % (calls,)
    assert ("OpenPage", "edit") in calls, "没切回原页：%r" % (calls,)
    assert "SetRenderSettings" not in kinds, "有快照时不该走兜底设置：%r" % (calls,)
    print("PASS 有快照：作业删除 + 预设还原 + 切回原页")


def main():
    case_close_without_render()
    case_cleanup_render_noop()
    case_restore_after_render()
    case_restore_skips_when_already_there()
    case_snapshot_still_restored()
    print("ALL CLOSE-PAGE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
