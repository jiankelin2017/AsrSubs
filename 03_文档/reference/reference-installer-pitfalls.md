---
title: 参考 · 安装器已知坑
type: reference
status: Active
version: 1.0.0
last_update: 2026-11-06
applies_to: AsrSubs 1.2.5
maintainer: Jian
supersedes: archive\archive-dev-doc-20261105.md §11 / 03_文档\archive\archive-uninstall-fix-20261030.md（历史根因部分）
superseded_by: —
---

# 参考 · 安装器已知坑

## 一句话摘要

改 `AsrSubs.iss` / `installer.py` 前必读的 7 条实测结论；前两条是用户截图复现后定位的 P0。

> 适用条件：结论来自 `archive\archive-dev-doc-20261105.md` §11（2026-11-01 实测，Inno Setup 6）。本次治理未重新编译验证每条，但 2026-11-05 的 v1.2.0 出包全程成功，说明当前 `.iss` 未违反这些约束。

## 当前有效结论

| # | 结论 | 违反后果 |
|---|---|---|
| 1 | **Inno 6 里调 kernel32 一律用 W 版**（`GetDriveTypeW`、`GetDiskFreeSpaceExW`） | ANSI 版 `GetDriveTypeA` 把 Unicode 的 `"D:\"` 读成无反斜杠的 `"D"`，对**任何盘符**都返回 1（DRIVE_NO_ROOT_DIR）→ 向导报「找不到这个盘：D:\」。实测 C:/D: 的 A 版均返回 1、W 版均返回 3 |
| 2 | **`InitializeWizard` 阶段不能展开 `{app}`**；判断覆盖升级改用注册表卸载键 | 提前 `ExpandConstant('{app}')` 抛「An attempt was made to expand the "app" constant before it was initialized」，静默模式退出码 1、界面模式弹运行时错误框 |
| 3 | 预填逻辑**只在全新安装**生效 | 升级安装若覆盖 Inno 自动填好的上次位置，会装出第二个目录 |
| 4 | 打包用的 Python **必须有 tkinter** | PyInstaller 静态分析不报错，但打出的安装核心 exe 启动即退，且只有到用户机器上才发现 |
| 5 | `.iss` 的 const 段**不支持类型注解常量** | `MinFreeBytes: Int64 = 7516192768;` 编译不过；写 `MinFreeBytes = 7516192768;` |
| 6 | 多源下载必须做**会话级「不可达源黑名单」**；403/404 **不拉黑** | 见下方「下载卡住」 |
| 7 | 判断「下载卡住」**先看文件系统**，不要先杀进程 | 见下方「下载卡住」 |

## 关键变更（仅影响使用的里程碑）

- v4.8.16：修 P0 #1、#2；新增最大盘预填。
- v4.8.18：修「下不动」（#6）；`build.bat` 加 tkinter 闸门（#4）。
- v1.1.0：一键安装 / 一键卸载。
- v1.2.0：安装器逻辑**零改动**，仅重出包。见 `..\CHANGELOG.md`。

## 操作 / 参考细节

### #2 的注册表判断写法

真实键名（2026-11-06 静默装 / 卸实测核对，**末尾是两个右花括号**）：

```
HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{7A3F9C4E-2B8D-4E6A-9F1C-5D8A2B7E3C46}}_is1
    └─ InstallLocation 是否存在
```

为什么多一个 `}`：`AsrSubs.iss` 里写的是 `#define MyAppId "{{7A3F9C4E-…}}"`，ISPP 只把 `{{` 当转义（展开成一个左花括号），`}}` 不是转义序列、原样保留两个右花括号。这个字符串自 1.x 起没变过，**不要修** —— 改它等于换一个 AppId，已在机器上的安装会失去升级对象。

> ⚠ 本页旧版写的是 `{#MyAppIdGuid}_is1`：`MyAppIdGuid` 这个 `#define` 在脚本里**根本不存在**（只有 `MyAppId`），而且少了那个多余的 `}`。按旧写法去查注册表永远查不到，别再照抄。

### #3 的预填规则

`PickBestDrive()` 枚举本地固定盘（`GetDriveTypeW == DRIVE_FIXED`），够 7 GB 的盘里挑剩余空间最大者；一块都不够就退取最大盘，让安装核心的 `validate_base()` 报具体差额。

### #6 「下不动」的根因与修法

模型仓库有 5 个小文件（`tokenizer.json` 等）在国内镜像站不存在，另两个源（ModelScope / 官方 HF）直接连不上（WinError 10060）。旧逻辑对每个缺失文件把三个源各试两遍、每个不可达源干等 60 秒 × 2 次 → 5 文件 × 2 仓库白等 20~40 分钟，而权重早已下完，用户看到的就是「下不动」（安装其实早已完成，进程最后自行退出）。

现 `download()` 接受 `timeout/tag/dead`：超时与连接类失败把 tag 记入调用方的会话级 `dead` 集合，`_fetch_repo()` 后续文件直接跳过该源；小文件超时 60 → 20 秒；**403/404 不拉黑**（源可达只是没这个文件，换个文件同一源可能就有）。隔离测试（本地 HTTP 服务模拟正常 / 404 / 挂起）10 项断言全过。

### #7 排查顺序

```
1. 看 models\ 里两个权重（1.8 GB × 2）与 8 个文件是否都已就位
2. 看 _setup\_deployed.json 是否已部署
3. 再决定：继续等 / 杀进程 / 修代码
```

当时用户报「下不动」时，上述文件其实都已就位，进程只是在做无谓的可选文件重试。

### 编译警告：`PrivilegesRequired=admin` 却用了 per-user 路径

ISCC 6.7.3 编译 `AsrSubs.iss` 会给出 `UsedUserAreasWarning`：

> The [Setup] section directive "PrivilegesRequired" is set to "admin" but per-user areas (userappdata) are used by the script.

来源：`[UninstallDelete]` 里有一行 `{userappdata}\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\AsrSubs`（`Check: NeedDeleteResolveDirAD`）。提权安装时 `{userappdata}` 解析的是**执行安装的那个管理员账户**的 `AppData\Roaming`，不是当前登录的普通用户。

实际影响：**只会漏删，不会误删**。单账户机器（账户目录唯一）路径正确；多账户机器上，卸载只清管理员账户那一份，其他用户的同目录会留着。而且这行挂了 `NeedDeleteResolveDirAD` 这层 Check（读 `_setup\_deployed.json`），清单里没有该路径就一行都不删，不存在「删了别人文件」的风险。

处置：**不改**。主部署路径是 `{commonappdata}`（全机共享，谁装都能命中），`{userappdata}` 这行只是兜底。改成 `{commonappdata}` 能消警告，但会丢掉「安装核心当年确实写进用户级目录」这种情况的清理能力（**推测**：保留它就是为了兜这个，代码里没有写明缘由）。

### 卸载相关（当前行为）

卸载时 `data\output` 里的字幕产物**默认保留**，`installer.py:1430–1433` 有 `字幕输出 → data\output` 的搬运逻辑；卸载前自动备份到「文档」，并额外问一句要不要连产物一起删（默认「否」）。卸载前须先退出 DaVinci Resolve，否则文件被占用会删不掉。

### 历史根因（v1.28，2026-10-16）· 从 `archive\archive-uninstall-fix-20261030.md` 抢救

> 以下三条是当时实测得出的**写法级坑**，原文 `packer\` 路径已不存在。⚠ **待人工确认**：当前 `AsrSubs.iss` / `installer.py` 是否仍沿用这些写法，本次治理未逐行核对；改卸载逻辑前请先读现状代码。

| # | 当时的坑 | 当时的正确写法 |
|---|---|---|
| A | `[UninstallDelete]` 里写 `Type: filesandordirs; Name: "{app}\AsrSubs"` **整棵删**，把用户跑出来的字幕一起删掉（本机实测 4 个 `.srt` + 2 个 `.txt`） | 只删模型子树 `"{app}\AsrSubs\models"`，字幕输出原地保留；`使用说明.txt` 由 `[Files]` 安装清单自动清，不必单列 |
| B | **Inno 的 `{tmp}` 不是 `%TEMP%`**：它是 Setup / Uninstall 每次运行自建的临时子目录（形如 `%TEMP%\is-XXXXX.tmp`），退出时自动清空。在 `[UninstallDelete]` 里写 `"{tmp}\qwen3asr_dl"` 是**空操作**，编译不报错、日志里连一行删除记录都没有 | 在 `[Code]` 里用 `GetEnv('TEMP')` / `GetEnv('TMP')` 定位真实用户临时目录再删 |
| C | 卸载前的字幕备份若挂在 `InitializeUninstall`，用户点「取消」也会白备份一份 | 挂在 `CurUninstallStepChanged(usUninstall)`：确认卸载之后、真正删文件之前；复制时跳过 junction / 符号链接，避免把几 GB 模型一起拷走；备份失败只提示、不阻断卸载 |

当时修复后的隔离实测（独立 AppId 测试包、不碰正式安装）：字幕输出原地保留 4/4、文档备份逐字节一致、`models` 子树删除、`%TEMP%\qwen3asr_dl` 删除、`{app}` 只剩刻意保留的字幕。

## 失效内容说明

- `03_文档\archive\archive-uninstall-fix-20261030.md`（v1.28，2026-10-16）里的 `packer\AsrSubs.iss` / `packer\installer.py` **路径已不存在**；它记录的 `[UninstallDelete]` 整棵删问题已由当前安装器解决，三条写法级坑已合并进上文「历史根因」，**该文档已归档（2026-11-05），主目录不再保留**。
- 「GPU 版可能装成 CPU 版」是 v4.8.23 旧安装器（成品已于 2026-11-06 彻底删除）的缺陷，当前版本已加 `detect_nvidia()`；**不要再拿历史包装新机器**。
