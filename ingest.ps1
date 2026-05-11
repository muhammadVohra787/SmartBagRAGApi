param(
  [Parameter(Mandatory=$false)]
  [ValidateSet('local','dev')]
  [string]$Mode = 'local'
)

$env:RUN_MODE = $Mode

Write-Host "Running PDF/Markdown ingestion in [$Mode] mode..."
Write-Host "Using .env.$Mode for configuration"
Write-Host ""

python -m app.ingestion.mass.ingest_pdf_markdown_batch

if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "Ingestion failed! Check errors above." -ForegroundColor Red
  exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Ingestion complete!" -ForegroundColor Green
