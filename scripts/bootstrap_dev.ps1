# QuantPilot dev environment bootstrap (Windows PowerShell)
# Usage: powershell -ExecutionPolicy Bypass -File scripts/bootstrap_dev.ps1 [-NoSeed]

[CmdletBinding()]
param(
    [switch]$NoSeed,
    [switch]$Help
)

if ($Help) {
    Write-Host 'Usage: bootstrap_dev.ps1 [-NoSeed]'
    Write-Host '  -NoSeed   skip demo data seeding'
    exit 0
}

$DoSeed = -not $NoSeed.IsPresent
$ErrorActionPreference = 'Stop'

Set-Location -Path (Join-Path $PSScriptRoot '..')
$Root = (Get-Location).Path
$Compose = 'docker compose -f docker-compose.dev.yml'

function Info ($m) { Write-Host "==> $m" -ForegroundColor Blue }
function Ok ($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fatal ($m) { Write-Host "[FAIL] $m" -ForegroundColor Red; exit 1 }

# ============== [1/6] Prereq check ==============
Info '[1/6] Checking prerequisites'
try { docker version --format '{{.Server.Version}}' | Out-Null }
catch { Fatal 'Docker is not running or not installed. Start Docker Desktop first.' }
try { docker compose version | Out-Null }
catch { Fatal 'docker compose v2 not available. Upgrade Docker Desktop.' }
Ok ('Docker ' + (docker version --format '{{.Server.Version}}'))

# ============== [2/6] Generate .env ==============
Info '[2/6] Preparing .env'
$EnvFile = Join-Path $Root '.env'
$ExampleFile = Join-Path $Root '.env.example'
$AdminPassword = $null

if (Test-Path $EnvFile) {
    Ok '.env already exists, skip generation (delete it first to regenerate)'
} else {
    if (-not (Test-Path $ExampleFile)) { Fatal "$ExampleFile not found" }
    Copy-Item $ExampleFile $EnvFile

    if ($env:SKIP_PROMPT -eq '1') {
        $AdminPassword = if ($env:ADMIN_PASSWORD) { $env:ADMIN_PASSWORD } else { 'Quantpilot123!' }
        Warn ("SKIP_PROMPT=1, using default password: " + $AdminPassword)
    } else {
        Write-Host ''
        Write-Host 'Set the admin password (local dev only, min 8 chars):'
        while ($true) {
            $sec1 = Read-Host 'Password' -AsSecureString
            $sec2 = Read-Host 'Confirm' -AsSecureString
            $p1 = [System.Net.NetworkCredential]::new('', $sec1).Password
            $p2 = [System.Net.NetworkCredential]::new('', $sec2).Password
            if ($p1 -ne $p2) { Warn 'Passwords do not match'; continue }
            if ($p1.Length -lt 8) { Warn 'At least 8 characters required'; continue }
            $AdminPassword = $p1
            break
        }
    }

    Info 'Generating bcrypt hash...'
    $hashCmd = "pip install -q bcrypt && python -c `"import bcrypt,os; print(bcrypt.hashpw(os.environ['P'].encode(), bcrypt.gensalt()).decode())`""
    $Hash = (docker run --rm -e P=$AdminPassword python:3.12-slim sh -c $hashCmd).Trim()
    if (-not $Hash) { Fatal 'bcrypt hash generation failed' }
    Ok 'bcrypt hash generated'

    $bytes = New-Object byte[] 64
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $JwtKey = [System.BitConverter]::ToString($bytes).Replace('-', '').ToLower()
    Ok ("JWT key generated ({0} hex chars)" -f $JwtKey.Length)

    function Get-RandomPass([int]$len = 24) {
        $rb = New-Object byte[] $len
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($rb)
        ([System.Convert]::ToBase64String($rb) -replace '[/=+]', '').Substring(0, $len)
    }
    $DbPass = Get-RandomPass 24
    $RedisPass = Get-RandomPass 24

    $content = Get-Content $EnvFile -Raw -Encoding UTF8

    function Set-EnvVar([ref]$ref, [string]$key, [string]$val, [bool]$quoteSingle = $false) {
        $v = if ($quoteSingle) { "'$val'" } else { $val }
        $pattern = "(?m)^$([regex]::Escape($key))=.*$"
        if ([regex]::IsMatch($ref.Value, $pattern)) {
            $ref.Value = [regex]::Replace($ref.Value, $pattern, "$key=$v")
        } else {
            $ref.Value += "`n$key=$v`n"
        }
    }

    Set-EnvVar ([ref]$content) 'ADMIN_PASSWORD_HASH' $Hash $true
    Set-EnvVar ([ref]$content) 'JWT_SECRET_KEY' $JwtKey
    Set-EnvVar ([ref]$content) 'DB_PASSWORD' $DbPass
    Set-EnvVar ([ref]$content) 'REDIS_PASSWORD' $RedisPass

    [System.IO.File]::WriteAllText($EnvFile, $content, (New-Object System.Text.UTF8Encoding $false))
    Ok ".env generated ($EnvFile)"
}

# ============== [3/6] Start containers ==============
Info '[3/6] Starting db / redis / backend'
Invoke-Expression "$Compose up -d --build"
if ($LASTEXITCODE -ne 0) { Fatal 'docker compose up failed' }

# ============== [4/6] Wait for PostgreSQL ==============
Info '[4/6] Waiting for PostgreSQL'
$ready = $false
for ($i = 1; $i -le 30; $i++) {
    docker compose -f docker-compose.dev.yml exec -T db pg_isready -U quantpilot 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok ("PostgreSQL ready in {0}s" -f $i); $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { Fatal 'PostgreSQL not ready within 30s. Check: docker compose -f docker-compose.dev.yml logs db' }

Info 'Waiting for backend container'
$ready = $false
for ($i = 1; $i -le 60; $i++) {
    docker compose -f docker-compose.dev.yml exec -T backend true 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok 'backend container ready'; $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { Fatal 'backend not up within 60s. Check: docker compose -f docker-compose.dev.yml logs backend' }

# ============== [5/6] Migrations ==============
Info '[5/6] Running alembic upgrade head'
docker compose -f docker-compose.dev.yml exec -T backend uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { Fatal 'alembic upgrade failed' }

# ============== [6/6] Seed demo data ==============
if ($DoSeed) {
    Info '[6/6] Seeding demo data'
    docker compose -f docker-compose.dev.yml exec -T backend uv run python scripts/seed_demo_data.py
    if ($LASTEXITCODE -ne 0) { Warn 'seed_demo_data exited non-zero, check output above' } else { Ok 'demo data seeded' }
} else {
    Info '[6/6] Skipping seed (-NoSeed)'
}

Write-Host ''
Start-Sleep -Seconds 2
try {
    Invoke-WebRequest -Uri 'http://localhost:8000/health' -UseBasicParsing -TimeoutSec 5 | Out-Null
    Ok 'QuantPilot backend is up: http://localhost:8000'
} catch {
    Warn 'Health check failed. Check: docker compose -f docker-compose.dev.yml logs backend'
}

Write-Host ''
Write-Host '===================== Bootstrap complete =====================' -ForegroundColor Green
Write-Host ' Backend API : http://localhost:8000'
Write-Host ' API docs    : http://localhost:8000/docs'
Write-Host ' Admin       : admin'
$pwdHint = if ($AdminPassword) { $AdminPassword } else { '(reused existing .env)' }
Write-Host (" Password    : {0}" -f $pwdHint)
Write-Host ''
Write-Host ' Start frontend (separate terminal):'
Write-Host '   cd frontend; npm install; npm run dev'
Write-Host ''
Write-Host ' Stop services: docker compose -f docker-compose.dev.yml down'
Write-Host '===============================================================' -ForegroundColor Green
