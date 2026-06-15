# SessionStart hook: pull latest remote (Tencent) DB backup to local cold storage,
# at most once per local calendar day. No backfill of missed days (only ever pulls
# the single latest remote file). Non-blocking: main path self-detaches the ~15min
# transfer into a hidden -Worker process and returns immediately.
#
# Auth: Windows OpenSSH (ssh.exe/scp.exe) with a dedicated passphrase-less key
# (~/.ssh/qp_tencent). Host identity pinned in ~/.ssh/known_hosts +
# StrictHostKeyChecking=yes (fingerprint SHA256:uDrxmYGmEiG906ddWMsCNXlRI9N5DrUZCg26KeTxd/0).
# No password is ever read or passed. (Replaced the old plink/pscp -pw QP_SSH_PW path
# on 2026-06-12; the env var is no longer needed by this hook.)
#
# ASCII-only on purpose: PowerShell 5.1 reads BOM-less .ps1 as ANSI, which corrupts
# non-ASCII (CJK) literals and breaks parsing.
param([switch]$Worker)
$ErrorActionPreference = 'Stop'
$root    = 'D:\MyWork\10Project\RD\QuantPilot'
$destDir = Join-Path $root 'backups\remote'
$marker  = Join-Path $destDir '.last_pull_date'
$log     = Join-Path $destDir 'pull.log'
$today   = (Get-Date).ToString('yyyy-MM-dd')
$h       = 'ubuntu@43.134.63.13'
$key     = Join-Path $env:USERPROFILE '.ssh\qp_tencent'
$kh      = Join-Path $env:USERPROFILE '.ssh\known_hosts'
$sshOpts = @('-i',$key,'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=15','-o',"UserKnownHostsFile=$kh",'-o','StrictHostKeyChecking=yes')
New-Item -ItemType Directory -Force -Path $destDir | Out-Null
function Log($m){ "$((Get-Date).ToString('s')) $m" | Out-File -Append -FilePath $log -Encoding utf8 }

if ($Worker) {
    try {
        if (-not (Test-Path $key)) { Log 'worker: key file missing, abort'; return }
        Log "worker: pull start for $today"
        $latest = (& ssh @sshOpts $h "ls -t ~/QuantPilot/backups/qp_*.sql.gz | head -1").Trim()
        if (-not $latest) { Log 'worker: no remote backup found'; return }
        $base = Split-Path $latest -Leaf
        $localPath = Join-Path $destDir $base
        if (Test-Path $localPath) { Log "worker: already have $base; mark done"; Set-Content -Path $marker -Value $today -NoNewline; return }
        & scp @sshOpts "${h}:$latest" $localPath
        if ($LASTEXITCODE -ne 0) { Log "worker: scp failed exit=$LASTEXITCODE"; return }
        $rb = [int64]((& ssh @sshOpts $h "stat -c %s '$latest'").Trim())
        $lb = (Get-Item $localPath).Length
        if ($rb -ne $lb) { Log "worker: SIZE MISMATCH remote=$rb local=$lb; delete partial"; Remove-Item $localPath -Force; return }
        Set-Content -Path $marker -Value $today -NoNewline
        Log "worker: pull OK $base ($lb bytes)"
        Get-ChildItem $destDir -Filter 'qp_*.sql.gz' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 7 | Remove-Item -Force -ErrorAction SilentlyContinue
    } catch { Log "worker: ERROR $_" }
    return
}

# main path: fast + non-blocking
if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $today)) {
    Write-Output "[remote-backup-pull] already pulled today ($today); skip"
    exit 0
}
if (-not (Test-Path $key)) {
    Write-Output "[remote-backup-pull] key ~/.ssh/qp_tencent missing; skip"
    exit 0
}
$self = $MyInvocation.MyCommand.Path
Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$self,'-Worker')
Write-Output "[remote-backup-pull] DUE today ($today) - launched background pull of latest remote backup (log: backups\remote\pull.log)"
exit 0
