# -*- coding: utf-8 -*-
"""常驻后台执行器 —— 取代 Lua 侧的 os.execute，彻底消除 CMD 窗口闪现。

背景
----
Resolve 的 Lua 里想启动外部进程只能用 os.execute('start "" /B ...')。
start /B 能让【目标程序】不弹窗，但 os.execute 本身要经 cmd.exe 解析，
cmd.exe 是控制台程序，被没有控制台的 GUI 进程（Resolve）拉起时 Windows
会新建一个控制台窗口 —— 哪怕命令瞬间结束也会「闪一下」。

之前每次点「开始生成字幕」要经历 探测 + 识别 两次 os.execute，
加上列目录/建目录的 io.popen，用户会看到好几次黑窗闪过。

解决办法
--------
本进程用 pythonw 启动一次（GUI 子系统，本身无窗口），之后常驻：
  · 扫 rt\\cmd.json（Lua 写命令）—— 用 mtime/size 变化触发，不是死轮询
  · 用 CREATE_NO_WINDOW 启动真正的任务脚本 —— 全程零窗口
  · 结果写 rt\\res.lua（Lua 用 loadfile 读，和别的数据通道一致）
  · 每 1 秒写 rt\\whb.lua 心跳，Lua 靠它判断本进程是否还活着
  · 持续写 rt\\pids.lua（还活着的子进程 PID），供精确 kill

这样一次会话最多只闪一次窗（拉起本进程时），且发生在工具窗口弹出前。

★ 兼容性：Lua 侧保留「worker 拉不起来就直接 os.execute」的兜底，
  所以即使本进程因为任何原因不可用，功能依然完整，只是会闪窗。

命令格式（rt\\cmd.json，UTF-8）：
    {"id": 7, "sess": "a1b2", "script": "resolve_bridge.py",
     "args": ["C:\\AsrSubs\\engine\\rt\\args.json"]}
    {"id": 8, "sess": "a1b2", "cmd": "ping"}   ← 保活（不启任何进程）
    {"id": 9, "sess": "a1b2", "cmd": "kill"}   ← 杀掉本进程启动的所有子任务
    {"id":10, "sess": "a1b2", "cmd": "quit"}   ← 退出本进程

★★ 为什么命令要带 sess（会话标识）：
  去重键原来是【只有 id】，而 Lua 每次运行脚本 App.S.cmdId 都从 0 重新开始。
  如果上一次运行只发过 1 条命令（last_id=1），新会话发的第一条命令 id 也是 1，
  会被当成重复命令【静默丢弃】—— Lua 那边干等 6 秒后回退 os.execute，
  于是"明明装了 worker 却还是闪黑窗"，而且极难排查。
  现在去重键是 (sess, id)，sess 是 Lua 每次启动脚本时新生成的随机串。

响应（rt\\res.lua，Lua chunk）：
    return { id = 7, ok = true, pid = 12345 }
"""
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.join(HERE, "rt")
CMD_FILE = os.path.join(RT, "cmd.json")
RES_FILE = os.path.join(RT, "res.lua")
WHB_FILE = os.path.join(RT, "whb.lua")
# 还活着的子进程 PID 列表 —— Lua 侧 worker 不在时用它精确 taskkill，
# 避免退化成 taskkill /IM python.exe 把机器上所有 python 都杀掉。
PIDS_FILE = os.path.join(RT, "pids.lua")

# ── 子进程日志 / 退出码 / 环境自检 ───────────────────────────
# ★ 以前 stdout/stderr 全进 DEVNULL：子进程一崩，traceback 直接丢进
#   黑洞，Lua 侧只能看到一个"任务超时"，排查全靠猜。
#   现在按「脚本_会话_命令号」落盘，出问题直接能定位到是哪一次。
LOGS_DIR = os.path.join(RT, "logs")
# 退出码回传：每条命令一个文件 rc_<sess>_<id>.lua。Lua 按名字精确取，
# 不会互相覆盖（单槽设计在两条命令前后脚结束时会丢数据）。
RC_PREFIX = "rc_"
# 环境自检结果（解释器路径 + torch/transformers 版本），供「关于」显示
ENV_FILE = os.path.join(RT, "env.lua")
LOG_KEEP_FILES = 40                  # 最多保留多少个日志文件
LOG_KEEP_BYTES = 8 * 1024 * 1024     # 日志总容量上限（8 MB）
RC_KEEP_FILES = 60

CREATE_NO_WINDOW = 0x08000000


def lq(s):
    r"""转成 Lua 字符串字面量。

    ★ 必须转义反斜杠：Windows 路径写进 .lua 文件时，"D:\AsrSubs" 里的
      \H 在 Lua 5.1 里是非法转义，loadfile 会直接报错 —— 表现为
      "Lua 读不到结果文件"，和真因（路径没写对）完全不沾边。
    """
    s = str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + s + '"'


def _close_logf(logf):
    if logf is None or logf is subprocess.DEVNULL:
        return
    try:
        logf.close()
    except Exception:
        pass


def _sweep(dirpath, match, keep, cap):
    """日志清理：文件数或总容量超限就删最旧的（按 mtime）。"""
    try:
        items, total = [], 0
        for n in os.listdir(dirpath):
            if not match(n):
                continue
            p = os.path.join(dirpath, n)
            if not os.path.isfile(p):
                continue
            try:
                sz, mt = os.path.getsize(p), os.path.getmtime(p)
            except OSError:
                continue
            items.append((mt, sz, p))
            total += sz
        if len(items) <= keep and total <= cap:
            return
        items.sort()                     # 旧的在前
        while items and (len(items) > keep or total > cap):
            _mt, sz, p = items.pop(0)
            try:
                os.remove(p)
                total -= sz
            except OSError:
                pass
    except OSError:
        pass


def _clean_logs():
    # ★ 目录必须在这里就建好：do_run 是先 _clean_logs() 再 open(日志)，
    #   而 _mkdir 定义在文件后部 —— 放在调用处建目录会变成"用了才建"，
    #   第一次跑必然 FileNotFoundError。
    _mkdir(LOGS_DIR)
    _sweep(LOGS_DIR, lambda n: n.endswith(".log"), LOG_KEEP_FILES, LOG_KEEP_BYTES)
    _sweep(RT, lambda n: n.startswith(RC_PREFIX) and n.endswith(".lua"),
           RC_KEEP_FILES, 4 * 1024 * 1024)


def pkg_versions():
    """从 site-packages 的 *.dist-info 目录名读版本号。

    ★ 不用 import：光是 import torch 就要几秒和上 GB 内存，
      而 worker 必须一直是个轻量常驻进程。
    """
    out = {}
    try:
        sp = ""
        for p in sys.path:
            if p and p.lower().endswith("site-packages") and os.path.isdir(p):
                sp = p
                break
        if not sp:
            sp = os.path.join(os.path.dirname(os.path.dirname(PY)),
                              "Lib", "site-packages")
        for d in glob.glob(os.path.join(sp, "*.dist-info")):
            m = re.match(r"^([A-Za-z0-9_.\-]+?)-([0-9][^-]*)\.dist-info$",
                         os.path.basename(d))
            if m:
                out[m.group(1).lower().replace("_", "-")] = m.group(2)
    except Exception:
        pass
    return out


def write_env():
    """把实际在用的解释器和关键包版本写进 env.lua。

    Lua 在「关于」里显示它，启动时也核对一遍 —— 缺 torch/transformers
    时直接说清楚"运行环境不完整"，而不是让用户在识别跑到一半才崩。
    """
    try:
        v = pkg_versions()
        body = ("python = %s, torch = %s, transformers = %s, ok = %s"
                % (lq(PY), lq(v.get("torch", "")), lq(v.get("transformers", "")),
                   "true" if (v.get("torch") and v.get("transformers")) else "false"))
        write_lua(ENV_FILE, body)
    except Exception as e:
        errlog("write_env 失败: %s" % e)


def watch_rc(rc_path, cid, proc):
    """子进程结束时把退出码写进 rc_<sess>_<id>.lua。

    Lua 以前判断"任务做完了"只能轮询输出文件。进程如果在写出任何输出
    之前就崩了，Lua 要干等满超时（保存分支 20 秒）才报错，而且报的是
    "读取字幕超时"这种和真因无关的话。有了退出码，失败当时就能报。
    """
    try:
        rc = proc.wait()
    except Exception:
        rc = -1
    try:
        write_lua(rc_path, "id = %d, rc = %d" % (cid, int(rc)))
    except Exception:
        pass


def _py_candidates():
    r"""任务解释器候选（第一个存在的就用）。

    ★★ 独立性：绝不能因为别的插件被卸载/更新就跑不起来。
       早期版本曾共享 DaVinci ASR 插件的 venv —— 对方一卸，
       本工具所有任务都起不来，而 Lua 侧只报"任务超时"。
       现在运行环境完全自持：3.0 是 <根>\runtime，2.x 是 <根>\AsrSubs\.venv。
       （引擎目录下的 .venv 只有开发机才有。）
    """
    here = os.path.dirname(os.path.abspath(__file__))
    base = os.path.dirname(here)          # 例 D:\AsrSubs
    out = []

    def add(p):
        if p and p not in out:
            out.append(p)

    # 1) 用户手动指定（rt\settings.lua：return { python = "..." }）
    #    ★ 反斜杠归一化：本工具写文件用单反斜杠，但用户手改时很可能按
    #      Lua 字符串规矩写成 "C:\\legacy"（双反斜杠）。两种都要认，
    #      否则用户明明填对了路径却"不生效"，极难排查。
    try:
        st = os.path.join(RT, "settings.lua")
        if os.path.isfile(st):
            with open(st, "r", encoding="utf-8-sig", errors="replace") as f:
                txt = f.read()
            m = re.search(r"""python\s*=\s*["']([^"']+)["']""", txt)
            if m:
                p = m.group(1)
                add(p)
                if not os.path.isfile(p):
                    flat = p.replace("\\\\", "\\")
                    if flat != p:
                        add(flat)
    except OSError:
        pass

    # 2) 本工具自带的运行环境（当前默认）：
    #    3.0：<根>\runtime\Scripts\python.exe
    #    2.x：<根>\AsrSubs\.venv\Scripts\python.exe
    #    开发机：引擎目录下的 .venv
    add(os.path.join(here, ".venv", "Scripts", "python.exe"))
    # paths.json（安装器生成）里写了就优先用它，避免目录再改一次就要动代码
    try:
        with open(os.path.join(here, "paths.json"), "r", encoding="utf-8") as f:
            _pj = json.load(f)
        if isinstance(_pj, dict) and _pj.get("runtime"):
            add(os.path.join(str(_pj["runtime"]), "Scripts", "python.exe"))
    except (OSError, ValueError):
        pass
    add(os.path.join(base, "runtime", "Scripts", "python.exe"))
    add(os.path.join(base, "AsrSubs", ".venv", "Scripts", "python.exe"))

    # 3) 系统 Python（最后保障：别的插件没了也还能跑）
    la = os.environ.get("LOCALAPPDATA") or ""
    roots = []
    if la:
        roots.append(os.path.join(la, "Programs", "Python"))
    roots.append("C:\\")
    for r in roots:
        for v in ("313", "312", "311", "310", "39", "38"):
            add(os.path.join(r, "Python" + v, "python.exe"))

    return out


def _pick_python():
    for p in _py_candidates():
        if os.path.isfile(p):
            return p
    return sys.executable      # 最后兜底：worker 自己的那个解释器


# 用【python.exe】而不是 pythonw.exe 跑任务：
#   任务是控制台程序，但配合 CREATE_NO_WINDOW 完全不会显示窗口；
#   而且 python.exe 能正常输出到日志文件，排查问题时有据可查。
PY = _pick_python()

# 空闲这么久且没有子任务在跑就自行退出，避免长期占着进程
IDLE_EXIT_SEC = 20 * 60

# 扫命令文件的间隔（秒）。用 mtime+size 变化触发，所以这个值只影响
# 「下发命令到真正启动」的延迟，不影响 CPU（每次只是两个 stat）。
POLL_SEC = 0.15


def _mkdir(p):
    try:
        if p:
            os.makedirs(p, exist_ok=True)
    except OSError:
        pass


def write_lua(path, body):
    """原子写 `return { <body> }`（先 .tmp 再 os.replace）。"""
    if not path:
        return
    _mkdir(os.path.dirname(path))
    try:
        tmp = path + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("return {" + body + "}\n")
        os.replace(tmp, path)
    except OSError:
        pass


def beat():
    write_lua(WHB_FILE, "t = %d" % int(time.time()))


class Children:
    """记录本进程启动的子任务，供 kill 使用。"""

    def __init__(self):
        self.procs = {}
        self.logfs = {}
        self.scripts = {}
        self._pids_sig = ""

    def add(self, key, proc, logf=None, script=""):
        # ★ 键必须是 (sess, id)：不同会话的 id 都从 1 重新计数，
        #   只用 id 会让新会话顶掉旧会话的记录，旧子进程从 pids.lua 里
        #   消失 → kill 杀不掉，显存被孤儿一直占着。
        self.procs[key] = proc
        if logf is not None:
            self.logfs[key] = logf
        # 记下它是什么脚本：do_run 靠这个做互斥（见 busy_scripts）
        if script:
            self.scripts[key] = str(script)

    def busy_scripts(self):
        """还活着的子进程分别是哪些脚本（互斥判据）。

        ★ 按【脚本名】互斥而不是按命令 id：同一个脚本同时只该有一个实例。
          连点两次「开始识别」会拉起两个 resolve_bridge，各加载一份
          3GB 模型 → Windows 上 WinError 1455（内存不足）双双失败；
          而且两个进程交叉写同一个 SRT / status.lua，
          发布出去的是半截文件，导入时间线就是残句。
        """
        self.reap()
        out = set()
        for k, p in self.procs.items():
            if p.poll() is None:
                s = self.scripts.get(k)
                if s:
                    out.add(s)
        return out

    def reap(self):
        for cid in list(self.procs):
            p = self.procs[cid]
            if p.poll() is not None:
                del self.procs[cid]
                self.scripts.pop(cid, None)
                # 进程没了就把它的日志句柄关掉，别把文件描述符漏光
                _close_logf(self.logfs.pop(cid, None))

    def any_alive(self):
        self.reap()
        return any(p.poll() is None for p in self.procs.values())

    def alive_pids(self):
        self.reap()
        return [p.pid for p in self.procs.values() if p.poll() is None]

    def sync_pids(self):
        """把存活子进程 PID 写进 pids.lua（只在变化时写，避免高频落盘）。"""
        pids = self.alive_pids()
        sig = ",".join(str(x) for x in sorted(pids))
        if sig == self._pids_sig:
            return
        self._pids_sig = sig
        write_lua(PIDS_FILE, ", ".join(str(x) for x in sorted(pids)))

    def kill_all(self, include_server=True):
        """杀掉子任务。include_server=False 时保留常驻 ASR 服务。

        ★ v4.8.25：用户点「停止」只需要停掉当前识别，不该把预热好的
          常驻服务一起关掉 —— 否则下一次识别又要重新加载模型（约 12 秒）。
          关窗口 / 退出插件才需要连服务一起收掉（它占着显存）。
        """
        n = 0
        for cid, p in list(self.procs.items()):
            try:
                if p.poll() is None:
                    p.terminate()
                    n += 1
            except Exception:
                pass
        # 给一点时间优雅退出，然后强杀
        deadline = time.time() + 3
        for cid, p in list(self.procs.items()):
            try:
                while p.poll() is None and time.time() < deadline:
                    time.sleep(0.1)
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass
        self.procs.clear()
        for lf in list(self.logfs.values()):
            _close_logf(lf)
        self.logfs.clear()
        self.scripts.clear()
        self._pids_sig = ""
        write_lua(PIDS_FILE, "")
        # ★ 常驻 ASR 服务（asr_server.py）不是本进程拉起的，不在 procs 里，
        #   只杀上面的子进程它会继续占着 2~3GB 显存把整个识别跑完。
        #   心跳文件第 4 段就是它的 pid。
        #   ★ v4.8.25：只有 include_server=True（killall / quit）时才收它 ——
        #   用户点「停止」后保留预热服务，下次识别不用重新加载模型。
        if include_server:
            n += _kill_resident_server()
        return n


def _kill_resident_server():
    """结束常驻 ASR 服务。返回杀掉的个数（0 或 1）。"""
    hb = os.path.join(RT, "asr_hb.txt")
    pid = 0
    try:
        with open(hb, "r", encoding="utf-8", errors="replace") as f:
            raw = (f.readline() or "").strip()
        # ★ 心跳现在是 JSON（模型路径含空格/为空都不会错位）。
        #   旧格式 <时间戳> <ready> <模型路径> <pid> <busy> 用空格分隔，
        #   模型路径带空格时 parts[3] 会变成路径碎片 → int() 失败 → pid=0；
        #   更糟的是碎片恰好是数字时可能 taskkill 掉无关进程。故一并兼容解析。
        if raw.startswith("{"):
            pid = int((json.loads(raw) or {}).get("pid") or 0)
        else:
            parts = raw.split()
            if len(parts) >= 4:
                pid = int(parts[3])
    except Exception:
        pid = 0
    if pid <= 0 or pid == os.getpid():
        return 0
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       creationflags=CREATE_NO_WINDOW,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
        errlog("已结束常驻 ASR 服务 pid=%d" % pid)
        return 1
    except Exception as e:
        errlog("结束常驻 ASR 服务失败 pid=%d: %s" % (pid, e))
        return 0


def do_run(cmd, children):
    cid = int(cmd.get("id") or 0)
    script = str(cmd.get("script") or "")

    # ★ 空脚本名会退化成"把目录当程序启动"，必然失败。显式报错，
    #   免得 Lua 那边等 6 秒才知道没起来。
    if not script:
        write_lua(RES_FILE, "id = %d, ok = false, err = %s" % (
            cid, json.dumps("命令缺少 script 字段", ensure_ascii=False)))
        return

    args = [str(a) for a in (cmd.get("args") or [])]

    exe = script if os.path.isabs(script) else os.path.join(HERE, script)
    if not os.path.exists(exe):
        write_lua(RES_FILE, "id = %d, ok = false, err = %s" % (
            cid, json.dumps("找不到脚本：" + exe, ensure_ascii=False)))
        return

    # ── 互斥：同一个脚本已经有实例在跑就不再拉一个 ────────────
    #   例外：ping / kill / probe 这类轻量命令随时可以发。
    if script in children.busy_scripts():
        write_lua(RES_FILE, "id = %d, ok = false, busy = true, err = %s" % (
            cid, json.dumps("已有同一个任务在运行：" + os.path.basename(script),
                            ensure_ascii=False)))
        errlog("busy 拒绝：%s id=%d" % (script, cid))
        return

    # ── 日志文件：按「脚本_会话_命令号」命名 ──────────────────
    #   stderr 合并进 stdout（STDOUT），traceback 和正常输出按真实
    #   顺序落在同一个文件里，排查时不用对两个文件。
    _clean_logs()
    sess = str(cmd.get("sess") or "")
    safe_sess = re.sub(r"[^0-9A-Za-z_\-]", "", sess)[:8] or "s"
    base = os.path.splitext(os.path.basename(script))[0]
    safe_base = re.sub(r"[^0-9A-Za-z_\-]", "_", base) or "task"
    log_path = os.path.join(LOGS_DIR, "%s_%s_%d.log" % (safe_base, safe_sess, cid))
    logf = None
    try:
        logf = open(log_path, "w", encoding="utf-8", errors="replace")
    except OSError:
        logf = subprocess.DEVNULL

    try:
        # ★ 子进程强制 UTF-8：Python 默认按系统 ANSI 代码页写 stdout，
    #   中文 print 到 worker 的 utf-8 日志句柄里会变乱码，
    #   出问题时日志看着像天书。PYTHONUTF8 一句话解决。
        # ★★ v4.8.20：光设环境变量【不够】—— 装机版 runtime 是嵌入式发行版
        #   （runtime\Scripts\python312._pth），解释器固定在 isolated 模式
        #   （sys.flags.ignore_environment=1），PYTHON* 环境变量一律不认，
        #   实测 stdout 仍是 gbk，日志里的中文继续变乱码。改用命令行开关
        #   -X utf8（不受 -E / isolated 影响）。下面两行环境变量留给
        #   非嵌入式解释器（开发机 venv）走同一条路。
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [PY, "-X", "utf8", exe] + args,
            cwd=HERE,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except Exception as e:
        _close_logf(logf)
        write_lua(RES_FILE, "id = %d, ok = false, err = %s" % (
            cid, json.dumps("启动失败：%s: %s" % (type(e).__name__, e),
                            ensure_ascii=False)))
        return

    key = (sess, cid)
    children.add(key, proc, logf, script=script)
    children.sync_pids()
    # log 字段让 Lua 一出事就能直接把路径报给用户
    write_lua(RES_FILE, "id = %d, ok = true, pid = %d, log = %s"
              % (cid, proc.pid, lq(log_path)))

    # 退出码回传（守护线程：worker 退出时不会被他拖住）
    rc_path = os.path.join(RT, "%s%s_%d.lua" % (RC_PREFIX, safe_sess, cid))
    try:
        os.remove(rc_path)
    except OSError:
        pass
    threading.Thread(target=watch_rc, args=(rc_path, cid, proc),
                     daemon=True).start()


ERRLOG = os.path.join(RT, "worker_err.log")


def errlog(msg):
    """把异常写进文件。

    ★ 不能用 sys.stderr：本进程是用 pythonw 启动的，stderr 直接被丢掉，
      出了问题什么都看不到。写文件才能事后排查。
    """
    try:
        if os.path.exists(ERRLOG) and os.path.getsize(ERRLOG) > 20000:
            os.remove(ERRLOG)
        with open(ERRLOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S") + "  " + str(msg) + "\n")
    except OSError:
        pass


def cmd_signature():
    """命令文件的 (mtime, size) —— 变了才值得重新读一遍。"""
    try:
        st = os.stat(CMD_FILE)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


# ── 单实例保护 ───────────────────────────────────────────────
# ★ 为什么需要：worker 靠 rt\cmd.json 这个【单槽】通道收命令，两个 worker
#   同时活着就会抢同一条命令 —— 2026-11-01 实测踩到过：残留的旧 worker
#   先抢到 kill 并按老逻辑把常驻服务一起杀了，新 worker 只看到"命令消失"，
#   表现出来是"kill 杀了服务、killall 报缺字段"，看着像代码有 bug，
#   其实是环境里同时有两个 worker。正常使用不会同时起（Lua 先看心跳），
#   但连点开始、或上一轮进程残留（被强杀/崩溃）时就会出现。
#
# ★ 为什么用 pid 文件而不是 Windows 命名互斥体：
#   命名互斥体由内核回收、没有残留问题，但完全不可见 —— 出问题时没法
#   人肉确认"到底谁占着"。pid 文件能落盘、能直接打开看，代价是自己
#   处理残留：读到 pid 后要判【那个 pid 还活着、且确实是 python】。
#   只判存活不够 —— PID 会被系统复用，指到一个无关的活进程上就会误判
#   "还有实例在跑"，于是 worker 永远起不来。所以还要查映像名。
PID_FILE = os.path.join(RT, "worker.pid")


def _proc_image(pid):
    """取进程映像全路径；取不到返回 ""。"""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return ""
                return buf.value
            finally:
                k32.CloseHandle(h)
        except Exception:
            return ""
    try:
        return os.readlink("/proc/%d/exe" % int(pid))
    except OSError:
        return ""


def _pid_alive(pid):
    """进程是否还活着（绝不能用 os.kill(pid,0)：Windows 上那是 TerminateProcess）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            h = k32.OpenProcess(0x00100000, False, pid)     # SYNCHRONIZE
            if not h:
                return False
            try:
                return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT
            finally:
                k32.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid_file():
    """读锁文件 → dict 或 None。刚写完还没落盘时可能读到空文件，重试几次。"""
    for _ in range(3):
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                body = f.read().strip()
        except OSError:
            return None
        if body:
            try:
                info = json.loads(body)
                if isinstance(info, dict) and info.get("pid"):
                    return info
            except ValueError:
                pass
        time.sleep(0.15)
    return None


def _is_worker_pid(pid):
    """那个 pid 是不是还活着的 worker（而不是被复用的无关进程）。"""
    if not _pid_alive(pid):
        return False
    img = _proc_image(pid)
    if img:
        return "python" in os.path.basename(img).lower()
    return True          # 查不到映像名就保守认定占用，宁可不起第二个


def _pid_file_age():
    """worker.pid 有多久没被改过了（秒）。读不到返回 0（当作"刚建"）。"""
    try:
        return max(0.0, time.time() - os.path.getmtime(PID_FILE))
    except OSError:
        return 0.0


def acquire_single():
    """抢占单实例锁。抢到返回 True，已有活着的 worker 返回 False。"""
    for _ in range(3):
        try:
            fd = os.open(PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = _read_pid_file()
            if info and _is_worker_pid(info.get("pid")):
                return False                     # 有活的 worker，本进程让路
            if info is None:
                # ★★ v1.2.0：读不出内容，不一定是"残留"。
                #   对方刚用 O_EXCL 建好文件、还没把 JSON 写进去时，读到的就是
                #   空文件 —— 老代码把它当残留【删掉】，然后自己重建，于是两个
                #   worker 同时活着，正好绕过了这套保护（抢同一条 cmd.json）。
                #   现在按文件年龄区分：刚建（60 秒内）的让路，放久了的
                #   （崩溃残留）才清。
                if _pid_file_age() < 60:
                    errlog("worker.pid 刚被创建、内容还没落盘，本进程让路")
                    return False
            # 残留（上次被强杀/崩溃没来得及删）→ 清掉重抢
            try:
                os.remove(PID_FILE)
                errlog("清理残留的 worker.pid（pid=%s 已不在）"
                       % (info.get("pid") if info else "空文件/过期"))
            except OSError:
                pass
            continue
        except OSError as e:
            # 锁文件都写不了（目录只读等）：不拿它挡住功能，放行
            errlog("单实例锁不可用（%s），跳过保护" % e)
            return True
        # ★ v1.2.0：fdopen 失败时裸 fd 没人接管，必须自己关，否则每次重启
        #   漏一个句柄。fdopen 成功之后由 with 负责关闭，这里不能重复 close。
        try:
            f = os.fdopen(fd, "w", encoding="utf-8")
        except OSError as e:
            try:
                os.close(fd)
            except OSError:
                pass
            errlog("写 worker.pid 失败（%s），跳过保护" % e)
            return True
        try:
            with f:
                json.dump({"pid": os.getpid(), "t": int(time.time()),
                           "exe": sys.executable}, f)
        except OSError:
            pass
        return True
    return False


def release_single():
    """退出时释放锁；只删自己写的那个，别误删新实例的。"""
    try:
        info = _read_pid_file()
        if info and int(info.get("pid") or 0) != os.getpid():
            return
        os.remove(PID_FILE)
    except (OSError, TypeError, ValueError):
        pass


def main():
    _mkdir(RT)

    # ★ 单实例闸门必须在【任何清理动作之前】：下面会删 res.lua 和上一轮的
    #   rc_*.lua，如果这是个被重复拉起的进程，那些删除会打断正在干活的那个
    #   worker —— 表现为"命令刚发出去就没了"。
    if not acquire_single():
        try:
            with open(PID_FILE, "r", encoding="utf-8") as f:
                who = json.load(f).get("pid")
        except (OSError, ValueError):
            who = "?"
        errlog("已有 worker 在跑（pid=%s），本进程直接退出" % who)
        return 0

    try:
        return _main_loop()
    finally:
        release_single()


def _main_loop():
    _mkdir(RT)
    children = Children()
    last_key = None          # ★ 去重键是 (sess, id)，不是只有 id
    last_beat = 0.0
    last_cmd_at = time.time()
    # ★ 启动时就把 cmd.json 的签名记下来：否则 worker 一启动就会发现
    #   "文件和 None 不一样"，把上一轮残留的命令原样再执行一遍 ——
    #   重复识别、重复落轨、重复删建时间线都是这么来的。
    #   只认【启动之后新写进来的】命令。
    last_sig = cmd_signature()
    # ★ beat 必须放在基线采集【之后】：Lua 可能在 worker 启动的毫秒级
    #   窗口内就把 cmd.json 写好了，先 beat 再采基线的话，
    #   那条命令会被当成"启动前就存在的旧文件"直接吞掉 → 命令丢失。
    beat()

    # 启动时清掉旧心跳/结果之外，也清掉可能残留的 res.lua，
    # 否则 Lua 会读到上一次会话的结果。
    try:
        if os.path.exists(RES_FILE):
            os.remove(RES_FILE)
    except OSError:
        pass

    # 上一会话残留的退出码文件全部清掉：留着会让 Lua 读到旧结果，
    # 把"这次成功"误判成"上次失败"。
    try:
        for n in os.listdir(RT):
            if n.startswith(RC_PREFIX) and n.endswith(".lua"):
                os.remove(os.path.join(RT, n))
    except OSError:
        pass

    # 环境自检：把实际用的解释器和 torch/transformers 版本落盘
    write_env()
    errlog("worker 启动，解释器=%s" % PY)

    while True:
        now = time.time()

        # ── 心跳 ──────────────────────────────────────────────
        if now - last_beat >= 1.0:
            beat()
            last_beat = now

        # ── 读命令（只在文件真的变了时才读）─────────────────────
        sig = cmd_signature()
        if sig is not None and sig != last_sig:
            try:
                # utf-8-sig：带 BOM 的 json 用 utf-8 读会永久失败，
                # 还会每 0.15 秒重试刷屏
                with open(CMD_FILE, "r", encoding="utf-8-sig") as f:
                    cmd = json.load(f)
            except Exception as e:
                # ★ 半截 JSON（Lua 正写到一半）。这里【不能】把 last_sig 更新掉
                #   —— 否则这条命令就被永久跳过了，Lua 那边只能干等到超时。
                #   不更新 last_sig，下一轮会自动重读同一个文件。
                cmd = None
                errlog("读命令失败（半截文件，稍后重试）: %s" % e)
            if isinstance(cmd, dict):
                last_sig = sig
                cid = int(cmd.get("id") or 0)
                sess = str(cmd.get("sess") or "")
                key = (sess, cid)
                if key != last_key:
                    last_key = key
                    last_cmd_at = now
                    name = str(cmd.get("cmd") or "")
                    if name == "quit":
                        # ★ quit = 收摊走人，所以顺手把还在跑的子任务清干净。
                        #   否则正在识别的子进程会变成孤儿继续占着显存 ——
                        #   它的父进程没了，也没人再管它，用户只能手动杀。
                        n = children.kill_all()
                        write_lua(RES_FILE, "id = %d, ok = true, quit = true, "
                                            "killed = %d" % (cid, n))
                        return 0
                    if name == "ping":
                        # 只为了刷新"最近有命令"时间，防止空闲自杀
                        write_lua(RES_FILE, "id = %d, ok = true, ping = true" % cid)
                    elif name == "kill":
                        # ★ v4.8.25：kill = 只停当前任务，保留预热好的常驻服务。
                        #   以前 kill 连 asr_server 一起杀，用户点「停止」后
                        #   再开始识别又要重新加载模型（实测多等 12 秒）。
                        #   关窗口 / 退出插件走 killall，那时才该连服务一起收掉。
                        n = children.kill_all(include_server=False)
                        write_lua(RES_FILE,
                                  "id = %d, ok = true, killed = %d" % (cid, n))
                    elif name == "killall":
                        n = children.kill_all(include_server=True)
                        write_lua(RES_FILE,
                                  "id = %d, ok = true, killed = %d" % (cid, n))
                    else:
                        do_run(cmd, children)

        children.sync_pids()

        # ── 空闲退出 ──────────────────────────────────────────
        # ★ 还有子任务在跑就绝不能退：一退那些进程就成了没人管的孤儿，
        #   它们的显存要等到用户手动杀才释放。
        if now - last_cmd_at > IDLE_EXIT_SEC and not children.any_alive():
            return 0

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        errlog("崩溃:\n" + traceback.format_exc())
        sys.exit(1)
