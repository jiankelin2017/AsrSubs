# -*- coding: utf-8 -*-
"""v4.0.7 R2 离线单测：relang_subtitles 的「删旧 + 落新」编排逻辑 + 三项修复。

不连真实 DaVinci：用假的 mp / tl / 媒体池项模拟 Resolve API。
覆盖：
  · 主路径（边车专用轨 + 落轨跨度）：按 spans 精确删本插件旧字幕；
  · 兜底路径（无边车）：退回来源 stem 匹配，保留其它轨用户字幕；
  · relang 的 killed>0 / killed==0 两分支；
  · 修复1：优先复用插件专用轨，绝不凭空新建 ST2；
  · 修复2：落轨失败时保留旧边车不动（不覆写）；
  · 修复3：边车指向空轨时，靠"唯一非空轨"救回被污染的边车；
  · 修复3b：边车与当前时间线不匹配时（跨时间线），不串扰。
"""
import json
import os
import sys
import tempfile

# ★ v4.0.9：本脚本现位于 01_源码\tools\，而被测模块 land_subtitle.py 在 01_源码\。
#   原来脚本在 01_源码\ 根目录时，"脚本所在目录"恰好就是模块所在目录；
#   搬进 tools\ 后必须显式回退一级，否则 import 不到模块。
SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

import land_subtitle as L


# ── 假对象 ────────────────────────────────────────────────────────────
class FakeMPI:
    def __init__(self, name, path=""):
        self._name = name
        self._path = path
    def GetName(self):
        return self._name
    def GetClipProperty(self):
        return {"File Path": self._path, "Original File Path": self._path}


class FakeSubItem:
    """字幕条目。span 可选：给了就模拟真机的 GetStart/GetEnd。"""
    _uid = 0
    def __init__(self, mpi, span=None):
        self._mpi = mpi
        self._span = span
        FakeSubItem._uid += 1
        self._uid = "uid%d" % FakeSubItem._uid
    def GetMediaPoolItem(self):
        return self._mpi
    def GetStart(self):
        return self._span[0] if self._span else 0
    def GetEnd(self):
        return self._span[1] if self._span else 0
    def GetUniqueId(self):
        return self._uid


class FakeTimeline:
    def __init__(self, tracks, name="Timeline 1"):
        self._tracks = {i: list(items) for i, items in tracks.items()}
        self._enabled = {i: True for i in tracks}
        self.deleted = []
        self.enabled_log = []
        self._name = name
        self.added = 0
        self.deleted_tracks = []      # ★ v4.0.8：记录被删除的字幕轨

    def GetName(self):
        return self._name
    def GetTrackCount(self, kind):
        return max(self._tracks.keys()) if self._tracks else 0
    def GetItemListInTrack(self, kind, idx):
        return list(self._tracks.get(idx, []))
    def DeleteClips(self, items, ripple=False):
        for i, its in list(self._tracks.items()):
            keep = [x for x in its if x not in items]
            for dead in set(items) & set(its):
                self.deleted.append(dead)
            self._tracks[i] = keep
    def AddTrack(self, kind):
        self.added += 1
        nxt = self.GetTrackCount(kind) + 1
        self._tracks[nxt] = []
        self._enabled[nxt] = True
    def DeleteTrack(self, kind, idx):
        """★ v4.0.8：模拟真机 DeleteTrack（删除后后面的轨索引前移）。"""
        idx = int(idx)
        if idx not in self._tracks:
            return False
        del self._tracks[idx]
        self._enabled.pop(idx, None)
        self.deleted_tracks.append(idx)
        # 索引前移
        for k in sorted([k for k in self._tracks if k > idx], reverse=True):
            self._tracks[k - 1] = self._tracks.pop(k)
            self._enabled[k - 1] = self._enabled.pop(k, True)
        return True
    def SetTrackEnabled(self, kind, idx, on):
        self._enabled[idx] = bool(on)
        self.enabled_log.append((idx, bool(on)))
    def GetIsTrackEnabled(self, kind, idx):
        return self._enabled.get(idx, False)
    def GetStartTimecode(self):
        return "00:00:00:00"
    def SetCurrentTimecode(self, tc):
        pass
    def GetSetting(self, key):
        return "30"


class FakeMP:
    def __init__(self):
        self.imported = []
    def GetRootFolder(self):
        return self
    def GetSubFolderList(self):
        return []
    def GetClipList(self):
        return []
    def DeleteClips(self, items):
        pass
    def ImportMedia(self, paths):
        self.imported.extend(paths)
        class _I:
            def GetName(self):
                return "fake.srt"
        return [_I()]


# ── 测试 ──────────────────────────────────────────────────────────────
SRT = "我的直播.srt"   # stem = "我的直播"

def make_plugin_item(span=None):
    return FakeSubItem(FakeMPI("我的直播", "我的直播.srt"), span)

def make_user_item(name, span=None):
    return FakeSubItem(FakeMPI(name, name + ".srt"), span)

def write_sidecar(srt_path, track, spans=None, owned=True, tl="Timeline 1"):
    with open(srt_path + ".landed.json", "w", encoding="utf-8") as f:
        json.dump({"track": track, "spans": spans or [],
                   "owned": owned, "tl": tl, "ver": 2}, f, ensure_ascii=False)


def test_is_our_subtitle_item():
    assert L._is_our_subtitle_item(make_plugin_item(), SRT) is True
    assert L._is_our_subtitle_item(make_user_item("别的字幕"), SRT) is False
    class _NoMPI:
        def GetMediaPoolItem(self):
            return None
    assert L._is_our_subtitle_item(_NoMPI(), SRT) is False
    print("  ✓ _is_our_subtitle_item 判据正确（兜底路径仍可用）")


def test_clear_by_spans(tmp):
    """主路径：边车带 spans → 按落轨跨度精确删本插件旧字幕，用户条目保留。"""
    d = os.path.join(tmp, "by_spans"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({
        1: [make_plugin_item((0, 60)), make_plugin_item((75, 120)),
            make_user_item("同轨用户字幕", (200, 260))],
        2: [make_user_item("另一轨用户字幕")],
    })
    write_sidecar(os.path.join(d, SRT), 1, spans=[[0, 60], [75, 120]])
    killed = L.clear_our_subtitle_items(tl, os.path.join(d, SRT))
    assert killed == 2, "按跨度精确删应删 2 条，实际 %d" % killed
    names = [it._mpi.GetName() for its in tl._tracks.values() for it in its]
    assert "同轨用户字幕" in names, "误删了同轨用户字幕"
    assert "另一轨用户字幕" in names, "误删了其它轨用户字幕"
    print("  ✓ 主路径：按落轨跨度精确删本插件旧字幕、保留用户字幕")


def test_clear_fallback_stem(tmp):
    """兜底：无边车 → 来源 stem 匹配，只删本插件、保留所有用户字幕。"""
    d = os.path.join(tmp, "fallback"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({
        1: [make_plugin_item(), make_plugin_item(), make_user_item("用户手工1")],
        2: [make_user_item("用户手工2")],
    })
    killed = L.clear_our_subtitle_items(tl, os.path.join(d, SRT))
    assert killed == 2, "兜底应只删 2 条本插件字幕，实际 %d" % killed
    names = [it._mpi.GetName() for its in tl._tracks.values() for it in its]
    assert "用户手工1" in names and "用户手工2" in names, "误删了用户手工字幕"
    assert all(it._mpi.GetName() != "我的直播" for its in tl._tracks.values() for it in its)
    print("  ✓ 兜底：stem 匹配只删本插件、保留用户字幕")


def test_clear_rescue_polluted_sidecar(tmp):
    """修复3：边车指向空轨（被失败落轨污染）→ 靠「唯一非空轨」救回。"""
    d = os.path.join(tmp, "rescue"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({
        1: [make_plugin_item((0, 60)), make_plugin_item((75, 120))],  # 真正的插件轨
        2: [],                                                        # 边车误记的空轨
    })
    # 污染边车：track=2（空）、spans=[] —— 正是真机 v4.0.6 失败落轨写出来的
    write_sidecar(os.path.join(d, SRT), 2, spans=[])
    killed = L.clear_our_subtitle_items(tl, os.path.join(d, SRT))
    assert killed == 2, "救回后应删掉真正插件轨上的 2 条，实际 %d" % killed
    assert not tl._tracks.get(1), "真正插件轨未清空"
    print("  ✓ 修复3：边车指向空轨时，靠唯一非空轨救回被污染的边车")


def test_clear_other_timeline_not_applied(tmp):
    """修复3b：边车属于另一条时间线 → 不串扰（当无边车处理）。"""
    d = os.path.join(tmp, "other_tl"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({1: [make_user_item("别的轨字幕")]}, name="Timeline 2")
    write_sidecar(os.path.join(d, SRT), 1, spans=[], tl="Timeline 1")
    killed = L.clear_our_subtitle_items(tl, os.path.join(d, SRT))
    assert killed == 0, "跨时间线不应删除任何东西，实际 %d" % killed
    assert tl._tracks.get(1), "跨时间线误删了条目"
    print("  ✓ 修复3b：边车与当前时间线不匹配时不串扰")


def test_relang_killed_gt_0(tmp):
    """killed>0：删旧 + 落新 + 恢复其它轨启用态 + 强制落点轨启用。"""
    d = os.path.join(tmp, "killed_gt_0"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({
        1: [make_plugin_item((0, 60)), make_plugin_item((75, 120))],
        2: [make_user_item("U1"), make_user_item("U2")],
        3: [],
    })
    tl._enabled[2] = False  # 用户禁用轨2
    write_sidecar(os.path.join(d, SRT), 1, spans=[[0, 60], [75, 120]])
    mp = FakeMP()

    calls = {}
    def fake_land(mp_, tl_, land_srt, expected_offset, fps, work, meta_key=None, **kw):
        calls["land"] = True
        calls["meta_key"] = meta_key
        return {"ok": True, "placed": 2, "track": 1, "first": 0,
                "srt_first": 0, "aligned": True, "method": "inplace"}
    orig = L.land_inplace
    L.land_inplace = fake_land
    try:
        d_res = L.relang_subtitles(mp, tl, os.path.join(d, SRT), "x.mp4", 30.0, d)
    finally:
        L.land_inplace = orig

    assert calls.get("land") is True, "未调用 land_inplace"
    assert d_res["relang_cleared"] == 2, "relang_cleared 应为 2，实际 %s" % d_res.get("relang_cleared")
    assert d_res["method"] == "relang"
    assert tl._enabled[2] is False, "用户禁用的轨2 被错误启用"
    assert tl._enabled[1] is True, "落点轨未启用"
    names = [it._mpi.GetName() for its in tl._tracks.values() for it in its]
    assert "U1" in names and "U2" in names, "用户字幕丢失"
    print("  ✓ relang(killed>0)：删旧 + 落新 + 保留用户轨禁用态 + 强制落点轨启用")


def test_relang_killed_eq_0(tmp):
    """killed==0：无本插件字幕（无边车）→ 仅同步媒体池，不新建字幕轨。"""
    d = os.path.join(tmp, "killed_eq_0"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({1: [make_user_item("纯用户字幕")]})
    mp = FakeMP()
    d_res = L.relang_subtitles(mp, tl, os.path.join(d, SRT), "x.mp4", 30.0, d)
    assert d_res["relang_cleared"] == 0
    assert d_res["placed"] == 0
    assert d_res["method"] == "relang"
    assert os.path.join(d, SRT) in mp.imported, "killed==0 时应同步媒体池"
    assert tl._enabled[1] is True
    print("  ✓ relang(killed==0)：仅同步媒体池、不新建字幕轨")


def test_land_reuses_dedicated_track(tmp):
    """修复1：land_inplace 优先复用插件专用轨，绝不新建 ST2。"""
    d = os.path.join(tmp, "reuse"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({
        1: [make_plugin_item((0, 60))],   # 专用轨（插件的旧字幕）
        2: [],                             # 空轨
    })
    write_sidecar(os.path.join(d, SRT), 1, spans=[[0, 60]])
    mp = FakeMP()

    # 让 AppendToTimeline 把字幕落到 track 1（模拟真机行为）
    def fake_append(items):
        tl._tracks[1] = tl._tracks[1] + [make_plugin_item((0, 60))]
    mp.AppendToTimeline = fake_append

    L.land_inplace(mp, tl, os.path.join(d, SRT), 0, 30.0, d, meta_key=os.path.join(d, SRT))
    assert tl.added == 0, "复用了专用轨却仍新建了字幕轨（凭空多出 ST2）"
    print("  ✓ 修复1：复用插件专用轨，不新建 ST2")


def test_land_does_not_write_sidecar_on_failure(tmp):
    """修复2：落轨失败（0 条）时保留旧边车不动，不被覆写。"""
    d = os.path.join(tmp, "nooverwrite"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({1: [], 2: []})
    meta_path = os.path.join(d, SRT)
    write_sidecar(meta_path, 1, spans=[[0, 60]])
    before = open(meta_path + ".landed.json", encoding="utf-8").read()
    mp = FakeMP()
    mp.AppendToTimeline = lambda items: None   # 落 0 条

    L.land_inplace(mp, tl, meta_path, 0, 30.0, d, meta_key=meta_path)
    after = open(meta_path + ".landed.json", encoding="utf-8").read()
    assert before == after, "失败落轨把旧边车覆写了（会把后续定位带偏）"
    print("  ✓ 修复2：落轨失败保留旧边车不动")


def test_land_writes_sidecar_on_success(tmp):
    """成功落轨：边车记录实际落点轨 + 落轨跨度。"""
    d = os.path.join(tmp, "writesidecar"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({1: []})
    meta_path = os.path.join(d, SRT)
    mp = FakeMP()
    def fake_append(items):
        tl._tracks[1] = tl._tracks[1] + [make_plugin_item((0, 60))]
    mp.AppendToTimeline = fake_append

    L.land_inplace(mp, tl, meta_path, 0, 30.0, d, meta_key=meta_path)
    meta = json.load(open(meta_path + ".landed.json", encoding="utf-8"))
    assert meta["track"] == 1, "边车轨索引应为 1，实际 %s" % meta.get("track")
    assert meta["spans"] == [[0, 60]], "边车跨度应记录 [(0,60)]，实际 %s" % meta.get("spans")
    assert meta["owned"] is True, "落进空轨应标记 owned=True"
    assert meta["tl"] == "Timeline 1", "边车应记录时间线名"
    print("  ✓ 成功落轨：边车记录落点轨 + 跨度 + owned + 时间线名")


def test_write_lua_exports_relang_cleared(tmp):
    """回归：write_lua 必须把 relang_cleared 写进 Lua 文件。

    ★ v4.0.7：v4.0.6 的 write_lua 漏了这个字段 → Lua 侧 tonumber(d.relang_cleared)
      恒为 nil → 永远显示"时间线此前无本插件字幕"，与实际删了几条完全对不上
      （真机日志明明写着"整轨清除专用字幕轨 1 旧条目 53 条"）。
    """
    d = os.path.join(tmp, "writelua"); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "land.lua")
    L.write_lua(out, {"ok": True, "placed": 31, "total": 31, "track": 1,
                       "method": "relang", "relang_cleared": 53})
    txt = open(out, encoding="utf-8").read()
    assert "relang_cleared = 53" in txt, "write_lua 未导出 relang_cleared：\n%s" % txt
    print("  ✓ 回归：write_lua 正确导出 relang_cleared（Lua 侧不再误报无旧字幕）")


# ── ★ v4.0.8 修复4：落轨失败不得留下孤儿空字幕轨 ──────────────────────
SRT_OK = """1
00:00:00,000 --> 00:00:01,500
测试字幕第一行

"""


def write_srt(path, body=SRT_OK):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def test_land_failure_reclaims_self_created_track(tmp):
    """★ v4.0.8 修复4：落轨失败时，本次自建的空字幕轨必须被回收。

    真机复验暴露：land_inplace 在「没有可用空轨」时会 AddTrack 新建一条，
    落不上就留在时间线上 → 用户看到「ST2 凭空出现」的空轨。
    这与 v4.0.6 真机日志「整轨清除专用字幕轨 1 旧条目 53 条 → 已挂到 0 条
    → 边车写成 track=2」是同一条路径。
    """
    d = os.path.join(tmp, "reclaim"); os.makedirs(d, exist_ok=True)
    # 只有一条【非空】轨 → 没有可用空轨 → 会走 AddTrack
    tl = FakeTimeline({1: [make_user_item("别的字幕", (0, 60))]})
    srt_path = write_srt(os.path.join(d, SRT))
    mp = FakeMP()
    mp.AppendToTimeline = lambda items: None   # 落 0 条 → 失败

    r = L.land_inplace(mp, tl, srt_path, 0, 30.0, d, meta_key=srt_path)
    assert tl.added == 1, "本用例前提是确实新建了一条轨，实际 %d" % tl.added
    assert tl.deleted_tracks == [2], \
        "落轨失败后自建的空轨未被回收，deleted_tracks=%s" % tl.deleted_tracks
    assert tl.GetTrackCount("subtitle") == 1, \
        "回收后轨道数应回到 1，实际 %d" % tl.GetTrackCount("subtitle")
    # 用户的轨必须完好
    assert len(tl.GetItemListInTrack("subtitle", 1)) == 1, "用户的字幕被误删了"
    assert r.get("reclaimed_track") == 2, \
        "返回值应报告回收了哪条轨，实际 %s" % r.get("reclaimed_track")
    print("  ✓ 修复4：落轨失败，回收本次自建的空字幕轨（不留孤儿轨）")


def test_land_empty_srt_early_return(tmp):
    """★ v4.0.8 修复4a：SRT 一条字幕都没有 → 直接早退，连轨都不碰。

    空 SRT 落轨必然 0 条，而 0 条会走「失败回收」路径；不如在动时间线
    之前就返回，绝不碰任何字幕轨（也就不可能留下孤儿空轨）。
    """
    d = os.path.join(tmp, "emptysrt"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({1: []})
    srt_path = write_srt(os.path.join(d, SRT), body="")
    mp = FakeMP()
    called = []
    mp.AppendToTimeline = lambda items: called.append(items)

    r = L.land_inplace(mp, tl, srt_path, 0, 30.0, d, meta_key=srt_path)
    assert not called, "空 SRT 不该触发 AppendToTimeline"
    assert tl.added == 0, "空 SRT 不该新建字幕轨"
    assert tl.GetTrackCount("subtitle") == 1, "空 SRT 不该改动轨道数"
    assert r.get("ok") is False, "空 SRT 应返回失败"
    assert "0 条" in (r.get("diag") or ""), "诊断信息应说明 0 条：%s" % r.get("diag")
    print("  ✓ 修复4a：SRT 0 条提前早退，完全不碰时间线")


def test_land_success_keeps_self_created_track(tmp):
    """★ v4.0.8 修复4 回归：落轨【成功】时自建轨必须保留（不得误删）。"""
    d = os.path.join(tmp, "keep"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({1: [make_user_item("别的字幕", (0, 60))]})
    srt_path = write_srt(os.path.join(d, SRT))
    mp = FakeMP()
    def fake_append(items):
        tl._tracks[2] = tl._tracks.get(2, []) + [make_plugin_item((0, 60))]
    mp.AppendToTimeline = fake_append

    r = L.land_inplace(mp, tl, srt_path, 0, 30.0, d, meta_key=srt_path)
    assert tl.added == 1, "前提：确实新建了一条轨"
    assert tl.deleted_tracks == [], "落轨成功却把自建轨删了：%s" % tl.deleted_tracks
    assert r.get("ok") is True, "落轨应成功"
    assert r.get("reclaimed_track") == 0, "成功时不应报告回收"
    assert len(tl.GetItemListInTrack("subtitle", 2)) == 1, "字幕应留在自建轨上"
    print("  ✓ 修复4 回归：落轨成功时自建轨保留，不误删")


def test_delete_track_refuses_nonempty(tmp):
    """★ v4.0.8 修复4 安全约定：_delete_track 绝不删非空轨。"""
    d = os.path.join(tmp, "nonempty"); os.makedirs(d, exist_ok=True)
    tl = FakeTimeline({1: [make_user_item("别的字幕", (0, 60))]})
    ok = L._delete_track(tl, 1)
    assert ok is False, "非空轨被删了"
    assert tl.deleted_tracks == [], "非空轨被删了：%s" % tl.deleted_tracks
    assert len(tl.GetItemListInTrack("subtitle", 1)) == 1, "用户的字幕被误删"
    print("  ✓ 修复4 安全约定：拒绝删除非空字幕轨")


def test_write_lua_exports_reclaimed_track(tmp):
    """★ v4.0.8：write_lua 导出 reclaimed_track（便于 Lua 侧提示回收）。"""
    d = os.path.join(tmp, "writelua2"); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, "land.lua")
    L.write_lua(out, {"ok": False, "placed": 0, "total": 0, "track": 2,
                       "method": "inplace", "reclaimed_track": 2})
    txt = open(out, encoding="utf-8").read()
    assert "reclaimed_track = 2" in txt, "write_lua 未导出 reclaimed_track：\n%s" % txt
    print("  ✓ 回归：write_lua 正确导出 reclaimed_track")


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="r2_test_")
    try:
        test_is_our_subtitle_item()
        test_clear_by_spans(tmp)
        test_clear_fallback_stem(tmp)
        test_clear_rescue_polluted_sidecar(tmp)
        test_clear_other_timeline_not_applied(tmp)
        test_relang_killed_gt_0(tmp)
        test_relang_killed_eq_0(tmp)
        test_land_reuses_dedicated_track(tmp)
        test_land_does_not_write_sidecar_on_failure(tmp)
        test_land_writes_sidecar_on_success(tmp)
        test_write_lua_exports_relang_cleared(tmp)
        # ★ v4.0.8 修复4
        test_land_failure_reclaims_self_created_track(tmp)
        test_land_empty_srt_early_return(tmp)
        test_land_success_keeps_self_created_track(tmp)
        test_delete_track_refuses_nonempty(tmp)
        test_write_lua_exports_reclaimed_track(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL R2 RELANG TESTS PASSED")
