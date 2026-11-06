# -*- coding: utf-8 -*-
"""模型目录体检单测：直接跑，不需要 Resolve、不加载模型。

★ 为什么要留这份用例：v4.8.18 的「识别失败：未识别到语音内容」根因是
  模型下错仓库（qwen_asr 原版 Qwen3-ASR-0.6B 落在 -hf 目录）。这种错
  不报错，只是把全部张量判"缺失"、随机初始化，最后表现为 0 条字幕 ——
  靠人眼是发现不了的。check_model_layout() 是唯一的拦截点，所以给它上用例。
"""
import io
import json
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asr_engine as E  # noqa: E402

fails = []


def eq(actual, expected, label):
    if actual[0] != expected:
        fails.append("%s\n    期望: %r\n    实际: %r" % (label, expected, actual))
    else:
        print("  ok  " + label)


def fake_dir(config, keys):
    """造一个只有 config.json + safetensors 头部的假模型目录。"""
    d = tempfile.mkdtemp(prefix="asr_layout_")
    with io.open(os.path.join(d, "config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False)
    hdr = json.dumps({k: {"dtype": "BF16", "shape": [1], "data_offsets": [0, 0]}
                      for k in keys}).encode()
    with open(os.path.join(d, "model.safetensors"), "wb") as f:
        f.write(struct.pack("<Q", len(hdr)) + hdr)
    return d


FLAT = {"model_type": "qwen3_asr", "text_config": {}, "audio_config": {}}
THINKER = {"model_type": "qwen3_asr",
           "thinker_config": {"text_config": {}, "audio_config": {}}}

print("== 1 正常布局（扁平 config + model.* 权重）==")
eq(E.check_model_layout(fake_dir(FLAT, ["model.audio_tower.conv1.weight"])), True,
   "扁平原版 -hf 布局通过")

print("== 2 原版仓库布局（thinker_config + thinker.*）==")
eq(E.check_model_layout(fake_dir(THINKER, ["thinker.model.embed_tokens.weight"])), False,
   "thinker_config 被拦下")

print("== 3 只错一半 ==")
eq(E.check_model_layout(fake_dir(FLAT, ["thinker.model.embed_tokens.weight"])), False,
   "config 对、权重前缀是 thinker.* 也要拦")
eq(E.check_model_layout(fake_dir(THINKER, ["model.embed_tokens.weight"])), False,
   "权重对、config 藏 thinker_config 也要拦")

print("== 4 缺文件 / 坏文件 ==")
empty = tempfile.mkdtemp(prefix="asr_layout_")
eq(E.check_model_layout(empty), False, "空目录被拦下")
eq(E.check_model_layout(os.path.join(empty, "根本不存在")), False, "不存在的目录被拦下")

broken = tempfile.mkdtemp(prefix="asr_layout_")
with io.open(os.path.join(broken, "config.json"), "w", encoding="utf-8") as f:
    f.write("{ 这不是 json")
eq(E.check_model_layout(broken), False, "config.json 坏掉被拦下")

print("== 5 内置模板与模型目录里的官方模板一致（模型没装就跳过）==")
try:
    import transformers  # noqa: F401
    got_all = True
    for sub, const, label in (
            ("Qwen3-ASR-0.6B-hf", E._FALLBACK_ASR_TEMPLATE, "识别模型模板"),
            ("Qwen3-ForcedAligner-0.6B-hf", E._FALLBACK_ALIGN_TEMPLATE, "对齐模型模板")):
        p = os.path.join(E.PATHS.get("models") or str(E._ROOT_DIR / "models"),
                         sub, "chat_template.jinja")
        if not os.path.isfile(p):
            p = os.path.join(r"D:\AsrSubs\models", sub, "chat_template.jinja")
        if not os.path.isfile(p):
            print("  --  跳过（没找到 %s）" % p)
            got_all = False
            continue
        with io.open(p, "r", encoding="utf-8") as f:
            eq((const == f.read(),), True, "%s 与官方 jinja 逐字一致" % label)
    if not got_all:
        print("  --  部分模板文件缺失，跳过比对")
except ImportError:
    print("  --  没装 transformers，跳过")

print()
if fails:
    print("失败 %d 项：" % len(fails))
    for f in fails:
        print("  · " + f)
    sys.exit(1)
print("全部用例通过")
