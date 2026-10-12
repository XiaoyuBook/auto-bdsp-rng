# Download and verify an incremental ZIP for existing clients; installation stays in the app.
[CmdletBinding()]
param(
    [string]$Tag = 'v3.4.0',
    [string]$CurrentVersion = '3.3.3',
    [string]$CacheRoot,
    [string]$MetadataFile
)
$ErrorActionPreference = 'Stop'
if ($Tag -notmatch '^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$' -or
    $CurrentVersion -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$') {
    throw 'Use stable versions such as -Tag v3.4.0 -CurrentVersion 3.3.3.'
}
$targetVersion = $Tag.Substring(1)
$baseUrl = "https://gitee.com/shekongsk/auto-bdsp-rng/releases/download/$Tag/"
if ($MetadataFile) {
    $metadata = Get-Content -LiteralPath $MetadataFile -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $release = Invoke-RestMethod -Uri "https://gitee.com/api/v5/repos/shekongsk/auto-bdsp-rng/releases/tags/$Tag" -TimeoutSec 30
    $marker = [regex]::Match($release.body, '(?s)<!-- auto-bdsp-update:(.*?) -->')
    if (-not $marker.Success) { throw 'This Gitee release has not finished synchronization.' }
    $metadata = $marker.Groups[1].Value | ConvertFrom-Json
}
if ($metadata.schema_version -ne 1 -or $metadata.tag_name -cne $Tag) { throw 'Invalid update metadata.' }
$assetName = "auto-bdsp-rng-v$CurrentVersion-to-$Tag-windows-x64.update.zip"
$matches = @($metadata.assets | Where-Object { $_.name -ceq $assetName })
if ($matches.Count -ne 1) { throw 'No direct incremental package for this version. Specify your actual CurrentVersion.' }
$asset = $matches[0]
if ($asset.digest -cnotmatch '^sha256:[0-9a-f]{64}$' -or $asset.size -le 0 -or $asset.size -gt 2147483648 -or
    $asset.browser_download_url -cne ($baseUrl + $assetName)) { throw 'Invalid package metadata.' }
$pieces = if ($asset.parts) { @($asset.parts) } else { @($asset) }
if ($pieces.Count -lt 1 -or $pieces.Count -gt 256) { throw 'Invalid part count.' }
$total = [long]0
for ($index = 0; $index -lt $pieces.Count; $index++) {
    $piece = $pieces[$index]
    $expectedName = if ($asset.parts) { $assetName + ('.part{0:D4}' -f ($index + 1)) } else { $assetName }
    if ($piece.name -cne $expectedName -or $piece.browser_download_url -cne ($baseUrl + $expectedName) -or
        $piece.digest -cnotmatch '^sha256:[0-9a-f]{64}$' -or $piece.size -le 0 -or $piece.size -gt 94371840) {
        throw 'Invalid part name, URL, size or hash.'
    }
    $total += [long]$piece.size
}
if ($total -ne [long]$asset.size) { throw 'Part sizes do not match the original package.' }
if (-not $CacheRoot) {
    $CacheRoot = Join-Path $env:LOCALAPPDATA 'auto_bdsp_rng\updates\downloads'
}
$destinationDir = Join-Path $CacheRoot $Tag
$partsDir = Join-Path $destinationDir '.gitee-parts'
New-Item -ItemType Directory -Path $partsDir -Force | Out-Null
$destination = Join-Path $destinationDir $assetName
function Test-VerifiedFile([string]$Path, $Info) {
    return (Test-Path -LiteralPath $Path -PathType Leaf) -and
        ((Get-Item -LiteralPath $Path).Length -eq [long]$Info.size) -and
        ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $Info.digest.Substring(7))
}
if (-not (Test-VerifiedFile $destination $asset)) {
    foreach ($piece in $pieces) {
        $partPath = Join-Path $partsDir $piece.name
        if (Test-VerifiedFile $partPath $piece) { continue }
        Write-Host "Downloading from Gitee: $($piece.name)"
        $partial = $partPath + '.downloading'
        & curl.exe --silent --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 30 --max-time 900 --speed-limit 1024 --speed-time 120 --max-filesize $piece.size --output $partial $piece.browser_download_url
        if ($LASTEXITCODE -ne 0) { throw "Gitee download failed (curl exit $LASTEXITCODE). Run the script again to resume verified parts." }
        if (-not (Test-VerifiedFile $partial $piece)) { throw 'Gitee part size or SHA-256 mismatch.' }
        Move-Item -LiteralPath $partial -Destination $partPath -Force
    }
    $merged = $destination + '.assembling'
    $output = [IO.File]::Open($merged, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        foreach ($piece in $pieces) {
            $inputStream = [IO.File]::OpenRead((Join-Path $partsDir $piece.name))
            try { $inputStream.CopyTo($output) } finally { $inputStream.Dispose() }
        }
    } finally { $output.Dispose() }
    if (-not (Test-VerifiedFile $merged $asset)) { throw 'Reconstructed package SHA-256 does not match the original release.' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($merged)
    try {
        $entry = $zip.GetEntry('update.json')
        if (-not $entry -or $entry.Length -gt 8388608) { throw 'Invalid update manifest.' }
        $reader = [IO.StreamReader]::new($entry.Open())
        try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        if ($manifest.schema_version -ne 1 -or $manifest.application -cne 'auto-bdsp-rng' -or
            $manifest.platform -cne 'windows-x64' -or $manifest.from_version -cne $CurrentVersion -or
            $manifest.to_version -cne $targetVersion) { throw 'Package does not match the requested upgrade.' }
    } finally { $zip.Dispose() }
    Move-Item -LiteralPath $merged -Destination $destination -Force
}
Write-Host "Verified update cached: $destination"
Write-Host 'Open the application, choose Help -> Check for updates, and install the update.'
Write-Host 'The existing updater will validate the package, preserve modified user files, and handle rollback.'
