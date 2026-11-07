@echo off
rem TartanGrid: public HTTPS address for other devices. Keep this window open.
cd /d "%~dp0"
set "CF=cloudflared"
where cloudflared >nul 2>nul && goto run
set "CF=%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe"
if exist "%CF%" goto run
set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
if exist "%CF%" goto run
set "CF=%ProgramFiles%\cloudflared\cloudflared.exe"
if exist "%CF%" goto run
echo Installing cloudflared with winget...
winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
set "CF=%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe"
if exist "%CF%" goto run
set "CF=%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
if exist "%CF%" goto run
set "CF=%ProgramFiles%\cloudflared\cloudflared.exe"
if exist "%CF%" goto run
echo [X] cloudflared was not found after installing. Close this window and double-click tg_tunnel.bat again.
pause
exit /b 1
:run
echo.
echo Find the line with  https://....trycloudflare.com  below. That is the address for other devices.
echo Keep this window open. Closing it stops the address (a new one is created next time).
echo.
if exist "%~dp0tg_tunnel.log" del "%~dp0tg_tunnel.log"
"%CF%" tunnel --url http://127.0.0.1:8000 --logfile "%~dp0tg_tunnel.log"
pause
