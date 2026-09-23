# Удаление оркестрации с машины. Обратен install-local.ps1.
#
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Global
#   powershell -ExecutionPolicy Bypass -File uninstall.ps1 -All
#
# Идемпотентен. Трогает только файлы установщика. PS 5.1 + pwsh.
param(
    [switch]$Global,
    [switch]$All
)

$ErrorActionPreference = "Stop"

# --- helpers (до использования) ---

function Test-IsWindows {
    return ($env:OS -eq "Windows_NT")
}

function Get-HomeDir {
    if (Test-IsWindows) {
        return $env:USERPROFILE
    }
    return $env:HOME
}

function Write-Utf8NoBom {
    param([string]$Path, [string]$Text, [switch]$Append)
    $full = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    $dir = [System.IO.Path]::GetDirectoryName($full)
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    if ($Append) {
        [System.IO.File]::AppendAllText($full, $Text, $enc)
    } else {
        [System.IO.File]::WriteAllText($full, $Text, $enc)
    }
}

function Get-PythonArgs {
    # Порядок как у установщика: python -> py -3 -> python3
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($null -ne $cmd) {
            & python -c "import sys; raise SystemExit(0 if sys.version_info[0] >= 3 else 1)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return ,@("python")
            }
        }
        $cmd = Get-Command py -ErrorAction SilentlyContinue
        if ($null -ne $cmd) {
            & py -3 -c "pass" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return ,@("py", "-3")
            }
        }
        $cmd = Get-Command python3 -ErrorAction SilentlyContinue
        if ($null -ne $cmd) {
            & python3 -c "pass" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return ,@("python3")
            }
        }
    } finally {
        $ErrorActionPreference = $prevEap
    }
    return $null
}

function Invoke-PythonFile {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$PyArgs,
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath
    )
    # PS 5.1: не использовать [1..N] при Length=1 (диапазон 1..0 ломает вызов)
    if ($PyArgs.Length -eq 1) {
        & $PyArgs[0] $ScriptPath
    } elseif ($PyArgs.Length -eq 2) {
        & $PyArgs[0] $PyArgs[1] $ScriptPath
    } else {
        throw "Unexpected python invocation args"
    }
}

function Get-TempDir {
    if ($env:TEMP -and $env:TEMP.Trim() -ne "") {
        return $env:TEMP
    }
    if ($env:TMPDIR -and $env:TMPDIR.Trim() -ne "") {
        return $env:TMPDIR
    }
    return [System.IO.Path]::GetTempPath()
}

function Remove-DirSafe {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
        return $true
    }
    return $false
}

function Remove-FileSafe {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
        return $true
    }
    return $false
}

function Remove-KimiHooksBlock {
    # Снять маркированный блок + все [[hooks]] с reground.py в command
    param([string]$ConfigPath)
    $startMarker = "# >>> orchestration-kit hooks >>>"
    $endMarker = "# <<< orchestration-kit hooks <<<"
    $raw = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8)
    $afterMarkers = New-Object System.Collections.Generic.List[string]
    $inBlock = $false
    foreach ($line in $raw) {
        if (-not $inBlock) {
            if ($line -eq $startMarker) {
                $inBlock = $true
                continue
            }
            $afterMarkers.Add($line) | Out-Null
        } else {
            if ($line -eq $endMarker) {
                $inBlock = $false
            }
            continue
        }
    }
    # Удалить [[hooks]]-блоки, у которых command содержит reground.py
    $newLines = New-Object System.Collections.Generic.List[string]
    $i = 0
    $arr = $afterMarkers.ToArray()
    while ($i -lt $arr.Length) {
        $stripped = $arr[$i].Trim()
        if ($stripped -eq "[[hooks]]" -or $stripped.StartsWith("[[hooks]]")) {
            $block = New-Object System.Collections.Generic.List[string]
            $block.Add($arr[$i]) | Out-Null
            $i++
            while ($i -lt $arr.Length -and -not $arr[$i].TrimStart().StartsWith("[[")) {
                $block.Add($arr[$i]) | Out-Null
                $i++
            }
            $hasReground = $false
            foreach ($bl in $block) {
                $cmdVal = $null
                # без якоря EOL: допускаем хвост / # comment после кавычек
                if ($bl -match '^\s*command\s*=\s*"([^"]*)"') {
                    $cmdVal = $Matches[1]
                } elseif ($bl -match "^\s*command\s*=\s*'([^']*)'") {
                    $cmdVal = $Matches[1]
                }
                if (($null -ne $cmdVal) -and ($cmdVal -like "*reground.py*")) {
                    $hasReground = $true
                    break
                }
            }
            if (-not $hasReground) {
                foreach ($bl in $block) {
                    $newLines.Add($bl) | Out-Null
                }
            }
        } else {
            if ($arr[$i] -ne $startMarker -and $arr[$i] -ne $endMarker) {
                $newLines.Add($arr[$i]) | Out-Null
            }
            $i++
        }
    }
    # Убрать хвостовые пустые строки
    while ($newLines.Count -gt 0) {
        $last = $newLines[$newLines.Count - 1]
        if ($null -eq $last) {
            $newLines.RemoveAt($newLines.Count - 1)
            continue
        }
        if ($last.Trim() -eq "") {
            $newLines.RemoveAt($newLines.Count - 1)
            continue
        }
        break
    }
    if ($newLines.Count -eq 0) {
        Write-Utf8NoBom -Path $ConfigPath -Text ""
    } else {
        $nl = [Environment]::NewLine
        Write-Utf8NoBom -Path $ConfigPath -Text ([string]::Join($nl, $newLines.ToArray()) + $nl)
    }
}

function Clear-KimiConfigToml {
    param([string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return
    }
    $cfgText = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
    if ($null -eq $cfgText) {
        $cfgText = ""
    }
    if (($cfgText -notmatch "orchestration-kit hooks") -and ($cfgText -notmatch "reground\.py")) {
        return
    }
    $bak = $ConfigPath + ".bak-uninstall"
    if (-not (Test-Path -LiteralPath $bak)) {
        Copy-Item -LiteralPath $ConfigPath -Destination $bak -Force
    }
    Remove-KimiHooksBlock -ConfigPath $ConfigPath
    # Паритет с bash: success только если reground.py больше нет
    $after = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
    if ($null -eq $after) {
        $after = ""
    }
    if ($after -match "reground\.py") {
        Write-Warning "  в $ConfigPath ещё есть reground.py — зачистка неполная"
    } else {
        Write-Host "  хуки reground зачищены в $ConfigPath (бэкап: .bak-uninstall)"
    }
}

function Remove-GitignoreOrchLines {
    param([string]$GitignorePath)
    if (-not (Test-Path -LiteralPath $GitignorePath)) {
        return
    }
    $raw = Get-Content -LiteralPath $GitignorePath -Encoding UTF8
    $kept = New-Object System.Collections.Generic.List[string]
    foreach ($line in $raw) {
        # Как sed в bash: удалить строки, содержащие .orchestration/ или __pycache__/
        if ($line.Contains(".orchestration/")) {
            continue
        }
        if ($line.Contains("__pycache__/")) {
            continue
        }
        $kept.Add($line) | Out-Null
    }
    $nl = [Environment]::NewLine
    if ($kept.Count -eq 0) {
        Write-Utf8NoBom -Path $GitignorePath -Text ""
    } else {
        Write-Utf8NoBom -Path $GitignorePath -Text ([string]::Join($nl, $kept.ToArray()) + $nl)
    }
}

# --- пути ---

$IsWin = Test-IsWindows
$HomeDir = Get-HomeDir
$Target = (Get-Location).Path

if ($Global) {
    $ClaudeDir = Join-Path $HomeDir ".claude"
    $CodexHooks = Join-Path (Join-Path $HomeDir ".codex") "hooks.json"
} else {
    $ClaudeDir = Join-Path $Target ".claude"
    $CodexHooks = Join-Path (Join-Path $Target ".codex") "hooks.json"
}

if ($env:KIMI_HOME -and $env:KIMI_HOME.Trim() -ne "") {
    $KimiDir = $env:KIMI_HOME
} else {
    $KimiDir = Join-Path $HomeDir ".kimi-code"
}

$PyArgs = Get-PythonArgs

Write-Host "== Удаление оркестрации =="
if ($Global) {
    Write-Host "  режим: -Global (пользовательский уровень)"
} else {
    Write-Host "  режим: локальный (папка $Target)"
}
if ($All) {
    Write-Host "  -All: будет удалён и .orchestration"
}

# ========== 1/5 Claude Code ==========
Write-Host ""
Write-Host "== 1/5 Claude Code =="
try {
    $skillDir = Join-Path (Join-Path $ClaudeDir "skills") "orchestration"
    if (Remove-DirSafe -Path $skillDir) {
        Write-Host "  удалён скилл: $skillDir/"
    } else {
        Write-Host "  скилл не найден (уже удалён?)"
    }
} catch {
    Write-Host "  предупреждение: не удалось удалить скилл Claude: $_"
}

try {
    $cmdFile = Join-Path (Join-Path $ClaudeDir "commands") "orch-menu.md"
    if (Remove-FileSafe -Path $cmdFile) {
        Write-Host "  удалена команда: orch-menu.md"
    }
} catch {
    Write-Host "  предупреждение: не удалось удалить orch-menu.md: $_"
}

try {
    $settingsPath = Join-Path $ClaudeDir "settings.json"
    if (Test-Path -LiteralPath $settingsPath) {
        if ($null -eq $PyArgs) {
            Write-Host "  хуки не вычищены (нет python): удалите вручную"
        } else {
            $tmpPy = Join-Path (Get-TempDir) ("orch-uninstall-claude-" + [guid]::NewGuid().ToString("N") + ".py")
            $pyBody = @'
import json, os, sys
path = os.environ["ORCH_CLAUDE_SETTINGS"]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)

def is_ours(entry):
    s = json.dumps(entry)
    return ("reground.py" in s) or ("orchestration-kit" in s)

hooks = d.get("hooks", {})
changed = False
for event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
    if event in hooks:
        filtered = [e for e in hooks[event] if not is_ours(e)]
        if len(filtered) < len(hooks[event]):
            hooks[event] = filtered
            changed = True
        if not filtered:
            del hooks[event]
            changed = True
if changed:
    json.dump(d, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(path, "a", encoding="utf-8").write("\n")
    print("  хуки удалены")
else:
    print("  хуки не найдены")
'@
            try {
                Write-Utf8NoBom -Path $tmpPy -Text $pyBody
                $env:ORCH_CLAUDE_SETTINGS = $settingsPath
                Invoke-PythonFile -PyArgs $PyArgs -ScriptPath $tmpPy
            } finally {
                if (Test-Path -LiteralPath $tmpPy) {
                    Remove-Item -LiteralPath $tmpPy -Force -ErrorAction SilentlyContinue
                }
                if (Test-Path Env:ORCH_CLAUDE_SETTINGS) {
                    Remove-Item Env:ORCH_CLAUDE_SETTINGS -ErrorAction SilentlyContinue
                }
            }
        }
    }
} catch {
    Write-Host "  предупреждение: ошибка очистки hooks settings.json: $_"
}

# ========== 2/5 Codex ==========
Write-Host ""
Write-Host "== 2/5 Codex =="
try {
    if (Test-Path -LiteralPath $CodexHooks) {
        if ($null -eq $PyArgs) {
            Write-Host "  хуки не вычищены (нет python): удалите вручную"
        } else {
            $tmpPy = Join-Path (Get-TempDir) ("orch-uninstall-codex-" + [guid]::NewGuid().ToString("N") + ".py")
            $pyBody = @'
import json, os, sys
path = os.environ["ORCH_CODEX_HOOKS"]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)

def is_ours(entry):
    s = json.dumps(entry)
    return ("reground.py" in s) or ("orchestration-kit" in s)

hooks = d.get("hooks", {})
# unlink только если КАЖДАЯ запись в КАЖДОМ event — наша
ours = (
    all(all(is_ours(e) for e in v) for v in hooks.values())
    if hooks else False
)
if ours and len(hooks) <= 3:
    os.unlink(path)
    print("  удалён файл: " + path + " (содержал только наши хуки)")
else:
    changed = False
    for event in list(hooks.keys()):
        filtered = [e for e in hooks[event] if not is_ours(e)]
        if len(filtered) < len(hooks[event]):
            hooks[event] = filtered
            changed = True
        if not filtered:
            del hooks[event]
    if changed:
        d["hooks"] = hooks
        json.dump(d, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        print("  наши хуки удалены (чужие сохранены)")
    else:
        print("  наши хуки не найдены")
'@
            try {
                Write-Utf8NoBom -Path $tmpPy -Text $pyBody
                $env:ORCH_CODEX_HOOKS = $CodexHooks
                Invoke-PythonFile -PyArgs $PyArgs -ScriptPath $tmpPy
            } finally {
                if (Test-Path -LiteralPath $tmpPy) {
                    Remove-Item -LiteralPath $tmpPy -Force -ErrorAction SilentlyContinue
                }
                if (Test-Path Env:ORCH_CODEX_HOOKS) {
                    Remove-Item Env:ORCH_CODEX_HOOKS -ErrorAction SilentlyContinue
                }
            }
        }
    } else {
        Write-Host "  hooks.json не найден"
    }
} catch {
    Write-Host "  предупреждение: ошибка очистки Codex hooks: $_"
}

try {
    $codexPrompt = Join-Path (Join-Path (Join-Path $HomeDir ".codex") "prompts") "orch-menu.md"
    if (Remove-FileSafe -Path $codexPrompt) {
        Write-Host "  удалён prompt: ~/.codex/prompts/orch-menu.md"
    }
} catch {
    Write-Host "  предупреждение: не удалось удалить Codex prompt: $_"
}

# ========== 3/5 Kimi ==========
Write-Host ""
Write-Host "== 3/5 Kimi =="
try {
    $kimiSkill = Join-Path (Join-Path $KimiDir "skills") "orchestration"
    if (Remove-DirSafe -Path $kimiSkill) {
        Write-Host "  удалён скилл: $kimiSkill/"
    }
} catch {
    Write-Host "  предупреждение: не удалось удалить скилл Kimi: $_"
}

try {
    Clear-KimiConfigToml -ConfigPath (Join-Path $KimiDir "config.toml")
    # Доп. путь: CLAUDE_CONFIG_DIR/.kimi-code/config.toml (если задан и отличается)
    if ($env:CLAUDE_CONFIG_DIR -and $env:CLAUDE_CONFIG_DIR.Trim() -ne "") {
        $extraKimi = Join-Path (Join-Path $env:CLAUDE_CONFIG_DIR ".kimi-code") "config.toml"
        $primary = Join-Path $KimiDir "config.toml"
        if ($extraKimi -ne $primary) {
            Clear-KimiConfigToml -ConfigPath $extraKimi
        }
    }
} catch {
    Write-Host "  предупреждение: ошибка очистки Kimi config.toml: $_"
}

# ========== 4/5 .agents/skills ==========
Write-Host ""
Write-Host "== 4/5 Папки .agents/skills =="
$agentsDirs = @(
    (Join-Path (Join-Path (Join-Path $Target ".agents") "skills") "orchestration"),
    (Join-Path (Join-Path (Join-Path $HomeDir ".agents") "skills") "orchestration")
)
foreach ($dir in $agentsDirs) {
    try {
        if (Remove-DirSafe -Path $dir) {
            Write-Host "  удалён: $dir"
        }
    } catch {
        Write-Host "  предупреждение: не удалось удалить $dir : $_"
    }
}

# ========== 5/5 Рабочие файлы ==========
Write-Host ""
Write-Host "== 5/5 Рабочие файлы =="
if ($All) {
    try {
        $orchDir = Join-Path $Target ".orchestration"
        if (Remove-DirSafe -Path $orchDir) {
            Write-Host "  удалён: $orchDir/ (compass, params, сессии, логи)"
        }
    } catch {
        Write-Host "  предупреждение: не удалось удалить .orchestration: $_"
    }

    $panelFiles = @(
        (Join-Path $Target "panel.ps1"),
        (Join-Path $Target "panel.cmd"),
        (Join-Path $Target "panel.out.log"),
        (Join-Path $Target "panel.err.log")
    )
    $panelRemoved = $false
    foreach ($pf in $panelFiles) {
        try {
            if (Remove-FileSafe -Path $pf) {
                $panelRemoved = $true
            }
        } catch {
            Write-Host "  предупреждение: не удалось удалить $pf : $_"
        }
    }
    if ($panelRemoved) {
        Write-Host "  удалены: panel.ps1 / panel.cmd / логи панели"
    }

    try {
        $gi = Join-Path $Target ".gitignore"
        if (Test-Path -LiteralPath $gi) {
            Remove-GitignoreOrchLines -GitignorePath $gi
            Write-Host "  строки .orchestration убраны из .gitignore"
        }
    } catch {
        Write-Host "  предупреждение: не удалось поправить .gitignore: $_"
    }
} else {
    Write-Host "  .orchestration/ сохранён (используйте -All для полного удаления)"
}

Write-Host ""
Write-Host "== Готово =="
Write-Host "  Оркестрация удалена. Для повторной установки: powershell -ExecutionPolicy Bypass -File install-local.ps1"
