# -*- coding: utf-8 -*-
"""验证 2+3：裁段识别的换算与 ffmpeg 裁剪。

① _source_seconds：源 fps 换算（25fps 素材放 30fps 时间线上不能按 30 算）
② ffmpeg 只抽一段：时长对、内容与整段抽取的同一区间一致
"""
import io
import os
import subprocess
import sys

sys.path.insert(0, r"D:\插件\AsrSubs_整合_20261031\01_源码")
import timeline_source as T

ok = True


def check(cond, label, detail=""):
    global ok
    print("  %s  %s %s" % ("ok" if cond else "XX", label, detail))
    ok = ok and bool(cond)


print("== ① 源时间换算 ==")
# 25fps 素材，从源第 300 帧（=12.000s）起，用 250 帧（=10.000s）
it = {"sourceStart": 300, "sourceEnd": 550, "srcFps": 25.0}
a, b = T._source_seconds(it, 30.0)
check(abs(a - 12.0) < 1e-6 and abs(b - 10.0) < 1e-6,
      "25fps 源按 25 换算", "→ %.3f / %.3f" % (a, b))

# 取不到源 fps 时退回时间线 fps
it2 = {"sourceStart": 300, "sourceEnd": 550, "srcFps": 0}
c, d = T._source_seconds(it2, 25.0)
check(abs(c - 12.0) < 1e-6 and abs(d - 10.0) < 1e-6, "缺源 fps 退回时间线 fps", "→ %.3f / %.3f" % (c, d))

# 非法区间 → 0（调用方据此不走快速路径）
e, f = T._source_seconds({"sourceStart": 100, "sourceEnd": 100, "srcFps": 25}, 25)
check(e == 0 and f == 0, "空区间返回 0", "→ %s / %s" % (e, f))

print("== ② ffmpeg 只抽一段 ==")
import resolve_bridge as B
# ★ P1：样片路径由环境变量提供，避免把开发机上的真实录制文件写进仓库。
SRC = os.environ.get("ASRSUBS_TEST_VIDEO", "")
if not SRC or not os.path.isfile(SRC):
    print("  （未设置 ASRSUBS_TEST_VIDEO 或样片不存在，跳过依赖真实媒体的 ② 段）")
    print("== 结果 ==", "ok" if ok else "XX")
    sys.exit(0 if ok else 1)
TMP = os.environ.get("TEMP", ".")
full = os.path.join(TMP, "_trim_full.wav")
part = os.path.join(TMP, "_trim_part.wav")
check(B._ffmpeg_to_wav(SRC, full), "整段抽取")
check(B._ffmpeg_to_wav(SRC, part, ss=30.0, dur=20.0), "抽 30~50 秒")

import numpy as np
import soundfile as sf

a1, sr1 = sf.read(full, dtype="float32")
a2, sr2 = sf.read(part, dtype="float32")
d1, d2 = len(a1) / sr1, len(a2) / sr2
check(abs(d2 - 20.0) < 0.15, "裁剪时长 ≈20s", "实际 %.3fs" % d2)
check(sr1 == 16000 and sr2 == 16000, "16k 单声道")

# 内容对得上：整段里 [30,50) 与裁剪结果逐样本比较（允许 seek 的几十毫秒误差）
start = int(30.0 * sr1)
seg = a1[start:start + len(a2)]
n = min(len(seg), len(a2))
corr = float(np.dot(seg[:n], a2[:n]) / (np.linalg.norm(seg[:n]) * np.linalg.norm(a2[:n]) + 1e-9))
check(corr > 0.98, "裁剪内容与整段同一区间一致", "相关系数 %.3f" % corr)

for p in (full, part):
    try:
        os.remove(p)
    except OSError:
        pass

print()
print("结果:", "全部通过" if ok else "有失败")
sys.exit(0 if ok else 1)
