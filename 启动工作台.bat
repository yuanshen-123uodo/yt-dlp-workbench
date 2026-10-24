@echo off
chcp 65001 >nul
cd /d "%~dp0"
title yt-dlp Workbench

rem ---------- 查找 Python（优先 PATH 上的 python，其次 py 启动器） ----------
set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE (
  echo [错误] 未找到 Python，请先安装 Python 3.8 或更高版本：
  echo        https://www.python.org/downloads/
  echo        安装时勾选 "Add Python to PATH"。
  echo.
  pause
  exit /b 1
)

rem ---------- 首次运行自动获取 yt-dlp.exe ----------
if not exist "bin\yt-dlp.exe" (
  if not exist "bin" mkdir "bin"
  echo [首次运行] 正在下载 yt-dlp.exe ...
  where curl >nul 2>nul && (
    curl -fL -o "bin\yt-dlp.exe" https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe
    if errorlevel 1 curl -fL --ssl-no-revoke -o "bin\yt-dlp.exe" https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe
  )
  if not exist "bin\yt-dlp.exe" (
    powershell -NoProfile -Command "Invoke-WebRequest https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe -OutFile bin\yt-dlp.exe"
  )
  if not exist "bin\yt-dlp.exe" (
    echo.
    echo [失败] 自动下载 yt-dlp.exe 失败（网络问题？）。
    echo        请手动下载后放到 bin 目录：
    echo        https://github.com/yt-dlp/yt-dlp/releases/latest
    echo        （已安装 yt-dlp 并加入 PATH 的用户可跳过，服务会自动使用）
    echo.
    pause
    exit /b 1
  )
  echo [完成] yt-dlp.exe 已就绪。
  echo.
)

echo 正在启动 yt-dlp 工作台 ...

%PYEXE% server.py
if errorlevel 1 (
  echo.
  echo 服务异常退出。请确认使用 Python 3.8 及以上版本（本工具仅用标准库，无需安装第三方包）。
  echo.
  pause
)
