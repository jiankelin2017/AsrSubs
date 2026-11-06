# -*- coding: utf-8 -*-
r"""校验单文件源码 AsrSubs.lua 可用于部署。

★★★ 形态变更（v3.2.6，2026-10-30）★★★
  早期把源码拆在 lua_src\*.lua 的 13 个分片里，构建时纯文本拼接成单文件
  （理由：Resolve 的 Lua 环境里 package.path 不可靠，插件必须是单文件）。
  但 3.0.6 → 3.2.6 之间的全部改动都直接改在装机版单文件上，分片一直停在
  3.0.5；只要跑一次拼接，搜索 / 高亮 / 多选删除 / 粗剪就会被整体回退。
  所以决定：单文件 01_源码\AsrSubs.lua 就是唯一源码，分片已归档到
  09_归档\08_旧版本清理_20261030\lua_src_v3.0.5\。

  本脚本随之从「拼接器」退化为「体检器」：不生成任何文件，只做三项校验，
  任何一项不过就返回 1，让 verify_all.py 和 build.bat 立刻拦下来。

版本号：唯一来源仍是 version.txt（由 sync_version.py 注入代码里的
  `local VERSION = "x.y.z"`）。本脚本只校验两者一致，绝不改写 —— 改写
  只允许发生在一个地方，否则又退化成多处维护。

用法：
    python tools/build_lua.py            # 校验（默认行为）
    python tools/build_lua.py --check    # 同上，供 verify_all.py / build.bat 调用
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # …\01_源码
LUA = os.path.join(ROOT, "AsrSubs.lua")
VER_FILE = os.path.join(ROOT, "version.txt")

# 只认行首的 local VERSION = "x.y.z"（同文件里没有第二处这样的写法）
VER_RE = re.compile(r'(?m)^local\s+VERSION\s*=\s*"([^"]*)"')


def read_version():
    if not os.path.isfile(VER_FILE):
        raise SystemExit("失败：找不到 version.txt（%s）" % VER_FILE)
    with io.open(VER_FILE, "r", encoding="utf-8") as f:
        v = f.read().strip()
    if not re.match(r"^\d+\.\d+(\.\d+)?$", v):
        raise SystemExit("失败：version.txt 内容不像版本号：%r" % v)
    return v


def main():
    version = read_version()
    fails = []

    # ── 1. 源码在不在 ────────────────────────────────────────────
    if not os.path.isfile(LUA):
        print("失败：找不到源码 %s" % LUA)
        return 1

    # 用 newline="" 读，避免 Windows 下把 \r\n 归一化后长度对不上
    with io.open(LUA, "r", encoding="utf-8", newline="") as f:
        text = f.read()

    # ── 2. 分片时代的占位符必须已全部替换 ────────────────────────
    #    历史上 @VERSION@ 由拼接时替换；现在单文件里若还残留，
    #    说明有人手工从分片拷过来的，Lua 会把 @VERSION@ 当非法字符。
    if "@VERSION@" in text:
        fails.append("源码里仍有未替换的 @VERSION@ 占位符")

    # ── 3. 代码版本号 == version.txt ─────────────────────────────
    m = VER_RE.search(text)
    if not m:
        fails.append('代码里找不到 `local VERSION = "x.y.z"`')
    else:
        code_ver = m.group(1)
        if code_ver != version:
            fails.append("版本号不一致：代码 %s ≠ version.txt %s（跑 tools/sync_version.py 修）"
                         % (code_ver, version))

    if fails:
        print("失败：")
        for x in fails:
            print("  · " + x)
        return 1

    lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    print("校验通过：单文件制 v%s，%d 行，%d 字符" % (version, lines, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
