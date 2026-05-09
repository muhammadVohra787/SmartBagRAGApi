param(
  [Parameter(Mandatory=$false)]
  [ValidateSet('local','dev')]
  [string]$Mode = 'local'
)

$env:RUN_MODE = $Mode

Write-Host "Running ingestion in [$Mode] mode..."
Write-Host "Using .env.$Mode for configuration"
Write-Host ""

python ingest_batch.py

if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "Ingestion failed! Check errors above." -ForegroundColor Red
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Ingestion complete!" -ForegroundColor Green
