# -*- coding: utf-8 -*-
"""把根目录的开发源同步进 packer/payload（安装载荷）。

★ 为什么必须有这一步：
  payload\\ 里的文件才是「安装后真正落到 Resolve 脚本目录 / 引擎目录」的东西。
  v1.28 时代靠人工保持 payload 与根目录一致，备份说明里专门写了一句
  「payload 需与根目录源码保持同步（打包时以根目录为源）」—— 靠人记。
  一旦漏同步，就会出现「本地测试是新的、装出来的却是旧的」这种最难查的问题。

用法：
    python tools/sync_payload.py           # 同步
    python tools/sync_payload.py --check   # 只检查差异，不写文件（打包前跑）
"""
import filecmp
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                 # …\01_源码（源码都在这一层）
PARENT = os.path.dirname(ROOT)               # …\AsrSubs
# ★ v2.0.10 目录重组：payload 跟着 packer 搬到了 02_构建与安装器\
PAYLOAD = os.path.join(PARENT, "02_构建与安装器", "payload")

# 根目录 → payload 的文件清单
#   · models.lua / models.txt 不部署（Lua 从引擎目录 ASCII_ROOT 读）
#   · get-pip.py 只在 payload 里有（安装器内嵌用），不从根目录同步
FILES = [
    "AsrSubs.lua",
    "asr_engine.py",
    "asr_server.py",
    "corrections.py",
    # ★ v3.2.6：粗剪依赖。AsrSubs.lua 第 299 行 CUT_SCRIPT = ASCII_ROOT\cut_timeline.py，
    #   缺了它「切除视频」直接报「粗剪脚本缺失」。之前只在装机版 engine 里有，
    #   payload 漏登记，打出来的安装包是坏的一半功能。
    "cut_timeline.py",
    # ★ v3.4.0：原地粗剪引擎。cut_timeline.py 按 engine=inplace 分发到它，
    #   payload 漏登记就会「本地测试是新的、装出来的没有原地粗剪」。
    "cut_inplace.py",
    "diag.py",
    "export_files.py",
    "hotwords.py",
    "land_subtitle.py",
    "qwen_version.py",
    "render_to_wav.xml",
    "resolve_bridge.py",
    "subs_io.py",
    "timeline_source.py",
    "txt_out.py",
    "worker.py",
    "使用说明.txt",
]

# ★ v2.0.10：使用说明.txt 搬去了 03_文档\，其余仍在 01_源码\。
#   这里只登记"不在 ROOT 的文件"，目标文件名不变（payload 里还是要叫 使用说明.txt）。
SRC_DIR = {
    "使用说明.txt": os.path.join(PARENT, "03_文档"),
}


def main():
    check_only = "--check" in sys.argv
    os.makedirs(PAYLOAD, exist_ok=True)
    missing, diff, same = [], [], 0

    for name in FILES:
        src = os.path.join(SRC_DIR.get(name, ROOT), name)
        dst = os.path.join(PAYLOAD, name)
        if not os.path.isfile(src):
            missing.append(name)
            continue
        if os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False):
            same += 1
            continue
        diff.append(name)
        if not check_only:
            shutil.copyfile(src, dst)

    if missing:
        print("缺少源文件：")
        for m in missing:
            print("  · " + m)
    if diff:
        if check_only:
            print("payload 与源码不一致（需同步）：")
            for d in diff:
                print("  · " + d)
        else:
            print("已同步 %d 个文件到 payload：" % len(diff))
            for d in diff:
                print("  · " + d)
    if not missing and not diff:
        print("payload 与源码一致（%d 个文件）" % same)
        return 0
    return 1 if (check_only and (missing or diff)) else 0


if __name__ == "__main__":
    sys.exit(main())
