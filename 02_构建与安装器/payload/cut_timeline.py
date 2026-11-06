# -*- coding: utf-8 -*-
"""
AsrSubs 粗剪任务入口（v4.0.0）
================================
由 AsrSubs.lua 调用：

  python cut_timeline.py <config.json>

config.json（UTF-8）：
  { "mode": "cut",
    "srt":   "C:/.../xxx.srt",          # 当前字幕文件（原地重挂用，时间码不变）
    "cuts":  [[12.34, 15.90], ...],     # 要切掉的区间（秒，时间线时间）
    "pad":   0.10,                      # 切口前后各多切一点（秒）
    "work":  "C:/AsrSubs/engine/rt",
    "engine": "inplace",                # v4.0.0 固定为原地增量剪辑
    "out_lua": "C:/AsrSubs/engine/rt/cut.lua" }

产出：out_lua（return {...}），Lua 侧轮询读取。

★ v4.0.0 变更（用户拍板「字幕无间隙功能删除」）：
  · 粗剪引擎【固定为原地增量剪辑】（cut_inplace.do_cut_inplace）。
  · 删除「新建 X · 粗剪 时间线逐段重排」引擎 do_cut 与「切回原时间线」do_restore
    （两者仅服务于已移除的「字幕无间隙 / 闭合空隙」场景，属死代码）。
  · 本文件保留 cut_inplace.py 复用的纯工具：merge / parse_srt / duration_of，
    以及结果写出 write_res；main 固定分发到 cut_inplace。

★ 两条设计红线（原地引擎踩过的坑，见 cut_inplace.py）：
  1. AppendClipInfo 的 endFrame 是【排他】语义：传 100..200 得到正好 100 帧。
  2. 必须先算完 / 校验完再动手改时间线；整次改动包进 BeginUndo/EndUndo 一个撤销步。
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ★ 控制台编码兜底：worker 用管道拉起，stdout 在中文 Windows 上是 GBK。
#   一句 print 里出现 GBK 编码不了的字符（比如 ⚠）会抛 UnicodeEncodeError，
#   把整次切除打断在尴尬位置。保留原编码，只让不可编码字符降级成 ?，绝不抛错。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

import land_subtitle as L          # 复用已经验证过的连接/落轨原语

# ★ 本文件既可能被 import（cut_inplace 要用这里的 merge/parse_srt/duration_of），
#   也可能被直接当脚本跑（__name__ == "__main__"）。后一种情况下 sys.modules
#   里没有 "cut_timeline" 这个名字，cut_inplace 再 import 一次会把整个模块重复
#   加载一遍（两份状态、白耗时间）。先把当前模块登记进去，保证两边同一份。
sys.modules.setdefault("cut_timeline", sys.modules[__name__])


# ── SRT 读取（cut_inplace 复用）─────────────────────────────────────
def _sec(x):
    x = x.strip().replace(",", ".")
    p = x.split(":")
    try:
        if len(p) == 3:
            return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
        if len(p) == 2:
            return int(p[0]) * 60 + float(p[1])
        return float(p[0])
    except Exception:
        return 0.0


def parse_srt(path):
    """容错解析 SRT（BOM / CRLF / 缺序号都吃）。"""
    with open(path, encoding="utf-8-sig") as f:
        txt = f.read()
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    rows = []
    for blk in txt.split("\n\n"):
        lines = [x for x in blk.split("\n") if x.strip() != ""]
        if len(lines) < 2:
            continue
        i = 1 if lines[0].strip().isdigit() else 0
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        a, b = lines[i].split("-->")[:2]
        rows.append({"s": _sec(a), "e": _sec(b), "t": "\n".join(lines[i + 1:])})
    return rows


# ── 区间运算（cut_inplace 复用：全部用整数帧，避免逐段 round 累积漂移）──
def merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            if b > out[-1][1]:
                out[-1][1] = b
        else:
            out.append([a, b])
    return out


def duration_of(tl, fps):
    """时间线当前时长（秒）。取不到就返回 0，绝不让它把主流程带崩。"""
    try:
        return (int(tl.GetEndFrame() or 0) - int(tl.GetStartFrame() or 0)) / fps
    except Exception:
        return 0.0


# ── 结果文件 ───────────────────────────────────────────────────────
def lua_str(s):
    s = "" if s is None else str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    return '"%s"' % s


def write_res(path, d):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write("return {\n")
        f.write("  ok = %s,\n" % ("true" if d.get("ok") else "false"))
        for k in ("mode", "engine", "timeline", "src_timeline", "backup",
                  "srt", "err", "diag"):
            f.write("  %s = %s,\n" % (k, lua_str(d.get(k))))
        for k in ("cut_segs", "clips", "subs", "frames", "srt_rows", "lost"):
            f.write("  %s = %d,\n" % (k, int(d.get(k) or 0)))
        for k in ("removed", "duration", "fps"):
            f.write("  %s = %.3f,\n" % (k, float(d.get(k) or 0)))
        f.write("}\n")


# ── 主流程（v4.0.0：固定分发到原地增量剪辑）────────────────────────
def main():
    res = {"ok": False, "mode": "cut", "engine": "inplace",
           "clips": 0, "subs": 0, "err": ""}
    out_lua = ""
    try:
        if len(sys.argv) < 2:
            raise RuntimeError("缺少参数文件 config.json")
        with open(sys.argv[1], encoding="utf-8") as f:
            cfg = json.load(f)
        out_lua = str(cfg.get("out_lua") or "")
        mode = str(cfg.get("mode") or "cut").lower()
        # ★ v4.0.0：只接受 cut。restore / rebuild 引擎已随「字幕无间隙」一并移除；
        #   若残留旧配置传进来，明确拒绝而不是误跑原地剪切，避免误删时间线。
        if mode == "restore":
            raise RuntimeError("本版本已移除「切回原时间线」功能，请在时间线面板按 Ctrl+Z 撤销")
        from cut_inplace import do_cut_inplace
        do_cut_inplace(cfg, res)
        res["ok"] = bool(res.get("ok"))
    except Exception as e:
        res["ok"] = False
        res["err"] = str(e)
        traceback.print_exc()
    write_res(out_lua, res)
    print(json.dumps({k: v for k, v in res.items() if k != "diag"}, ensure_ascii=False))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
