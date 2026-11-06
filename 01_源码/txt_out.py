# -*- coding: utf-8 -*-
"""TXT 文稿输出 —— 全项目唯一实现。

★ v2.0.0 之前这里有两份几乎一样的代码：
    resolve_bridge.write_txt(blocks, path)   ← 参数是 (起, 止, 文本) 元组
    subs_io.write_txt(blocks, path)          ← 参数是 {"s","e","t"} 字典
  两份在「结尾残余句」那个分支上写法不同，其中 resolve_bridge 那份是错的：

      if lines and lines[-1][-1] in _SENT_END:
          lines.append(tail)
      else:
          lines[-1] = (lines[-1] + tail) if lines else tail   # ← 空列表时崩

  只要全文一个句末标点都没有（勾选「去除标点符号」就是这个效果），
  上面的正则一条都匹配不到，lines 为空 → 走 else → lines[-1] 抛
  IndexError: list assignment index out of range。
  实测日志（v1.28）：
      文稿写出失败（不影响字幕）：IndexError: list assignment index out of range

  现在两份合成这一份，入参自动归一化，空列表分支写死成 append。
"""

import os
import re
import unicodedata

# 句末标点：切句用。分号/逗号不算句末，避免把一句话切碎。
_SENT_END = "。！？!?…；;，,、"

# 收尾符号：句末标点之后常跟的引号/括号，切句时要连带吃掉，
# 否则 ” 会被甩到下一行开头。
_CLOSERS = "”’」』）】》〉］｝}’"

_SENT_RE = re.compile(r"[^。！？!?…]*[。！？!?…][" + _CLOSERS + r"]*")


def _text_of(b):
    """把一条字幕块取出纯文本。兼容元组 (起, 止, 文本) 与字典 {"t": ...}。"""
    if isinstance(b, dict):
        return str(b.get("t") or "")
    if isinstance(b, (tuple, list)) and len(b) >= 3:
        return str(b[2] or "")
    return str(b or "")


def _dw(s):
    """显示宽度：全角算 2 列，半角算 1 列（与 asr_engine._dw 同一标准）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
               for ch in s)


def _group_by_width(blocks, target=60):
    """全文没有句末标点时，按字幕块边界重新分组成可读的行。

    ★ 为什么需要：勾选「去除标点符号」后，识别结果里一个句末标点都没有，
      按标点切句就只剩「一整篇一行」——84 分钟的稿子变成一行十几万字，
      根本没法看。字幕块本身已经是被断句/断行切好的语义片段，所以退而
      求其次：按块累加，宽度到 target 列就换行。虽然没有标点好看，
      但至少是一段一段的。
    """
    out, buf, w = [], "", 0
    for b in blocks:
        t = _text_of(b).replace("\n", "").replace("\r", "").strip()
        if not t:
            continue
        tw = _dw(t)
        if buf and w + tw > target:
            out.append(buf)
            buf, w = t, tw
        else:
            buf += t
            w += tw
    if buf:
        out.append(buf)
    return out


def build_lines(blocks):
    """把字幕块拼成「一句一行」的文稿行列表（不落盘，便于单测）。"""
    joined = "".join(_text_of(b) for b in blocks)
    joined = joined.replace("\n", "").replace("\r", "")
    if not joined.strip():
        return []

    # 全文一个句末标点都没有（= 勾了「去除标点符号」）→ 按块宽度分组，
    # 不做标点切句，否则整篇会挤成一行。
    if not any(ch in _SENT_END for ch in joined):
        return _group_by_width(blocks) or [joined.strip()]

    lines = []
    pos = 0
    for m in _SENT_RE.finditer(joined):
        s = m.group().strip()
        pos = m.end()
        if s:
            lines.append(s)

    tail = joined[pos:].strip()
    if tail:
        # ★ 这里就是 v1.28 崩溃的地方：lines 可能是空的。
        #   空 → 直接把残余当第一句（append），绝不能写 lines[-1]。
        if not lines:
            lines.append(tail)
        elif lines[-1][-1] in _SENT_END:
            lines.append(tail)
        else:
            lines[-1] = lines[-1] + tail

    # 句末没标点就补一个（勾了「去除标点符号」时必然出现）
    fixed = []
    for s in lines:
        if s and s[-1] not in _SENT_END and s[-1] not in _CLOSERS:
            s += "。"
        fixed.append(s)

    # 兜底：极端情况下（全是标点/不可见字符）fixed 仍可能为空
    if not fixed and joined.strip():
        fixed = [joined.strip()]
    return fixed


def write_txt(blocks, path):
    """写出文稿，返回行数。原子写：先 .tmp 再 replace，读方看不到半截文件。"""
    lines = build_lines(blocks)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return len(lines)
