Write-Host "Starting Mithra AI Backend..." -ForegroundColor Magenta
Set-Location "$PSScriptRoot\backend"

if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    python -m venv venv
}

Write-Host "Activating venv and installing dependencies..." -ForegroundColor Yellow
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt --quiet

Write-Host "Installing Playwright browsers (Chromium for LinkedIn scraping)..." -ForegroundColor Yellow
python -m playwright install chromium --with-deps 2>$null

Write-Host "Starting FastAPI server on http://localhost:8000" -ForegroundColor Green
uvicorn main:app --reload --host 0.0.0.0 --port 8000
