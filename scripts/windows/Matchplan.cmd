@echo off
rem ===========================================================================================
rem  Matchplan: your FPL planning website, on your own PC. Double-click to start it.
rem
rem  Each start it: updates the program, downloads the newest football data if there is any,
rem  refreshes FPL's public data, builds and starts the site, and opens it on your team.
rem  Leave the window open while you use the site. Close the window to stop it.
rem
rem  It only reads public data. It never signs in to FPL and never changes your team.
rem  Settings (edit with Notepad if you need to):
rem ===========================================================================================
set "ENTRY=8700026"
set "BRANCH=claude/project-thread-9jksh9"
set "REPO=https://github.com/OPEXAI-GG/fpl-ai-agent"
set "HOME_DIR=%LOCALAPPDATA%\Matchplan"
set "APP=%HOME_DIR%\fpl-ai-agent"
rem ===========================================================================================

setlocal EnableExtensions
title Matchplan
if not exist "%HOME_DIR%" mkdir "%HOME_DIR%"
set "PATH=%USERPROFILE%\.local\bin;%ProgramFiles%\nodejs;%ProgramFiles%\Git\cmd;%PATH%"

echo.
echo  Matchplan - starting. This window shows progress; keep it open while you use the site.
echo.

rem ---- tools: git, uv (Python), Node.js ----------------------------------------------------
where git >nul 2>nul || call :install "Git" "Git.Git" || goto :fail
where uv >nul 2>nul || call :install_uv || goto :fail
where node >nul 2>nul || call :install "Node.js" "OpenJS.NodeJS.LTS" || goto :fail
node -e "process.exit(+process.versions.node.split('.')[0] >= 22 ? 0 : 1)" || (
  echo  Node.js 22 or newer is needed. Your version:
  node --version
  call :install "Node.js" "OpenJS.NodeJS.LTS" || goto :fail
)

rem ---- program: clone once, then follow the branch ------------------------------------------
if not exist "%APP%\.git" (
  echo  Downloading Matchplan for the first time...
  git clone --branch "%BRANCH%" "%REPO%.git" "%APP%" || git clone "%REPO%.git" "%APP%" || goto :fail
) else (
  echo  Checking for program updates...
  pushd "%APP%"
  git fetch --quiet origin "%BRANCH%" 2>nul && git reset --quiet --hard FETCH_HEAD
  if errorlevel 1 (
    rem the branch is gone once it is merged: follow master instead
    git fetch --quiet origin master 2>nul && git reset --quiet --hard FETCH_HEAD
    if errorlevel 1 echo  Could not check for updates ^(offline?^). Using the version you have.
  )
  popd
)

rem ---- run -----------------------------------------------------------------------------------
cd /d "%APP%"
uv run --frozen fpl-agent web local --entry %ENTRY%
if errorlevel 1 goto :fail
goto :eof

:install_uv
echo  uv (it runs the Python side of Matchplan) is not installed.
choice /M "  Install uv now from astral.sh"
if errorlevel 2 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" || exit /b 1
where uv >nul 2>nul || (echo  uv was installed but is not on PATH yet. Close this window and double-click again. & exit /b 1)
exit /b 0

:install
echo  %~1 is not installed (or too old).
where winget >nul 2>nul || (echo  Install %~1 yourself, then double-click this again. & exit /b 1)
choice /M "  Install %~1 now with winget"
if errorlevel 2 exit /b 1
winget install --id %~2 -e --accept-source-agreements --accept-package-agreements || exit /b 1
echo  %~1 installed. Close this window and double-click Matchplan again.
pause
exit 1

:fail
echo.
echo  Matchplan stopped. The messages above say why; logs are in
echo  %APP%\data_cache\web\logs
pause
exit /b 1
