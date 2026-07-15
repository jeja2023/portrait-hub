#requires -Version 5.1

[CmdletBinding()]
param(
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RepoRootPrefix = $RepoRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar

function Get-GitOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = @(& git --no-pager -C $RepoRoot -c core.safecrlf=false @Arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "Git 命令执行失败：git $($Arguments -join ' ')"
    }
    return $output
}

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & git --no-pager -C $RepoRoot -c core.safecrlf=false @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git 命令执行失败：git $($Arguments -join ' ')"
    }
}

function Confirm-Action {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Prompt
    )

    $answer = Read-Host "$Prompt [y/N]"
    return $answer -match "(?i)^(y|yes)$"
}

function Get-ChangedFiles {
    $files = @()
    $files += @(Get-GitOutput -Arguments @("-c", "core.quotePath=false", "diff", "--name-only"))
    $files += @(Get-GitOutput -Arguments @("-c", "core.quotePath=false", "diff", "--cached", "--name-only"))
    $files += @(Get-GitOutput -Arguments @("-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard"))
    return @($files | Where-Object { $_ } | Sort-Object -Unique)
}

function Test-RiskyFileName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $Path))
    if (-not [System.IO.File]::Exists($fullPath)) {
        return $false
    }

    $name = [System.IO.Path]::GetFileName($Path)
    $lowerName = $name.ToLowerInvariant()
    $allowedEnvironmentTemplates = @(".env.example", ".env.sample", ".env.template")

    if ($lowerName -like ".env*" -and $allowedEnvironmentTemplates -notcontains $lowerName -and $lowerName -notlike ".env.*.example") {
        return $true
    }
    if ($lowerName -in @(".npmrc", ".pypirc", "credentials.json", "service-account.json", "id_rsa", "id_ed25519")) {
        return $true
    }
    if ($lowerName -match "\.(pem|key|p12|pfx|jks|keystore)$") {
        return $true
    }
    return $false
}

function Find-SecretSignatures {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Paths
    )

    $patterns = @(
        @{ Name = "GitHub 令牌"; Pattern = "gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}" },
        @{ Name = "AWS 访问密钥"; Pattern = "AKIA[0-9A-Z]{16}" },
        @{ Name = "Slack 令牌"; Pattern = "xox[baprs]-[A-Za-z0-9-]{10,}" },
        @{ Name = "私钥"; Pattern = "-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----" }
    )
    $findings = @()

    foreach ($relativePath in $Paths) {
        $fullPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $relativePath))
        if (-not $fullPath.StartsWith($RepoRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "检测到仓库外路径：$relativePath"
        }
        if (-not [System.IO.File]::Exists($fullPath)) {
            continue
        }

        $fileInfo = Get-Item -LiteralPath $fullPath
        if ($fileInfo.Length -gt 5MB) {
            continue
        }

        try {
            $content = [System.IO.File]::ReadAllText($fullPath)
        }
        catch {
            continue
        }
        if ($content.IndexOf([char]0) -ge 0) {
            continue
        }

        foreach ($entry in $patterns) {
            if ($content -match $entry.Pattern) {
                $findings += "$relativePath（$($entry.Name)）"
            }
        }
    }

    return @($findings | Sort-Object -Unique)
}

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "未找到 git，请先安装 Git 并确保其位于 PATH 中。"
    }

    $reportedRoot = (Get-GitOutput -Arguments @("rev-parse", "--show-toplevel") | Select-Object -First 1)
    $resolvedReportedRoot = [System.IO.Path]::GetFullPath($reportedRoot)
    if (-not $resolvedReportedRoot.Equals($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "脚本目录不是当前仓库根目录：$RepoRoot"
    }

    $originUrl = (Get-GitOutput -Arguments @("remote", "get-url", "origin") | Select-Object -First 1)
    $isGitHubOrigin = $originUrl -match "(?i)^(https://github\.com/|ssh://(?:[^@/]+@)?github\.com(?::\d+)?/|[^@/\s]+@github\.com:)"
    if (-not $isGitHubOrigin) {
        throw "origin 不是受支持的 GitHub 地址；已拒绝推送。"
    }

    $branchOutput = @(& git -C $RepoRoot symbolic-ref --quiet --short HEAD)
    if ($LASTEXITCODE -ne 0 -or $branchOutput.Count -eq 0) {
        throw "当前处于 detached HEAD 状态；请先切换或创建分支。"
    }
    $branch = $branchOutput[0]
    & git -C $RepoRoot check-ref-format --branch $branch *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "当前分支名无效：$branch"
    }
    if ($branch -cne "main") {
        throw "本仓库只允许从 main 分支提交和推送；当前分支为 $branch。"
    }

    Write-Host "仓库：$RepoRoot"
    Write-Host "远端：github.com"
    Write-Host "分支：$branch"

    $displayLimit = 80
    $status = @(Get-GitOutput -Arguments @("-c", "core.quotePath=false", "status", "--short", "--untracked-files=all"))
    if ($status.Count -gt 0) {
        Write-Host ""
        Write-Host "待处理改动："
        $status | Select-Object -First $displayLimit | ForEach-Object { Write-Host $_ }
        if ($status.Count -gt $displayLimit) {
            Write-Host "... 另有 $($status.Count - $displayLimit) 个文件未逐项显示。"
        }
    }
    else {
        Write-Host ""
        Write-Host "工作区没有未提交改动。"
    }

    $changedFiles = @(Get-ChangedFiles)
    $riskyFiles = @($changedFiles | Where-Object { Test-RiskyFileName -Path $_ })
    if ($riskyFiles.Count -gt 0) {
        Write-Host ""
        Write-Host "检测到可能包含凭据的文件名：" -ForegroundColor Red
        $riskyFiles | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        throw "安全检查未通过；请确认文件内容并更新忽略规则后再操作。"
    }

    $secretFindings = @(Find-SecretSignatures -Paths $changedFiles)
    if ($secretFindings.Count -gt 0) {
        Write-Host ""
        Write-Host "检测到高置信度密钥特征（具体内容未显示）：" -ForegroundColor Red
        $secretFindings | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        throw "安全检查未通过；请移除或轮换相关凭据。"
    }

    & git -C $RepoRoot -c core.safecrlf=false diff --check HEAD --
    if ($LASTEXITCODE -ne 0) {
        throw "检测到空白字符错误，请修复后再提交。"
    }

    if ($CheckOnly) {
        Write-Host ""
        Write-Host "[成功] 只读检查通过；未暂存、提交或推送任何内容。" -ForegroundColor Green
        exit 0
    }

    Write-Host ""
    Write-Host "正在刷新 origin 状态..."
    Invoke-GitCommand -Arguments @("fetch", "--prune", "origin")

    $remoteRef = "refs/remotes/origin/$branch"
    & git -C $RepoRoot show-ref --verify --quiet $remoteRef
    $remoteBranchExists = $LASTEXITCODE -eq 0
    if ($remoteBranchExists) {
        $counts = ((Get-GitOutput -Arguments @("rev-list", "--left-right", "--count", "HEAD...$remoteRef") | Select-Object -First 1) -split "\s+")
        $remoteOnlyCount = [int]$counts[1]
        if ($remoteOnlyCount -gt 0) {
            throw "远端分支领先本地 $remoteOnlyCount 个提交；请先拉取并处理合并或变基。"
        }
    }

    if ($branch -in @("main", "master")) {
        $protectedConfirmation = Read-Host "当前位于受保护分支 $branch；请输入完整分支名以继续"
        if ($protectedConfirmation -cne $branch) {
            Write-Host "操作已取消；没有修改暂存区。"
            exit 2
        }
    }

    if ($status.Count -gt 0) {
        if (-not (Confirm-Action -Prompt "确认暂存并提交以上全部改动")) {
            Write-Host "操作已取消；没有修改暂存区。"
            exit 2
        }

        Invoke-GitCommand -Arguments @("add", "--all", "--", ".")
        & git -C $RepoRoot diff --cached --quiet
        if ($LASTEXITCODE -eq 0) {
            Write-Host "没有可提交的改动。"
        }
        elseif ($LASTEXITCODE -eq 1) {
            Write-Host ""
            Write-Host "即将提交："
            Invoke-GitCommand -Arguments @("-c", "core.quotePath=false", "diff", "--cached", "--stat", "--stat-count=$displayLimit")

            $commitMessage = Read-Host "请输入提交说明（直接回车使用“更新项目代码”）"
            if ([string]::IsNullOrWhiteSpace($commitMessage)) {
                $commitMessage = "更新项目代码"
            }
            Invoke-GitCommand -Arguments @("commit", "--message", $commitMessage)
        }
        else {
            throw "无法检查暂存区状态。"
        }
    }

    $headBeforePush = (Get-GitOutput -Arguments @("rev-parse", "HEAD") | Select-Object -First 1)
    Write-Host ""
    Write-Host "正在推送当前分支 $branch..."
    Invoke-GitCommand -Arguments @("push", "--set-upstream", "origin", $branch)

    $remoteHead = (Get-GitOutput -Arguments @("rev-parse", "refs/remotes/origin/$branch") | Select-Object -First 1)
    if ($remoteHead -ne $headBeforePush) {
        throw "推送命令已返回，但远端跟踪分支未指向当前提交。"
    }

    Write-Host ""
    Write-Host "[成功] 已将 $branch 推送至 GitHub，提交：$($headBeforePush.Substring(0, 12))" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ""
    Write-Host "[错误] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
