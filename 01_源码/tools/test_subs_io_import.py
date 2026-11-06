# -*- coding: utf-8 -*-
"""subs_io.py 的同目录导入必须自带 sys.path 保护 —— 回归用例。

★ v4.8.19 的坑：装机版 runtime 是嵌入式发行版（runtime\\Scripts\\python312._pth），
  解释器固定在 isolated 模式，**脚本自己所在的目录不在 sys.path 里**。
  subs_io.py 用 from txt_out / import hotwords 引同目录模块，却没像别的入口
  脚本那样插 sys.path，于是运行时报
      ModuleNotFoundError: No module named 'txt_out'
  界面显示成「读取字幕超时（字幕文件仍在：…）」+「subs_io.py 异常退出(1)」。

  这里用 `python -P` 复现同样的环境（-P = 不把脚本目录放进 sys.path），
  三种模式各跑一遍。任何一处漏了保护，这条用例就会红。
"""
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
SCRIPT = os.path.join(SRC, "subs_io.py")

fails = []


def check(ok, label, detail=""):
    if ok:
        print("  ok  " + label)
    else:
        fails.append("%s\n    %s" % (label, detail))
        print("  XX  " + label)


tmp = tempfile.mkdtemp(prefix="subs_io_test_")
srt = os.path.join(tmp, "a.srt")
io.open(srt, "w", encoding="utf-8-sig", newline="\n").write(
    "1\n00:00:01,000 --> 00:00:02,500\n这是第一句。\n\n"
    "2\n00:00:02,500 --> 00:00:04,000\n第二句没有句末标点\n\n")


def run(cfg, tag):
    cp = os.path.join(tmp, "cfg_%s.json" % tag)
    io.open(cp, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False))
    # -P：不把脚本目录塞进 sys.path（等价于嵌入式解释器的 isolated 行为）
    # -X utf8：与 worker.py 启动子进程的方式保持一致
    p = subprocess.run([sys.executable, "-P", "-X", "utf8", SCRIPT, cp],
                       cwd=SRC, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p


print("== 1 dump（from txt_out + import hotwords）==")
out_lua = os.path.join(tmp, "subs.lua")
p = run({"mode": "dump", "srt": srt, "out": out_lua}, "dump")
check(p.returncode == 0 and os.path.isfile(out_lua),
      "dump 模式下同目录导入正常", (p.stdout or "") + (p.stderr or ""))

print("== 2 totxt（走 txt_out.write_txt）==")
in_json = os.path.join(tmp, "subs_in.json")
io.open(in_json, "w", encoding="utf-8").write(json.dumps(
    {"blocks": [{"s": 1.0, "e": 2.5, "t": "甲。"},
                {"s": 2.5, "e": 4.0, "t": "乙"}]}, ensure_ascii=False))
out_txt = os.path.join(tmp, "out.txt")
p = run({"mode": "totxt", "in": in_json, "txt": out_txt}, "totxt")
check(p.returncode == 0 and os.path.isfile(out_txt),
      "totxt 模式下 write_txt 可用", (p.stdout or "") + (p.stderr or ""))

print("== 3 save（回写 SRT）==")
p = run({"mode": "save", "srt": srt, "in": in_json}, "save")
check(p.returncode == 0 and "00:00:01,000" in io.open(
    srt, encoding="utf-8-sig").read(), "save 模式可用", (p.stdout or "") + (p.stderr or ""))

print()
if fails:
    print("失败 %d 项：" % len(fails))
    for f in fails:
        print("  · " + f)
    sys.exit(1)
print("全部用例通过")
