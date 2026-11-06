# -*- coding: utf-8 -*-
"""R1 偏移校准逻辑离线单测（不连 DaVinci）。"""
import sys, os
# ★ v4.0.9：本脚本现位于 01_源码\tools\，而被测模块 cut_inplace.py 在 01_源码\。
#   原来脚本在 01_源码\ 根目录时，"脚本所在目录"恰好就是模块所在目录；
#   搬进 tools\ 后必须显式回退一级，否则 import 不到模块。
SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cut_inplace as CI

FPS = 30
OFF = 300  # 素材从第 300 帧（=10s）起：SRT 内部秒 → 时间线帧 偏移 +300

# 真实字幕：内部秒 + 偏移；时长各不相同（贴近真实）
SRT = [(0.0, 2.3), (3.1, 5.0), (5.5, 9.2), (10.0, 11.4), (12.0, 14.8)]


class FakeItem:
    def __init__(self, sf, ef):
        self.sf, self.ef = sf, ef
    def GetStart(self): return float(self.sf)
    def GetEnd(self): return float(self.ef)


class FakeTL:
    def __init__(self, items):
        self._items = items
    def GetTrackCount(self, kind):
        return 1 if kind == "subtitle" and self._items else 0
    def GetItemListInTrack(self, kind, idx):
        return list(self._items) if kind == "subtitle" else []


def build(offset):
    items = [FakeItem(int(s * FPS) + offset, int(e * FPS) + offset) for s, e in SRT]
    return FakeTL(items)


def test_basic():
    tl = build(OFF)
    # 删第 2、4 条（内部 [3.1,5.0]、[10.0,11.4]）
    cuts = [(3.1, 5.0), (10.0, 11.4)]
    got = CI._subtitle_offset_frames(tl, cuts, FPS, 0, 100000)
    assert got == OFF, "期望偏移 %d，得到 %d" % (OFF, got)
    print("PASS 基础偏移校准: off=%d" % got)


def test_no_subtitle_track():
    tl = FakeTL([])
    got = CI._subtitle_offset_frames(tl, [(2, 4)], FPS, 0, 100000)
    assert got == 0, "无字幕轨应回退 0，得到 %d" % got
    print("PASS 无字幕轨安全回退: off=%d" % got)


def test_zero_offset():
    tl = build(0)
    cuts = [(3.1, 5.0), (10.0, 11.4)]
    got = CI._subtitle_offset_frames(tl, cuts, FPS, 0, 100000)
    assert got == 0, "偏移为 0 时应得 0，得到 %d" % got
    print("PASS 零偏移: off=%d" % got)


def test_consensus_fallback():
    tl = FakeTL([])
    got = CI._subtitle_offset_frames(tl, [(2, 4), (6, 8)], FPS, 0, 100000)
    assert got == 0
    print("PASS 无锚点回退: off=%d" % got)


def test_single_cut():
    # 单段 cut 也能校准（时长一致锁定被删那条）
    tl = build(OFF)
    got = CI._subtitle_offset_frames(tl, [(3.1, 5.0)], FPS, 0, 100000)
    assert got == OFF, "单段应校准出 %d，得到 %d" % (OFF, got)
    print("PASS 单段校准: off=%d" % got)


def test_too_far_no_vote():
    # cut 区间时长与任何字幕都不一致 → 不投票 → 回退 0
    tl = build(OFF)
    got = CI._subtitle_offset_frames(tl, [(100.0, 200.0)], FPS, 0, 100000)
    assert got == 0, "时长不符应回退 0，得到 %d" % got
    print("PASS 时长不符不投票: off=%d" % got)


def test_merged_cut_no_single_match():
    # 合并区间覆盖多条 → 时长与单条不符 → 该段不投票；但若另有单段仍可校准
    tl = build(OFF)
    got = CI._subtitle_offset_frames(tl, [(1.5, 12.0), (3.1, 5.0)], FPS, 0, 100000)
    assert got == OFF, "合并+单段混合应校准出 %d，得到 %d" % (OFF, got)
    print("PASS 合并+单段混合: off=%d" % got)


if __name__ == "__main__":
    test_basic()
    test_no_subtitle_track()
    test_zero_offset()
    test_consensus_fallback()
    test_single_cut()
    test_too_far_no_vote()
    test_merged_cut_no_single_match()
    print("ALL R1 TESTS PASSED")
