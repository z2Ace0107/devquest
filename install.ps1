# DevQuest — 一键安装脚本
# 用法: .\install.ps1

Write-Host "DevQuest — 一键安装" -ForegroundColor Cyan

$repo_dir = $PSScriptRoot
$venv_dir = "$repo_dir\.venv"
$venv_python = "$venv_dir\Scripts\python.exe"
$claude_home = "$env:USERPROFILE"
$skills_dir = "$env:USERPROFILE\.claude\skills\devquest"
$claude_json = "$claude_home\.claude.json"
$settings_json = "$env:USERPROFILE\.claude\settings.json"

# 1. 创建虚拟环境 + 安装依赖
Write-Host "[1/5] 安装依赖..."
if (-not (Test-Path $venv_python)) {
    Write-Host "  创建虚拟环境..."
    python -m venv $venv_dir
}
& $venv_python -m pip install -r "$repo_dir\requirements.txt" -q 2>$null
Write-Host "  依赖安装完成"

# 2. 创建 .env（如果不存在）
Write-Host "[2/5] 配置环境变量..."
if (-not (Test-Path "$repo_dir\.env")) {
    if (Test-Path "$repo_dir\.env.example") {
        Copy-Item "$repo_dir\.env.example" "$repo_dir\.env"
        Write-Host "  已创建 .env，请填入你的 API Key" -ForegroundColor Yellow
    } else {
        Write-Host "  未找到 .env.example，请手动创建 .env" -ForegroundColor Yellow
    }
} else {
    Write-Host "  .env 已存在，跳过"
}

# 3. 安装 Skill
Write-Host "[3/5] 安装 Skill..."
New-Item -ItemType Directory -Force -Path $skills_dir | Out-Null
Copy-Item -Force "$repo_dir\skill\SKILL.md" "$skills_dir\SKILL.md"

# 4. 注册 MCP Server（指向 venv Python）
Write-Host "[4/5] 注册 MCP Server..."
$mcp_entry = @{
    devquest = @{
        type    = "stdio"
        command = $venv_python
        args    = @("$repo_dir\backend\mcp_server.py")
        cwd     = $repo_dir
        env     = @{}
    }
}

if (Test-Path $claude_json) {
    $config = Get-Content $claude_json -Raw | ConvertFrom-Json
    if (-not $config.mcpServers) { $config | Add-Member -NotePropertyName mcpServers -NotePropertyValue @{} -Force }
    $config.mcpServers | Add-Member -NotePropertyName devquest -NotePropertyValue $mcp_entry.devquest -Force
    $config | ConvertTo-Json -Depth 4 | Set-Content $claude_json
} else {
    @{ mcpServers = $mcp_entry } | ConvertTo-Json -Depth 4 | Set-Content $claude_json
}

# 5. 注册 MCP 权限
Write-Host "[5/5] 注册权限..."
$permissions = @(
    "mcp__devquest__search_experience",
    "mcp__devquest__save_problem",
    "mcp__devquest__record_feedback",
    "mcp__devquest__ingest_sessions",
    "mcp__devquest__ingest_status",
    "mcp__devquest__extract_from_text",
    "mcp__devquest__list_problems",
    "mcp__devquest__get_dashboard",
    "mcp__devquest__rebuild_index",
    "mcp__devquest__generate_star",
    "mcp__devquest__update_score",
    "mcp__devquest__run_reflection",
    "mcp__devquest__get_suggestions",
    "mcp__devquest__push_feishu_weekly"
)

if (Test-Path $settings_json) {
    $settings = Get-Content $settings_json -Raw | ConvertFrom-Json
    if (-not $settings.permissions) { $settings | Add-Member -NotePropertyName permissions -NotePropertyValue @{} -Force }
    if (-not $settings.permissions.allow) { $settings.permissions | Add-Member -NotePropertyName allow -NotePropertyValue @() -Force }
    $allow = [System.Collections.ArrayList]($settings.permissions.allow)
    foreach ($p in $permissions) {
        if ($p -notin $allow) { $allow.Add($p) | Out-Null }
    }
    $settings.permissions.allow = $allow
    $settings | ConvertTo-Json -Depth 4 | Set-Content $settings_json
} else {
    @{ permissions = @{ allow = $permissions } } | ConvertTo-Json -Depth 4 | Set-Content $settings_json
}

Write-Host ""
Write-Host "安装完成！" -ForegroundColor Green
Write-Host ""
Write-Host "下一步：" -ForegroundColor Yellow
Write-Host "  1. 编辑 $repo_dir\.env，填入你的 API Key"
Write-Host "  2. 重启 Claude Code"
Write-Host "  3. 输入 /devquest save 测试"
