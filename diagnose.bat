@echo off
cd /d "%~dp0"
py -m pip install -q -r requirements.txt || python -m pip install -q -r requirements.txt
py -m tapline.diagnose %* || python -m tapline.diagnose %*
pause
