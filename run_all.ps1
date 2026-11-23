# =====================================================
# run_all.ps1 - Start backend + frontend
# =====================================================

# 1. Load .env into session
Get-Content .\backend\.env | ForEach-Object {
    if ($_ -match '^\s*([^#=\s]+)\s*=\s*(.+?)\s*$') {
        $n = $matches[1]
        $v = $matches[2]
        Set-Item -Path "Env:$n" -Value $v
    }
}

# 2. Activate venv
& .\backend\venv\Scripts\Activate.ps1

# 3. Start backend - run uvicorn with WorkingDirectory set to "backend"
#    so that "app.main:app" resolves to backend\app\main.py
$backendDir = Join-Path $PSScriptRoot "backend"
$uvicornExe = Join-Path $backendDir "venv\Scripts\uvicorn.exe"

Start-Process -FilePath $uvicornExe `
    -ArgumentList "app.main:app","--host","0.0.0.0","--port","8000" `
    -WorkingDirectory $backendDir `
    -NoNewWindow

# 4. Start frontend
Start-Process -FilePath "npm.cmd" `
    -ArgumentList "--prefix","frontend","run","dev","--","--host","0.0.0.0","--port","5173" `
    -WorkingDirectory $PSScriptRoot `
    -NoNewWindow

# 5. Wait for backend
$ok = $false
for ($i = 1; $i -le 15; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/" -UseBasicParsing -TimeoutSec 3
        Write-Host "`nBackend OK on attempt $i (status $($r.StatusCode))"
        $ok = $true
        break
    } catch {
        Write-Host "Waiting... attempt $i"
    }
}
if (-not $ok) { Write-Host "`nBackend did not respond after 15 attempts." }

Write-Host "`nBackend:  http://127.0.0.1:8000/"
Write-Host "Frontend: http://127.0.0.1:5173/"
