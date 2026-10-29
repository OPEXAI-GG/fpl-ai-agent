@echo off
"C:\Program Files\nodejs\node.exe" "%~dp0set-codex-sandbox.cjs"
if errorlevel 1 (
  echo Fix failed. Keep this error and report it to 7amo.
) else (
  echo Fix applied. Restart Munder Difflin once, then tell 7amo to verify and resume.
)
pause