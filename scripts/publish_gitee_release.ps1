# Publish directly from Windows, without registering a runner or starting Actions.
[CmdletBinding()]
param(
    [string]$Tag = 'v3.4.0',
    [string]$Directory,
    [string]$Python,
    [switch]$UseExistingFiles,
    [switch]$PrepareOnly
)
$ErrorActionPreference = 'Stop'
if ($Tag -notmatch '^v\d+\.\d+\.\d+$') { throw 'Expected a stable tag such as v3.4.0.' }
$repoRoot = Split-Path $PSScriptRoot -Parent
if (-not $Python) {
    $Python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        $commonDir = & git -C $repoRoot rev-parse --path-format=absolute --git-common-dir
        if ($LASTEXITCODE -eq 0) {
            $Python = Join-Path (Split-Path $commonDir -Parent) '.venv\Scripts\python.exe'
        }
    }
    if (-not (Test-Path -LiteralPath $Python)) { $Python = (Get-Command python.exe).Source }
}
& $Python -c "import sys,requests; assert sys.version_info[:2] == (3,12), 'Publishing requires Python 3.12'; print('Python and requests ready')"
if ($LASTEXITCODE -ne 0) { throw 'Specify -Python with a Python 3.12 interpreter that has requests installed.' }
if (-not $Directory) { $Directory = Join-Path $repoRoot "release\gitee-$Tag" }
$Directory = [IO.Path]::GetFullPath($Directory)
$names = @('GH_TOKEN','GITEE_ACCESS_TOKEN','PYTHONUNBUFFERED','GIT_TERMINAL_PROMPT','GCM_INTERACTIVE')
$previous = @{}
foreach ($name in $names) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PYTHONUNBUFFERED = '1'
    if (-not $env:GH_TOKEN) {
        # Use the configured Git credential for GitHub API discovery; never print it.
        $env:GIT_TERMINAL_PROMPT = '0'
        $env:GCM_INTERACTIVE = 'never'
        $credentialLines = "protocol=https`nhost=github.com`n`n" | & git credential fill 2>$null
        if ($LASTEXITCODE -eq 0) {
            foreach ($line in $credentialLines) {
                if ($line.StartsWith('password=')) { $env:GH_TOKEN = $line.Substring(9) }
            }
        }
        $credentialLines = $null
    }
    if (-not $PrepareOnly -and -not $env:GITEE_ACCESS_TOKEN) {
        $secureToken = Read-Host 'Gitee access token (hidden input; never saved to disk)' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
        try { $env:GITEE_ACCESS_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer); $secureToken.Dispose() }
        if (-not $env:GITEE_ACCESS_TOKEN) { throw 'Gitee token is required for publication.' }
    }
    Push-Location -LiteralPath $repoRoot
    try {
        if (-not $UseExistingFiles) {
            & $Python scripts/sync_gitee_release.py --tag $Tag --directory $Directory --download-only
            if ($LASTEXITCODE -ne 0) { throw 'Release download failed.' }
        }
        if ($PrepareOnly) {
            & $Python scripts/sync_gitee_release.py --tag $Tag --directory $Directory --prepare-only
            if ($LASTEXITCODE -ne 0) { throw 'Local preparation failed.' }
            Write-Host "Manual release text: $Directory\gitee-release-body.md"
        } else {
            & $Python scripts/sync_gitee_tag.py --tag $Tag
            if ($LASTEXITCODE -ne 0) { throw 'Tag verification failed.' }
            & $Python scripts/sync_gitee_release.py --tag $Tag --directory $Directory
            if ($LASTEXITCODE -ne 0) { throw 'Gitee publication failed.' }
        }
    } finally { Pop-Location }
} finally {
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
