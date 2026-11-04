@echo off
rem Double-click to run Chip's brain. Keeps the window open if it stops.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -m chipbrain %*
) else (
  python -m chipbrain %*
)
pause
