# Deletes the RF Alert Manager VM permanently (to recreate it from scratch with launch-vm.ps1).
param([string]$Name = "rf-vm")
$ErrorActionPreference = "Stop"

$mp = (Get-Command multipass -ErrorAction SilentlyContinue).Source
if (-not $mp) { $mp = "C:\Program Files\Multipass\bin\multipass.exe" }

& $mp delete $Name --purge
