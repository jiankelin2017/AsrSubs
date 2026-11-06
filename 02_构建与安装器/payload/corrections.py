# -*- coding: utf-8 -*-
"""权重词纠错：按用户给的表，把 ASR 的同音/近音错误强行改掉。

★ 为什么要有这个模块（v2.0.7）
  原来的「短语列表 / 提示」是把文字当 prompt 喂给模型。用户实测两个问题：
    ① 模型会把 prompt 当正文吐出来 —— 提示词直接出现在字幕里；
    ② 位置还不对（prompt 每一窗都拼一次，落在哪一窗全看运气）。
  所以提示词整项移除，改成【事后确定性替换】：识别完、写 SRT 之前，
  按表替换一遍。改出来的字幕一定对，不依赖模型合不合作。

格式（一行一条）：
    人参=人生,人叁
    重庆=崇庆
  等号左边 = 正确写法；右边 = 会被替换掉的错误写法。
  右边可用 , ， 、 ; ； 或空格分隔多个。
  空行、# 开头、// 开头的行忽略；没有等号或某侧为空的行会跳过并上报行号。

★ 三个必须做对的细节（都是这类功能最容易翻车的地方）
  1. 长词优先：同一位置既有「人生」又有「人生哲学」能匹配时，取长的。
     否则 人生哲学 会被咬成 人参哲学。
  2. 不允许链式替换：A→B 之后 B 又被下一条规则改成 C，最后出来的词谁都想不到。
     做法是【单次扫描、逐位置取最长匹配】，扫一遍就出结果，没有"替换后的文本
     再被替换"这回事。
  3. 保护已经正确的词：若某条规则的正确词本身包含另一条的错误词
     （例如 正确=人生哲学、错误=人生），逐位置取最长匹配天然就能保住
     「人生哲学」不被改成「人生哲学哲学」——因为 4 个字比 2 个字长。
     但「既是正确词又是错误写法」的词（表本身自相矛盾）不保护，按错误写法处理，
     并把矛盾报给用户 —— 静悄悄挑一边是最难排查的那种行为。
"""

_SEPS = ",，、;；"


def _split_wrongs(s):
    """把右边的多个错误写法拆开（逗号/顿号/分号/空白都算分隔符）。"""
    out = []
    cur = []
    for ch in s:
        if ch in _SEPS or ch.isspace():
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def parse_rules(text):
    """解析用户写的纠错表 → (rules, problems)

    rules    = [(正确词, [错误写法, ...]), ...]
    problems = ["第 3 行：没有等号，已跳过", ...]（调用方写进日志给用户看）
    """
    rules = []
    problems = []
    raw_lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for i, raw in enumerate(raw_lines, 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # 全角等号也认：中文输入法下很容易打成 ＝
        line = line.replace("＝", "=")
        if "=" not in line:
            problems.append("第 %d 行：没有等号，已跳过" % i)
            continue
        left, right = line.split("=", 1)
        correct = left.strip()
        wrongs = [w for w in _split_wrongs(right) if w and w != correct]
        if not correct:
            problems.append("第 %d 行：等号左边为空，已跳过" % i)
            continue
        if not wrongs:
            problems.append("第 %d 行：等号右边没有有效词，已跳过" % i)
            continue
        rules.append((correct, wrongs))

    # 自相矛盾的词：既在某条左边（正确），又在另一条右边（错误）
    as_correct = set(c for c, _ in rules)
    as_wrong = set(w for _, ws in rules for w in ws)
    for w in sorted(as_correct & as_wrong):
        problems.append("「%s」既是正确词又是错误写法，按错误写法处理（会把它改成对应正确词）" % w)
    return rules, problems


def _flatten(rules):
    """→ (需要保护的正确词列表, [(错误词, 正确词), ...])，两者都按长度降序。"""
    corrects, pairs = [], []
    seen_c, seen_w = set(), set()
    for correct, wrongs in rules:
        if correct not in seen_c:
            seen_c.add(correct)
            corrects.append(correct)
        for w in wrongs:
            if w == correct or w in seen_w:
                continue
            seen_w.add(w)
            pairs.append((w, correct))
    # ★ 矛盾词不保护：它作为「错误写法」的优先级更高（见 parse_rules 的提示）
    corrects = [c for c in corrects if c not in seen_w]
    corrects.sort(key=len, reverse=True)
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return corrects, pairs


def _index_by_first(words):
    """按首字建索引，避免每个位置都把所有词试一遍（长字幕上能差一个数量级）。"""
    idx = {}
    for item in words:
        w = item[0]
        if not w:
            continue
        idx.setdefault(w[0], []).append(item)
    for k in idx:
        # 同一个首字下，长的排前面 —— 这样"找到第一个"就是最长匹配
        idx[k].sort(key=lambda x: len(x[0]), reverse=True)
    return idx


def apply_text(text, rules):
    """对一段文本做纠错 → (新文本, 命中次数)。rules 可为空。

    ★ 单次扫描 + 每位置取最长匹配，不是"逐条 replace"。
      逐条 replace 会遇到两个坑（都实测过）：
        · 先替换 人生→人参，会让 人生哲学 被咬成 人参哲学（长词没了）；
        · A→B 之后 B 又被下一条改成 C（链式污染）。
      逐位置取最长匹配两个坑都没有，而且天然只扫一遍。
    """
    if not text or not rules:
        return text, 0
    s = str(text)
    corrects, pairs = _flatten(rules)
    if not pairs:
        return s, 0

    # corrects 存成 (词, 词)，和 pairs 的 (错误词, 正确词) 形状一致，方便同一套匹配逻辑
    c_idx = _index_by_first([(c, c) for c in corrects])
    w_idx = _index_by_first(pairs)

    out = []
    hits = 0
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        # ① 这个位置能匹配到的最长「正确词」
        best_c, c_len = None, 0
        for word, _ in c_idx.get(ch, ()):
            if s.startswith(word, i):
                best_c, c_len = word, len(word)
                break
        # ② 这个位置能匹配到的最长「错误词」
        best_w, w_correct, w_len = None, None, 0
        for word, corr in w_idx.get(ch, ()):
            if s.startswith(word, i):
                best_w, w_correct, w_len = word, corr, len(word)
                break
        # ③ 谁长听谁的；一样长时，已经正确的优先保留（少改一次是一次）
        if best_c and c_len >= w_len:
            out.append(best_c)
            i += c_len
        elif best_w:
            out.append(w_correct)
            hits += 1
            i += w_len
        else:
            out.append(ch)
            i += 1
    return "".join(out), hits


def apply_to_blocks(blocks, rules):
    """对字幕块列表做纠错（原地改）→ 总命中次数。

    兼容两种块格式：元组 (起, 止, 文本) 与字典 {"s","e","t"} / {"start","end","text"}。
    """
    if not blocks or not rules:
        return 0
    total = 0
    for idx, b in enumerate(blocks):
        if isinstance(b, dict):
            key = "t" if "t" in b else ("text" if "text" in b else None)
            if not key:
                continue
            new, n = apply_text(b.get(key), rules)
            if n:
                b[key] = new
                total += n
        else:
            if len(b) < 3:
                continue
            new, n = apply_text(b[2], rules)
            if n:
                blocks[idx] = (b[0], b[1], new)
                total += n
    return total


def describe(rules):
    """给日志用的一句话摘要。"""
    if not rules:
        return "未配置"
    n_wrong = sum(len(w) for _, w in rules)
    return "%d 条规则 / %d 个错误写法" % (len(rules), n_wrong)
