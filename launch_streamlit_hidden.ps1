$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$appPy = Join-Path $Root 'app.py'
if (-not (Test-Path -LiteralPath $appPy)) {
    Set-Content -Path (Join-Path $Root 'streamlit_launch_error.txt') -Value "Missing app.py at:`r`n$appPy"
    exit 1
}

function Write-LaunchLog([string]$Message) {
    $log = Join-Path $Root 'streamlit_launch_error.txt'
    Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format 's'), $Message)
}

try {
    $pythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($pythonw) {
        Start-Process -FilePath $pythonw.Source `
            -ArgumentList @('-m', 'streamlit', 'run', $appPy) `
            -WorkingDirectory $Root `
            -WindowStyle Hidden
        exit 0
    }

    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        Start-Process -FilePath $py.Source `
            -ArgumentList @('-3w', '-m', 'streamlit', 'run', $appPy) `
            -WorkingDirectory $Root `
            -WindowStyle Hidden
        exit 0
    }

    $paths = @(
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        [Environment]::GetEnvironmentVariable('Path', 'Machine')
    ) -join ';'

    Set-Content -LiteralPath (Join-Path $Root 'streamlit_launch_error.txt') -Value @(
        'Cannot find pythonw.exe or py.exe when launching from Explorer shortcuts.'
        'Fix PATH or reinstall Python with "Add Python to PATH".'
        ''
        'PATH snapshot:'
        $paths
    )
    exit 1
} catch {
    Write-LaunchLog $_.Exception.Message
    exit 1
}
