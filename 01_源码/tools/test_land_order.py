# -*- coding: utf-8 -*-
"""v4.1.0 离线单测：落轨的两条硬约束。

不连真实 DaVinci：用假 mp / tl / 条目模拟 Resolve API。

覆盖：
  1) 顺序铁律 —— land_inplace 里 ImportMedia 必须发生在任何时间线写操作之前
                  （剪包助手真机取证：先动时间线会触发工程保存 → ImportMedia 返回空列表）；
  2) 导入回退 —— 第一轮 ImportMedia 返回 [] 时自动重试第二轮；
  3) 导入全败 —— 两轮都空 → 抛 RuntimeError（不再静默落 0 条）；
  4) API 优先 —— relang 在「整轨独占 + 条数一致 + 起始帧全配上」时走 SetName 直改，
                  完全不碰媒体池；
  5) 直改护栏 —— SetName 返回 True 但回读不一致时判失败（该版本不可用）；
  6) 退回路径 —— 轨道非独占时放弃直改，且导入仍先于删旧字幕。

运行：python tools\test_land_order.py
"""
import os
import sys
import tempfile

SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

import land_subtitle as L

FPS = 25.0
SRT_BODY = (
    "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n"
    "2\n00:00:02,000 --> 00:00:04,000\n世界\n\n"
)

fails = []


def eq(actual, expected, label):
    if actual != expected:
        fails.append("%s\n    期望: %r\n    实际: %r" % (label, expected, actual))
        print("  FAIL " + label)
    else:
        print("  ok   " + label)


def ok(cond, label):
    eq(bool(cond), True, label)


# ── 假对象 ────────────────────────────────────────────────────────────
class FakeItem:
    _n = 0

    def __init__(self, name, start, end=None, settable=True):
        FakeItem._n += 1
        self._name = name
        self._start = start
        self._end = start + 10 if end is None else end
        self._uid = "u%d" % FakeItem._n
        self._settable = settable

    def GetName(self):
        return self._name

    def SetName(self, v):
        if not self._settable:
            return True          # 模拟"返回成功但不生效"的版本
        self._name = v
        return True

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetUniqueId(self):
        return self._uid

    def GetMediaPoolItem(self):
        return None


class FakeFolder:
    def __init__(self):
        self.clips = []

    def GetClipList(self):
        return list(self.clips)

    def GetSubFolderList(self):
        return []


class FakeTL:
    def __init__(self, tracks=None, name="Timeline 1"):
        self.tracks = {i: list(v) for i, v in (tracks or {1: []}).items()}
        self.enabled = {i: True for i in self.tracks}
        self.writes = []
        self._name = name
        self.timecode = "01:00:00:00"
        self.inserted = 0

    def GetName(self):
        return self._name

    def GetTrackCount(self, kind):
        return len(self.tracks)

    def GetItemListInTrack(self, kind, i):
        return list(self.tracks.get(i, []))

    def GetIsTrackEnabled(self, kind, i):
        return self.enabled.get(i, True)

    def SetTrackEnable(self, kind, i, v):
        self.writes.append("enable")
        self.enabled[i] = bool(v)
        return True

    def AddTrack(self, kind):
        self.writes.append("addtrack")
        n = len(self.tracks) + 1
        self.tracks[n] = []
        self.enabled[n] = True
        return True

    def GetStartTimecode(self):
        return "01:00:00:00"

    def GetCurrentTimecode(self):
        return self.timecode

    def SetCurrentTimecode(self, tc):
        self.writes.append("timecode")
        self.timecode = tc
        return True

    def DeleteClips(self, items, ripple=False):
        self.writes.append("delete")
        return True

    def InsertIntoTimeline(self, items):
        self.writes.append("insert")
        self.inserted += 1
        return True

    def DeleteTrack(self, kind, idx):
        self.writes.append("deletetrack")
        self.tracks.pop(idx, None)
        self.enabled.pop(idx, None)
        return True


class FakeMP:
    def __init__(self, tl, results=None):
        self.tl = tl
        self.folder = FakeFolder()
        self.results = list(results or [])
        self.imports = []        # 每次 ImportMedia 调用那一刻的时间线写日志
        self.deletes = []

    def GetRootFolder(self):
        return self.folder

    def SetCurrentFolder(self, f):
        pass

    def RefreshFolders(self):
        pass

    def ImportMedia(self, paths):
        self.imports.append(list(self.tl.writes))
        if self.results:
            return self.results.pop(0)
        return []

    def AppendToTimeline(self, items):
        self.tl.writes.append("append")
        return []

    def DeleteClips(self, items):
        self.deletes.append(items)
        return True


class FakeMPI:
    def GetName(self):
        return "srt"

    def GetClipProperty(self):
        return {"File Path": "", "Type": "Subtitle"}


# ── 环境准备：真 SRT 文件 + 静音重试 ─────────────────────────────────
def make_srt():
    fd, p = tempfile.mkstemp(suffix=".srt", prefix="asr_test_")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as f:
        f.write(SRT_BODY)
    return p


def patch_sleep():
    L.IMPORT_SETTLE_SEC = 0
    L.IMPORT_RETRY_SEC = 0


def patch_light(monkeypatches=None):
    """把 land_inplace 里与本次断言无关的重活替换掉，只留下"顺序"与"落轨"两条主线。"""
    patches = {
        "_read_landed_meta": lambda p: None,
        "_dedicated_info": lambda tl, meta: (None, "无边车", False),
        "count_srt": lambda p: 2,
        "srt_first_frame": lambda p, f: 0,
        "subtitle_clip_ids": lambda tl, i: set(),
        "_write_landed_meta": lambda *a, **k: None,
        "_remove_our_items": lambda tl, t, m, o: 0,
    }
    patches.update(monkeypatches or {})
    saved = {}
    for k, v in patches.items():
        saved[k] = getattr(L, k)
        setattr(L, k, v)
    return saved


def restore(saved):
    for k, v in saved.items():
        setattr(L, k, v)


# ── 用例 ──────────────────────────────────────────────────────────────
def case_1_import_before_timeline():
    print("\n== 1 顺序铁律：导入先于时间线写操作 ==")
    srt = make_srt()
    tl = FakeTL({1: []})
    mp = FakeMP(tl, results=[[FakeMPI()]])
    saved = patch_light()
    try:
        L.land_inplace(mp, tl, srt, 0, FPS, os.path.dirname(srt))
    finally:
        restore(saved)
        os.unlink(srt)
    ok(mp.imports, "ImportMedia 被调用")
    eq(mp.imports[0], [], "导入那一刻时间线还没有任何写操作")
    ok(tl.writes, "导入之后才开始写时间线")


def case_2_import_retry():
    print("\n== 2 导入回退：第一轮空、第二轮成功 ==")
    srt = make_srt()
    tl = FakeTL({1: []})
    sentinel = FakeMPI()
    mp = FakeMP(tl, results=[[], [sentinel]])
    expect_base = os.path.splitext(os.path.basename(srt))[0]
    item, base = L.import_to_pool(mp, srt)
    os.unlink(srt)
    eq(len(mp.imports), 2, "共尝试两轮")
    ok(item is sentinel, "第二轮结果被采用")
    eq(base, expect_base, "返回基名与原文件一致")


def case_3_import_fail_raises():
    print("\n== 3 导入全败：抛错而不是静默落 0 条 ==")
    srt = make_srt()
    tl = FakeTL({1: []})
    mp = FakeMP(tl, results=[[], []])
    raised = False
    try:
        L.import_to_pool(mp, srt)
    except RuntimeError:
        raised = True
    os.unlink(srt)
    ok(raised, "两轮都空 → RuntimeError")
    eq(len(mp.imports), 2, "确实试了两轮")


def case_4_direct_write():
    print("\n== 4 API 优先：SetName 直改，不碰媒体池 ==")
    srt = make_srt()
    it1, it2 = FakeItem("旧一", 0), FakeItem("旧二", 50)
    tl = FakeTL({1: [it1, it2]})
    mp = FakeMP(tl)
    saved = patch_light({
        "_read_landed_meta": lambda p: {"track": 1, "spans": [[0, 50]], "owned": True},
        "_dedicated_info": lambda tl_, meta: (1, "边车", True),
        "clear_our_subtitle_items": lambda tl_, key: 0,
    })
    L._SETNAME_CAPS = {"checked": False, "ok": None}
    try:
        r = L.relang_subtitles(mp, tl, srt, "", FPS, os.path.dirname(srt),
                               meta_key="k")
    finally:
        restore(saved)
        os.unlink(srt)
    eq(r.get("method"), "direct", "走直改分支")
    eq(mp.imports, [], "完全没有导入媒体池")
    eq(it1.GetName(), "你好", "第 1 条文本已改")
    eq(it2.GetName(), "世界", "第 2 条文本已改")


def case_5_setname_unreliable():
    print("\n== 5 直改护栏：SetName 返回 True 但回读不一致 → 判失败 ==")
    bad = FakeItem("旧", 0, settable=False)
    eq(L.set_subtitle_text(bad, "新"), False, "回读不一致视为不可用")

    srt = make_srt()
    bad = FakeItem("旧一", 0, settable=False)
    good = FakeItem("旧二", 50)
    tl = FakeTL({1: [bad, good]})
    mp = FakeMP(tl, results=[[FakeMPI()]])
    saved = patch_light({
        "_read_landed_meta": lambda p: {"track": 1, "spans": [[0, 10]], "owned": True},
        "_dedicated_info": lambda tl_, meta: (1, "边车", True),
        "clear_our_subtitle_items": lambda tl_, key: 1,
    })
    L._SETNAME_CAPS = {"checked": False, "ok": None}
    try:
        r = L.relang_subtitles(mp, tl, srt, "", FPS, os.path.dirname(srt), meta_key="k")
    finally:
        restore(saved)
        os.unlink(srt)
    eq(r.get("direct", False), False, "没有采用直改")
    ok(mp.imports, "退回 SRT 往返（有导入）")
    eq(L._SETNAME_CAPS["ok"], False, "本会话已记住 SetName 不可用")


def case_6_fallback_order():
    print("\n== 6 退回路径：非独占轨 → 导入仍先于删旧 ==")
    srt = make_srt()
    tl = FakeTL({1: [FakeItem("用户字幕", 0)]})
    mp = FakeMP(tl, results=[[FakeMPI()]])
    marks = []

    def _clear(tl_, key):
        marks.append("clear")
        tl_.writes.append("delete")
        return 1

    saved = patch_light({
        "_read_landed_meta": lambda p: {"track": 1, "spans": [[0, 10]], "owned": False},
        "_dedicated_info": lambda tl_, meta: (1, "边车", False),
        "clear_our_subtitle_items": _clear,
    })
    L._SETNAME_CAPS = {"checked": False, "ok": None}
    try:
        L.relang_subtitles(mp, tl, srt, "", FPS, os.path.dirname(srt), meta_key="k")
    finally:
        restore(saved)
        os.unlink(srt)
    ok(mp.imports, "有导入")
    eq(mp.imports[0], [], "导入时时间线仍无写操作")
    eq(marks[:1], ["clear"], "清旧字幕发生在导入之后")


def case_7_enable_guard():
    """v4.1.1：目标轨启用失败时必须放弃落轨，绝不把字幕落到别的轨。"""
    print("\n== 7 启用护栏：目标轨启不起来 → 放弃，不落任何条目 ==")

    class FakeTLLocked(FakeTL):
        SetTrackEnable = None          # 模拟 21.1.2 上旧实现只认错名字的情形
        SetTrackEnabled = None
        SetTrackEnableState = None

    srt = make_srt()
    tl = FakeTLLocked({1: []})
    tl.enabled = {1: False}            # 轨道起始为禁用，且没有任何可用的启用方法
    mp = FakeMP(tl, results=[[FakeMPI()]])
    saved = patch_light()
    try:
        r = L.land_inplace(mp, tl, srt, 0, FPS, os.path.dirname(srt))
    finally:
        restore(saved)
        os.unlink(srt)
    eq(r.get("ok"), False, "落轨被放弃")
    ok("启用失败" in (r.get("diag") or ""), "诊断写明启用失败")
    eq(tl.inserted, 0, "没有 Appendi/Insert 任何条目")
    eq(sum(len(v) for v in tl.tracks.values()), 0, "轨道上没有任何新条目")


def case_8_media_storage_fallback():
    """v4.3.1：两轮 ImportMedia 都失败 → MediaStorage 第三通路兜底。"""
    print("\n== 8 第三通路：ImportMedia 全败时走 MediaStorage ==")

    class FakeStorage:
        def __init__(self, items):
            self.items = items
            self.calls = 0

        def AddItemListToMediaPool(self, paths):
            self.calls += 1
            return list(self.items)

    class FakeResolver:
        def __init__(self, storage):
            self.storage = storage

        def GetMediaStorage(self):
            return self.storage

    srt = make_srt()
    tl = FakeTL({1: []})
    mp = FakeMP(tl, results=[[], []])
    sentinel = FakeMPI()
    storage = FakeStorage([sentinel])
    item, base = L.import_to_pool(mp, srt, resolver=FakeResolver(storage))
    os.unlink(srt)
    eq(len(mp.imports), 2, "前两轮 ImportMedia 都试过")
    eq(storage.calls, 1, "MediaStorage 被调用一次")
    ok(item is sentinel, "第三通路的结果被采用")


def main():
    patch_sleep()
    print("v4.1.0 落轨顺序 / API 优先 单测")
    case_1_import_before_timeline()
    case_2_import_retry()
    case_3_import_fail_raises()
    case_4_direct_write()
    case_5_setname_unreliable()
    case_6_fallback_order()
    case_7_enable_guard()
    case_8_media_storage_fallback()

    print("\n" + "=" * 56)
    if fails:
        print("FAILED %d 项：" % len(fails))
        for f in fails:
            print(" - " + f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
