# -*- coding: utf-8 -*-
r"""AsrSubs 开发目录同步工具 —— 让 D:\插件\AsrSubs_整合_20261031（开发目录）保持最新。

问题背景
--------
真正的"被测试 / 被 Resolve 加载"的引擎在 D:\AsrSubs\engine（Lua 里的
ASCII_ROOT 指向它）。开发目录 D:\插件\AsrSubs_整合_20261031\01_源码 是规范源码。
两边很容易因为"在 engine 里改完测试、忘了回灌"而漂移
（2026-10-31 就曾发生：01_源码 停在 4.0.4，engine 已经 4.0.5）。

本脚本用来消灭这种漂移：

    python sync_dev.py            # 默认：只读检查（不写任何文件）
    python sync_dev.py --sync     # 把 engine 的已测代码回灌进 01_源码
    python sync_dev.py --deploy   # 在回灌基础上，再推到 engine / payload / 菜单

★ 顺序陷阱（已在脚本内自动处理）：
  回灌会把 engine 里的版本号（AsrSubs.lua 的 local VERSION、qwen_version.py
  的 VERSION）一起带进 01_源码，覆盖掉 sync_version.py 刚注入的新版本号
  （2026-10-31 实际踩过：version.txt 已是 4.0.7，回灌后又变回 4.0.6）。
  因此本脚本在【回灌后】和【部署前】都会自动重跑 tools/sync_version.py。

任何模式都会打印三项检查汇总：
  1) engine ↔ 01_源码 差异（仅比对规范文件集，忽略 .bak/测试/探针/rt）
  2) 版本号一致性：version.txt == AsrSubs.lua 的 local VERSION == qwen_version.VERSION
  3) 01_源码 ↔ 安装包 payload 的 AsrSubs.lua MD5 是否一致
"""

import argparse
import filecmp
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))          # D:\插件\AsrSubs_整合_20261031
SRC = os.path.join(HERE, "01_源码")
DOC = os.path.join(HERE, "03_文档")
PAYLOAD = os.path.join(HERE, "02_构建与安装器", "payload")
ENGINE = r"D:\AsrSubs\engine"
# ★ v4.0.9 整理：engine\packer 已归档到 09_归档\11_v4.0.9整理_20261031\engine\packer
#   —— 那是 2.x 时代的独立打包区（q3\ / Qwen字幕工具\ 旧布局），当前打包走
#      02_构建与安装器\build.bat + 02_构建与安装器\payload，packer 不再参与。
#   保留此常量仅为兼容旧引用；deploy() 不再向它同步。
PACKER = os.path.join(ENGINE, "packer", "payload")
PD_LUA = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"),
    "Blackmagic Design", "DaVinci Resolve", "Fusion",
    "Scripts", "Utility", "AsrSubs", "AsrSubs.lua")

# ── 规范文件集：engine ↔ 01_源码 之间同步的对象 ──────────────────────
LUA_PY = [
    "AsrSubs.lua",
    "asr_engine.py", "asr_server.py", "corrections.py",
    "cut_inplace.py", "cut_timeline.py", "diag.py", "export_files.py",
    "hotwords.py", "land_subtitle.py", "qwen_version.py",
    "resolve_bridge.py", "subs_io.py", "timeline_source.py",
    "txt_out.py", "worker.py",
]
CONFIG = ["models.lua", "models.txt", "paths.json", "paths.lua"]
DOC_FILES = {"使用说明.txt": DOC}

# 构造 (name, engine_path, dev_path) 三元组
CANON = []
for n in LUA_PY + CONFIG:
    CANON.append((n, os.path.join(ENGINE, n), os.path.join(SRC, n)))
for n, d in DOC_FILES.items():
    CANON.append((n, os.path.join(ENGINE, n), os.path.join(d, n)))


def md5(path):
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(1 << 16), b""):
                h.update(blk)
    except OSError:
        return None
    return h.hexdigest()


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def get_lua_version(lua_text):
    if lua_text is None:
        return None
    for line in lua_text.splitlines():
        if "local VERSION" in line:
            m = re.search(r'["\']([0-9]+\.[0-9]+\.[0-9]+)["\']', line)
            if m:
                return m.group(1)
    return None


def get_py_version(py_text):
    if py_text is None:
        return None
    m = re.search(r'VERSION\s*=\s*["\']([0-9.]+)["\']', py_text)
    return m.group(1) if m else None


def check_drift(apply):
    print("=" * 64)
    print(" 1) engine <-> 01_源码 差异检查")
    print("=" * 64)
    if not os.path.isdir(ENGINE):
        print("  x 找不到 engine 目录：%s" % ENGINE)
        return False
    if not os.path.isdir(SRC):
        print("  x 找不到 01_源码 目录：%s" % SRC)
        return False

    missing, differ = [], []
    for name, ep, dp in CANON:
        if not os.path.isfile(ep):
            continue
        if not os.path.isfile(dp):
            missing.append((name, ep, dp))
            continue
        if not filecmp.cmp(ep, dp, shallow=False):
            differ.append((name, ep, dp))

    if not missing and not differ:
        print("  [OK] engine 与 01_源码 规范文件集完全一致")
        return True

    for name, ep, dp in missing:
        print("  01_源码 缺少（engine 有）：+ %s" % name)
    for name, ep, dp in differ:
        print("  两边不一致：~ %s" % name)

    if apply:
        print("\n  -> 回灌 engine -> 01_源码 ...")
        for name, ep, dp in (missing + differ):
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copyfile(ep, dp)
            print("    已复制 %s" % name)
        # ★ 回灌后必须重跑版本注入：engine 里的 AsrSubs.lua / qwen_version.py
        #   自带版本号（local VERSION / VERSION），回灌会把 01_源码 里刚由
        #   sync_version.py 注入的新版本号覆盖回旧值（2026-10-31 实际踩过：
        #   version.txt 已是 4.0.7，回灌后又变回 4.0.6）。
        sync_version()
    else:
        print("\n  演练模式（未改动）。加 --sync 执行回灌。")
    return False


def sync_version():
    """回灌后重跑 01_源码\\tools\\sync_version.py，把 version.txt 的版本号
    重新注入到 Lua / .iss / installer.py / qwen_version.py / 使用说明.txt。"""
    sp = os.path.join(SRC, "tools", "sync_version.py")
    if not os.path.isfile(sp):
        print("    (找不到 sync_version.py，跳过版本注入)")
        return
    print("    -> 重跑 sync_version.py（回灌会覆盖版本号，必须重新注入）...")
    try:
        subprocess.run([sys.executable, sp], cwd=SRC,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print("    版本号已重新注入")
    except Exception as e:
        print("    sync_version.py 执行失败：%s" % e)


def check_version():
    print("\n" + "=" * 64)
    print(" 2) 版本号一致性")
    print("=" * 64)
    vt = (read_text(os.path.join(SRC, "version.txt")) or "").strip() or "(缺失)"
    lua = get_lua_version(read_text(os.path.join(SRC, "AsrSubs.lua")))
    qv = get_py_version(read_text(os.path.join(SRC, "qwen_version.py")))
    print("  version.txt        : %s" % vt)
    print("  AsrSubs.lua VERSION: %s" % lua)
    print("  qwen_version       : %s" % qv)
    ok = bool(vt) and vt != "(缺失)" and vt == lua == qv
    print("  %s" % ("[OK] 一致" if ok else "x 不一致"))
    return ok


def check_payload_md5():
    print("\n" + "=" * 64)
    print(" 3) 01_源码 <-> 安装包 payload  MD5")
    print("=" * 64)
    name = "AsrSubs.lua"
    a = md5(os.path.join(SRC, name))
    b = md5(os.path.join(PAYLOAD, name))
    print("  01_源码 : %s" % a)
    print("  payload : %s" % b)
    ok = bool(a) and bool(b) and a == b
    print("  %s" % ("[OK] 一致" if ok else "x 不一致（打包前请 sync_payload）"))
    return ok


def deploy():
    print("\n" + "=" * 64)
    print(" 部署：01_源码 -> payload / 菜单 / engine")
    print("=" * 64)
    # ★ 部署前先确保版本号已注入到 01_源码 里的各个文件
    #   （回灌会把 engine 的旧版本号带回来，必须重跑注入）
    sync_version()
    # 安装包 payload
    sp = os.path.join(SRC, "tools", "sync_payload.py")
    if os.path.isfile(sp):
        print("  - sync_payload.py（安装包载荷）...")
        subprocess.run([sys.executable, sp], cwd=SRC)
    # ★ v4.0.9 整理：不再向 engine\packer\payload 同步 —— 那是 2.x 旧打包区，
    #   已整体归档。当前唯一有效的安装包载荷是 02_构建与安装器\payload
    #   （由 sync_payload.py 维护，build.bat 用相对路径 "payload;payload" 取它）。
    #   历史原因：packer/payload 是 02_构建与安装器/payload 的冗余副本，
    #   两份并存会让"五份 Lua MD5 一致"这类检查多一个易漂移的副本。
    if os.path.isdir(PACKER):
        print("  - packer payload（已归档，保留兼容不写入）...")
    # 菜单 Lua（实时）
    if os.path.isfile(PD_LUA):
        print("  - ProgramData 菜单 Lua（实时，Resolve 需重新运行脚本才生效）...")
        shutil.copyfile(os.path.join(SRC, "AsrSubs.lua"), PD_LUA)
        print("    已复制 AsrSubs.lua")
    else:
        print("  - ProgramData 菜单 Lua 不存在，跳过（Resolve 未部署？）")
    # ★ engine（ASCII_ROOT）：Resolve 实际加载的 Python 引擎目录
    #   —— 漏了它，插件仍会跑旧代码（2026-10-31 的部署曾因此不一致）。
    #   ★ 文档类文件（如 使用说明.txt，住在 03_文档\）也要一并同步：
    #     它带版本号，不同步会让 engine↔01_源码 的漂移检查永远报差异
    #     （2026-10-31 实测：engine 里还是 4.0.7，01_源码 已 4.0.8）。
    if os.path.isdir(ENGINE):
        print("  - engine（ASCII_ROOT，Resolve 实际加载的 Python）...")
        n = 0
        for name, ep, dp in CANON:
            src = os.path.join(SRC, name)
            if not os.path.isfile(src):
                # 文档类文件的"源码"在 03_文档\ 而不是 01_源码\
                src = dp if os.path.isfile(dp) else src
                if not os.path.isfile(src):
                    continue
            os.makedirs(os.path.dirname(ep), exist_ok=True)
            shutil.copyfile(src, ep)
            n += 1
        print("    已同步 %d 个文件到 engine" % n)


def main():
    ap = argparse.ArgumentParser(description="AsrSubs 开发目录同步工具")
    ap.add_argument("--sync", action="store_true", help="把 engine 回灌进 01_源码")
    ap.add_argument("--deploy", action="store_true",
                    help="在回灌基础上再推到 payload/菜单/engine")
    a = ap.parse_args()
    apply = a.sync or a.deploy

    ok1 = check_drift(apply=apply)
    ok2 = check_version()
    ok3 = check_payload_md5()

    if a.deploy:
        deploy()
        # ★ 部署后必须【重新校验】：上面三个 ok 值是部署前的状态，
        #   部署动作本身会改变文件（版本号注入 / payload 同步），复用旧值
        #   会误报"不一致"（2026-10-31 实测：部署明明成功，汇总却报差异）。
        print("\n  --- 部署后复验 ---")
        ok1 = check_drift(apply=False)
        ok2 = check_version()
        ok3 = check_payload_md5()

    print("\n" + "=" * 64)
    print(" 汇总")
    print("=" * 64)
    print("  engine<->01_源码 : %s" % ("一致" if ok1 else "有差异(见上)"))
    print("  版本号          : %s" % ("一致" if ok2 else "不一致"))
    print("  01_源码<->payload: %s" % ("一致" if ok3 else "不一致"))
    if a.deploy:
        print("  已执行部署（payload/菜单/engine）")
    return 0 if (ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    sys.exit(main())
