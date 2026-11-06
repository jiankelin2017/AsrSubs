# -*- coding: utf-8 -*-
r"""把 version.txt 的版本号同步到所有需要写死版本的位置。

用法：
    python tools/sync_version.py          # 同步
    python tools/sync_version.py --check  # 只检查是否一致（CI/打包前用）

★ 唯一来源 = 根目录 version.txt。这个脚本只做「读一处、写多处」，
  绝不允许反向（从代码里回读版本），否则又会退化成多处维护。

同步目标：
    1. AsrSubs.lua                   —— 代码里的 local VERSION = "x.y.z"
    2. 02_构建与安装器/AsrSubs.iss    —— #define MyAppVersion "x.y.z"
    3. 02_构建与安装器/installer.py   —— APP_VERSION = "x.y.z"
    4. qwen_version.py               —— VERSION = "x.y.z"
    5. 03_文档/使用说明.txt           —— 第 2 行抬头的 " vX.Y.Z"

★ v3.2.6（2026-10-30）源码形态变更：不再有 lua_src 分片，单文件
  AsrSubs.lua 就是唯一源码，所以版本号改成由本脚本直接写进代码。
  以前这里写着「lua_src 里的 @VERSION@ 由 build_lua.py 注入，这里不碰」——
  那条已经作废：build_lua.py 现在只校验不改写，写入权收归本脚本。

★ 为什么 5 也要进来：v2.0.3 发版时忘了改它，说明文档抬头一直停在 v2.0.2，
  用户打开说明看到旧版本号，会以为没装上新的。凡是"写死版本"的地方都得进这张表。

★ v2.0.10 目录重组：项目从「源码备份\05_开发分支」搬到了 D:\插件\AsrSubs\，
  并按编号分成了 01_源码 / 02_构建与安装器 / 03_文档 / 04_安装包。
  所以本脚本的 ROOT 是 01_源码，而 iss / installer.py / 使用说明.txt 都在
  【同级】目录里 —— 路径统一走 PARENT，别写死 "..\.."。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # …\01_源码
PARENT = os.path.dirname(ROOT)               # …\AsrSubs

TARGETS = [
    # (相对路径, 正则, 替换模板)
    # ★ v3.2.6 起 Lua 源码是单文件，版本字面量直接由这里注入。
    #   (?m) 让 ^ 匹配行首；count=1 保证只改第一处，不会误伤
    #   日志里出现的 "VERSION" 字样或注释。
    (os.path.join(ROOT, "AsrSubs.lua"),
     r'(?m)^(local\s+VERSION\s*=\s*")[^"]*(")',
     r'\g<1>%s\g<2>'),
    (os.path.join(PARENT, "02_构建与安装器", "AsrSubs.iss"),
     r'(#define\s+MyAppVersion\s+")[^"]*(")',
     r'\g<1>%s\g<2>'),
    (os.path.join(PARENT, "02_构建与安装器", "installer.py"),
     r'(APP_VERSION\s*=\s*")[^"]*(")',
     r'\g<1>%s\g<2>'),
    (os.path.join(ROOT, "qwen_version.py"),
     r'(VERSION\s*=\s*")[^"]*(")',
     r'\g<1>%s\g<2>'),
    # ★ (?m) 必须加：否则 ^ 只匹配全文开头。这里只匹配「行首一个空格 + vX.Y.Z」，
    #   不会碰到下面「v2.0.3：…」这种顶格写的更新记录条目。
    (os.path.join(PARENT, "03_文档", "使用说明.txt"),
     r'(?m)^( v)\d+\.\d+(?:\.\d+)?',
     r'\g<1>%s'),
]


def read_version():
    with io.open(os.path.join(ROOT, "version.txt"), "r", encoding="utf-8") as f:
        v = f.read().strip()
    if not re.match(r"^\d+\.\d+(\.\d+)?$", v):
        raise SystemExit("version.txt 内容不像版本号：%r" % v)
    return v


def main():
    check_only = "--check" in sys.argv
    ver = read_version()
    bad = []

    for rel, pattern, repl in TARGETS:
        # ★ TARGETS 里存的已经是绝对路径（跨目录了），不能再 join(ROOT, rel) ——
        #   Windows 下 os.path.join("C:\\a", "C:\\b") 会直接返回 "C:\\b"，
        #   看着能用，其实是撞对的，换个平台就翻车。
        path = rel
        name = os.path.relpath(rel, PARENT)
        if not os.path.exists(path):
            bad.append("%s：文件不存在" % name)
            continue
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        if not re.search(pattern, text):
            bad.append("%s：找不到可替换的版本字段" % name)
            continue
        # ★ 不能对替换串用 re.escape：它会把 "2.0.0" 变成 "2\.0\.0" 写进文件
        new = re.sub(pattern, repl % ver, text, count=1)
        if new == text:
            print("  一致   %s" % name)
            continue
        if check_only:
            bad.append("%s：版本与 version.txt（%s）不一致" % (name, ver))
            continue
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
        print("  已同步 %s → %s" % (name, ver))

    if bad:
        print("失败：")
        for b in bad:
            print("  · " + b)
        return 1
    print("版本号 %s 已同步完毕。" % ver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
