# Deploys the committed HEAD to the VM: builds a release archive with git archive,
# copies it with multipass transfer and runs infra/vm-deploy.sh there (build + compose up).
# Uncommitted changes are NOT deployed.
# Usage: powershell -ExecutionPolicy Bypass -File infra\deploy-vm.ps1 [-Name rf-vm]
param([string]$Name = "rf-vm")
$ErrorActionPreference = "Stop"

$mp = (Get-Command multipass -ErrorAction SilentlyContinue).Source
if (-not $mp) { $mp = "C:\Program Files\Multipass\bin\multipass.exe" }

$repo = Split-Path $PSScriptRoot -Parent
$archive = Join-Path $env:TEMP "rf-release.tar"

$sha = (git -C $repo rev-parse --short HEAD).Trim()
Write-Host "Packaging $sha..."
git -C $repo archive --format=tar -o $archive HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }

& $mp transfer $archive "${Name}:/tmp/rf-release.tar"
if ($LASTEXITCODE -ne 0) { throw "multipass transfer failed" }

# Run the deploy script straight from the archive.
& $mp exec $Name -- bash -c "tar -xOf /tmp/rf-release.tar infra/vm-deploy.sh | bash -s /tmp/rf-release.tar"
if ($LASTEXITCODE -ne 0) { throw "deploy failed on $Name" }
Remove-Item $archive
