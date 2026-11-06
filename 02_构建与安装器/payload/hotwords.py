# -*- coding: utf-8 -*-
"""全文高频热词统计（v2.0.8）

作用：字幕载入/识别完成后，统计全文出现最多的实词，写到 rt\\hotwords.lua，
      由界面底部那一行展示。目的是让人一眼看出「哪些词在这片子里反复出现」
      —— 名字、专名、主题词写错的时候，它会明显扎眼，可以直接复制进
      上面的「权重词纠错」表。

为什么用【比例】而不是【绝对次数】做阈值：
    100 条短片的 5 次和 2000 条长片的 5 次完全不是一个概念，
    绝对阈值换个素材就废。所以阈值 = max(下限, 总词数 × 0.2%)。
    实测参考（2309 条 / 10437 词）：0.2% ≈ 21 次，
    命中 我们429 / 长辈123 / 可以123 / 你们92 / 一个79 … / 人生40 / 人参39，
    数量刚好，既不会糊满一行，也不会把「人生」这种真正关心的词砍掉。
    0.5% 就只剩 8 个，人生(40)、人参(39) 全被砍 —— 太严。

为什么过滤词性：
    「我们 / 那么 / 一个 / 一下」次数再高也没有参考价值。jieba 的 posseg
    能标词性（实测：我们|r、长辈|n、人生哲学|nt、宣传|vn），
    只留名词 / 动词 / 形容词 / 成语 / 简称，列表里剩下的就是人名和专名。
"""

import math
import os

# 阈值：总词数的 0.2%，下限 3 次（给短视频兜底），最多显示 15 个
THR_RATIO = 0.002
THR_MIN = 3
TOP_N = 15

# 词性白名单。jieba 的 flag 形如 n / nr / ns / nt / nz / v / vn / a / ad / i / l / j
#   n*  名词（含人名 nr、地名 ns、机构名 nt、专名 nz）
#   v*  动词      a*  形容词      i 成语      l 习用语      j 简称
_KEEP_EXACT = {"v", "vn", "vd", "a", "ad", "an", "i", "l", "j"}

# 口语高频虚词/填充词：词性上是实词，但作为「热词」毫无信息量。
# 只收那些在口语字幕里几乎必然冲进前列的，不要乱加 —— 过滤过头
# 会把「人参」这种真正要看的词也误杀。
STOP = set("""
我们 你们 他们 她们 咱们 大家 自己 人家 别人 各位
这个 那个 这些 那些 这样 那样 这里 那里 这么 那么 怎么 什么 为什么 怎么样
一个 一下 一点 一些 一直 一起 一样 一定 一般 一边 一次 一天
可以 就是 然后 因为 所以 但是 不过 而且 如果 虽然 还是 或者 只是 已经 还有
没有 不是 不会 不能 不要 不用 不行 不对 知道 觉得 认为 应该 需要 可能
现在 时候 真的 其实 非常 特别 比较 有点 有些 东西 事情 问题 地方
感觉 看到 看着 听到 起来 出来 下来 上来 过来 过去 回来 回去
今天 明天 昨天 以后 之前 之后 上次 下次 好不好 有没有 是不是 对不对
直接 反正 居然 竟然 千万 千万
""".split())


def _is_cjk(ch):
    return "\u4e00" <= ch <= "\u9fff"


_JIEBA_OK = None        # 三态：None=还没试过 / True=能用 / False=没装


def _jieba_available():
    global _JIEBA_OK
    if _JIEBA_OK is None:
        try:
            import jieba  # noqa: F401
            _JIEBA_OK = True
        except Exception:
            _JIEBA_OK = False
    return _JIEBA_OK


def _segment(text):
    """分词 → [(词, 词性)]。

    ★ v4.8.24：jieba 缺失时【不再】用"连续汉字取二字组合"凑数。
      旧降级版实测产出的是「位朋」「里最」这类伪词 —— 5 个热词里 2 个
      看不懂，比不显示更糟。现在直接返回空，由 write_lua 写一个
      no_jieba 标记，界面显示"热词需要 jieba"。
    """
    if not _jieba_available():
        return []
    try:
        import jieba.posseg as pseg
        return [(w, f) for w, f in pseg.cut(text)]
    except Exception:
        return []


def _block_text(b):
    """兼容字典 {"t":..} 与元组 (起, 止, 文本) 两种字幕块格式。"""
    if isinstance(b, dict):
        return str(b.get("t") or "")
    try:
        return str(b[2] or "")
    except (IndexError, TypeError):
        return ""


def count_blocks(blocks):
    """统计实词词频。返回 (Counter, 总词数)。

    总词数是【分母】：所有能算作「词」的 token 都计入（含虚词、单字），
    但标点和空白不计。分母用过滤后的词数会让阈值小得离谱。

    ★ 全文拼成一段再切，不要一块一块地切：jieba 每次 cut 都有固定开销，
      2309 条逐条切要 1.27 秒，拼起来切一次只要 0.3 秒左右。
      块之间用换行连接，非汉字字符本来就是 jieba 的天然断点，
      不会出现「跨两条字幕拼出一个词」。
    """
    from collections import Counter

    texts = [_block_text(b) for b in blocks]
    text = "\n".join(t for t in texts if t)
    if not text.strip():
        return Counter(), 0

    cnt = Counter()
    total = 0
    for w, f in _segment(text):
        w = w.strip()
        if not w:
            continue
        # 标点（x）和纯符号不计入分母
        if f == "x" or not any(_is_cjk(c) or c.isalnum() for c in w):
            continue
        total += 1
        if len(w) < 2:                    # 单字噪音太大，不要
            continue
        if not (f.startswith("n") or f in _KEEP_EXACT):
            continue
        if w in STOP:
            continue
        cnt[w] += 1
    return cnt, total


def top_words(cnt, total, ratio=THR_RATIO, min_count=THR_MIN, top_n=TOP_N):
    """按阈值筛 + 次数降序（同次数按字典序，保证每次结果稳定）。"""
    thr = max(min_count, int(math.ceil(total * ratio)))
    items = [(w, c) for w, c in cnt.items() if c >= thr]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return items[:top_n], thr


def _quote(s):
    out = []
    for ch in str(s):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def write_lua(blocks, path, ratio=THR_RATIO, min_count=THR_MIN, top_n=TOP_N):
    """统计并写 rt\\hotwords.lua。返回 (总词数, 阈值, 条数)。"""
    cnt, total = count_blocks(blocks)
    items, thr = top_words(cnt, total, ratio, min_count, top_n)

    parts = ["return {total = %d, thr = %d, n = %d, no_jieba = %s, words = {\n"
             % (total, thr, len(items),
                "true" if not _jieba_available() else "false")]
    for w, c in items:
        parts.append("  {%s, %d},\n" % (_quote(w), c))
    parts.append("}}\n")

    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(parts))
    os.replace(tmp, path)
    return total, thr, len(items)
