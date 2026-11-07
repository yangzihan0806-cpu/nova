@echo off
rem TartanGrid: start the server with the Security audit panel enabled. Keep this window open.
rem The audit reader credential is created once in tg_audit_token.txt (git-ignored; never share it on screen).
cd /d "%~dp0"
if not exist "tg_audit_token.txt" (
  .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))" > "tg_audit_token.txt"
)
set /p TG_SECURITY_AUDIT_TOKEN=<"tg_audit_token.txt"
echo Security audit enabled. The reader credential is in tg_audit_token.txt in this folder.
call "%~dp0tg_server.bat"
