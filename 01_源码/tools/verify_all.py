# -*- coding: utf-8 -*-
r"""一键验收：把发版前该查的全查一遍，输出一张通过/失败清单。

用法（在 01_源码 目录下）：
    python tools/verify_all.py

为什么要有它：
  这套工程现在有 5 个测试脚本、3 个一致性检查（版本 / payload / 部署），
  发版前靠手工一条条敲，漏一条就可能把「本地是新的、装出来是旧的」发出去。
  这个脚本只做两件事：跑、汇总。任何一条红了就退出码非 0。

检查项：
  1. Python 语法     全部 .py 过一遍 py_compile（含 02_构建与安装器\installer.py）
  2. 版本号一致      version.txt ↔ iss ↔ installer.py ↔ qwen_version.py ↔ 使用说明.txt
  3. Lua 构建        build_lua.py 重新拼一次（顺带确认 @VERSION@ 都替换掉了）
  4. Lua 编译        lupa 真虚拟机编译（查 200 local 上限，静态分析查不出）
  5. Lua 语法        luaparse 静态检查（可选：依赖 node_modules，缺了就跳过）
  6. 单元测试        test_corrections / test_hotwords / test_r1_offset /
                     test_r2_relang / test_subtrack_info / test_diag_text
  7. payload 一致    02_构建与安装器\payload 与 01_源码 逐字节比对
  8. 部署一致        ProgramData 脚本目录、安装引擎目录（D:\AsrSubs\engine 或
                      D:\AsrSubs）里的 lua 与源码一致（比对时归一化 INSTALL_ROOT 行，
                      并校验 paths.json 指向自己）

★ 需要 node 的几项（4/5/6 里的 test_diag_text）找不到 node 时会标「跳过」，
  不算失败 —— 验收脚本本身不该因为环境缺件就把人卡住。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

# ★ 控制台是 GBK 时，日志里只要出现一个 ✓ / … / emoji 就会 UnicodeEncodeError
#   直接崩在打印上（实测遇到过：验收跑到一半抛异常，看着像代码坏了）。
#   统一按 UTF-8 输出，编不出来的字符替换掉，别让它掩盖真正的结论。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                        # …\01_源码
PARENT = os.path.dirname(ROOT)                      # …\AsrSubs
PY = sys.executable

# 部署副本：必须和源码完全一致，否则用户点开的是旧版。
# ★ 3.0 起引擎在 <根>\engine（默认 D:\AsrSubs\engine）；
#   2.x 的 D:\AsrSubs 只在真的存在时才核对 —— 迁移期两种机器都要能验收。
# ★ 菜单目录 3.0 叫「AsrSubs」（ASCII），2.x 叫「AsrSubs」；哪个在就核对哪个。
UTILITY_DIR = os.path.join(
    os.environ.get("ProgramData", r"C:\ProgramData"),
    "Blackmagic Design", "DaVinci Resolve", "Fusion", "Scripts", "Utility")

# ★ Resolve 在 Windows 上会从这个两处之一加载 Fusion 脚本（ProgramData 是"给所有用户"，
#   AppData 是"给当前用户"）。安装器两处都写，验收也必须两处都核对，
#   否则只改了其中一份就会出现"菜单里有，但打开是旧版"。
UTILITY_DIRS = [UTILITY_DIR]
_r = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming")),
    "Blackmagic Design", "DaVinci Resolve", "Support", "Fusion", "Scripts", "Utility")
if os.path.normcase(_r) != os.path.normcase(UTILITY_DIR):
    UTILITY_DIRS.append(_r)

# ★ 两代菜单名：3.x 是 AsrSubs（ASCII），2.x 是 Qwen ASR（旧中文名）。
#   ★★ 这里曾经写成 ["AsrSubs", "AsrSubs"] —— 去标识化时把旧名也替换成了
#      AsrSubs，于是同一个目录被数了两遍，check_deploy 每次都误报
#      「菜单重复」，验收永远过不了。恢复成两个真正不同的名字后，
#      既能识别老目录残留（同时有两者 = 没清干净），也不会自己撞自己。
MENU_DIRS = ["AsrSubs", "Qwen ASR"]

INSTALL_ENGINES = [
    r"D:\AsrSubs\engine",       # 3.0 新布局
    r"D:\AsrSubs",             # 2.x 老布局（迁移期保留）
]


def resolve_luas():
    out = []
    for base in UTILITY_DIRS:
        for d in MENU_DIRS:
            p = os.path.join(base, d, "AsrSubs.lua")
            if os.path.isfile(p):
                out.append(p)
    return out


def deploy_targets():
    out = resolve_luas()
    for d in INSTALL_ENGINES:
        p = os.path.join(d, "AsrSubs.lua")
        if os.path.isfile(p):
            out.append(p)
    return out

NODE_MODULES = os.environ.get(
    "QWEN_NODE_MODULES",
    os.path.join(os.path.expanduser("~"), ".workbuddy-ai", "binaries",
                 "node", "workspace", "node_modules"))

RESULTS = []          # [(是否通过, 名称, 说明)]


def ok(name, note=""):
    RESULTS.append((True, name, note))


def bad(name, note=""):
    RESULTS.append((False, name, note))


def skip(name, note=""):
    RESULTS.append((None, name, note))


def run(args, cwd=None, env=None):
    """返回 (退出码, stdout, stderr)。

    ★ stdout 和 stderr 要分开：jieba 启动时会往 stderr 打
      「Building prefix dict…」这类噪音，合并以后"最后一行"就取不到真正的结论了。
    """
    e = dict(os.environ)
    # ★ v1.0.1：强制子进程用 UTF-8 输出。
    #   中文 Windows 的控制台是 GBK，测试脚本里的「✓」和部分中文会直接抛
    #   UnicodeEncodeError，把「测试通过」误判成失败 —— 2026-11-01 的验收
    #   就因此红过一项（R2），实际加 UTF8 后是全绿的。
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    if env:
        e.update(env)
    p = subprocess.run(args, cwd=cwd or ROOT, env=e,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def last_line(s):
    lines = [l for l in (s or "").strip().splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def find_node():
    for c in (os.environ.get("QWEN_NODE"),
              shutil.which("node"),
              os.path.expanduser(
                  r"~\workbuddy-ai\binaries\node\versions\22.22.2-6\node.exe")):
        if c and os.path.isfile(c):
            return c
    return None


def _is_reparse(p):
    """是不是 junction / 符号链接（os.path.islink 对 junction 返回 False）。"""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ★ 部署副本不是源码的逐字节拷贝：installer.deploy() 会把
#   `local INSTALLED_ROOT = "..."  --<<INSTALL_ROOT>>` 这行改写成
#   `local INSTALLED_ROOT = "<真实安装目录>"`（去掉标记、填入本机路径）。
#   直接比 md5 必然判"不一致"，而且这个"不一致"是正常的 —— 但真把这条检查
#   放宽成"不比了"，就又回到"本地是新的、装出来是旧的"没人管。
#   折中办法：把这一行归一成占位符再比，其它任何一个字节不同照样报错。
_LUA_ROOT_RE = re.compile(
    r'(?m)^([ \t]*local[ \t]+INSTALLED_ROOT[ \t]*=[ \t]*)"[^"]*"'
    r'(?:[ \t]*--[ \t]*<<INSTALL_ROOT>>)?[ \t]*$')


def lua_fingerprint(path):
    """返回 (归一化后的 md5, 副本里的 INSTALLED_ROOT 值或 None)。"""
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", "replace")
    root = None
    m = _LUA_ROOT_RE.search(text)
    if m:
        line = m.group(0)
        q = line.find('"')
        if q >= 0:
            end = line.find('"', q + 1)
            root = line[q + 1:end] if end > q else None
        text = _LUA_ROOT_RE.sub(lambda mm: mm.group(1) + '"<INSTALL_ROOT>"', text)
    return hashlib.md5(text.encode("utf-8", "replace")).hexdigest(), root


# ── 1. Python 语法 ────────────────────────────────────────────────
def check_py_compile():
    import py_compile
    targets = [os.path.join(ROOT, n) for n in sorted(os.listdir(ROOT))
               if n.endswith(".py")]
    installer = os.path.join(PARENT, "02_构建与安装器", "installer.py")
    if os.path.isfile(installer):
        targets.append(installer)
    fails = []
    for p in targets:
        try:
            py_compile.compile(p, doraise=True,
                               cfile=os.path.join(HERE, "_pyc_tmp.pyc"))
        except Exception as e:
            fails.append("%s：%s" % (os.path.basename(p), e))
    tmp = os.path.join(HERE, "_pyc_tmp.pyc")
    if os.path.isfile(tmp):
        os.remove(tmp)
    if fails:
        bad("Python 语法（%d 个文件）" % len(targets), "；".join(fails[:3]))
    else:
        ok("Python 语法（%d 个文件）" % len(targets))


# ── 2. 版本号 ─────────────────────────────────────────────────────
def check_version():
    rc, out, err = run([PY, os.path.join(HERE, "sync_version.py"), "--check"])
    if rc == 0:
        ok("版本号一致", "version.txt 已同步到 5 处（含 AsrSubs.lua）")
    else:
        bad("版本号一致", (out + err).strip().replace("\n", " ")[:160])


# ── 3. Lua 构建 ───────────────────────────────────────────────────
def check_build():
    rc, out, err = run([PY, os.path.join(HERE, "build_lua.py")])
    if rc == 0 and os.path.isfile(os.path.join(ROOT, "AsrSubs.lua")):
        ok("Lua 构建", last_line(out))
    else:
        bad("Lua 构建", last_line(out + err)[:160])


# ── 4/5. Lua 静态检查 + 真机编译 ──────────────────────────────────
def check_lua_lupa():
    """★ v4.0.9：用 lupa（已装）做 Lua 编译校验，替代 fengari。

    为什么必须换：fengari 走 npm 依赖（node_modules/），而本机的
    node_modules 从未安装 → `require('fengari')` 直接抛错 → 这一项
    永远失败，把整个验收链变成"必红"。lupa 是 Python 扩展，pip 装好
    就能用，不依赖 node 生态。

    ★ lupa 的 Lua 版本是 5.4，Resolve 内嵌的是 5.3；5.4 的编译器在
      "too many local variables" 这类限制上与 5.3 一致（都是 200），
      正好覆盖 fengari 原本要查的那个点（BuildUI 是个巨型函数，
      每次加控件都在逼近这条线）。
    """
    try:
        import lupa  # noqa: F401
    except ImportError:
        skip("Lua 编译（lupa）", "未装 lupa（pip install lupa）")
        return
    lua_path = os.path.join(ROOT, "AsrSubs.lua")
    if not os.path.isfile(lua_path):
        bad("Lua 编译（lupa）", "找不到 AsrSubs.lua")
        return
    try:
        import io
        import lupa
        rt = lupa.LuaRuntime()
        rt.compile(io.open(lua_path, encoding="utf-8").read())
        ok("Lua 编译（lupa）", "AsrSubs.lua 编译通过")
    except Exception as e:
        bad("Lua 编译（lupa）", str(e)[:160])


def check_lua(node):
    # ★ v4.0.9：luaparse（JS 静态检查）依赖未安装的 node_modules，会永远失败。
    #   改为：先用 lupa 做真编译（能覆盖 fengari 原本的"200 local 上限"检查点），
    #   再尝试 luaparse（若依赖可用则加一层语法静态检查，不可用就跳过）。
    check_lua_lupa()
    if not node:
        skip("Lua 语法（luaparse）", "未找到 node")
        return
    env = {"NODE_PATH": NODE_MODULES}
    lua = os.path.join(ROOT, "AsrSubs.lua")
    if not os.path.isdir(NODE_MODULES):
        skip("Lua 语法（luaparse）",
             "node_modules 未安装（luaparse/fengari），已由 lupa 覆盖编译校验")
        return
    rc, out, err = run([node, os.path.join(HERE, "check_lua.js"), lua], env=env)
    (ok if rc == 0 else bad)("Lua 语法（luaparse）",
                             last_line(out) if rc == 0 else last_line(err))


# ── 6. 单元测试 ───────────────────────────────────────────────────
def check_tests(node):
    # ★ v4.0.9 整理：补齐 v4.0.7~v4.0.9 新增的三套关键测试。
    #   之前这里只有纠错表/热词两个老单测，导致发版验收【跑不到最关键的落轨测试】——
    #   R1（粗剪区间校准）、R2（字幕更新删旧重挂）、字幕轨清单（v4.0.9）全都漏网。
    for name, cmd in (
        ("纠错表单测", [PY, os.path.join(HERE, "test_corrections.py")]),
        ("热词单测", [PY, os.path.join(HERE, "test_hotwords.py")]),
        # ↓ v4.0.9 新增：落轨与字幕轨的三套核心回归
        ("R1 粗剪区间校准", [PY, os.path.join(HERE, "test_r1_offset.py")]),
        ("R2 字幕重挂+落轨回收", [PY, os.path.join(HERE, "test_r2_relang.py")]),
        ("字幕轨清单", [PY, os.path.join(HERE, "test_subtrack_info.py")]),
        # ↓ v4.1.0 新增：落轨顺序铁律 + SetName 直改护栏
        ("落轨顺序/API优先", [PY, os.path.join(HERE, "test_land_order.py")]),
        # ↓ v1.2.0 新增：「开始生成」三条前置条件 + fail-open 契约
        ("开始按钮前置条件", [PY, os.path.join(HERE, "test_can_start.py")]),
    ):
        rc, out, err = run(cmd)
        # ★ v4.8.24：取到"跳过"字样时不要再记成【通过】。
        #   test_hotwords.py 找不到样片 SRT 时只打印"跳过"并返回 0，
        #   原来会被算作通过 —— 等于用一个没跑的测试冒充绿灯。
        line = last_line(out) if rc == 0 else last_line(err)
        if rc == 0 and ("跳过" in line or "SKIP" in line.upper()):
            skip(name, line)
            continue
        (ok if rc == 0 else bad)(name, line)

    # ★ v4.0.9：下面两个 JS 测试依赖 node_modules 里的 fengari。
    #   本机 node_modules 从未安装 → 直接跑必然抛 "Cannot find module 'fengari'"，
    #   会把验收链变成"必红"。因此统一：依赖缺失就【跳过】并说明原因，
    #   而不是当成失败（验收脚本不该因为环境缺件就把人卡住 —— 见文件头说明）。
    if not node:
        skip("诊断文本测试", "未找到 node")
        return
    if not os.path.isdir(NODE_MODULES):
        skip("诊断文本测试", "node_modules 未安装（需 fengari）")
        skip("粗剪门槛/撤销逻辑", "node_modules 未安装（需 fengari）")
        return
    rc, out, err = run([node, os.path.join(HERE, "test_diag_text.js"),
                        os.path.join(ROOT, "AsrSubs.lua")],
                       env={"NODE_PATH": NODE_MODULES})
    (ok if rc == 0 else bad)("诊断文本测试",
                             last_line(out) if rc == 0 else last_line(err))

    # ★ v1.1.1：关窗不该切页。
    #   用户实测：打开 AsrSubs、什么都不做、关掉，DaVinci 自己跳到交付页。
    #   根因是 Cleanup→CleanupRender 无条件删渲染预设（会切页）+ ReturnToPrevPage
    #   只看旧 prevPage。这条用例锁死"没渲染就一次 API 都不许调"。
    close_py = os.path.join(HERE, "test_close_page.py")
    if os.path.isfile(close_py):
        rc, out, err = run([PY, close_py])
        (ok if rc == 0 else bad)("关窗不切页",
                                 last_line(out) if rc == 0 else last_line(out + err))
    else:
        skip("关窗不切页", "没有 test_close_page.py")

    # ★ v4.0.2：粗剪门槛 / 撤销分流的真 Lua 逻辑回归（CutGate 四场景 + 撤销 bug）
    logic_js = os.path.join(HERE, "test_logic_v402.js")
    if os.path.isfile(logic_js):
        rc, out, err = run([node, logic_js, os.path.join(ROOT, "AsrSubs.lua")],
                           env={"NODE_PATH": NODE_MODULES})
        (ok if rc == 0 else bad)("粗剪门槛/撤销逻辑",
                                 last_line(out) if rc == 0 else last_line(out + err))
    else:
        skip("粗剪门槛/撤销逻辑", "没有 test_logic_v402.js")


# ── 6b. 老布局迁移 ────────────────────────────────────────────────
def check_migrate():
    # ★ 迁移是唯一会「搬走并删源」的代码，写错就是用户数据丢失。
    #   真机验证要 D:\AsrSubs 老布局 + 几分钟搬运，这里用假目录覆盖三条路径：
    #   同盘换目录 / 原地升级 / 跨卷。
    p = os.path.join(HERE, "test_migrate.py")
    if not os.path.isfile(p):
        skip("迁移逻辑", "没有 test_migrate.py")
        return
    rc, out, err = run([PY, p])
    (ok if rc == 0 else bad)("迁移逻辑",
                             last_line(out) if rc == 0 else last_line(out + err))


# ── 7. payload ───────────────────────────────────────────────────
def check_payload():
    rc, out, err = run([PY, os.path.join(HERE, "sync_payload.py"), "--check"])
    if rc == 0:
        ok("payload 一致", last_line(out))
    else:
        bad("payload 一致", (out + err).strip().replace("\n", " ")[:160])


# ── 8. 部署 ──────────────────────────────────────────────────────
def check_deploy():
    src = os.path.join(ROOT, "AsrSubs.lua")
    if not os.path.isfile(src):
        bad("部署一致", "源码里没有 AsrSubs.lua")
        return
    h, _src_root = lua_fingerprint(src)

    # ★★ 重复菜单检测（踩过：Resolve「脚本」菜单里出现两个 AsrSubs）★★
    #   Resolve 把 ProgramData、AppData\Support、Program Files 三处的
    #   Fusion\Scripts 全都扫进菜单。同一个菜单名出现在 >1 处，用户就看到
    #   两个一模一样的子菜单；同时存在 AsrSubs 和 AsrSubs 也一样（老版没清干净）。
    #   ★ 旧版本的验收只比 md5，两份都"一致"就通过了 —— 菜单重复根本测不出来。
    per_name = {}
    for base in UTILITY_DIRS:
        for name in MENU_DIRS:
            if os.path.isfile(os.path.join(base, name, "AsrSubs.lua")):
                per_name.setdefault(name, []).append(base)
    for name, bases in per_name.items():
        if len(bases) > 1:
            bad("部署一致", "菜单重复：同一个「%s」在 %d 个脚本目录里都有"
                            "（Resolve 菜单会显示两项）：%s"
                % (name, len(bases), "；".join(bases)))
            return
    if len(per_name) > 1:
        bad("部署一致", "菜单里同时有 %s → 应只剩 AsrSubs（老目录没清干净）"
            % " 和 ".join(sorted(per_name)))
        return

    targets = deploy_targets()
    if not targets:
        bad("部署一致", "没找到任何部署副本（菜单目录和安装目录都不存在）")
        return
    miss, diff, roots = [], [], []
    for p in targets:
        if not os.path.isfile(p):
            miss.append(p)
            continue
        th, troot = lua_fingerprint(p)
        if troot:
            roots.append(troot)
        if th != h:
            diff.append(p)
    if miss:
        bad("部署一致", "找不到：%s" % miss[0])
        return
    if diff:
        bad("部署一致", "与源码不一致：%s" % diff[0])
        return

    # 3.0 布局：装到 D:\AsrSubs 的机器必须带 paths.json，且 engine 指向自己。
    # ★ paths.json 是安装器生成的"目录名唯一事实来源"，它丢了就等于退回按
    #   目录名猜 —— 改名后最容易在这里静默退化。
    notes = []
    for d in INSTALL_ENGINES:
        if not os.path.isfile(os.path.join(d, "AsrSubs.lua")):
            continue
        # ★ 3.1 起取消中文门面：盘上只该有安装目录本身。
        #   老版本建的 <安装目录>字幕工具 是 0 字节 junction，升级时应该被
        #   摘掉；它还在就说明升级没走到那一步（用户会继续以为"有两份"）。
        link = os.path.dirname(d) + "字幕工具"
        if os.path.isdir(link) and _is_reparse(link):
            bad("部署一致", "旧版中文门面还在：%s（应已摘掉，只留 %s）"
                % (link, os.path.dirname(d)))
            return
        pj = os.path.join(d, "paths.json")
        if not os.path.isfile(pj):
            if d == r"D:\AsrSubs\engine":
                bad("部署一致", "新布局缺少 paths.json：%s" % pj)
                return
            continue
        try:
            with open(pj, encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError) as e:
            bad("部署一致", "paths.json 读不出来：%s" % e)
            return
        if os.path.normcase(info.get("engine", "")) != os.path.normcase(d):
            bad("部署一致", "paths.json 的 engine 与目录不符：%s" % info.get("engine"))
            return
        for key in ("runtime", "models", "data", "output", "logs", "cache"):
            if not info.get(key):
                bad("部署一致", "paths.json 缺 %s 字段" % key)
                return
        notes.append(os.path.basename(os.path.dirname(d)) + "/paths.json ✓")
    uniq = []
    for r in roots:
        if r not in uniq:
            uniq.append(r)
    if uniq:
        notes.append("引擎指向 " + "、".join(uniq))
    ok("部署一致", "%d 处部署副本与源码一致（%s%s）"
       % (len(targets), h[:8], ("；" + "、".join(notes)) if notes else ""))


def main():
    print("=" * 68)
    print(" AsrSubs 工程验收")
    print("=" * 68)
    print("项目根：%s" % PARENT)
    print("")

    node = find_node()
    check_py_compile()
    check_version()
    check_build()
    check_lua(node)
    check_tests(node)
    check_migrate()
    check_payload()
    check_deploy()

    print("")
    n_fail = 0
    for good, name, note in RESULTS:
        if good is True:
            mark = "[通过]"
        elif good is None:
            mark = "[跳过]"
        else:
            mark = "[失败]"
            n_fail += 1
        line = "  %s %-22s %s" % (mark, name, note)
        print(line.rstrip())

    print("")
    if n_fail:
        print("验收未通过：%d 项失败" % n_fail)
        return 1
    print("验收通过：%d 项全部通过%s"
          % (len(RESULTS),
             "（含 %d 项跳过）" % sum(1 for r in RESULTS if r[0] is None)
             if any(r[0] is None for r in RESULTS) else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
