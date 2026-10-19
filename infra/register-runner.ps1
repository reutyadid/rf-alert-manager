# Registers the VM as a GitHub Actions self-hosted runner (label: rf-vm), running as a service.
# Get a token: GitHub repo -> Settings -> Actions -> Runners -> New self-hosted runner (Linux).
# Usage: powershell -ExecutionPolicy Bypass -File infra\register-runner.ps1 -Token <token> [-Name rf-vm]
param(
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Name = "rf-vm",
    [string]$Repo = "reutyadid/rf-alert-manager"
)
$ErrorActionPreference = "Stop"

$mp = (Get-Command multipass -ErrorAction SilentlyContinue).Source
if (-not $mp) { $mp = "C:\Program Files\Multipass\bin\multipass.exe" }

& $mp transfer (Join-Path $PSScriptRoot "install-runner.sh") "${Name}:/tmp/install-runner.sh"
if ($LASTEXITCODE -ne 0) { throw "multipass transfer failed" }

& $mp exec $Name -- env "RUNNER_TOKEN=$Token" bash /tmp/install-runner.sh $Repo
if ($LASTEXITCODE -ne 0) { throw "runner installation failed" }
