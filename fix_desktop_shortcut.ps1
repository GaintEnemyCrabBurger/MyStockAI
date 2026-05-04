$ErrorActionPreference = 'Stop'

$Y = $PSScriptRoot
$vbs = Join-Path $Y 'start_mystockai_silent.vbs'

if (-not (Test-Path -LiteralPath $vbs)) {
    Write-Error "Missing: $vbs"
    exit 1
}

function Add-TypeKnownFoldersDesktop {
    try {
        [KnownFoldersDesktop]::GetDesktop() | Out-Null
        return
    } catch {
    }

    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class KnownFoldersDesktop {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHGetKnownFolderPath(
        [MarshalAs(UnmanagedType.LPStruct)] Guid rfid,
        uint dwFlags,
        IntPtr hToken,
        out IntPtr pszPath);

    public static string GetDesktop() {
        Guid rfid = new Guid("B4BF389D-6E69-495E-A89F-FBB039BB623E");
        IntPtr pszPath = IntPtr.Zero;
        try {
            int hr = SHGetKnownFolderPath(rfid, 0, IntPtr.Zero, out pszPath);
            if (hr != 0) {
                return null;
            }
            return Marshal.PtrToStringUni(pszPath);
        } finally {
            if (pszPath != IntPtr.Zero) {
                Marshal.FreeCoTaskMem(pszPath);
            }
        }
    }
}
"@ -ErrorAction Stop
}

function Add-DesktopPaths {
    param([System.Collections.Generic.HashSet[string]]$Set)

    $add = {
        param([string]$p)
        if ([string]::IsNullOrWhiteSpace($p)) { return }
        $n = $p.TrimEnd('\')
        if ($n.Length -gt 0) { [void]$Set.Add($n) }
    }

    & $add ([Environment]::GetFolderPath('Desktop'))

    try {
        $raw = (Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders' -Name Desktop).Desktop
        & $add ([Environment]::ExpandEnvironmentVariables($raw))
    } catch {}

    try {
        & $add ((New-Object -ComObject Shell.Application).NameSpace(0x10).Self.Path)
    } catch {}

    try {
        & $add ([Environment]::GetFolderPath('CommonDesktopDirectory'))
    } catch {}

    $od = $env:OneDrive
    if (-not [string]::IsNullOrWhiteSpace($od)) {
        & $add (Join-Path $od 'Desktop')
    }
    $odc = $env:OneDriveCommercial
    if (-not [string]::IsNullOrWhiteSpace($odc)) {
        & $add (Join-Path $odc 'Desktop')
    }

    $deskCn = (-join @([char]0x684C, [char]0x9762))
    $profileRoot = $env:USERPROFILE
    if (-not [string]::IsNullOrWhiteSpace($profileRoot)) {
        & $add (Join-Path $profileRoot 'Desktop')
        & $add (Join-Path $profileRoot (Join-Path 'OneDrive' 'Desktop'))
        & $add (Join-Path $profileRoot (Join-Path 'OneDrive' $deskCn))
    }

    try {
        Add-TypeKnownFoldersDesktop
        $kfDesk = [KnownFoldersDesktop]::GetDesktop()
        & $add $kfDesk
    } catch {
        Write-Host "KnownFoldersDesktop unavailable: $($_.Exception.Message)"
    }
}

$targets = New-Object 'System.Collections.Generic.HashSet[string]'
Add-DesktopPaths $targets

$shortNames = @(
    ((-join @([char]0x77ED, [char]0x7EBF, [char]0x770B, [char]0x677F)) + '.lnk'),
    'MyStockAI-Kanban.lnk'
)

$ws = New-Object -ComObject WScript.Shell

foreach ($desk in [string[]]$targets) {
    if (-not (Test-Path -LiteralPath $desk)) {
        Write-Host "Skip missing folder: $desk"
        continue
    }
    foreach ($shortName in $shortNames) {
        $lnkPath = Join-Path $desk $shortName
        try {
            $sc = $ws.CreateShortcut($lnkPath)
            $sc.TargetPath = $vbs
            $sc.WorkingDirectory = $Y
            $sc.Description = 'MyStockAI Streamlit no-console'
            $sc.Save()
            Write-Host "Created: $lnkPath"
        } catch {
            Write-Host "Skip (no write access): $lnkPath :: $($_.Exception.Message)"
        }
    }
}

$userDesk = [Environment]::GetFolderPath('Desktop')
$userLnkCn = Join-Path $userDesk $shortNames[0]
$userLnkEn = Join-Path $userDesk $shortNames[1]
Write-Host "Verify CN shortcut exists: $(Test-Path -LiteralPath $userLnkCn)"
Write-Host "Verify EN shortcut exists: $(Test-Path -LiteralPath $userLnkEn)"
Write-Host "Folders tried: $($targets.Count)"
