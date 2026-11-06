@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 打包用 Python：项目自带的 .venv 优先，其次是装好的运行环境。
rem ★ 以前这里指向 DaVinci-ASR 插件的 venv —— 那插件已删，
rem   打包环境必须完全自持，否则 build.bat 直接失效。
rem ★ 顺序有讲究：3.0 迁移会把 D:\AsrSubs\runtime 搬到 D:\AsrSubs\runtime，
rem   所以两个位置都要认，否则迁移完这台机器就打不了包。
set PY=%~dp0..\.venv\Scripts\python.exe
if not exist "%PY%" set PY=D:\AsrSubs\runtime\Scripts\python.exe
if exist "%PY%" goto :havepy
rem 最后一招：PATH 里能找到的 python（取第一个）
for /f "delims=" %%P in ('where python 2^>nul') do (
  set "PY=%%P"
  goto :havepy
)
:havepy
rem ★ v4.8.16：安装核心是 tkinter GUI，打包 Python 必须带 tkinter。
rem   缺 tkinter 时 PyInstaller 静态分析不报错，但打出的 exe 一启动就
rem   打印"缺少 tkinter，无法启动界面"退出（workbuddy 自带的 3.13 就没带）。
rem   当前 PY 不合格就换 py launcher 的 3.12（PyInstaller 6.22.3 + tkinter 8.6）。
"%PY%" -c "import tkinter" >nul 2>&1
if not errorlevel 1 goto :pyok
py -3.12 -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo [错误] 找不到带 tkinter 的 Python（当前 %PY% 缺 tkinter，py -3.12 也不可用）
  echo        安装核心是 tkinter GUI；缺 tkinter 会打出启动即退的安装核心。
  pause & exit /b 1
)
for /f "delims=" %%P in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
:pyok
set ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

echo ============================================
echo  AsrSubs · 安装器 打包
echo  PyInstaller onedir + Inno Setup
echo ============================================
echo.

if not exist "%PY%" (
  echo [错误] 找不到 Python: %PY%
  pause & exit /b 1
)
if not exist "%ISCC%" (
  echo [错误] 找不到 Inno Setup 编译器: %ISCC%
  echo        先装 Inno Setup 6: https://jrsoftware.org/isdl.php
  pause & exit /b 1
)

if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
if exist *.spec del /q *.spec

rem ★★★ v3.2.6 新增：打包前先校验，防止打出旧版本 ★★★
rem   事故背景：3.0.6→3.2.6 的改动只做在装机版单文件上，lua_src 分片停在
rem   3.0.5，version.txt 也停在 3.0.5。那时跑本脚本会打出「3.0.5 的程序 +
rem   3.2.6 的说明书」，搜索/高亮/多选删除/粗剪全部丢失。
rem   现在源码是单文件制，这两道闸保证「代码版本号 == version.txt == 安装器」。
echo [0/3] 校验源码与版本号...
"%PY%" "%~dp0..\01_源码\tools\build_lua.py"
if errorlevel 1 (
  echo [失败] 源码校验没过，打包中止（防止打出旧版本）
  pause & exit /b 1
)
"%PY%" "%~dp0..\01_源码\tools\sync_version.py" --check
if errorlevel 1 (
  echo [失败] 版本号不同步，打包中止。先跑 tools\sync_version.py
  pause & exit /b 1
)
"%PY%" "%~dp0..\01_源码\tools\sync_payload.py" --check
if errorlevel 1 (
  echo [失败] payload 与源码不一致，打包中止。先跑 tools\sync_payload.py
  pause & exit /b 1
)
echo [0/3] 校验通过。
echo.

echo [1/2] PyInstaller 打包安装核心（onedir）...
"%PY%" -m PyInstaller ^
  --onedir ^
  --noconsole ^
  --clean ^
  --name "安装核心" ^
  --add-data "payload;payload" ^
  installer.py
if errorlevel 1 (
  echo [失败] PyInstaller 出错，请看上面的日志
  pause & exit /b 1
)
if not exist "dist\安装核心\安装核心.exe" (
  echo [失败] 没有生成 安装核心.exe
  pause & exit /b 1
)

echo [2/2] Inno Setup 编译安装包...
"%ISCC%" "AsrSubs.iss"
if errorlevel 1 (
  echo [失败] Inno 编译出错，请看上面的日志
  pause & exit /b 1
)

echo.
if exist "dist\AsrSubs安装器.exe" (
  echo [完成] dist\AsrSubs安装器.exe
  for %%F in ("dist\AsrSubs安装器.exe") do echo        大小: %%~zF 字节
  echo        双击运行 → 自动预填空间最大的本地盘\AsrSubs（可改任意本地盘/目录）→ 自动弹出下载向导
) else (
  echo [失败] 没有生成安装包
)
echo.
pause
