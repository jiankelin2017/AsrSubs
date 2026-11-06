# -*- coding: utf-8 -*-
"""hotwords.py 自测：拿真实 SRT 跑一遍，看阈值和排序对不对。

用法：
    C:\\AsrSubs\\engine\\.venv\\Scripts\\python.exe tools\\test_hotwords.py [srt]
不传 srt 就用 C:\\legacy\\AsrSubs\\字幕输出 里最新的那份。
"""
import glob
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import hotwords                      # noqa: E402
import subs_io                       # noqa: E402


def main():
    srt = sys.argv[1] if len(sys.argv) > 1 else ""
    if not srt:
        cand = sorted(glob.glob(r"D:\AsrSubs\engine\字幕输出\*.srt"),
                      key=os.path.getmtime)
        if not cand:
            print("找不到样片 SRT，跳过")
            return 0
        srt = cand[-1]
    print("样片：%s" % srt)

    t0 = time.time()
    blocks = subs_io.parse_srt(subs_io.read_text_any(srt))
    t1 = time.time()
    cnt, total = hotwords.count_blocks(blocks)
    items, thr = hotwords.top_words(cnt, total)
    t2 = time.time()

    print("字幕 %d 条 / 总词数 %d / 去重实词 %d" % (len(blocks), total, len(cnt)))
    print("解析 %.2fs  分词统计 %.2fs" % (t1 - t0, t2 - t1))
    print("阈值 = max(%d, ceil(%d × %.3f)) = %d" % (
        hotwords.THR_MIN, total, hotwords.THR_RATIO, thr))
    print("命中 %d 个，展示前 %d：" % (len(items), len(items)))
    print("  " + "  ·  ".join("%s %d" % (w, c) for w, c in items))

    # 写文件并回读，确认 Lua 语法结构没问题
    out = os.path.join(ROOT, "tools", "_hotwords_sample.lua")
    n, t, k = hotwords.write_lua(blocks, out)
    print("写出 %s：total=%d thr=%d n=%d" % (out, n, t, k))
    with open(out, "r", encoding="utf-8") as f:
        head = f.read().splitlines()[:4]
    print("\n".join(head))
    os.remove(out)

    # 边界：空字幕不能崩
    n2, t2_, k2 = hotwords.write_lua([], os.path.join(ROOT, "tools", "_hw_empty.lua"))
    print("空字幕：total=%d thr=%d n=%d" % (n2, t2_, k2))
    os.remove(os.path.join(ROOT, "tools", "_hw_empty.lua"))

    # 边界：极短字幕（低于下限时用 THR_MIN）
    tiny = [{"s": 0, "e": 1, "t": "人参很补"},
            {"s": 1, "e": 2, "t": "人参真好"},
            {"s": 2, "e": 3, "t": "人参不错"}]
    cnt3, tot3 = hotwords.count_blocks(tiny)
    it3, thr3 = hotwords.top_words(cnt3, tot3)
    print("短字幕：总词 %d 阈值 %d 结果 %s" % (tot3, thr3, it3))
    assert thr3 == hotwords.THR_MIN, "下限没生效"
    assert it3 and it3[0][0] == "人参", "人参 没排第一：%s" % (it3,)
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
