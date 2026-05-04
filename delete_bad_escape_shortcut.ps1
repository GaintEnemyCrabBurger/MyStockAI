param(
    [string]$ForcePath,
    [switch]$RestartExplorer
)

$ErrorActionPreference = 'Continue'

if (-not [string]::IsNullOrWhiteSpace($ForcePath)) {
    foreach ($t in @($ForcePath, ('\\?\' + $ForcePath))) {
        if (Test-Path -LiteralPath $t) {
            Remove-Item -LiteralPath $t -Force
            Write-Host "Removed forced path: $t"
        }
    }
    exit 0
}

$nameExact = 'u{77ED}u{7EBF}u{770B}u{677F}.lnk'
$nameNoExt = 'u{77ED}u{7EBF}u{770B}u{677F}'

function Add-Desk([System.Collections.Generic.HashSet[string]]$Set, [string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $t = $Path.TrimEnd('\')
    if ($t.Length -eq 0) { return }
    if (-not (Test-Path -LiteralPath $t)) { return }
    [void]$Set.Add($t)
}

function ShouldRemoveShortcutName([string]$nm) {
    if ([string]::IsNullOrWhiteSpace($nm)) { return $false }
    if ($nm -eq $nameExact) { return $true }
    if ($nm -eq $nameNoExt) { return $true }
    if ($nm.StartsWith('u{77ED}')) { return $true }
    return $false
}

function Remove-ShortcutViaShell([string]$deskPath) {
    try {
        $shell = New-Object -ComObject Shell.Application
        $folder = $shell.Namespace($deskPath)
        if (-not $folder) {
            Write-Host "Shell.Namespace returned null for: $deskPath"
            return
        }

        foreach ($item in @($folder.Items())) {
            $nm = [string]$item.Name
            if (-not (ShouldRemoveShortcutName $nm)) { continue }

            try {
                $psi = $folder.ParseName($nm)
                if (-not $psi) {
                    Write-Host "ParseName failed for: $nm"
                    continue
                }

                Write-Host ("Shell deleting via InvokeVerb(delete): " + $nm)
                $psi.InvokeVerb('delete')
            } catch {
                Write-Host ("Shell delete failed for ${nm}: $($_.Exception.Message)")
            }
        }
    } catch {
        Write-Host ("Shell.Application desktop cleanup failed: $($_.Exception.Message)")
    }
}

function Remove-ShortcutViaFilesystem([string]$deskPath) {
    Get-ChildItem -LiteralPath $deskPath -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
        $nm = $_.Name

        if ($nm.Contains('{')) {
            Write-Host ("Brace shortcut on disk: " + $nm)
        }

        if (-not (ShouldRemoveShortcutName $nm)) { return }

        try {
            $long = '\\?\' + $_.FullName
            if (Test-Path -LiteralPath $long) {
                Remove-Item -LiteralPath $long -Force -ErrorAction Stop
                Write-Host ("Removed(long): " + $long)
            } else {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
                Write-Host ("Removed: " + $_.FullName)
            }
        } catch {
            Write-Host ("FAILED: " + $_.FullName + " :: " + $_.Exception.Message)
        }
    }
}

$desks = New-Object 'System.Collections.Generic.HashSet[string]'
Add-Desk $desks ([Environment]::GetFolderPath('Desktop'))

try {
    $raw = (Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -Name Desktop).Desktop
    Add-Desk $desks ([Environment]::ExpandEnvironmentVariables($raw))
} catch {}

try {
    Add-Desk $desks ((New-Object -ComObject Shell.Application).NameSpace(0x10).Self.Path)
} catch {}

Add-Desk $desks ([Environment]::GetFolderPath('CommonDesktopDirectory'))

if (-not [string]::IsNullOrWhiteSpace($env:OneDrive)) {
    Add-Desk $desks (Join-Path $env:OneDrive 'Desktop')
}

if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    Add-Desk $desks (Join-Path $env:USERPROFILE 'OneDrive\Desktop')
}

Write-Host ('Desktop folders scanned: ' + $desks.Count)

foreach ($desk in [string[]]$desks) {
    Write-Host "--- $desk ---"
    Remove-ShortcutViaShell $desk
    Remove-ShortcutViaFilesystem $desk
}

$userDesk = [Environment]::GetFolderPath('Desktop')
foreach ($extra in @(
        (Join-Path $userDesk $nameExact),
        ('\\?\' + (Join-Path $userDesk $nameExact))
    )) {
    if (Test-Path -LiteralPath $extra) {
        try {
            Remove-Item -LiteralPath $extra -Force
            Write-Host ("Removed(extra): " + $extra)
        } catch {
            Write-Host ("FAILED(extra): " + $extra + " :: " + $_.Exception.Message)
        }
    }
}

Write-Host 'Done. If an icon remains on Desktop: Win+R -> explorer.exe restart Explorer once.'

if ($RestartExplorer) {
    try {
        Stop-Process -Name explorer -Force -ErrorAction Stop
        Start-Sleep -Seconds 1
        Start-Process explorer.exe | Out-Null
        Write-Host 'Explorer restarted.'
    } catch {
        Write-Host ("Explorer restart skipped/failed: $($_.Exception.Message)")
    }
}
