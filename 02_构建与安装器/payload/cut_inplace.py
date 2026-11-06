# -*- coding: utf-8 -*-
"""
原地增量粗剪（AsrSubs v4.0.0）
================================
v4.0.0 起这是【唯一的粗剪引擎】（「字幕无间隙」功能删除后，closed-gap 场景不再存在，
新建「X · 粗剪」时间线逐段重排的旧引擎 cut_timeline.do_cut/do_restore 已移除）。

  · 本模块在【你正在编辑的这条时间线上】直接动手 —— 只动被删区间覆盖的
    那几个片段，其余片段、转场、调色、Fusion 特效、音频全部原封不动。
  · 靠 Resolve 21.1.2 新增的片段编辑 API：
        TimelineItem.SetLeftOffset / SetRightOffset   （相当于剃刀，裁两端）
        Timeline.DeleteClips(items, ripple=False)     （整段删掉）
        TimelineItem.CopyAttributes / CopyGrades      （新片段继承原片段属性）
  · 全程包在 BeginUndo/EndUndo 里，Ctrl+Z 一次退回。

★ 保留空隙（默认行为，不再有「字幕无间隙」开关）：切口前后留气口，
  字幕按原时间码重挂、时间码不变。

★ 三种处理方式（按区间端点与片段的关系）
  1. 片段整段落在区间内          → DeleteClips 删掉
  2. 区间只切到片段的左端/右端   → SetLeftOffset / SetRightOffset 裁掉
  3. 区间把片段切成两半（最常见）→ 裁掉左半，再把右半段作为新片段追加回来，
                                   并从原片段复制属性与调色
"""
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

import land_subtitle as L
import cut_timeline as C

PAD_DEFAULT = 0.10


def _subtitle_offset_frames(tl, cuts_sec, fps, tl_start, tl_end):
    """★ v4.0.5 R1：用时间线【真实字幕轨】位置校准切除区间。

    背景（bug A 类根因）：切除区间原本直接用 Lua 内存里字幕的时间码
    （SRT 内部秒）× 帧率得到时间线帧。但当素材不在时间线第 0 帧起（有偏移）
    时，字幕落轨时已被整体平移，时间线真实位置 = SRT 秒 + 偏移，于是切除区间
    与视频错位（切错 / 切空）。本函数读时间线字幕轨条目的真实起止帧，对每段
    切除区间，在字幕轨里找【时长一致】的那条（即被删的那条字幕，因为 cut 区间
    本就是被删字幕的时间区间），用「真实起点 − 内部秒×帧率」反推偏移，
    多段投票取众数，得到「SRT 秒 → 时间线帧」的偏移，上层据此把切除区间整体
    校准到真实位置。

    用「时长一致」而非「位置最近」做关联：当偏移很大时，cut 的内部秒位置离
    任何真实字幕都很远，"最近"会选错条目；而时长一致能稳定锁定被删那条。
    无字幕轨 / 无法可靠确定偏移时返回 0（上层保持原行为，安全回退）。

    ★ 真机注意（v4.0.6 修正）：真实字幕 TimelineItem 的 GetStartFrame()/
      GetEndFrame() 返回 None；必须用 GetStart()/GetEnd() 才能读到正确帧
      （land_inplace 对齐校验用的也是 GetStart）。本函数据此读取。
    """
    # 收集时间线字幕轨真实条目（起止帧）
    items = []
    try:
        n = tl.GetTrackCount("subtitle") or 0
    except Exception:
        n = 0
    for i in range(1, n + 1):
        try:
            its = tl.GetItemListInTrack("subtitle", i) or []
        except Exception:
            its = []
        for it in its:
            try:
                sf = float(it.GetStart())
                ef = float(it.GetEnd())
            except Exception:
                continue
            if ef > sf:
                items.append((sf, ef))
    if not items or not cuts_sec:
        return 0
    from collections import Counter
    votes = Counter()
    for s, e in cuts_sec:
        if not (e > s):
            continue
        cut_span = (e - s) * fps
        s_f = s * fps
        # 在时长一致的字幕里挑【最贴近】的那条（即被删的那条）投一票；
        # 每段 cut 只投一票，避免不同字幕都过筛产生相互竞争的偏移票。
        tol = max(2.0, 0.05 * cut_span)
        best_off = None
        best_d = None
        for sf, ef in items:
            d = abs((ef - sf) - cut_span)
            if d > tol:
                continue
            if best_d is None or d < best_d:
                best_d = d
                best_off = int(round(sf - s_f))
        if best_off is not None:
            votes[best_off] += 1
    if not votes:
        return 0
    top2 = votes.most_common(2)
    off, cnt = top2[0]
    cnt2 = top2[1][1] if len(top2) > 1 else 0
    # 众数未严格胜出（平票 / 噪声）→ 不可靠，回退 0
    if cnt <= cnt2:
        return 0
    return off


# ══════════════════════════════════════════════════════════════
# 一、采集：拿到带 TimelineItem 对象的片段清单
# ══════════════════════════════════════════════════════════════
def gather(tl):
    """收集视频/音频片段，保留 item 对象引用（时间戳 + 源帧 + 媒体项引用）。"""
    out = []
    for kind, mt in (("video", 1), ("audio", 2)):
        try:
            n = tl.GetTrackCount(kind) or 0
        except Exception:
            n = 0
        for ti in range(1, n + 1):
            try:
                items = tl.GetItemListInTrack(kind, ti) or []
            except Exception:
                items = []
            for it in items:
                try:
                    st = int(round(float(it.GetStart() or 0)))
                    en = int(round(float(it.GetEnd() or 0)))
                except Exception:
                    continue
                if en <= st:
                    continue
                try:
                    ss = int(it.GetSourceStartFrame())
                except Exception:
                    ss = 0
                try:
                    se = int(it.GetSourceEndFrame())
                except Exception:
                    se = 0
                try:
                    mi = it.GetMediaPoolItem()
                except Exception:
                    mi = None
                out.append({
                    "it": it, "mi": mi, "kind": kind, "mt": mt, "track": ti,
                    "start": st, "end": en, "ss": ss, "se": se,
                    "speed": L._clip_speed(it),
                })
    return out


# ══════════════════════════════════════════════════════════════
# 二、规划：区间 × 片段 → 动作清单（纯函数，可离线单测）
# ══════════════════════════════════════════════════════════════
def plan_inplace(items, eff):
    """按区间生成动作清单。

    ★ 区间【倒序】处理：DeleteClips 不 ripple，删后面不影响前面的帧号；
      倒序还能保证 split 追加的右半段不会被后续区间重复处理。

    动作元组：
      ("del",   item)                 整段删除
      ("trimr", item, target)         右端裁到 target
      ("triml", item, target)         左端裁到 target
      ("split", item, a, b)           裁掉左半到 a，右半 [b, end) 追加回来
    """
    dead = set()
    acts = []
    for a, b in sorted(eff, reverse=True):
        for c in items:
            key = (c["track"], c["start"], c["end"])
            if key in dead:
                continue
            if c["end"] <= a or c["start"] >= b:
                continue
            left_out = c["start"] < a
            right_out = c["end"] > b
            if left_out and right_out:
                acts.append(("split", c, a, b))
                dead.add(key)
            elif left_out:
                acts.append(("trimr", c, a))
            elif right_out:
                acts.append(("triml", c, b))
            else:
                acts.append(("del", c))
                dead.add(key)
    return acts


# ══════════════════════════════════════════════════════════════
# 三、执行
# ══════════════════════════════════════════════════════════════
def _edge(it, side):
    try:
        v = float(it.GetStart() if side == "left" else it.GetEnd())
        return int(round(v))
    except Exception:
        return None


def move_edge(it, side, target, tol=1):
    """把片段 side 端移到时间线帧 target。

    ★ Set*Offset 的语义（官方 pyi）：「把这一端设为"还有 offset 帧可用"」。
      裁掉 cut 帧 = 那一端的可用余量增加 cut 帧，所以统一是
          Set*(Get*() + cut)
      这个式子在「余量」和「裁量」两种读法下都成立（都是增量），
      真机验证见 tools/probe_item_edit.py。
    ★ 每次改完立刻读回核对：不到位就返回 False，由上层整体中止并提示 Ctrl+Z，
      绝不带着"裁错位置"的时间线往下走。
    """
    cur = _edge(it, side)
    if cur is None:
        return False, "读不到%s端点" % ("左" if side == "left" else "右")
    cut = (target - cur) if side == "left" else (cur - target)
    if cut == 0:
        return True, "已到位"
    if cut < 0:
        # 要往外扩（不是裁），原地粗剪不该出现，交给上层
        return False, "需要向外扩展 %d 帧（超出本引擎范围）" % (-cut)
    getter = it.GetLeftOffset if side == "left" else it.GetRightOffset
    setter = it.SetLeftOffset if side == "left" else it.SetRightOffset
    try:
        off = float(getter() or 0)
    except Exception:
        off = 0.0
    try:
        ok = bool(setter(off + cut))
    except Exception as e:
        return False, "Set%sOffset 抛错：%s" % ("Left" if side == "left" else "Right", e)
    got = _edge(it, side)
    if got is None:
        return False, "改完读不回端点"
    if abs(got - target) > tol:
        return False, ("写入未到位：目标 %d 实际 %d（Set 返回 %s，原余量 %g，裁 %d）"
                       % (target, got, ok, off, cut))
    return True, "OK"


def _track_items(tl, kind, idx):
    try:
        return tl.GetItemListInTrack(kind, idx) or []
    except Exception:
        return []


def snap_item(it):
    """从一个 TimelineItem 取重建所需的快照（含轨道信息）。

    ★ 必须在【裁剪之前】调用：裁完之后 GetEnd() 已经是新值，
      拿它算"右半段从哪开始"就会算错。
    """
    try:
        ti = it.GetTrackTypeAndIndex()
        kind, track = str(ti[0]), int(ti[1])
    except Exception:
        return None
    try:
        st = int(round(float(it.GetStart() or 0)))
        en = int(round(float(it.GetEnd() or 0)))
        ss = int(it.GetSourceStartFrame())
    except Exception:
        return None
    if en <= st:
        return None
    return {"it": it, "mi": it.GetMediaPoolItem(), "kind": kind,
            "mt": 2 if kind == "audio" else 1, "track": track,
            "start": st, "end": en, "ss": ss, "speed": L._clip_speed(it)}


def append_half(mp, tl, g, src_from, target_start):
    """把片段 g 的时间线区间 [src_from, g.end) 作为新条目追加到 target_start。

    g 必须是 snap_item() 的快照（裁剪前的原始值）。
    """
    sp = float(g.get("speed") or 1.0)
    span = g["end"] - src_from          # 时间线帧跨度
    if span <= 0:
        return None
    if abs(sp - 1.0) > 1e-6:
        ss = g["ss"] + int(round((src_from - g["start"]) * sp))
    else:
        ss = g["ss"] + (src_from - g["start"])
    rec = {
        "mediaPoolItem": g["mi"],
        "mediaType": g["mt"],
        "startFrame": int(ss),
        "endFrame": int(ss + span),     # ★ 排他（AppendClipInfo 的坑，见 cut_timeline 顶部）
        "recordFrame": int(target_start),
        "trackIndex": int(g["track"]),
    }
    before = _track_items(tl, g["kind"], g["track"])
    try:
        mp.AppendToTimeline([rec])
    except Exception as e:
        print("[inplace] 追加右半段失败：%s" % e, flush=True)
        return None
    after = _track_items(tl, g["kind"], g["track"])
    if len(after) <= len(before):
        print("[inplace] 追加右半段没有落上（%s%d 轨上条目数未变）"
              % (g["kind"], g["track"]), flush=True)
        return None
    # 找出新增的那个：起点 == target_start 且不在 before 里
    for it in after:
        if any(it is b for b in before):
            continue
        try:
            if abs(int(round(float(it.GetStart() or 0))) - int(target_start)) <= 1:
                return it
        except Exception:
            pass
    for it in after:
        if not any(it is b for b in before):
            return it
    return None


def split_group(it):
    """拆分时要一起处理的整组片段：本片段 + 它链接的音频/视频。"""
    grp = [snap_item(it)]
    try:
        for lk in (it.GetLinkedItems() or []):
            g = snap_item(lk)
            if g:
                grp.append(g)
    except Exception:
        pass
    return [g for g in grp if g]


def copy_look(src, dst):
    """把原片段的调色 / 视频属性搬到新片段上（转场搬不过来，这点在确认框里说明）。"""
    done = []
    try:
        if src.CopyGrades([dst]):
            done.append("调色")
    except Exception:
        pass
    try:
        if src.CopyAttributes([dst], {"allVideoAttributes": True}):
            done.append("视频属性")
    except Exception:
        pass
    return done


def _srt_ranges(srt_path):
    """SRT 里每条字幕的 (起, 止) 秒。解析失败返回 []。"""
    try:
        rows = C.parse_srt(srt_path)
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            out.append((float(r["s"]), float(r["e"])))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _looks_like_our_sub(it, ranges, fps, tol=0.35):
    """这条时间线字幕的位置，能在本次 SRT 里找到对应的一条吗？

    ★ v1.0.0：档名判据失效时的兜底。SRT 被改名/换目录后 stem 匹配全落空，
      按名字一条都清不掉，重挂就叠字（真机实测 39 条 + 38 条 = 两条字幕轨）。

    ★★ 这里【不能】要求 `GetMediaPoolItem()` 非空 —— 实测字幕条目的这个方法
      返回 None（Resolve 21.1，字幕轨上的条目拿不到来源媒体项）。原先
      `_is_our_subtitle` 就是卡在这一条上：判据永远为假，注释里说的"来源
      媒体项匹配"实际上从来没生效过，靠的只是运气（有时候能拿到）。
      所以时间码兜底只认位置，把"是不是本插件的"交给上层按【整条轨的
      吻合比例】判断（见 clear_sub_track），不拿单条去赌。
    """
    try:
        st = float(it.GetStart()) / fps
        en = float(it.GetEnd()) / fps
    except (TypeError, ValueError, ZeroDivisionError):
        return False
    for a, b in ranges:
        if abs(a - st) <= tol and abs(b - en) <= tol:
            return True
    return False


def _is_our_subtitle(it, srt_path):
    """这条时间线字幕，来源是不是【本插件这次要重挂的 SRT】？

    ★ v4.0.4（用户拍板 D）：粗剪重挂字幕前只清「本插件落的字幕」，
      保留用户手工拖进媒体池/时间线的其它字幕轨，避免误删用户数据。
      落轨进程与粗剪进程是两个独立 Python 进程，内存不共享，只能靠
      【字幕条目的来源媒体项】跨进程识别。

    判据用去扩展名的 basename（stem），因为：
      · 适配字幕落轨时若素材有起始偏移，main() 会生成一份 land_srt
        （= 原始 basename + ".srt"，放在 work 目录），其 stem 与原始
        SRT 完全相同 → stem 匹配天然兼容「原版」和「偏移版」两种来源；
      · import_to_pool 导入时媒体项名就是 stem，GetName() 判据也一致。
    两条判据任一命中即认定为本插件字幕；完全取不到来源信息时保守返回
    False（宁可残留也不误删用户字幕）。
    """
    stem = os.path.splitext(os.path.basename(srt_path or ""))[0].lower()
    if not stem:
        return False
    try:
        mi = it.GetMediaPoolItem()
    except Exception:
        mi = None
    if not mi:
        return False
    # 判据①：媒体项名（import_to_pool 用 stem 命名）
    try:
        nm = (mi.GetName() or "").strip().lower()
    except Exception:
        nm = ""
    if nm and nm == stem:
        return True
    # 判据②：来源文件路径的 stem（兼容偏移版 land_srt 所在目录不同的情况）
    try:
        props = mi.GetClipProperty() or {}
    except Exception:
        props = {}
    if isinstance(props, dict):
        fp = str(props.get("File Path") or props.get("Original File Path") or "")
        if fp and os.path.splitext(os.path.basename(fp))[0].lower() == stem:
            return True
    return False


def clear_sub_track(tl, srt_path, fps=24.0):
    """清掉本插件落的字幕条目，返回 (删除条数, 靠时间码认领的条数)。

    ★ v4.0.4：以前整轨清空，会误删用户在时间线上手工加的字幕。
    ★ v1.0.0：原判据（来源媒体项名字 == SRT 文件名）在 Resolve 21.1 上不成立 ——
      字幕条目的 `GetMediaPoolItem()` 返回 None，于是"一条都清不掉"，重挂时
      `land_inplace` 看见轨上有东西就新开一条轨，结果 39 条 + 38 条两条轨叠着
      （真机实测）。现在改成两级判据：

        ① 名字对得上 → 直接清（老路径，能用就用）；
        ② 名字对不上 → 用【时间码吻合比例】判断这条轨是不是我们的：
             · ≥90% 的条目能在 SRT 里找到同位置 → 认定整轨是我们的，整轨清
               （这样"本次删掉的那一条"也会被清掉，不会留在轨上变成幽灵字幕）
             · 60%~90% → 只清对得上的那些，剩下的原样留着，并在日志里说明
             · <60% → 判断不了，跳过这条轨（宁可留旧字幕，也不误删用户内容）

    ★ 字幕清理发生在 EndUndo 之后（视频剪辑的撤销组已经关了），
      所以这里必须保守：宁可少删，不能乱删。
    """
    n, guessed = 0, 0
    ranges = None
    try:
        cnt = tl.GetTrackCount("subtitle") or 0
    except Exception:
        cnt = 0
    for i in range(1, cnt + 1):
        items = _track_items(tl, "subtitle", i)
        if not items:
            continue
        mine = [it for it in items if _is_our_subtitle(it, srt_path)]
        whole = False
        if not mine:
            if ranges is None:
                ranges = _srt_ranges(srt_path)
            if not ranges:
                continue
            hit = [it for it in items if _looks_like_our_sub(it, ranges, fps or 24.0)]
            if not hit:
                continue
            ratio = len(hit) / float(len(items))
            if ratio >= 0.9:
                mine, whole = items, True          # 整轨都是我们的（含本次删掉的那条）
            elif ratio >= 0.6:
                mine = hit
            else:
                print("[inplace] 字幕轨 %d 只有 %d/%d 条与 SRT 时间码吻合，"
                      "判断不了归属，跳过（不误删）"
                      % (i, len(hit), len(items)), flush=True)
                continue
            guessed += len(hit)
            print("[inplace] 档名对不上（SRT 改过名？），按时间码认出字幕轨 %d："
                  "%d/%d 条吻合%s" % (i, len(hit), len(items),
                                     "，整轨清理" if whole else "，只清吻合的"),
                  flush=True)
        # ★ 传副本、条数先取：DeleteClips 有可能顺手改动我们手里这个列表，
        #   直接 len(mine) 会数出 0（单测里就复现过）。
        cnt_this = len(mine)
        try:
            tl.DeleteClips(list(mine), False)
            n += cnt_this
        except Exception as e:
            print("[inplace] 清本插件字幕轨 %d 失败：%s" % (i, e), flush=True)
    return n, guessed


# ══════════════════════════════════════════════════════════════
# 四、主流程
# ══════════════════════════════════════════════════════════════
def do_cut_inplace(cfg, res):
    r = L.connect()
    if not r:
        raise RuntimeError("无法连接 DaVinci Resolve（Resolve 是否在运行？）")
    proj = r.GetProjectManager().GetCurrentProject()
    if not proj:
        raise RuntimeError("取不到当前工程")
    mp = proj.GetMediaPool()
    tl = proj.GetCurrentTimeline()
    if not tl:
        raise RuntimeError("当前没有打开的时间线")

    fps = float(tl.GetSetting("timelineFrameRate") or 24) or 24.0
    tl_start = int(tl.GetStartFrame() or 0)
    tl_end = int(tl.GetEndFrame() or 0)
    # ★ v4.8.21：切口长度 ≠ 时长缩短（保留空隙，不 ripple）。
    #   两个数分开记，界面上才不会再出现「省了 3 秒」而时间线一帧没少。
    dur_before_s = (tl_end - tl_start) / fps if fps else 0.0
    work = cfg.get("work") or os.path.dirname(cfg.get("out_lua") or "") or "."

    items = gather(tl)
    if not items:
        raise RuntimeError("当前时间线上没有视频/音频片段")

    pad = float(cfg.get("pad") if cfg.get("pad") is not None else PAD_DEFAULT)
    pad_f = int(round(pad * fps))

    raw = []
    for pair in (cfg.get("cuts") or []):
        try:
            s, e = float(pair[0]), float(pair[1])
        except Exception:
            continue
        a = max(tl_start, int(round(s * fps)) - pad_f)
        b = min(tl_end, int(round(e * fps)) + pad_f)
        if b > a:
            raw.append([a, b])
    raw = C.merge(raw)

    # ---- ★ v4.0.5 R1：用时间线真实字幕轨位置校准切除区间 ----
    #   根除「偏移场景下依赖 Lua 内存时间码导致切错 / 切空」的 bug A 类根因。
    cuts_pairs = []
    for p in (cfg.get("cuts") or []):
        try:
            cuts_pairs.append((float(p[0]), float(p[1])))
        except Exception:
            pass
    # ★ v1.0.1：先把素材覆盖算出来 —— R1 要用它判断"这次校准到底有没有收益"。
    coverage = C.merge([[max(c["start"], tl_start), min(c["end"], tl_end)] for c in items])

    def _cov_ratio(rngs):
        total = sum(b - a for a, b in rngs)
        if total <= 0:
            return 0.0
        hit = 0
        for a, b in rngs:
            for ca, cb in coverage:
                hit += max(0, min(b, cb) - max(a, ca))
        return hit / total

    sub_off = _subtitle_offset_frames(tl, cuts_pairs, fps, tl_start, tl_end)
    if sub_off:
        # ★★ v1.0.1：R1 只在"按原区间根本切不到素材"时才有意义。
        #   实测踩到过：残留字幕里恰有一条 2.68 秒的条目（79.91~82.59s），
        #   这次要切的是尾部静音 2.70 秒（96.5~99.2s）——「时长一致」把它们
        #   配上了，单票即生效，偏移被算成 -498 帧（-16.6 秒），
        #   画面实际被切在 79.9s，切走了 16 秒内容，而日志一切正常。
        #   判据：把区间平移过去之后，落在素材上的比例必须【严格变高】；
        #   原区间本来就切得到素材时，说明时间线是对齐的，一律不校准。
        before = raw[0] if raw else [0, 0]
        shifted = C.merge([[max(tl_start, int(round(a + sub_off))),
                            min(tl_end, int(round(b + sub_off)))] for a, b in raw])
        base_r, new_r = _cov_ratio(raw), _cov_ratio(shifted)
        if new_r <= base_r + 1e-6:
            print("[inplace] R1 校准出偏移 %d 帧，但平移后覆盖没有变多"
                  "（%.0f%% → %.0f%%），按原区间切（不校准）"
                  % (sub_off, base_r * 100, new_r * 100), flush=True)
            sub_off = 0
        else:
            raw = shifted
            # 把"平移了多少秒、从哪挪到哪"写进日志：以前只说偏移帧数，
            # 切错位置时从输出上完全看不出来。
            print("[inplace] R1 校准：字幕轨整体偏移 %d 帧（%.2f 秒），切除区间已平移"
                  "；首段 %.2fs → %.2fs（覆盖 %.0f%% → %.0f%%）"
                  % (sub_off, sub_off / fps if fps else 0,
                     before[0] / fps if fps else 0,
                     (raw[0][0] if raw else 0) / fps if fps else 0,
                     base_r * 100, new_r * 100),
                  flush=True)

    if not raw:
        raise RuntimeError("没有有效的切除区间（删除记录为空？）")

    eff = []
    for a, b in raw:
        for ca, cb in coverage:
            x, y = max(a, ca), min(b, cb)
            if y > x:
                eff.append([x, y])
    eff = C.merge(eff)
    if not eff:
        raise RuntimeError("切除区间都落在空隙里，没有可切的素材")

    cut_s = sum(b - a for a, b in eff) / fps
    removed = cut_s          # 旧字段，保持对老 Lua 的兼容
    print("原地粗剪：%d 段区间 / 切口共 %.2fs" % (len(eff), cut_s), flush=True)

    # ---- 还原清单先落盘 ----
    # ★ v4.8.21：文件名不再叫 cut_pre（"pre" 让人以为能"还原"）。
    #   它是一份【剪切记录】：撤销靠 Ctrl+Z（上面 begin_undo/end_undo 是原生一步）。
    backup = os.path.join(work, "cut_log_" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(backup, "w", encoding="utf-8") as f:
        json.dump({
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeline": tl.GetName(),
            "engine": "inplace",
            "fps": fps,
            "srt": cfg.get("srt") or "",
            "clips": [{"track": c["track"], "kind": c["kind"], "name":
                       (c["mi"].GetName() if c["mi"] else ""),
                       "start": c["start"], "end": c["end"], "ss": c["ss"],
                       "se": c.get("se"), "speed": c["speed"]} for c in items],
            "eff": [[int(a), int(b)] for a, b in eff],
        }, f, ensure_ascii=False, indent=1)
    print("剪切记录:", backup, flush=True)
    res["backup"] = backup

    # ---- 执行（全程一个原生撤销步）----
    # ★ v3.4.0：区间【倒序】逐段处理，每段都重新 gather 当前时间线状态。
    #   不能一次性 plan 完再执行 —— 同一片段被两个区间切到时，第一个 split
    #   已经改变了它的 end，静态快照会让第二个 split 把整段内容重复追加一遍。
    errs = []
    n_split = n_del = n_trim = 0
    L.begin_undo(proj, "AsrSubs 原地粗剪")
    try:
        for a, b in sorted(eff, reverse=True):
            acts = plan_inplace(gather(tl), [[a, b]])
            for act in acts:
                kind = act[0]
                c = act[1]
                if kind == "del":
                    # ★ 已经被"链接删除"带走的不再重复删（DeleteClips 对失效条目返回 False）
                    if _edge(c["it"], "left") is None:
                        continue
                    grp = [c["it"]]
                    try:
                        for lk in (c["it"].GetLinkedItems() or []):
                            if _edge(lk, "left") is not None:
                                grp.append(lk)
                    except Exception:
                        pass
                    try:
                        tl.DeleteClips(grp, False)
                        n_del += 1
                    except Exception as e:
                        errs.append("删除 %s%d 轨片段失败：%s" % (c["kind"], c["track"], e))
                elif kind == "trimr":
                    ok, msg = move_edge(c["it"], "right", act[2])
                    if ok:
                        n_trim += 1
                    else:
                        errs.append("裁右端到 %d 失败：%s" % (act[2], msg))
                elif kind == "triml":
                    ok, msg = move_edge(c["it"], "left", act[2])
                    if ok:
                        n_trim += 1
                    else:
                        errs.append("裁左端到 %d 失败：%s" % (act[2], msg))
                elif kind == "split":
                    b2 = act[3]
                    it = c["it"]
                    grp = split_group(it)
                    ok, msg = move_edge(it, "right", act[2])
                    if not ok:
                        errs.append("拆分时裁右端到 %d 失败：%s" % (act[2], msg))
                        continue
                    for g in grp:
                        newit = append_half(mp, tl, g, b2, b2)
                        if not newit:
                            errs.append("拆分后 %s%d 轨右半段（%d 之后）追加失败"
                                        % (g["kind"], g["track"], b2))
                            continue
                        if g["kind"] == "video":
                            cp = copy_look(g["it"], newit)
                            print("[inplace] 拆分 %s%d：[%d,%d) + 新片段 %d 起，属性 %s"
                                  % (g["kind"], g["track"], g["start"], act[2], b2,
                                     "/".join(cp) or "无"), flush=True)
                        else:
                            print("[inplace] 拆分 %s%d：[%d,%d) + 新片段 %d 起"
                                  % (g["kind"], g["track"], g["start"], act[2], b2),
                                  flush=True)
                    n_split += 1
                if errs:
                    break        # 出错立刻停，不留半截时间线
            if errs:
                break
    except Exception:
        L.end_undo(proj)
        raise
    L.end_undo(proj)

    if errs:
        raise RuntimeError("原地粗剪中断：" + "；".join(errs[:3])
                           + "（已改的部分请按 Ctrl+Z 撤销）")
    if n_split + n_del + n_trim == 0:
        raise RuntimeError("没有需要处理的片段（时间线未改动）")
    print("[inplace] 合计：删 %d 段 / 裁 %d 处 / 拆分 %d 段"
          % (n_del, n_trim, n_split), flush=True)

    # ---- 字幕重挂（保留空隙模式下 SRT 时间不用改，只是重落一遍）----
    srt_path = str(cfg.get("srt") or "")
    landed = 0
    srt_rows = 0
    killed = 0
    guessed = 0
    if srt_path and os.path.exists(srt_path):
        try:
            srt_rows = len(C.parse_srt(srt_path))
        except Exception:
            srt_rows = 0
        killed, guessed = clear_sub_track(tl, srt_path, fps)
        if killed:
            print("[inplace] 清掉本插件旧字幕 %d 条（用户手工字幕已保留）" % killed,
                  flush=True)
        if guessed:
            print("[inplace] 其中 %d 条是靠时间码认领的（SRT 文件名变了）" % guessed,
                  flush=True)
        try:
            d = L.land_inplace(mp, tl, srt_path, tl_start, fps, work)
            landed = int(d.get("placed") or 0)
            print("[inplace] 字幕重挂：%d 条，首帧 %s / SRT 首条 %s"
                  % (landed, d.get("first"), d.get("srt_first")), flush=True)
        except Exception as e:
            print("[inplace] 字幕重挂失败（视频已切好，可再点「适配字幕」）：%s" % e, flush=True)

    # ---- 统计：切口 / 真正缩短 / 留下的空隙 ----
    #   恒等式 cut_s == saved_s + gap_s（容差 1 帧）：切在时间线尾部才会缩短，
    #   切在中间或头部只留空洞 —— 这正是旧文案说错的地方。
    dur_after_s = (int(tl.GetEndFrame() or 0) - tl_start) / fps if fps else 0.0
    saved_s = max(0.0, dur_before_s - dur_after_s)
    gap_s = max(0.0, cut_s - saved_s)
    print("[inplace] 切口 %.2fs ＝ 时长缩短 %.2fs ＋ 留空 %.2fs"
          % (cut_s, saved_s, gap_s), flush=True)

    clips = 0
    for kind in ("video", "audio"):
        try:
            for i in range(1, (tl.GetTrackCount(kind) or 0) + 1):
                clips += len(_track_items(tl, kind, i))
        except Exception:
            pass

    res.update({
        "ok": True,
        "mode": "cut",
        "engine": "inplace",
        "timeline": tl.GetName(),
        "src_timeline": tl.GetName(),
        "srt": srt_path,
        "cut_segs": len(raw),
        "clips": clips,
        "subs": landed,
        "frames": int(tl.GetEndFrame() or 0) - tl_start,
        "srt_rows": srt_rows,
        "cleared": killed,          # 清掉的旧字幕条数（含兜底认领）
        "cleared_by_time": guessed,  # 其中靠时间码认领的条数（名称判据失效时）
        "lost": max(0, srt_rows - landed),
        "removed": removed,        # 兼容旧 Lua：等于 cut_s
        "cut_s": round(cut_s, 3),
        "saved_s": round(saved_s, 3),
        "gap_s": round(gap_s, 3),
        "dur_before": round(dur_before_s, 3),
        "dur_after": round(dur_after_s, 3),
        "duration": C.duration_of(tl, fps),
        "fps": fps,
        "diag": "" if ((not srt_rows) or landed > 0) else "字幕未重新落到时间线",
    })
