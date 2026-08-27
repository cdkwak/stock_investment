[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$ProjectRoot)

Set-Location $ProjectRoot
$pythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$runnerPath = Join-Path $ProjectRoot "scripts\manual\collect\collect_toss_domestic_ur246.py"
& $pythonPath $runnerPath --project-root $ProjectRoot --confirm-ur246-window
exit $LASTEXITCODE
