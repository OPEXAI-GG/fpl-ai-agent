@echo off
rem FPL Sim: opens your team dashboard in the browser. Close this window to stop it.
title FPL Sim
cd /d "%~dp0"
where py >nul 2>nul && ((py -3 fplsim_local.py || pause) & goto :eof)
for %%P in ("%USERPROFILE%\anaconda3\python.exe" "%LOCALAPPDATA%\anaconda3\python.exe" "%USERPROFILE%\miniconda3\python.exe" "%ProgramData%\anaconda3\python.exe" "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe") do if exist %%P ((%%P fplsim_local.py || pause) & goto :eof)
where python >nul 2>nul && ((python fplsim_local.py || pause) & goto :eof)
echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.
pause
