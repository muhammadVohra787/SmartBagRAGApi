param(
  [Parameter(Mandatory=$false)]
  [ValidateSet('local','dev')]
  [string]$Mode = 'local'
)

$env:RUN_MODE = $Mode

Write-Host "Starting API in [$Mode] mode..."

python -m uvicorn app.main:app --reload