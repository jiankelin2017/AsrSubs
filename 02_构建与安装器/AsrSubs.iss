;══════════════════════════════════════════════════════════════════════════
; AsrSubs · Inno Setup 安装脚本
;──────────────────────────────────────────────────────────────────────────
; 分工：
;   本安装包（十几 MB）=「铺文件 + Windows 标准外壳」
;     · 把 PyInstaller onedir 产物（安装核心，含内嵌的引擎脚本）
;       解到 {app}\_setup\
;     · 注册开始菜单 / 桌面快捷方式 / 卸载信息
;     · 装完自动调起「安装核心」—— 那才是真正干活的：
;       下载 Python + torch + 两个模型（3.4~6 GB，在线下载不打进安装包）、
;       部署引擎到 {app}\engine、把 Lua 脚本写进 DaVinci 脚本目录
;
;   安装后的目录（全部 ASCII，Lua 才能用 io.open / os.execute）：
;     {app}\_setup\   安装器本体
;     {app}\engine\   引擎脚本 + paths.json / paths.lua
;     {app}\runtime\  Python 3.12 + torch
;     {app}\models\   Qwen3-ASR-0.6B-hf + Qwen3-ForcedAligner-0.6B-hf
;     {app}\data\    output\（字幕产物）logs\（日志）cache\（缓存）
;     {app}\docs\    使用说明
;
;   默认位置 = 自动挑选的「空间最大的本地固定盘」\AsrSubs（预填好，直接下一步即可）；
;   向导第一页可以改成任意本地固定盘 / 任意目录 —— 安装核心会按 {app}
;   把 engine\runtime\models\data\docs 全部铺到这个位置下面。
;   ★ 路径必须纯 ASCII：Lua 的 io.open / os.execute 走 ANSI 接口，中文目录
;     会让插件"界面能开、一跑就报任务超时"。向导里会直接拦住非 ASCII 输入。
;   ★ 只装这一处，不再建中文别名目录：盘上就 D:\AsrSubs 一个（老版本建过的
;     "<安装目录>字幕工具" junction 由安装核心/卸载器顺手摘掉）。
;
; 构建：build.bat（PyInstaller --onedir → ISCC 编译）
;══════════════════════════════════════════════════════════════════════════

#define MyAppName "AsrSubs"
#define MyAppVersion "1.2.5"
#define MyAppExeName "安装核心.exe"
#define MyAppPublisher "legacy"

; ★ AppId 定下后就不能改 —— 升级覆盖安装和卸载都认它
; ★ 它的真实展开结果（2026-11-06 静默装 / 卸实测核对过，别再"顺手修正"）：
;   ISPP 只把「{{」当转义（展开成一个左花括号），「}}」不是转义序列、原样保留，
;   所以展开出来是 「{7A3F9C4E-...-5D8A2B7E3C46}}」—— 末尾是【两个】右花括号，
;   并不是一个合法 GUID。注册表里的卸载键因此长这样：
;     HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\
;       {7A3F9C4E-2B8D-4E6A-9F1C-5D8A2B7E3C46}}_is1
;   按上面这个带两个 } 的键名去查，才能查到。
;   自 1.x 起一直是这个字符串，Inno 就靠它认同产品的旧安装；
;   修成单花括号 = 换一个 AppId = 已在机器上的安装失去升级对象。
;   所以【不要修】，只在文档和排查时认准这个形态。
#define MyAppId "{{7A3F9C4E-2B8D-4E6A-9F1C-5D8A2B7E3C46}}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; ★ 别写死数字：这里原来硬编码 1.28.0.0，导致右键属性一直显示 v1.28，
;   而 AppVersion 已经是 2.0.10 —— 用户看属性会以为装的是旧版。
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName=AsrSubs 安装器
VersionInfoDescription=AsrSubs 一键安装包（运行环境与模型在线下载）
VersionInfoCopyright=Copyright (C) 2026 legacy
; ★ v4.8.15：默认改成「系统盘\AsrSubs」——写死 D:\AsrSubs 时，
;   在没有 D 盘的机器上，安装向导会直接报"找不到这个盘: D:\"。
DefaultDirName={sd}\AsrSubs
; ★ 目录页始终显示，默认值已经填好 D:\AsrSubs —— 想装到默认位置只需点「下一步」，
;   想换盘 / 换目录就当场改。
;   注意：路径必须纯 ASCII（Lua 走 ANSI 接口），NextButtonClick 里会拦中文，
;   并挡住盘符根目录（D:\ 这种）、UNC 网络路径和 & ! ^ % 这类命令行敏感字符。
DisableDirPage=no
; ★ v4.8.16：跳过欢迎页——双击后直接到目录页（已自动预填最优盘），
;   一键体验：两次「下一步」进入安装；想换盘 / 换目录就当场改。
DisableWelcomePage=yes
DefaultGroupName=AsrSubs
AllowNoIcons=yes
; 要把 Lua 脚本写进 %ProgramData% 下的 DaVinci 目录（全新机器还要建目录），
; 提权最稳；用户对安装器弹 UAC 也有心理预期
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=AsrSubs安装器
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=AsrSubs
UninstallDisplayIcon={app}\_setup\{#MyAppExeName}

[Languages]
; 安装界面用简体中文（Inno 6 自带）
Name: chinesesimplified; MessagesFile: compiler:Languages\ChineseSimplified.isl

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; onedir 产物整个铺进去（exe + _internal\，内嵌的 payload 脚本就在里面）
Source: "dist\安装核心\*"; DestDir: "{app}\_setup"; \
    Flags: recursesubdirs createallsubdirs ignoreversion
; 使用说明也放一份，开始菜单的说明快捷方式立刻有目标
; （安装核心跑完后还会用自己的 payload 覆盖一份，内容相同）
Source: "..\03_文档\使用说明.txt"; DestDir: "{app}\docs"; Flags: ignoreversion

[Icons]
Name: "{group}\AsrSubs 使用说明"; Filename: "{app}\docs\使用说明.txt"
Name: "{group}\重新运行安装向导"; Filename: "{app}\_setup\{#MyAppExeName}"; \
    Parameters: """{app}"""
Name: "{group}\卸载 AsrSubs"; Filename: "{uninstallexe}"
Name: "{commondesktop}\AsrSubs 使用说明"; Filename: "{app}\docs\使用说明.txt"; \
    Tasks: desktopicon

[Run]
; ★ v1.1.0：一键安装 —— 装完自动调起下载向导，并且带 --auto：
;   界面起来就按「检测到的默认选项」直接开始下载部署，不用再点一次
;   「开始安装」。位置由安装包给定、CPU/GPU 用检测结果（有卡默认 GPU）。
;   用户仍可在界面上按「取消」。
; /SILENT 安装时不自动跑（skipifsilent），留给用户手动点开始菜单项。
Filename: "{app}\_setup\{#MyAppExeName}"; Parameters: """{app}"" --auto"; \
    Description: "自动下载运行环境与模型并完成部署（3.4~6 GB，视版本而定）"; \
    Flags: nowait postinstall skipifsilent

[Messages]
; ★ v1.1.0：一键卸载前把"删什么/留什么/先退出 Resolve"一次说清。
ConfirmUninstall=确定要卸载 %1 吗？%n%n将删除：运行环境、模型、引擎、文档、菜单项、安装器本体。%n将保留：data\output 里的字幕产物（卸载前会自动备份一份到「文档」），以及你自己放进安装目录的其它文件。%n%n★ 卸载前请先退出 DaVinci Resolve，否则部分文件会被占用而删不掉。

[UninstallDelete]
; ── 先删 DaVinci 脚本目录里的副本 ──
; 只删安装核心真的写进去的（按 _deployed.json 清单核对），
; 没部署过 / 清单不在就一个都不碰，防止误删用户自己的东西。
; ★ 必须排在删 {app} 之前 —— 清单就在 {app}\_setup\ 里，
;   先删 {app} 的话清单没了，这些就都跳过不删了。
; ★ 3.0 的菜单目录改名为「AsrSubs」（ASCII：Resolve 用 ANSI fopen 加载
;   Fusion 脚本，中文目录会导致脚本根本加载不出来）；2.x 的「AsrSubs」目录
;   由安装核心在部署时清掉（只清本工具写的那两个文件），所以这里不用列。
Type: filesandordirs; Name: "{commonappdata}\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\AsrSubs"; Check: NeedDeleteResolveDirPD
Type: filesandordirs; Name: "{userappdata}\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\AsrSubs"; Check: NeedDeleteResolveDirAD
Type: filesandordirs; Name: "{commonpf64}\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\AsrSubs"; Check: NeedDeleteResolveDirPF
; ── 再删安装目录下本工具创建的子树（3.0 新布局）──
; 不整删 {app}：用户可能往安装目录里放过自己的东西，
; 只清本工具创建的目录（下载来的环境和模型都在里面）。
Type: filesandordirs; Name: "{app}\_setup"
Type: filesandordirs; Name: "{app}\engine"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\docs"
; ★★ data\output 不进这张表 ★★
; 字幕是用户跑几小时攒出来的，卸载一次全没了的教训已经吃过一次。
; data 下只清日志和缓存（可重建），output 原地保留；
; 另外卸载前还会自动另存一份到文档目录（见 [Code] 的 CurUninstallStepChanged）。
Type: filesandordirs; Name: "{app}\data\logs"
Type: filesandordirs; Name: "{app}\data\cache"
; ★★ 旧版中文门面 "<安装目录>字幕工具" 不在这张表里 ★★
; 2.0.10 及更早的安装器建过它（junction）。现在不再建，但升级/卸载时要
; 把残留摘掉 —— 它是重解析点，Inno 的 filesandordirs 会跟进去把真身
; 整个删掉，所以必须走 [Code] 的 RemoveDir()（只移除链接本身）。
; ── 下载缓存的清理【不在这里做】──
; 缓存堆在 %TEMP%\qwen3asr_dl（Python embed zip + torch wheel + 模型分片）。
; ★ 这里千万不能写 Type: filesandordirs; Name: "{tmp}\qwen3asr_dl"：
;   Inno 的 {tmp} 不是 %TEMP%，而是 Setup/Uninstall 每次运行自己新建的一个
;   临时子目录（形如 %TEMP%\is-XXXXX.tmp），退出时整棵自动清空。
;   指向它 = 空操作 —— 实测卸载日志里连一行删除记录都没有。
; 所以改到 [Code] 里用 GetEnv('TEMP') 定位真实的用户临时目录再删
; （见 DeleteDownloadCache）。安装核心成功后本来也会自己清一次
; （installer.py 的 Installer.clean_cache），这里是兜底。

[Code]
// 文件属性位（Inno 的 Pascal Script 没预定义这两个，自己给个名字）
const
  FA_DIRECTORY = $10;      // FILE_ATTRIBUTE_DIRECTORY
  FA_REPARSE   = $400;     // FILE_ATTRIBUTE_REPARSE_POINT（junction / 符号链接）
  DRIVE_FIXED  = 3;        // GetDriveTypeW：本地固定盘
  MinFreeBytes = 7516192768;  // 7 GB：GPU 版运行环境下限，与 validate_base 一致（超 32 位，Inno 自动按 Int64）

//──────────────────────────────────────────────────────────────────────────
// 安装位置：自动预填空间最大的本地固定盘，也允许用户改成任意本地固定盘 / 目录
//──────────────────────────────────────────────────────────────────────────
// ★ 不再 Skip 目录页：谁能装哪块盘、哪个目录只有用户自己知道
//   （C 盘空间紧的装 D 卡、双系统装 E 盘都很常见）。默认值已经填好，
//   想装默认位置就是一次「下一步」。
// ★ 校验必须在向导里就做掉，不能等安装核心（PyInstaller exe）跑起来再报：
//   那时几 GB 的下载已经开始了，用户白等。这里的规则和 installer.py 的
//   validate_base() 保持一致 —— 两边都要改。
// ★★ v4.8.16 必修缺陷：盘符 API 必须用 W 版 ★★
// GetDriveTypeA(String) 在本机实测对【存在的盘】返回 1（DRIVE_NO_ROOT_DIR）：
// Inno 6 的 external String 参数不做 ANSI 转换，"D:\" 的 UTF-16 字节被
// ANSI API 按空字符截成 "D"（无反斜杠根路径）→ 向导误报"找不到这个盘"，
// 用户明明有 D 盘也装不进去（2026-11-01 用户截图复现）。
// 实测对照（DrvTest 实验脚本）：GetDriveTypeW(C:\)=3、GetDriveTypeW(D:\)=3，
// 而 GetDriveTypeA 对 C:/D: 均返回 1。盘符相关 API 一律改用 W 版。
function GetDriveTypeW(lpRootPathName: String): Cardinal;
  external 'GetDriveTypeW@kernel32.dll stdcall';

// 取磁盘剩余空间（PickBestDrive 预填最优盘用）。同样必须 W 版。
function GetDiskFreeSpaceExW(lpDirectoryName: String;
  var lpFreeBytesAvailable: Int64; var lpTotalNumberOfBytes: Int64;
  var lpTotalNumberOfFreeBytes: Int64): Boolean;
  external 'GetDiskFreeSpaceExW@kernel32.dll stdcall';

// 在本地固定盘里挑剩余空间最大的盘。onlyBig=True 时只看剩余 ≥ MinFreeBytes
// （7 GB，GPU 版运行环境下限）的盘。返回盘符（形如 'E:'），没有则返回 ''。
// ★ 与 installer.py 的 default_base() 保持一致（两边都要改）。
function PickBestDrive(onlyBig: Boolean): String;
var
  i: Integer;
  drive, best: String;
  free, bestFree: Int64;
begin
  Result := '';
  best := '';
  bestFree := -1;
  for i := Ord('C') to Ord('Z') do
  begin
    drive := Chr(i) + ':';
    if GetDriveTypeW(drive + '\') <> DRIVE_FIXED then
      Continue;
    if not GetDiskFreeSpaceExW(drive + '\', free, free, free) then
      Continue;
    if onlyBig and (free < MinFreeBytes) then
      Continue;
    if free > bestFree then
    begin
      bestFree := free;
      best := drive;
    end;
  end;
  Result := best;
end;

// ★ v4.8.16：全新安装时自动预填「空间最大的本地固定盘」\AsrSubs。
//   优先在够 7 GB 的盘里挑最大；一块都不够就退而取最大盘，让安装核心的
//   validate_base() 报"具体差多少"，而不是预填一个莫名其妙的小盘。
// ★ 两种情况不碰目录框（Inno 在调用本过程前已把值填好）：
//   ① 覆盖升级：同一 AppId 装过，Inno 自动填上次的位置；里面有 _setup
//      就是本工具装的 —— 照旧原地升级，改了会让升级装到第二个目录去。
//   ② 显式指定：命令行 /DIR="..." 等非默认值，用户/脚本说了算。
//      （比较基准与 DefaultDirName 联动，改默认值记得同步这里）
// ★ 为什么不用 {app} 判断升级：InitializeWizard 阶段 {app} 还没初始化，
//   ExpandConstant('{app}') 直接抛「内部错误: An attempt was made to
//   expand the "app" constant before it was initialized」——
//   静默模式退出码 1、界面模式弹运行时错误框（2026-11-01 实测踩坑，
//   正式包一度全坏，用户截图复现）。
// ★ 为什么不用注册表卸载键拼 GUID：ISPP 的 {{ 转义在 [Code] 字符串里
//   极易拼错（实测拼出过缺左花括号的键名，升级判断永远为假），
//   不如 DirEdit 现值 + _setup 直判来得稳。
procedure InitializeWizard;
var
  best: String;
begin
  if DirExists(WizardForm.DirEdit.Text + '\_setup') then
    Exit;                       // ① 覆盖升级：保持 Inno 填好的上次位置
  if CompareText(WizardForm.DirEdit.Text,
                 ExpandConstant('{sd}\AsrSubs')) <> 0 then
    Exit;                       // ② 显式指定（/DIR 等）：用户说了算
  best := PickBestDrive(True);
  if best = '' then
    best := PickBestDrive(False);
  if best <> '' then
  begin
    WizardForm.DirEdit.Text := best + '\AsrSubs';
    Log('预填安装目录 = ' + WizardForm.DirEdit.Text +
        '（本地固定盘中剩余空间最大）');
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  p: string;
  i: Integer;
  dt: Cardinal;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  p := WizardDirValue;

  // ★ 必须是纯 ASCII：Lua 的 io.open / os.execute 走 ANSI 接口，
  //   中文路径会被 _isAsciiPath() 整条弃用 → 界面能开、一跑就"任务超时"。
  for i := 1 to Length(p) do
  begin
    if Ord(p[i]) > 127 then
    begin
      MsgBox('安装路径只能包含英文、数字和常规符号，不能有中文。' + #13#10 +
             '例如：D:\AsrSubs、E:\Tools\AsrSubs', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;

  // 必须是「盘符:\...」形式的绝对路径：UNC（\\server\share）和盘符根目录都不行
  if (Length(p) < 4) or (Copy(p, 2, 1) <> ':') or (Copy(p, 3, 1) <> '\') then
  begin
    MsgBox('请选择一个具体的文件夹，例如 D:\AsrSubs。' + #13#10 +
           '不能直接装在盘符根目录，也不能装到网络路径上。', mbError, MB_OK);
    Result := False;
    Exit;
  end;

  // 只能装在本地固定磁盘：U 盘一拔插件就坏，网络盘读几千个小文件会超时
  dt := GetDriveTypeW(Copy(p, 1, 2) + '\');
  if (dt = 1) or (dt = 2) or (dt = 4) or (dt = 5) or (dt = 6) then
  begin
    if dt = 1 then
      MsgBox('找不到这个盘：' + Copy(p, 1, 2) + '\' + #13#10 +
             '盘符是不是写错了？或者这块盘没接上。', mbError, MB_OK)
    else
      MsgBox('只能装在本地硬盘上。' + #13#10 +
             '当前选择的是：' + p + #13#10 +
             'U 盘 / 移动硬盘拔掉就坏，网络盘会因读取太慢而超时。', mbError, MB_OK);
    Result := False;
    Exit;
  end;

  // 这几个字符会被命令行解释掉（Lua 走 os.execute 启动 python）
  if (Pos('&', p) > 0) or (Pos('!', p) > 0) or
     (Pos('^', p) > 0) or (Pos('%', p) > 0) then
  begin
    MsgBox('安装路径不能包含 & ! ^ % 这四个字符。', mbError, MB_OK);
    Result := False;
  end;
end;

// 读安装核心留下的部署清单，确认某个 DaVinci 目录是我们写进去的
// ★ Inno Setup 6.7 的两个签名变化（都踩过，写注释备忘）：
//   · LoadStringFromFile 第二参数是 AnsiString，不是 String
//   · StringChange 改成【原地修改】（var S）+ 返回替换次数，
//     不再是 StringChange(S,...) 返回新串 —— txt := StringChange(...)
//     会报 Type mismatch
function ResolveDirDeployed(const dir: string): Boolean;
var
  man, needle: string;
  raw: AnsiString;
  txt: string;
begin
  Result := False;
  man := ExpandConstant('{app}\_setup\_deployed.json');
  if not LoadStringFromFile(man, raw) then
    Exit;                            // 向导没跑完 → 什么都没部署
  txt := raw;                        // AnsiString → String 直接赋值
  needle := dir;
  StringChange(txt, '\', '/');       // 清单里存的是正斜杠
  StringChange(needle, '\', '/');
  if Pos(needle, txt) > 0 then
    Result := True;
end;

function NeedDeleteResolveDirPD: Boolean;
begin
  Result := ResolveDirDeployed(ExpandConstant(
    '{commonappdata}\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\AsrSubs'));
end;

function NeedDeleteResolveDirAD: Boolean;
begin
  Result := ResolveDirDeployed(ExpandConstant(
    '{userappdata}\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\AsrSubs'));
end;

function NeedDeleteResolveDirPF: Boolean;
begin
  Result := ResolveDirDeployed(ExpandConstant(
    '{commonpf64}\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\AsrSubs'));
end;

//──────────────────────────────────────────────────────────────────────────
// 卸载：把「字幕输出」另存一份到文档目录（第二道保险）
//──────────────────────────────────────────────────────────────────────────
// 第一道保险在 [UninstallDelete]：data\output 根本不在删除表里，
// 所以卸载器本身不会碰用户跑出来的字幕。
//
// 这里再加一道：卸载前把 {app}\data\output 整棵复制到
//   %USERPROFILE%\Documents\AsrSubs 字幕备份\<时间戳>\
// 为什么还要多此一举？用户哪天手动删了安装目录，或者用 Geek 这类工具
// 「强制删除残留」把整个安装目录端掉，原地那份就没了。
// 文档里这份跟安装目录无关，能活下来。
//
// ★ 钩子选 CurUninstallStepChanged(usUninstall)：这个时刻已经在用户点过
//   「确认卸载」之后、真正开始删文件之前。若改用 InitializeUninstall，
//   用户点了取消也会白备份一份，不好。
// ★ 全程 best-effort：备份失败只弹提示，不阻断卸载 —— 反正原地那份没删，
//   丢不了东西。

// 递归复制目录里的文件。
// reparse point（junction / 符号链接）一律跳过、不跟进去 —— 旧版中文门面
// "<安装目录>字幕工具" 就是指向真身的 junction，跟进去会把 6 GB 拷两遍。
// 返回成功复制的文件数；失败数累加进 Failures。
function CopyTreeNoReparse(const src, dst: string; var Failures: Integer): Integer;
var
  fr: TFindRec;
  s, d: string;
begin
  Result := 0;
  if not FindFirst(src + '\*', fr) then
    Exit;
  repeat
    // Win32 的 * 通配会把 . 和 .. 也列出来，要滤掉
    if (fr.Name <> '.') and (fr.Name <> '..') and
       ((fr.Attributes and FA_REPARSE) = 0) then
    begin
      s := src + '\' + fr.Name;
      d := dst + '\' + fr.Name;
      if (fr.Attributes and FA_DIRECTORY) <> 0 then
      begin
        if ForceDirectories(d) then
          Result := Result + CopyTreeNoReparse(s, d, Failures);
      end
      else if CopyFile(s, d, False) then
        Result := Result + 1
      else
        Failures := Failures + 1;
    end;
  until not FindNext(fr);
  FindClose(fr);
end;

// 把「字幕输出」整棵另存到文档目录。
// ★ 单独拆成过程，不要在 CurUninstallStepChanged 里直接写带 Exit 的逻辑 ——
//   卸载钩子里还要做缓存清理，中间 Exit 会把后面的清理一起跳过。
procedure BackupSubtitleOutput;
var
  src, dst: string;
  n, fails: Integer;
begin
  src := ExpandConstant('{app}\data\output');
  if not DirExists(src) then
    Exit;                                  // 从没跑过 → 无产物，直接卸
  dst := ExpandConstant('{userdocs}\AsrSubs 字幕备份') + '\' +
         GetDateTimeString('yyyymmdd-hhnnss', #0, #0);
  if not ForceDirectories(dst) then
    Exit;                                  // 建不了目标目录就算了，原地那份不删
  fails := 0;
  n := CopyTreeNoReparse(src, dst, fails);
  if n = 0 then
    Exit;                                  // 空目录，没什么可备的
  if fails = 0 then
    // SuppressibleMsgBox：静默卸载（/SILENT）下不弹窗、直接返回默认值，
    // 不会把无人值守的卸载卡住
    SuppressibleMsgBox('卸载前已把字幕备份到：' + #13#10 + dst + #13#10 +
      '（共 ' + IntToStr(n) + ' 个文件）',
      mbInformation, MB_OK, IDOK)
  else
    SuppressibleMsgBox('字幕备份不完整：成功 ' + IntToStr(n) + ' 个，失败 ' +
      IntToStr(fails) + ' 个。' + #13#10 +
      '原始文件仍保留在：' + #13#10 + src,
      mbError, MB_OK, IDOK);
end;

// 清掉安装时下载的缓存目录（%TEMP%\qwen3asr_dl）。
// ★ 为什么不在 [UninstallDelete] 里写 "{tmp}\qwen3asr_dl"：
//   Inno 的 {tmp} 不是 %TEMP%，而是 Setup/Uninstall 每次运行新建的临时子目录
//   （%TEMP%\is-XXXXX.tmp），退出即整棵清空 —— 指向它等于什么都没做。
//   缓存的真实位置由安装核心（installer.py）用 tempfile.gettempdir() 决定，
//   就是用户临时目录，所以这里读 TEMP / TMP 环境变量来定位。
function DeleteDownloadCache: Boolean;
var
  base, p: string;
begin
  base := GetEnv('TEMP');
  if base = '' then
    base := GetEnv('TMP');
  if base = '' then
    base := ExpandConstant('{localappdata}') + '\Temp';   // 兜底
  p := AddBackslash(base) + 'qwen3asr_dl';
  if not DirExists(p) then
  begin
    Log('下载缓存不存在，跳过：' + p);
    Result := True;
    Exit;
  end;
  DelTree(p, True, True, True);
  Result := not DirExists(p);
  if Result then
    Log('已删除下载缓存：' + p)
  else
    Log('下载缓存未能完全删除（可手动删）：' + p);
end;

// 摘掉【旧版】中文门面（junction）—— 现在不再建，只清理残留。
// ★★ 只能用 RemoveDir：Windows 的 RemoveDirectory 对 junction 只摘掉
//    重解析点本身，不会动目标目录。换成 DelTree 就会【跟进去】把
//    安装目录真身整棵删掉 —— 那正是卸载最不该发生的事。
procedure RemoveEntryJunction;
var
  p: string;
begin
  p := ExpandConstant('{app}') + '字幕工具';
  if not DirExists(p) then
    Exit;
  if RemoveDir(p) then
    Log('已移除旧版中文门面：' + p)
  else
    Log('旧版中文门面不是 junction 或删不掉，保留原样：' + p);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  outDir: string;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  // usUninstall = 用户已确认卸载、真正开始删文件之前
  // ★ 顺序：先备份（读 data\output），再问要不要连产物一起删
  BackupSubtitleOutput;
  // ★ v1.1.0：一键卸载多给一个选择 —— 想彻底清干净的人不用再去手工翻目录。
  //   默认「否」（保留产物）；静默卸载同样按默认值走，绝不静默删用户字幕。
  outDir := ExpandConstant('{app}\data\output');
  if DirExists(outDir) then
  begin
    if SuppressibleMsgBox(
         '是否连「字幕产物」一起删除？' + #13#10 + outDir + #13#10 + #13#10 +
         '选「否」= 保留（推荐）。无论选哪个，卸载前都会在「文档」里留一份备份。',
         mbConfirmation, MB_YESNO, IDNO) = IDYES then
    begin
      DelTree(outDir, True, True, True);
      Log('已按用户选择删除字幕产物：' + outDir);
    end
    else
      Log('保留字幕产物：' + outDir);
  end;
  // ★ 顺序：备份/清理都做完，再摘掉旧版中文门面（junction）
  RemoveEntryJunction;
  DeleteDownloadCache;
end;
