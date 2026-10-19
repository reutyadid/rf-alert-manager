# Launches the RF Alert Manager VM with Multipass + cloud-init and prints its IP.
# Idempotent: if the VM already exists, only prints its IP.
# Usage: powershell -ExecutionPolicy Bypass -File infra\launch-vm.ps1 [-Name rf-vm] [-Cpus 2] [-Memory 2G] [-Disk 20G]
param(
    [string]$Name = "rf-vm",
    [int]$Cpus = 2,
    [string]$Memory = "2G",
    [string]$Disk = "20G"
)
$ErrorActionPreference = "Stop"

$mp = (Get-Command multipass -ErrorAction SilentlyContinue).Source
if (-not $mp) { $mp = "C:\Program Files\Multipass\bin\multipass.exe" }
if (-not (Test-Path $mp)) { throw "multipass not found. Install it from https://multipass.run" }

$cloudInit = Join-Path $PSScriptRoot "cloud-init.yaml"

$exists = (& $mp list --format csv | Select-String -Pattern "^$Name,") -ne $null
if ($exists) {
    Write-Host "VM '$Name' already exists."
} else {
    Write-Host "Launching '$Name' (Ubuntu 24.04, $Cpus CPU, $Memory RAM, $Disk disk)..."
    & $mp launch 24.04 --name $Name --cpus $Cpus --memory $Memory --disk $Disk --cloud-init $cloudInit --timeout 900
    if ($LASTEXITCODE -ne 0) { throw "multipass launch failed" }
}

Write-Host "Waiting for cloud-init to finish..."
& $mp exec $Name -- cloud-init status --wait
if ($LASTEXITCODE -ne 0) { throw "cloud-init failed; inspect with: multipass exec $Name -- sudo cat /var/log/cloud-init-output.log" }

$info = & $mp info $Name --format json | ConvertFrom-Json
$ip = $info.info.$Name.ipv4[0]
Write-Host "VM '$Name' is ready. IP: $ip"
