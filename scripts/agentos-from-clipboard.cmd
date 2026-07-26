@echo off
REM agentos-from-clipboard.cmd
REM Read clipboard content and send to the bus as a message.
REM
REM Usage:
REM   agentos-from-clipboard.cmd --to codex --from openclaw [--task t-001] [--type HANDOFF]
REM
REM Reads from clipboard via PowerShell `Get-Clipboard`, then pipes to agentos send.
setlocal

for /f "tokens=*" %%i in ('powershell -NoProfile -Command "Get-Clipboard"') do set "CLIP=%%i"

echo %CLIP% | agentos send %*