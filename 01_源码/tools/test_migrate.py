# -*- coding: utf-8 -*-
r"""迁移逻辑回归测试：2.x 老布局 → 3.0 新布局。

用法（在 01_源码 目录下）：
    python tools/test_migrate.py

为什么要单独测：
  迁移是"搬 6GB 文件 + 改名 + 删源"三件事，写错一句就是用户数据丢失，
  而真机跑一次要几分钟、还要有 D:\AsrSubs 这种老布局。这里用几 KB 的假
  目录把两条路径都走一遍：
    · 同卷  → os.rename（秒级）
    · 跨卷  → 复制 → 校验 → 删源
  两个必须成立的硬指标：
    1) 老目录最后被改名保留（*_migrated_*），绝不直接删；
    2) 字幕产物出现在 data\output，一个都不能少。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(os.path.dirname(ROOT), "02_构建与安装器")
sys.path.insert(0, BUILD)

import installer as I  # noqa: E402


def make_legacy(root, tag, junction_models=False):
    """造一份 2.x 布局（文件都是几字节的假货，只测搬运逻辑）。

    junction_models=True 时复刻真机形态：`<工具>\\models` 不是普通目录，
    而是指向 `<根>\\models` 的 junction（2.x 安装器就是这么建的）。
    """
    def w(p, s="x"):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(s)

    w(os.path.join(root, "engine", "resolve_bridge.py"), "print('engine')\n")
    w(os.path.join(root, "engine", "asr_engine.py"), "print('engine')\n")
    w(os.path.join(root, "engine", "rt", "settings.lua"), "return {}\n")
    # 开发机常见形态：venv 直接放在 runtime（3 GB）。迁移时应该搬到
    # runtime，而不是留在 engine 里白占地方。
    w(os.path.join(root, "engine", ".venv", "dev_only.txt"), "dev venv\n")
    # 安装器自带的那份 venv（有 python.exe，会被优先采用；另一份随后清掉）
    w(os.path.join(root, "AsrSubs", ".venv", "Scripts", "python.exe"), "MZ")
    if junction_models:
        real = os.path.join(root, "models")
        w(os.path.join(real, "Qwen3-ASR-0.6B-hf", "config.json"), "{}")
        w(os.path.join(real, "Qwen3-ASR-0.6B-hf", "model.safetensors"), "x" * 32)
        link = os.path.join(root, "AsrSubs", "models")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, real],
                           capture_output=True)
        if r.returncode != 0:
            # mklink 的报错是控制台代码页（简中 GBK），不能按 UTF-8 硬解
            msg = ((r.stdout or b"") + (r.stderr or b"")).decode("mbcs", "replace")
            raise RuntimeError("建 junction 失败：%s" % msg)
    else:
        w(os.path.join(root, "AsrSubs", "models",
                       "Qwen3-ASR-0.6B-hf", "config.json"), "{}")
        w(os.path.join(root, "AsrSubs", "models",
                       "Qwen3-ASR-0.6B-hf", "model.safetensors"), "x" * 32)
    w(os.path.join(root, "AsrSubs", "字幕输出", "a.srt"), "1\n")
    w(os.path.join(root, "AsrSubs", "字幕输出", "b.txt"), "2\n")
    return root


def snapshot(base):
    expect = [
        os.path.join(base, "engine", "resolve_bridge.py"),
        os.path.join(base, "runtime", "Scripts", "python.exe"),
        os.path.join(base, "models", "Qwen3-ASR-0.6B-hf", "model.safetensors"),
        os.path.join(base, "data", "output", "a.srt"),
        os.path.join(base, "data", "output", "b.txt"),
        os.path.join(base, "engine", "paths.json"),
        os.path.join(base, "engine", "paths.lua"),
    ]
    return [(p, os.path.isfile(p)) for p in expect]


def run_case(old_root, base, label, expect_kept=True, junction_models=False):
    old_root = make_legacy(old_root, label, junction_models=junction_models)
    orig = I.find_legacy_root
    I.find_legacy_root = lambda: old_root       # 绕开"只能装在盘根"的限制
    try:
        inst = I.Installer(base, False, True, lambda m: None,
                           lambda p, t: None, threading.Event())
        mode = inst.migrate_legacy()
    finally:
        I.find_legacy_root = orig
    problems = []
    for p, ok in snapshot(base):
        if not ok:
            problems.append("缺文件：%s" % p)
    if os.path.exists(os.path.join(base, "engine", ".venv")):
        problems.append("engine 里残留了 .venv（应该已经搬到 runtime）")
    # 跨盘/换目录时老目录必须改名保留，不能消失；
    # 原地升级（base 就是老目录）时老目录本身就是新装位置，不用改名。
    if expect_kept:
        parent = os.path.dirname(old_root)
        kept = [n for n in os.listdir(parent)
                if n.startswith(os.path.basename(old_root) + "_migrated_")]
        if not kept:
            problems.append("老目录没有被改名保留：%s" % old_root)
    return mode, problems


def check_base_rules():
    r"""安装位置校验：自定义 ASCII 目录放行，中文 / 盘根 / UNC 拦下。

    ★ 3.0 起用户可以把插件装到任意本地盘的任意目录（E:\Tools\AsrSubs 这种），
      所以"这个路径能不能用"和"目录结构怎么拼"都成了发版前必须回归的东西 ——
      拼错一级，engine/data/models 就全错位。
    """
    probs = []
    here = os.path.join(tempfile.gettempdir(), "asr_base_ok", "AsrSubs")
    ok, why = I.validate_base(here, False)
    if not ok:
        probs.append("自定义 ASCII 目录被误拦：%s（%s）" % (here, why))
    for bad, tag in ((os.path.join(os.path.dirname(here), "字幕工具", "AsrSubs"), "中文"),
                     ("D:\\", "盘根"),
                     ("\\\\nas\\share\\AsrSubs", "UNC 网络路径"),
                     ("D:\\AsrSubs.", "结尾点号")):
        okv, _ = I.validate_base(bad, False)
        if okv:
            probs.append("%s 路径没被拦下：%s" % (tag, bad))

    inst = I.Installer(here, False, True, lambda m: None,
                       lambda p, t: None, threading.Event())
    for key, want in (("engine", "engine"),
                      ("runtime", "runtime"),
                      ("models_root", "models"),
                      ("output", os.path.join("data", "output")),
                      ("logs", os.path.join("data", "logs")),
                      ("cache_dir", os.path.join("data", "cache")),
                      ("docs", "docs")):
        got = getattr(inst, key)
        if not got.startswith(here) or not got.endswith(want):
            probs.append("%s 推导错了：%s（应以 %s 结尾）" % (key, got, want))
    return probs


def check_single_menu():
    r"""菜单只写一份 + 中文门面已取消（老版本留下的 junction 会被摘掉）。

    ★ 两个都是真机上踩到的：
      ① 老版本"哪个脚本目录存在就都写一份"，Resolve 把 ProgramData、
         AppData\Support、Program Files 三处全扫进菜单 → 出现两个 AsrSubs。
      ② 老版本还会建 <安装目录>字幕工具 junction，用户看到盘上两个目录，
         永远在问"哪个能删"。现在不再建，升级时要把残留摘掉。
    """
    probs = []
    tmp = tempfile.mkdtemp(prefix="asr_menu_")
    a = os.path.join(tmp, "rootA", "Fusion", "Scripts", "Utility")
    b = os.path.join(tmp, "rootB", "Support", "Fusion", "Scripts", "Utility")
    os.makedirs(a)
    os.makedirs(b)
    # 先造假：两处各有一份老版本留下的重复菜单
    for d in (a, b):
        t = os.path.join(d, I.MENU_DIR)
        os.makedirs(t)
        with open(os.path.join(t, "AsrSubs.lua"), "w", encoding="utf-8") as f:
            f.write("-- old\n")

    base = os.path.join(tmp, "AsrSubs")
    os.makedirs(base, exist_ok=True)
    # 再造假：老版本留下的中文门面（junction → base）
    legacy = base + "字幕工具"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", legacy, base],
                       capture_output=True)
    if r.returncode != 0:
        probs.append("造测试用 junction 失败（后续检查无意义）")

    orig = I.resolve_script_dirs
    I.resolve_script_dirs = lambda: [a, b]
    try:
        inst = I.Installer(base, False, True, lambda m: None,
                           lambda p, t: None, threading.Event())
        inst.deploy()
    except Exception as e:                       # payload 缺失之类，别静默过
        probs.append("deploy 抛异常：%s: %s" % (type(e).__name__, e))
    finally:
        I.resolve_script_dirs = orig

    for d, want in ((a, True), (b, False)):
        t = os.path.join(d, I.MENU_DIR)
        has = os.path.isfile(os.path.join(t, "AsrSubs.lua"))
        if has != want:
            probs.append("%s 应该有菜单=%s，实际=%s" % (t, want, has))
    # 只往第一个写，且写的是真脚本（不是那句 "-- old"）
    p0 = os.path.join(a, I.MENU_DIR, "AsrSubs.lua")
    if os.path.isfile(p0) and os.path.getsize(p0) < 10000:
        probs.append("第一处写的不是真脚本（只有 %d 字节）" % os.path.getsize(p0))

    # 中文门面：必须被摘掉，而且安装目录本身不能少东西
    if os.path.isdir(legacy):
        probs.append("旧版中文门面没被摘掉：%s" % legacy)
    if not os.path.isfile(os.path.join(base, "engine", "AsrSubs.lua")):
        probs.append("摘门面时把安装目录搞坏了（engine 里没有脚本）")

    if os.path.isdir(legacy):                    # 兜底：别让临时目录残留链接
        try:
            os.rmdir(legacy)
        except OSError:
            pass
    shutil.rmtree(tmp, ignore_errors=True)
    return probs


def report(label, mode, probs):
    print("== %s ==" % label)
    print("  mode=%s（same=原地升级 / cross=换目录或跨盘）" % mode)
    for p in probs:
        print("  FAIL " + p)
    return len(probs)


def main():
    tmp = tempfile.mkdtemp(prefix="asr_mig_")
    fails = 0
    try:
        # ① 同盘换目录（os.rename 快路径，最接近 D:\AsrSubs → D:\AsrSubs 之外的场景）
        m, probs = run_case(os.path.join(tmp, "legacy"),
                            os.path.join(tmp, "AsrSubs"), "same_drive")
        fails += report("同盘换目录", m, probs)

        # ② 原地升级：装到老位置本身（base == 老根）。这是 C 盘老用户
        #    选「就装在 D:\AsrSubs」时的路径，最容易踩到"自己搬自己"。
        m3, probs3 = run_case(os.path.join(tmp, "legacy3"),
                              os.path.join(tmp, "legacy3"), "inplace",
                              expect_kept=False)
        fails += report("原地升级", m3, probs3)

        # ③ 跨卷（复制 → 校验 → 删源）。D 盘不存在就跳过。
        if os.path.isdir("D:\\"):
            base2 = os.path.join("D:\\", "_asr_mig_test")
            shutil.rmtree(base2, ignore_errors=True)
            m2, probs2 = run_case(os.path.join(tmp, "legacy2"), base2, "cross")
            fails += report("跨卷", m2, probs2)
            shutil.rmtree(base2, ignore_errors=True)
        else:
            print("== 跨卷 == 跳过（没有 D 盘）")

        # ④ 真机形态 + 真机路径：<工具>\models 是指向 <根>\models 的 junction，
        #    且老根在 C:、新根在 D:（跨卷 → 走 copytree，不是 rename）。
        #    ★ 这条最容易漏：copytree 跳过重解析点，如果先搬 <工具>，
        #      模型真身就留在老根里，新装看着"少了模型"，白重下 3.3 GB。
        #      也必须跨卷才测得出来 —— 同卷 rename 会把 junction 一起搬走，
        #      旧写法照样能过，属于"假通过"。
        if os.path.isdir("D:\\"):
            base4 = os.path.join("D:\\", "_asr_mig_jtest")
            shutil.rmtree(base4, ignore_errors=True)
            m4, probs4 = run_case(os.path.join(tmp, "legacy4"), base4,
                                  "junction", junction_models=True)
            fails += report("模型 junction（跨卷）", m4, probs4)
            shutil.rmtree(base4, ignore_errors=True)
        else:
            print("== 模型 junction == 跳过（没有 D 盘，测不出跨卷路径）")

        # ⑤ 任意盘 / 任意目录：安装目录既不在盘根，也不再叫 AsrSubs
        #    （E:\Tools\Custom\AsrPro 这种）。布局必须仍然按 <base>\engine 拼。
        m5, probs5 = run_case(os.path.join(tmp, "legacy5"),
                              os.path.join(tmp, "Tools", "Custom", "AsrPro"),
                              "custom")
        fails += report("自定义安装目录", m5, probs5)

        # ⑥ 安装位置校验规则（自定义目录放行 / 中文·盘根·UNC 拦下）
        fails += report("安装位置校验", "-", check_base_rules())

        # ⑦ 菜单只写一份（否则 Resolve 菜单里出现两个 AsrSubs）
        fails += report("菜单只写一份", "-", check_single_menu())

        print("")
        print("全部通过" if not fails else "失败 %d 项" % fails)
        return 0 if not fails else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
