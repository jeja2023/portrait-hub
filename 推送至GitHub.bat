@echo off
:: 设置编码为UTF-8，防止中文乱码
chcp 65001 > nul

echo 正在将更新和修改推送至 GitHub 远程仓库...

:: 添加所有修改
git add .
if %errorlevel% neq 0 (
    echo [错误] git add 失败，请检查本地修改。
    pause
    exit /b %errorlevel%
)

:: 提示用户输入提交信息，如果为空则使用默认提交信息
set /p commit_msg="请输入提交说明 (直接回车将使用默认说明 '更新项目代码'): "
if "%commit_msg%"=="" set commit_msg=更新项目代码

:: 提交修改
git commit -m "%commit_msg%"
if %errorlevel% neq 0 (
    echo [提示] 没有检测到需要提交的修改，或者提交未成功。
)

:: 推送至远程仓库
echo 正在推送至远程仓库...
git push origin main
if %errorlevel% neq 0 (
    echo 正在尝试使用当前分支推送...
    git push
)

if %errorlevel% neq 0 (
    echo [错误] 推送失败，请检查网络连接或远程仓库权限。
) else (
    echo [成功] 更新和修改已成功推送至 GitHub 远程仓库！
)

pause
