param(
  [Parameter(Mandatory=$false)]
  [ValidateSet('local','dev')]
  [string]$Mode = 'local'
)

$env:RUN_MODE = $Mode

Write-Host "Starting API in [$Mode] mode..."

# Load .env file to get PORT
$envFile = if ($Mode -eq 'local') { '.env.local' } else { '.env' }
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^PORT=(.+)$') {
            $port = $matches[1]
        }
    }
}

# Default to 8000 if PORT not found
if (-not $port) { $port = 8000 }

Write-Host "Using port: $port"

python -m uvicorn app.main:app --reload --port $port