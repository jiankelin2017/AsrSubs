# -*- coding: utf-8 -*-
r"""AsrSubs 文档防腐化巡检（默认纯只读，零外部命令）。

用法（在项目根执行）：
    py -3.12 check_docs.py                # 文档体系检查，只读
    py -3.12 check_docs.py --gates        # 追加三道只读构建闸门
    py -3.12 check_docs.py --strict       # WARN 也算失败（CI / 提交前用）
    py -3.12 check_docs.py --json         # 机器可读输出
    py -3.12 check_docs.py --quiet        # 只打印汇总与异常

退出码：0 = 无 FAIL（--strict 时还要求无 WARN）；1 = 有 FAIL；2 = 脚本自身异常。

设计约束：
  * 放项目根而非 01_源码\tools\ —— verify_all.py 的语法检查会扫
    tools\*.py，新增文件会改变「Python 语法（N 个文件）」的验收计数。
  * 默认不调用任何外部脚本、不写任何文件，便于 PR / 计划任务安全调用。
  * 检查口径与 03_文档\INDEX.md「五、核对规则」、_TEMPLATE.md「防腐化自检」一致。
  * 事实性结论只做「状态与文件是否自洽」的判断，不判断技术结论对错
    —— 后者必须人工复核，脚本不臆造。
"""

__version__ = "1.0.3"

import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(HERE, "03_文档")
SRC = os.path.join(HERE, "01_源码")
INDEX = os.path.join(DOC, "INDEX.md")
ARCHIVE = os.path.join(DOC, "archive")
ENGINE_VERSION = r"D:\AsrSubs\engine\version.txt"

# 分类目录 -> 允许的文件名（统一命名：类型-主题.md）
CLASS_DIRS = {
    "design": r"^design-[a-z0-9-]+\.md$",
    "guides": r"^guide-[a-z0-9-]+\.md$",
    "reference": r"^reference-[a-z0-9-]+\.md$",
    # ADR 主题名可含三段式版本号（adr-0003-version-reset-1.0.0.md），故允许点号
    "adr": r"^adr-\d{4}-[a-z0-9.\-]+\.md$",
}
ROOT_FILES = {"INDEX.md", "CHANGELOG.md", "_TEMPLATE.md", "使用说明.txt"}
ROOT_DIRS = {"design", "guides", "reference", "adr", "archive"}

CORE_SECTIONS = [
    "## 一句话摘要",
    "## 当前有效结论",
    "## 关键变更",
    "## 操作 / 参考细节",
    "## 失效内容说明",
]
FM_REQUIRED = ["title", "status", "version", "last_update", "applies_to", "maintainer"]

# 事实性过时表述（不含「合法的未完成待办」，例如真机验证）
BANNED = [
    "待归档", "原件仍在原位", "建议归档", "归档动作待",
    "16 通过", "1 项失败", "尚未部署", "未部署到 Resolve",
    "部署一致」项失败", "部署一致」失败",
    "engine 仍是 1.1.3", "引擎全套 1.1.3", "引擎全套仍是 1.1.3",
    "引擎全套未升级", "菜单脚本 1.2.0、引擎全套 1.1.3",
]
# 有意保留的历史快照 / 失效说明，命中即豁免
EXEMPT = ["历史", "快照", "已作废", "早间", "部署前", "旧数字", "之前", "当时", "失效", "旧结论"]

BAD_NAME = re.compile(r"final|副本|_v\d|拷贝|新建文件夹", re.I)
# applies_to 允许不带代码版本号的已知例外（体系自身文件）
APPLIES_EXCEPTIONS = {
    "INDEX.md", "CHANGELOG.md", "_TEMPLATE.md",
}

GATES = [
    ("Lua 单文件", "build_lua.py"),
    ("版本号", "sync_version.py"),
    ("payload", "sync_payload.py"),
]


def read(path):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
            return f.read().replace("\r\n", "\n")
    except OSError:
        return ""


def front_matter(text):
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) > 2 else ""


def rel(path):
    try:
        return os.path.relpath(path, HERE).replace("\\", "/")
    except ValueError:
        return path


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, group, msg, detail=None):
        self.items.append({"level": level, "group": group, "msg": msg,
                           "detail": detail or []})

    def count(self, level):
        return sum(1 for i in self.items if i["level"] == level)


def check_index(rep):
    """INDEX 状态列与文件实存是否自洽。"""
    lines = read(INDEX).split("\n")
    total, bad = 0, []
    for ln in lines:
        if not ln.startswith("|"):
            continue
        m = re.search(r"`([^`]+)`", ln)
        if not m:
            continue
        p = m.group(1)
        if "..." in p or not re.search(r"\.(md|txt|png|exe|lua|py|iss|bat)$", p):
            continue
        if "已存在" in ln or "已归档" in ln:
            expect = True
        elif "不存在" in ln:
            expect = False
        else:
            continue
        full = os.path.normpath(os.path.join(DOC, p.replace("\\", "/")))
        total += 1
        if os.path.isfile(full) != expect:
            bad.append("%s -> %s（期望%s）" % (p, "存在" if os.path.isfile(full) else "不存在",
                                              "存在" if expect else "不存在"))
    if bad:
        rep.add("FAIL", "INDEX 实存 / 误标", "%d 项不自洽（共 %d 项）" % (len(bad), total), bad)
    else:
        rep.add("PASS", "INDEX 实存 / 误标", "%d 项全部自洽" % total)

    # 同一主题不允许两行 Active 并存
    seen, dup = {}, []
    for n, ln in enumerate(lines, 1):
        if not ln.startswith("|") or "Active" not in ln or "---" in ln:
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        topic = cells[0]
        if topic in ("主题", ""):
            continue
        if topic in seen:
            dup.append("%s（见第 %d 行与第 %d 行）" % (topic, seen[topic], n))
        else:
            seen[topic] = n
    if dup:
        rep.add("FAIL", "同主题唯一性", "%d 组重复 Active 主题" % len(dup), dup)
    else:
        rep.add("PASS", "同主题唯一性", "%d 个 Active 主题无重复" % len(seen))


def check_root_whitelist(rep):
    """主目录只留当前版：越界文件即防复辟报警。"""
    try:
        entries = os.listdir(DOC)
    except OSError as e:
        rep.add("FAIL", "主目录白名单", "无法读取 03_文档：%s" % e)
        return
    files = [e for e in entries if os.path.isfile(os.path.join(DOC, e))]
    dirs = [e for e in entries if os.path.isdir(os.path.join(DOC, e))]
    extra_f = [f for f in files if f not in ROOT_FILES]
    extra_d = [d for d in dirs if d not in ROOT_DIRS]
    detail = ["越界文件：" + ", ".join(extra_f)] if extra_f else []
    detail += ["越界目录：" + ", ".join(extra_d)] if extra_d else []
    if detail:
        rep.add("FAIL", "主目录白名单", "%d 文件 / %d 目录越界" % (len(extra_f), len(extra_d)), detail)
    else:
        rep.add("PASS", "主目录白名单", "%d 个文件 + %d 个目录，无越界" % (len(files), len(dirs)))


def check_naming(rep):
    bad = []
    total = 0
    for d, pattern in CLASS_DIRS.items():
        for f in sorted(glob.glob(os.path.join(DOC, d, "*.md"))):
            total += 1
            name = os.path.basename(f)
            if not re.match(pattern, name) or BAD_NAME.search(name):
                bad.append("%s\\%s" % (d, name))
    if bad:
        rep.add("FAIL", "统一命名", "%d / %d 篇命名不合规" % (len(bad), total), bad)
    else:
        rep.add("PASS", "统一命名", "%d 篇均符合 类型-主题.md" % total)


def classified_docs():
    out = []
    for d in CLASS_DIRS:
        out += sorted(glob.glob(os.path.join(DOC, d, "*.md")))
    return sorted(out)


def check_front_matter(rep):
    aux = [os.path.join(HERE, "README.md"), INDEX,
           os.path.join(DOC, "CHANGELOG.md"), os.path.join(DOC, "_TEMPLATE.md"),
           os.path.join(HERE, "NOTICE.md")]
    # NOTICE.md 是纯法务声明，按约定不带 front-matter，不纳入检查
    aux = aux[:-1]
    files = classified_docs() + aux
    bad, empty_maintainer = [], []
    for f in files:
        fm = front_matter(read(f))
        miss = [k for k in FM_REQUIRED if not re.search(r"^" + k + r":", fm, re.M)]
        if miss:
            bad.append("%s 缺 %s" % (rel(f), ", ".join(miss)))
        m = re.search(r"^maintainer:\s*(.*)$", fm, re.M)
        # ★ v1.0.3：不再对维护人的具体取值做字面硬校验。
        #   署名由作者自行填写，巡检只保证字段存在且非空，
        #   避免把某个具体名字写死在巡检脚本里（个人信息清理）。
        if m is not None and not m.group(1).strip():
            empty_maintainer.append("%s 维护人为空" % rel(f))
    if bad or empty_maintainer:
        rep.add("FAIL", "front-matter", "%d 篇缺字段 / %d 篇维护人为空" %
                (len(bad), len(empty_maintainer)), bad + empty_maintainer)
    else:
        rep.add("PASS", "front-matter", "%d 个文件六字段齐全、维护人非空" % len(files))


def check_sections(rep):
    files = classified_docs()
    bad = []
    for f in files:
        s = read(f)
        miss = [h for h in CORE_SECTIONS if h not in s]
        if miss:
            bad.append("%s 缺 %s" % (rel(f), ", ".join(miss)))
    if bad:
        rep.add("FAIL", "五段结构", "%d / %d 篇缺段" % (len(bad), len(files)), bad)
    else:
        rep.add("PASS", "五段结构", "%d 篇五段齐全（ADR 不豁免）" % len(files))


def check_archive(rep):
    texts = [f for f in glob.glob(os.path.join(ARCHIVE, "*"))
             if f.lower().endswith((".md", ".txt")) and os.path.basename(f) != "README.md"]
    nohead = [os.path.basename(f) for f in texts if "已废弃" not in read(f)[:400]]
    if nohead:
        rep.add("FAIL", "归档件文首标注", "%d 个文本缺「已废弃，见」" % len(nohead), nohead)
    else:
        rep.add("PASS", "归档件文首标注", "%d 个文本全部已标注" % len(texts))

    readme = os.path.join(ARCHIVE, "README.md")
    if not os.path.isfile(readme):
        rep.add("FAIL", "归档清单", "archive\\README.md 不存在")
        return
    txt = read(readme)
    pngs = [os.path.basename(p) for p in glob.glob(os.path.join(ARCHIVE, "*.png"))]
    unlisted = [p for p in pngs if p not in txt]
    if unlisted:
        rep.add("WARN", "归档清单", "%d 张图片未登记在 archive\\README.md" % len(unlisted), unlisted)
    else:
        rep.add("PASS", "归档清单", "README.md 存在，%d 张图片均已登记" % len(pngs))


def check_banned(rep):
    files = [os.path.join(HERE, "README.md"), INDEX,
             os.path.join(DOC, "CHANGELOG.md"), os.path.join(DOC, "_TEMPLATE.md"),
             os.path.join(HERE, "NOTICE.md"),
             os.path.join(HERE, "04_安装包", "README_说明.txt")] + classified_docs()
    hits = []
    for f in files:
        if not os.path.isfile(f):
            continue
        is_template = os.path.basename(f) == "_TEMPLATE.md"
        for n, ln in enumerate(read(f).split("\n"), 1):
            # 模板 / 规程会以「禁用词」本身举例说明规则，这类条文不是过时表述
            stripped = ln.lstrip()
            if is_template and (stripped.startswith("|") or re.match(r"^\d+\.", stripped)):
                continue
            if any(b in ln for b in BANNED) and not any(x in ln for x in EXEMPT):
                hits.append("%s:%d  %s" % (rel(f), n, ln.strip()[:100]))
    if hits:
        rep.add("FAIL", "过时状态措辞", "%d 处事实性过时表述（非历史快照）" % len(hits), hits[:30])
    else:
        rep.add("PASS", "过时状态措辞", "%d 个文件无过时事实表述" % len(files))


def check_version_consistency(rep):
    ver = read(os.path.join(SRC, "version.txt")).strip()
    if not re.match(r"^\d+\.\d+\.\d+$", ver):
        rep.add("FAIL", "版本一致性", "01_源码\\version.txt 不是三段式版本号：%r" % ver)
        return ver
    bad = []
    for f in classified_docs():
        name = os.path.basename(f)
        if name in APPLIES_EXCEPTIONS:
            continue
        fm = front_matter(read(f))
        m = re.search(r"^applies_to:\s*(.+)$", fm, re.M)
        if not m or ver not in m.group(1):
            bad.append("%s 的 applies_to 未含 %s" % (name, ver))
    if bad:
        rep.add("WARN", "版本一致性", "%d 篇 applies_to 与代码版本 %s 不同（需人工复核）" %
                (len(bad), ver), bad)
    else:
        rep.add("PASS", "版本一致性", "Active 文档 applies_to 均对应代码版本 %s" % ver)

    if os.path.isfile(ENGINE_VERSION):
        ev = read(ENGINE_VERSION).strip()
        if ev != ver:
            rep.add("FAIL", "engine 版本", "engine\\version.txt = %s，源码 = %s" % (ev, ver))
        else:
            rep.add("PASS", "engine 版本", "engine\\version.txt = %s，与源码一致" % ev)
    else:
        rep.add("WARN", "engine 版本", "未找到 %s（非开发机可忽略）" % ENGINE_VERSION)
    return ver


def check_index_registration(rep):
    """新增治理资产必须在 INDEX 登记，避免「文档存在但总表看不见」。"""
    txt = read(INDEX)
    required = ["guide-doc-maintenance.md", "check_docs.py"]
    missing = [n for n in required if n not in txt]
    if missing:
        rep.add("FAIL", "治理资产登记", "%d 项未登记在 INDEX.md" % len(missing), missing)
    else:
        rep.add("PASS", "治理资产登记", "机制文档与巡检脚本均已在 INDEX 登记")


def check_path_style(rep):
    r"""Windows 路径风格：盘符后应接反斜杠，命令行示例用 /d/ 这类 POSIX 写法。

    坑（2026-11-05 两次实测）：工具层传递 Bash / heredoc 命令时会把 D:\ 转成
    D:/，写回文档后路径形态与项目其余部分不一致。叙述里出现「盘符 + 正斜杠」
    即视为被转正。规则条文 / 坑记录 / 历史归档件不检查。
    """
    files = [os.path.join(HERE, "README.md"), INDEX,
             os.path.join(DOC, "CHANGELOG.md"), os.path.join(DOC, "_TEMPLATE.md"),
             os.path.join(HERE, "NOTICE.md"),
             os.path.join(HERE, "04_安装包", "README_说明.txt")]
    files += classified_docs() + [os.path.join(ARCHIVE, "README.md")]
    exempt = ["坑", "示例", "误报", "规则", "禁用", "还原", "历史", "快照"]
    hits = []
    for f in files:
        if not os.path.isfile(f):
            continue
        for n, ln in enumerate(read(f).split("\n"), 1):
            # 负向前瞻排除「C:/D:」这类盘符列举（不是路径）
            if re.search(r"\b[A-Za-z]:/(?![A-Za-z]:)", ln) and not any(x in ln for x in exempt):
                hits.append("%s:%d  %s" % (rel(f), n, ln.strip()[:100]))
    if hits:
        rep.add("FAIL", "Windows 路径风格", "%d 处盘符后接正斜杠（疑被工具层转正）" % len(hits), hits[:20])
    else:
        rep.add("PASS", "Windows 路径风格", "%d 个文件无被转正的盘符路径" % len(files))


def check_gates(rep):
    for label, script in GATES:
        cmd = [sys.executable, os.path.join(SRC, "tools", script), "--check"]
        try:
            r = subprocess.run(cmd, cwd=SRC, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=90)
            out = (r.stdout or b"").decode("utf-8", "replace").strip().split("\n")[-1]
        except Exception as e:
            rep.add("FAIL", "构建闸门 · " + label, "调用失败：%s" % e)
            continue
        if r.returncode == 0:
            rep.add("PASS", "构建闸门 · " + label, out[:110])
        else:
            rep.add("FAIL", "构建闸门 · " + label, "退出码 %d：%s" % (r.returncode, out[:110]))


def main():
    ap = argparse.ArgumentParser(description="AsrSubs 文档防腐化巡检（只读）")
    ap.add_argument("--gates", action="store_true", help="追加三道只读构建闸门")
    ap.add_argument("--strict", action="store_true", help="WARN 也算失败")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--quiet", action="store_true", help="只打印汇总与异常")
    ap.add_argument("--version", action="version", version="check_docs.py " + __version__)
    a = ap.parse_args()

    rep = Report()
    check_index(rep)
    check_root_whitelist(rep)
    check_naming(rep)
    check_front_matter(rep)
    check_sections(rep)
    check_archive(rep)
    check_banned(rep)
    check_path_style(rep)
    check_version_consistency(rep)
    check_index_registration(rep)
    if a.gates:
        check_gates(rep)

    p = rep.count("PASS")
    f = rep.count("FAIL")
    w = rep.count("WARN")
    failed = f > 0 or (a.strict and w > 0)

    if a.json:
        print(json.dumps({"version": __version__, "pass": p, "fail": f, "warn": w,
                          "ok": not failed, "items": rep.items},
                         ensure_ascii=False, indent=2))
    else:
        print("=" * 66)
        print(" AsrSubs 文档防腐化巡检  v%s  （只读）" % __version__)
        print("=" * 66)
        for i in rep.items:
            if a.quiet and i["level"] == "PASS":
                continue
            print("[%-4s] %-20s %s" % (i["level"], i["group"], i["msg"]))
            for d in i["detail"][:12]:
                print("         - " + d)
            if len(i["detail"]) > 12:
                print("         - ... 另有 %d 条" % (len(i["detail"]) - 12))
        print("-" * 66)
        print(" 汇总：PASS %d | FAIL %d | WARN %d | 结果 %s" %
              (p, f, w, "不通过" if failed else "通过"))
        print(" 提示：事实结论的正确性不由本脚本判定，仍需人工复核。")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("check_docs.py 自身异常：%s: %s" % (type(exc).__name__, exc))
        sys.exit(2)
