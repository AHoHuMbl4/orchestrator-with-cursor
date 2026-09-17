# Локальная установка оркестрации для Claude Code, Codex, Kimi.
# Windows PowerShell 5.1 + PowerShell 7 (pwsh на Linux/macOS).
# Запуск из рабочей папки: pwsh /path/to/orchestration-kit/install-local.ps1
# Повторный запуск идемпотентен.
param(
    [switch]$Global
)

$ErrorActionPreference = "Stop"

# --- helpers (все функции до точки входа) ---

function Get-OrchTempDir {
    $d = $env:TEMP
    if (-not $d) { $d = $env:TMP }
    if (-not $d) { $d = [System.IO.Path]::GetTempPath() }
    if (-not $d) { $d = "/tmp" }
    return $d
}

function Get-ForwardSlashPath {
    param([string]$Path)
    return ($Path -replace '\\', '/')
}

function ConvertTo-OrchJsonString {
    param([string]$Text)
    $t = $Text.Replace('\', '\\')
    $t = $t.Replace('"', '\"')
    return $t
}

function Invoke-OrchPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$PyArgs
    )
    if ($script:PyCmd.Count -ge 2) {
        $rest = @()
        $i = 1
        while ($i -lt $script:PyCmd.Count) {
            $rest += $script:PyCmd[$i]
            $i++
        }
        $rest += $PyArgs
        & $script:PyCmd[0] @rest
    } else {
        & $script:PyCmd[0] @PyArgs
    }
}

function New-OrchHookCommand {
    param(
        [string]$Action,
        [string]$Engine,
        [switch]$WindowsStyle
    )
    $kitFwd = Get-ForwardSlashPath -Path $script:Kit
    $reg = "$kitFwd/bin/reground.py"
    if ($script:PyCmd.Count -ge 2 -and $script:PyCmd[0] -eq "py") {
        return "`"py`" `"-3`" `"$reg`" $Action --engine $Engine"
    }
    $pyFwd = Get-ForwardSlashPath -Path $script:PyExe
    return "`"$pyFwd`" `"$reg`" $Action --engine $Engine"
}

function Write-OrchMergePy {
    param([string]$OutPath)
    $code = @'
import json, os, sys
snip_path = sys.argv[1]
out_path = sys.argv[2]
snip = json.load(open(snip_path, encoding="utf-8"))
cur = {}
if os.path.exists(out_path):
    try:
        cur = json.load(open(out_path, encoding="utf-8"))
    except Exception:
        cur = {}
hooks = cur.setdefault("hooks", {})
for event, entries in snip.get("hooks", {}).items():
    merged = hooks.get(event, [])
    have = {json.dumps(e, sort_keys=True) for e in merged}
    for e in entries:
        if json.dumps(e, sort_keys=True) not in have:
            merged.append(e)
    hooks[event] = merged
if "description" in snip and "description" not in cur:
    cur["description"] = snip["description"]
json.dump(cur, open(out_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
open(out_path, "a", encoding="utf-8").write("\n")
'@
    [System.IO.File]::WriteAllText($OutPath, $code)
}

function Find-OrchPython {
    $script:HavePy = $false
    $script:PyExe = ""
    $script:PyCmd = @()

    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        $script:PyExe = $cmd.Source
        $script:PyCmd = @($script:PyExe)
        $script:HavePy = $true
        return
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $probe = & py -3 -c "print(1)" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $script:PyExe = "py"
                $script:PyCmd = @("py", "-3")
                $script:HavePy = $true
                return
            }
        } catch {
            # py -3 недоступен
        }
    }

    $cmd3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($cmd3 -and $cmd3.Source) {
        $script:PyExe = $cmd3.Source
        $script:PyCmd = @($script:PyExe)
        $script:HavePy = $true
        return
    }
}

function Test-OrchIntegrity {
    param([string]$KitDir)
    $sumsFile = Join-Path $KitDir "SHA256SUMS"
    if (-not (Test-Path -LiteralPath $sumsFile)) {
        Write-Host "ОШИБКА: нет файла SHA256SUMS в $KitDir"
        exit 1
    }
    $bad = @()
    $lines = Get-Content -LiteralPath $sumsFile -Encoding UTF8
    foreach ($line in $lines) {
        $trim = $line.Trim()
        if (-not $trim) { continue }
        if ($trim.StartsWith("#")) { continue }
        if ($trim.Length -lt 66) {
            $bad += "битая строка: $trim"
            continue
        }
        $hashExpected = $trim.Substring(0, 64)
        $rest = $trim.Substring(64)
        if (-not ($rest.StartsWith("  ") -or $rest.StartsWith(" *"))) {
            $bad += "битый разделитель: $trim"
            continue
        }
        $rel = $rest.Substring(2).Trim()
        if ($rel.StartsWith("./")) {
            $rel = $rel.Substring(2)
        }
        $filePath = Join-Path $KitDir ($rel -replace '/', [IO.Path]::DirectorySeparatorChar)
        if (-not (Test-Path -LiteralPath $filePath)) {
            $bad += "нет файла: $rel"
            continue
        }
        try {
            $actual = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash
            if ($actual.ToLowerInvariant() -ne $hashExpected.ToLowerInvariant()) {
                $bad += "несовпадение: $rel"
            }
        } catch {
            $bad += "ошибка чтения: $rel ($($_.Exception.Message))"
        }
    }
    if ($bad.Count -gt 0) {
        Write-Host "ОШИБКА: суммы не сошлись"
        foreach ($b in $bad) { Write-Host "  $b" }
        exit 1
    }
}

function Copy-OrchSkillTree {
    param(
        [string]$SrcOrch,
        [string]$DestOrch
    )
    $roles = Join-Path (Join-Path $DestOrch "references") "roles"
    if (-not (Test-Path -LiteralPath $roles)) {
        New-Item -ItemType Directory -Force -Path $roles | Out-Null
    }
    if (-not (Test-Path -LiteralPath $DestOrch)) {
        New-Item -ItemType Directory -Force -Path $DestOrch | Out-Null
    }
    Copy-Item -Path (Join-Path $SrcOrch "*") -Destination $DestOrch -Recurse -Force
}

function Copy-OrchFactoryRoles {
    param([string]$ClaudeOrch)
    $factory = Join-Path $ClaudeOrch "_factory_roles"
    $rolesDir = Join-Path (Join-Path $ClaudeOrch "references") "roles"
    if (-not (Test-Path -LiteralPath $factory)) { return }
    if (-not (Test-Path -LiteralPath $rolesDir)) {
        New-Item -ItemType Directory -Force -Path $rolesDir | Out-Null
    }
    $files = Get-ChildItem -LiteralPath $factory -Recurse -File -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($factory.Length).TrimStart('\', '/')
        $dest = Join-Path $rolesDir $rel
        $destParent = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $destParent)) {
            New-Item -ItemType Directory -Force -Path $destParent | Out-Null
        }
        if (-not (Test-Path -LiteralPath $dest)) {
            Copy-Item -LiteralPath $f.FullName -Destination $dest
        }
    }
}

function Install-OrchSkills {
    $srcOrch = Join-Path (Join-Path $script:Kit "skills") "orchestration"
    $claudeOrch = Join-Path (Join-Path $script:ClaudeDir "skills") "orchestration"
    $agentsOrch = Join-Path (Join-Path (Join-Path $script:Target ".agents") "skills") "orchestration"
    $cmdsDir = Join-Path $script:ClaudeDir "commands"
    if (-not (Test-Path -LiteralPath (Join-Path $script:ClaudeDir "skills"))) {
        New-Item -ItemType Directory -Force -Path (Join-Path $script:ClaudeDir "skills") | Out-Null
    }
    if (-not (Test-Path -LiteralPath $cmdsDir)) {
        New-Item -ItemType Directory -Force -Path $cmdsDir | Out-Null
    }
    if (-not (Test-Path -LiteralPath (Join-Path $script:Target ".agents"))) {
        New-Item -ItemType Directory -Force -Path (Join-Path (Join-Path $script:Target ".agents") "skills") | Out-Null
    }
    Copy-OrchSkillTree -SrcOrch $srcOrch -DestOrch $claudeOrch
    Copy-OrchSkillTree -SrcOrch $srcOrch -DestOrch $agentsOrch
    Copy-OrchFactoryRoles -ClaudeOrch $claudeOrch
    $menuSrc = Join-Path (Join-Path $script:Kit "commands") "claude-orch-menu.md"
    $menuDst = Join-Path $cmdsDir "orch-menu.md"
    Copy-Item -LiteralPath $menuSrc -Destination $menuDst -Force
    Write-Host "  скилл+команда: $($script:ClaudeDir) (+ .agents/skills в папке)"
}

function Install-OrchClaudeHooks {
    if (-not $script:HavePy) {
        Write-Host "  пропущено (нет python)"
        return
    }
    $tmp = Get-OrchTempDir
    $snipPath = Join-Path $tmp "orch-claude-snippet.json"
    $mergePy = Join-Path $tmp "orch-merge-claude.py"
    $cmdSs = New-OrchHookCommand -Action "session-start" -Engine "claude"
    $cmdPs = New-OrchHookCommand -Action "prompt-submit" -Engine "claude"
    $cmdPt = New-OrchHookCommand -Action "post-tool" -Engine "claude"
    $eSs = ConvertTo-OrchJsonString -Text $cmdSs
    $ePs = ConvertTo-OrchJsonString -Text $cmdPs
    $ePt = ConvertTo-OrchJsonString -Text $cmdPt
    $snip = @"
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "$eSs", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "$ePs", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "$ePt", "timeout": 10}]}
    ]
  }
}
"@
    [System.IO.File]::WriteAllText($snipPath, $snip)
    Write-OrchMergePy -OutPath $mergePy
    $settings = Join-Path $script:ClaudeDir "settings.json"
    try {
        Invoke-OrchPython -PyArgs @($mergePy, $snipPath, $settings)
        Write-Host "  хуки SessionStart/UserPromptSubmit/PostToolUse (абсолютный python)"
    } catch {
        Write-Host "  предупреждение: не удалось смержить Claude hooks: $($_.Exception.Message)"
    }
}

function Install-OrchCodexHooks {
    if (-not $script:HavePy) {
        Write-Host "  .codex/hooks.json пропущен (нет python)"
        return
    }
    $hooksDir = Split-Path -Parent $script:CodexHooks
    if (-not (Test-Path -LiteralPath $hooksDir)) {
        New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null
    }
    $tmp = Get-OrchTempDir
    $snipPath = Join-Path $tmp "orch-codex-snippet.json"
    $mergePy = Join-Path $tmp "orch-merge-codex.py"
    $cmdSs = New-OrchHookCommand -Action "session-start" -Engine "codex"
    $cmdPs = New-OrchHookCommand -Action "prompt-submit" -Engine "codex"
    $cmdPt = New-OrchHookCommand -Action "post-tool" -Engine "codex"
    $winSs = New-OrchHookCommand -Action "session-start" -Engine "codex" -WindowsStyle
    $winPs = New-OrchHookCommand -Action "prompt-submit" -Engine "codex" -WindowsStyle
    $winPt = New-OrchHookCommand -Action "post-tool" -Engine "codex" -WindowsStyle
    $eSs = ConvertTo-OrchJsonString -Text $cmdSs
    $ePs = ConvertTo-OrchJsonString -Text $cmdPs
    $ePt = ConvertTo-OrchJsonString -Text $cmdPt
    $wSs = ConvertTo-OrchJsonString -Text $winSs
    $wPs = ConvertTo-OrchJsonString -Text $winPs
    $wPt = ConvertTo-OrchJsonString -Text $winPt
    $snip = @"
{
  "description": "orchestration-kit: сверка курса",
  "hooks": {
    "SessionStart": [
      {"matcher": "startup|resume|clear|compact",
       "hooks": [{"type": "command", "command": "$eSs", "commandWindows": "$wSs", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "$ePs", "commandWindows": "$wPs", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "$ePt", "commandWindows": "$wPt", "timeout": 10}]}
    ]
  }
}
"@
    [System.IO.File]::WriteAllText($snipPath, $snip)
    Write-OrchMergePy -OutPath $mergePy
    try {
        Invoke-OrchPython -PyArgs @($mergePy, $snipPath, $script:CodexHooks)
        Write-Host "  $($script:CodexHooks) (после первого запуска codex: /hooks -> доверить)"
    } catch {
        Write-Host "  предупреждение: не удалось смержить Codex hooks: $($_.Exception.Message)"
    }
    $promptsDir = Join-Path (Join-Path $Home ".codex") "prompts"
    if (-not (Test-Path -LiteralPath $promptsDir)) {
        New-Item -ItemType Directory -Force -Path $promptsDir | Out-Null
    }
    $menuSrc = Join-Path (Join-Path $script:Kit "commands") "codex-orch-menu.md"
    $menuDst = Join-Path $promptsDir "orch-menu.md"
    Copy-Item -LiteralPath $menuSrc -Destination $menuDst -Force
    Write-Host "  ~/.codex/prompts/orch-menu.md (команда /prompts:orch-menu в Codex)"
}

function Remove-OrchKimiHookBlock {
    param([string]$CfgPath)
    $markerStart = "# >>> orchestration-kit hooks >>>"
    $markerEnd = "# <<< orchestration-kit hooks <<<"
    $lines = Get-Content -LiteralPath $CfgPath -Encoding UTF8
    $out = New-Object System.Collections.Generic.List[string]
    $skip = $false
    foreach ($line in $lines) {
        if ($line -eq $markerStart) {
            $skip = $true
            continue
        }
        if ($skip) {
            if ($line -eq $markerEnd) {
                $skip = $false
            }
            continue
        }
        $out.Add($line) | Out-Null
    }
    [System.IO.File]::WriteAllLines($CfgPath, $out.ToArray())
}

function Install-OrchKimi {
    $kimiHome = $env:KIMI_HOME
    if (-not $kimiHome) {
        $kimiHome = Join-Path $Home ".kimi-code"
    }
    $script:KimiDir = $kimiHome
    $srcOrch = Join-Path (Join-Path $script:Kit "skills") "orchestration"
    $kimiOrch = Join-Path (Join-Path $script:KimiDir "skills") "orchestration"
    $homeAgents = Join-Path (Join-Path (Join-Path $Home ".agents") "skills") "orchestration"
    if (-not (Test-Path -LiteralPath (Join-Path $script:KimiDir "skills"))) {
        New-Item -ItemType Directory -Force -Path (Join-Path $script:KimiDir "skills") | Out-Null
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Home ".agents"))) {
        New-Item -ItemType Directory -Force -Path (Join-Path (Join-Path $Home ".agents") "skills") | Out-Null
    }
    Copy-OrchSkillTree -SrcOrch $srcOrch -DestOrch $kimiOrch
    Copy-OrchSkillTree -SrcOrch $srcOrch -DestOrch $homeAgents
    Write-Host "  скилл: ~/.kimi-code/skills + ~/.agents/skills"

    if (-not $script:HavePy) {
        Write-Host "  хуки Kimi пропущены (нет python)"
        return
    }

    $kimiCfg = Join-Path $script:KimiDir "config.toml"
    if (-not (Test-Path -LiteralPath $kimiCfg)) {
        New-Item -ItemType File -Force -Path $kimiCfg | Out-Null
    }
    $cfgText = Get-Content -LiteralPath $kimiCfg -Raw -Encoding UTF8
    if (-not $cfgText) { $cfgText = "" }
    $hasBlock = $cfgText.Contains("orchestration-kit hooks")
    $hasOldPy = $false
    if ($hasBlock -and $cfgText.Contains('command = "python3 ')) {
        $hasOldPy = $true
    }
    if ($hasBlock -and $hasOldPy) {
        Copy-Item -LiteralPath $kimiCfg -Destination ($kimiCfg + ".bak-orch") -Force
        Remove-OrchKimiHookBlock -CfgPath $kimiCfg
        Write-Host "  ~/.kimi-code/config.toml: старый блок хуков заменён (абсолютный python)"
        $cfgText = Get-Content -LiteralPath $kimiCfg -Raw -Encoding UTF8
        if (-not $cfgText) { $cfgText = "" }
        $hasBlock = $cfgText.Contains("orchestration-kit hooks")
    }
    if (-not $hasBlock) {
        Copy-Item -LiteralPath $kimiCfg -Destination ($kimiCfg + ".bak-orch") -Force
        $cmdPs = New-OrchHookCommand -Action "prompt-submit" -Engine "kimi"
        $cmdHb = New-OrchHookCommand -Action "heartbeat" -Engine "kimi"
        # TOML basic string: экранируем внутренние двойные кавычки
        $cmdPsToml = $cmdPs.Replace('"', '\"')
        $cmdHbToml = $cmdHb.Replace('"', '\"')
        $block = @"

# >>> orchestration-kit hooks >>>
[[hooks]]
  event = "UserPromptSubmit"
  command = "$cmdPsToml"
  timeout = 10

[[hooks]]
  event = "SessionHeartbeat"
  command = "$cmdHbToml"
  timeout = 10
# <<< orchestration-kit hooks <<<
"@
        Add-Content -LiteralPath $kimiCfg -Value $block -Encoding UTF8
        Write-Host "  ~/.kimi-code/config.toml: блок хуков добавлен (бэкап .bak-orch)"
    } else {
        Write-Host "  ~/.kimi-code/config.toml: блок хуков уже актуален"
    }
}

function Ensure-OrchGitignore {
    $gi = Join-Path $script:Target ".gitignore"
    $linesToAdd = @(
        ".orchestration/counters/",
        ".orchestration/*.log",
        ".orchestration/*.pid",
        ".orchestration/prompt-*.run.md",
        ".orchestration/discovered.json",
        ".orchestration/cursor.key",
        "__pycache__/"
    )
    $existing = @()
    if (Test-Path -LiteralPath $gi) {
        $existing = Get-Content -LiteralPath $gi -Encoding UTF8
    }
    foreach ($line in $linesToAdd) {
        $found = $false
        foreach ($ex in $existing) {
            if ($ex -eq $line) { $found = $true; break }
        }
        if (-not $found) {
            Add-Content -LiteralPath $gi -Value $line -Encoding UTF8
            $existing += $line
        }
    }
}

function Install-OrchParamsAndPanel {
    $orchDir = Join-Path $script:Target ".orchestration"
    if (-not (Test-Path -LiteralPath $orchDir)) {
        New-Item -ItemType Directory -Force -Path $orchDir | Out-Null
    }
    $paramsDst = Join-Path $orchDir "params.json"
    $compassDst = Join-Path $orchDir "compass.md"
    if (-not (Test-Path -LiteralPath $paramsDst)) {
        Copy-Item -LiteralPath (Join-Path $script:Kit "params.json") -Destination $paramsDst
    }
    if (-not (Test-Path -LiteralPath $compassDst)) {
        Copy-Item -LiteralPath (Join-Path $script:Kit "compass.md") -Destination $compassDst
    }
    Ensure-OrchGitignore

    if (-not $script:IsWin) {
        try {
            $binDir = Join-Path $script:Kit "bin"
            Get-ChildItem -LiteralPath $binDir -Filter "*.py" -ErrorAction SilentlyContinue | ForEach-Object {
                try { & chmod +x $_.FullName 2>$null } catch { }
            }
        } catch { }
    }

    if (-not $script:HavePy) {
        Write-Host "  .orchestration/ посеян; panel.ps1 пропущен (нет python)"
        return
    }

    $tmp = Get-OrchTempDir
    $normPy = Join-Path $tmp "orch-norm-params.py"
    $normCode = @'
import sys, os
sys.path.insert(0, os.path.join(os.environ["ORCH_KIT"], "bin"))
import orchlib
p = orchlib.load_params()
if p.get("reground", {}).get("every_min") == 7:
    p["reground"]["every_min"] = 10
if p.get("execution", {}).get("executor") == "subagents":
    p["execution"]["executor"] = "auto"
orchlib.save_params(p)
'@
    [System.IO.File]::WriteAllText($normPy, $normCode)
    $prevKit = $env:ORCH_KIT
    try {
        $env:ORCH_KIT = $script:Kit
        try {
            Invoke-OrchPython -PyArgs @($normPy) 2>$null | Out-Null
        } catch {
            # некритично: старые дефолты могли уже быть нормализованы
        }
    } finally {
        if ($null -eq $prevKit) {
            Remove-Item Env:ORCH_KIT -ErrorAction SilentlyContinue
        } else {
            $env:ORCH_KIT = $prevKit
        }
    }

    $kitFwd = Get-ForwardSlashPath -Path $script:Kit
    $serverFwd = "$kitFwd/panel/server.py"
    $panelPs1 = Join-Path $script:Target "panel.ps1"
    if ($script:PyCmd.Count -ge 2 -and $script:PyCmd[0] -eq "py") {
        $panelBody = @'
param([switch]$Bg)
$server = "__SERVER__"
if ($Bg) {
  $p = Start-Process -FilePath "py" -ArgumentList @("-3","-u",$server) -WindowStyle Hidden -PassThru -RedirectStandardOutput panel.out.log -RedirectStandardError panel.err.log
  "панель в фоне: pid $($p.Id); остановка: Stop-Process -Id $($p.Id)"
} else {
  & py -3 -u $server @args
}
'@
        $panelBody = $panelBody.Replace("__SERVER__", $serverFwd)
    } else {
        $pyFwd = Get-ForwardSlashPath -Path $script:PyExe
        $panelBody = @'
param([switch]$Bg)
$py = "__PY__"
$server = "__SERVER__"
if ($Bg) {
  $p = Start-Process -FilePath $py -ArgumentList @("-u",$server) -WindowStyle Hidden -PassThru -RedirectStandardOutput panel.out.log -RedirectStandardError panel.err.log
  "панель в фоне: pid $($p.Id); остановка: Stop-Process -Id $($p.Id)"
} else {
  & $py -u $server @args
}
'@
        $panelBody = $panelBody.Replace("__PY__", $pyFwd).Replace("__SERVER__", $serverFwd)
    }
    [System.IO.File]::WriteAllText($panelPs1, $panelBody)

    $panelCmd = Join-Path $script:Target "panel.cmd"
    $serverNative = Join-Path (Join-Path $script:Kit "panel") "server.py"
    if ($script:PyCmd.Count -ge 2 -and $script:PyCmd[0] -eq "py") {
        $cmdBody = "@echo off`r`npy -3 -u `"$serverNative`" %*"
    } else {
        $cmdBody = "@echo off`r`n`"$($script:PyExe)`" -u `"$serverNative`" %*"
    }
    [System.IO.File]::WriteAllText($panelCmd, $cmdBody)
    Write-Host "  .orchestration/ посеян, panel.ps1 / panel.cmd готовы"
}

function Invoke-OrchSelfCheck {
    if (-not $script:HavePy) {
        Write-Host "  пропущено (нет python)"
        return
    }
    $discover = Join-Path (Join-Path $script:Kit "bin") "discover.py"
    $menu = Join-Path (Join-Path $script:Kit "bin") "menu.py"
    $reground = Join-Path (Join-Path $script:Kit "bin") "reground.py"
    try {
        Invoke-OrchPython -PyArgs @($discover) 1>$null 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  discover: ok"
        } else {
            Write-Host "  discover: предупреждение"
        }
    } catch {
        Write-Host "  discover: предупреждение"
    }
    try {
        $menuOut = Invoke-OrchPython -PyArgs @($menu, "--show") 2>$null
        if ($menuOut) {
            $first = ($menuOut | Select-Object -First 1)
            Write-Host $first
        }
    } catch {
        Write-Host "  menu: предупреждение ($($_.Exception.Message))"
    }
    try {
        $payload = '{"session_id":"install-check"}'
        # Прямой вызов & (не через функцию): stdin из пайпа доходит до native python
        if ($script:PyCmd.Count -ge 2) {
            $null = $payload | & $script:PyCmd[0] $script:PyCmd[1] $reground post-tool --engine claude 2>$null
        } else {
            $null = $payload | & $script:PyCmd[0] $reground post-tool --engine claude 2>$null
        }
        Write-Host "  reground молчит (порог не достигнут) — так и должно быть"
    } catch {
        Write-Host "  reground: предупреждение ($($_.Exception.Message))"
    }
    $sess = Join-Path (Join-Path (Join-Path $script:Target ".orchestration") "sessions") "install-check"
    if (Test-Path -LiteralPath $sess) {
        Remove-Item -LiteralPath $sess -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Show-OrchFinal {
    Write-Host ""
    Write-Host "УСТАНОВЛЕНО в $($script:Target)"
    if ($script:HavePy) {
        Write-Host "  Настройки:   ./panel.ps1  →  http://127.0.0.1:8765 (или соседний порт; -Bg — в фоне)"
        Write-Host "               В панели: тумблер вкл/выкл, задача, исполнители/критики/круги,"
        Write-Host "               модели, токен Cursor (как его взять — подсказка прямо у поля)."
        Write-Host "  Claude Code: запускайте claude в этой папке — скилл + хуки + /orch-menu."
        Write-Host "  Codex:       запускайте codex в этой папке; ПЕРВЫЙ РАЗ: /hooks -> доверить"
        Write-Host "               хуки orchestration (Codex требует явного trust)."
        Write-Host "  Kimi:        НОВАЯ сессия (скиллы регистрируются при старте); вызов"
        Write-Host "               /skill:orchestration; хуки подхватятся сами."
    } else {
        Write-Host "  БЕЗ PYTHON: скилл orchestration работает как инструкции (Claude/Codex/Kimi),"
        Write-Host "  но хуки сверки, панель и меню-скрипты не установлены. Поставьте python и"
        Write-Host "  повторите установщик — он доведёт остальное."
    }
}

# --- точка входа ---

$script:IsWin = ($env:OS -eq "Windows_NT") -or ($PSVersionTable.Platform -eq "Win32NT")

if (-not $PSScriptRoot) {
    Write-Host "ОШИБКА: запустите скрипт файлом, например:"
    Write-Host "  pwsh -File path\to\orchestration-kit\install-local.ps1"
    Write-Host "  (не вставляйте содержимое в консоль — `$PSScriptRoot будет пуст)"
    exit 1
}

$script:Kit = $PSScriptRoot
$script:Target = (Get-Location).Path

$script:ClaudeDir = Join-Path $script:Target ".claude"
$script:CodexHooks = Join-Path (Join-Path $script:Target ".codex") "hooks.json"
if ($Global) {
    $script:ClaudeDir = Join-Path $Home ".claude"
    $script:CodexHooks = Join-Path (Join-Path $Home ".codex") "hooks.json"
    Write-Host "--global: Claude/Codex ставятся на уровень пользователя (все папки)"
}

Find-OrchPython
if (-not $script:HavePy) {
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    Write-Host "!! ПИТОН НЕ НАЙДЕН (ни python, ни py -3, ни python3).            !!"
    Write-Host "!! Работать БУДЕТ: скилл orchestration (это просто инструкции).  !!"
    Write-Host "!! Работать НЕ будет: хуки сверки, панель, меню-скрипты.         !!"
    Write-Host "!! Установить:  Linux: sudo apt install python3                  !!"
    Write-Host "!!   macOS: brew install python (или python.org)                 !!"
    Write-Host "!!   Windows: winget install Python.Python.3.12 (или python.org, !!"
    Write-Host "!!   при установке отметить Add to PATH). Затем повторить ввод.  !!"
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
}

Write-Host "== 1/6 проверка целостности kit =="
Test-OrchIntegrity -KitDir $script:Kit
Write-Host "ok (TARGET=$($script:Target))"

Write-Host "== 2/6 скиллы (все три движка) =="
Install-OrchSkills

Write-Host "== 3/6 хуки Claude (.claude/settings.json) =="
Install-OrchClaudeHooks

Write-Host "== 4/6 Codex + Kimi =="
Install-OrchCodexHooks
Install-OrchKimi

Write-Host "== 5/6 параметры, панель =="
Install-OrchParamsAndPanel

Write-Host "== 6/6 снимок моделей и самопроверка =="
Invoke-OrchSelfCheck

Show-OrchFinal
