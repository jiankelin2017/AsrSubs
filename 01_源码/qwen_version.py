# -*- coding: utf-8 -*-
"""版本号的唯一来源（由 tools/sync_version.py 从 version.txt 生成，勿手改）。

★ 为什么要有这个文件：
  v1.28 时代版本号散在三处（Lua 的「关于」文本、Inno 的 .iss、打包记录），
  改版本时漏一处就会出现「界面显示 v1.28、安装包属性是 1.26」这种
  自相矛盾的情况（备份说明里记着真发生过）。现在只改 version.txt，
  其余全部由脚本同步。
"""

VERSION = "1.2.5"

# 给日志/诊断包用：'AsrSubs 2.0.0'
def banner():
    return "AsrSubs " + VERSION
