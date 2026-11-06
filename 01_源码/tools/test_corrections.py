# -*- coding: utf-8 -*-
"""corrections.py 单测：直接跑，不需要 Resolve。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corrections as C

fails = []
def eq(actual, expected, label):
    if actual != expected:
        fails.append("%s\n    期望: %r\n    实际: %r" % (label, expected, actual))
    else:
        print("  ok  " + label)

print("== 1 基本替换 ==")
rules, prob = C.parse_rules("人参=人生")
eq(len(rules), 1, "解析出 1 条规则")
eq(prob, [], "无格式问题")
eq(C.apply_text("这是人生", rules), ("这是人参", 1), "人生 → 人参")

print("== 2 多个错误写法 ==")
rules, _ = C.parse_rules("人参=人生,人叁、仁参")
eq(C.apply_text("人生和仁参", rules), ("人参和人参", 2), "逗号/顿号分隔多个错误词")

print("== 3 长词优先 ==")
rules, _ = C.parse_rules("哲学=人生哲学\n人参=人生")
eq(C.apply_text("人生哲学", rules)[0], "哲学", "长词优先，人生哲学 整词命中")

print("== 4 保护已正确的词 ==")
rules, _ = C.parse_rules("人生哲学=人生")
eq(C.apply_text("人生哲学", rules)[0], "人生哲学", "已写对的不再被套一层")
eq(C.apply_text("人生", rules)[0], "人生哲学", "单独的 人生 会被改")

print("== 5 不链式替换 ==")
rules, _ = C.parse_rules("人参=人生\n人参2=人参")
eq(C.apply_text("人生", rules)[0], "人参", "A→B 后 B 不被下一条再改")
eq(C.apply_text("人参", rules)[0], "人参2", "原文里本来就有的 人参 会被改成 人参2")

print("== 6 幂等 ==")
rules, _ = C.parse_rules("人参=人生")
once = C.apply_text("人生", rules)[0]
twice = C.apply_text(once, rules)[0]
eq(twice, once, "连跑两遍结果不变")

print("== 7 格式问题 ==")
rules, prob = C.parse_rules("人参=人生\n这一行没有等号\n=人生\n人参=\n\n# 注释\n// 注释")
eq(len(rules), 1, "只认 1 条有效规则")
eq(len(prob), 3, "3 条格式问题（无等号/左空/右空）")
print("     问题明细: " + " | ".join(prob))

print("== 8 全角等号 ==")
rules, prob = C.parse_rules("人参＝人生")
eq(len(rules), 1, "全角 ＝ 也能解析")
eq(C.apply_text("人生", rules)[0], "人参", "全角等号规则生效")

print("== 9 矛盾词 ==")
rules, prob = C.parse_rules("人参=人生\n人生=人身")
eq(len(rules), 2, "两条规则都保留")
eq(C.apply_text("人生", rules)[0], "人参", "矛盾词按错误写法处理")
eq(len(prob), 1, "报出 1 条矛盾提示")
print("     提示: " + " | ".join(prob))

print("== 10 块处理（元组 + 字典）==")
rules, _ = C.parse_rules("人参=人生")
blocks = [(0.0, 1.0, "这是人生"), {"s": 1.0, "e": 2.0, "t": "人生苦短"}, (2.0, 3.0, "无关")]
n = C.apply_to_blocks(blocks, rules)
eq(n, 2, "元组块与字典块都被改")
eq(blocks[0][2], "这是人参", "元组块文本已改")
eq(blocks[1]["t"], "人生苦短".replace("人生", "人参"), "字典块文本已改")
eq(blocks[2][2], "无关", "无关块不动")

print("== 11 空输入 ==")
eq(C.apply_text("", []), ("", 0), "空规则不处理")
eq(C.apply_to_blocks([], []), 0, "空块不处理")
eq(C.apply_text("原样", C.parse_rules("")[0]), ("原样", 0), "空表原样返回")

print("== 12 describe ==")
rules, _ = C.parse_rules("人参=人生,人叁\n重庆=崇庆")
eq(C.describe(rules), "2 条规则 / 3 个错误写法", "摘要文案")

print()
if fails:
    print("失败 %d 项：" % len(fails))
    for f in fails:
        print("  · " + f)
    sys.exit(1)
print("全部 12 组用例通过")
