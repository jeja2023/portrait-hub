@echo off
setlocal
chcp 65001 > nul

set "script_path=%~dp0tools\push_to_github.ps1"
set "powershell_path=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%script_path%" (
    echo [错误] 未找到推送脚本：%script_path%
    set "exit_code=1"
    goto :finish
)

if not exist "%powershell_path%" (
    echo [错误] 未找到 Windows PowerShell：%powershell_path%
    set "exit_code=1"
    goto :finish
)

"%powershell_path%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%script_path%"
set "exit_code=%errorlevel%"

:finish
if not "%exit_code%"=="0" (
    echo [错误] 推送未完成，退出代码：%exit_code%
)

if not defined CI pause
exit /b %exit_code%
