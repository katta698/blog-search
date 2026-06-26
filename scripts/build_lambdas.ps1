$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path $PSScriptRoot -Parent
$Dist = "$RepoRoot\dist"

function Build-Lambda {
    param([string]$Name)

    $src = "$RepoRoot\$Name"
    $out = "$Dist\$Name"
    $zip = "$Dist\$Name.zip"

    Write-Host "==> Building $Name..." -ForegroundColor Cyan

    if (Test-Path $out) { Remove-Item $out -Recurse -Force }
    New-Item -ItemType Directory -Path $out -Force | Out-Null

    pip install -r "$src\requirements.txt" -t $out --quiet
    Copy-Item "$src\handler.py" "$out\handler.py"

    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path "$out\*" -DestinationPath $zip

    $size = (Get-Item $zip).Length / 1KB
    Write-Host "    -> $zip ($([math]::Round($size))KB)" -ForegroundColor Green
}

New-Item -ItemType Directory -Path $Dist -Force | Out-Null

Build-Lambda "indexer"
Build-Lambda "query"

Write-Host ""
Write-Host "Done. Run 'terraform apply' from the terraform\ folder to deploy." -ForegroundColor Yellow
