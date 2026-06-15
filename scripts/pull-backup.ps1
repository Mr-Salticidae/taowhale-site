# taowhale 数据库备份本地拉取：每天由计划任务运行（任务名 TaowhaleBackupPull）
# 拉取服务器最新备份到 backups\，校验大小与 gzip 完整性，本地保留 30 份，日志 backups\pull.log
$ErrorActionPreference = "Stop"
$SshKey = "C:\Users\Administrator\.ssh\id_ed25519_whalesea"
$Remote = "ubuntu@43.128.2.110"
$RemoteDir = "/opt/whalesea/backups"
$LocalDir = "E:\AIGC工作站\taowhale-site\backups"
$TempDir = "C:\temp\taowhale-backup"
$Log = Join-Path $LocalDir "pull.log"
$SshOpts = @("-i", $SshKey, "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new")

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') $msg"
    Add-Content -Path $Log -Value $line -Encoding utf8
    Write-Output $line
}

try {
    New-Item -ItemType Directory -Force $LocalDir | Out-Null
    New-Item -ItemType Directory -Force $TempDir | Out-Null

    # 1. 找服务器上最新备份及其大小
    $latest = (& ssh @SshOpts $Remote "ls -t $RemoteDir/whale-*.db.gz | head -1").Trim()
    if (-not $latest) { Write-Log "FAIL 服务器上没有备份文件"; exit 1 }
    $name = Split-Path $latest -Leaf
    $remoteSize = [int64](& ssh @SshOpts $Remote "stat -c%s $latest").Trim()

    # 2. 已有同名且大小一致则跳过
    $dest = Join-Path $LocalDir $name
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -eq $remoteSize)) {
        Write-Log "SKIP 已有最新 $name ($remoteSize B)"
        exit 0
    }

    # 3. scp 到 ASCII 临时目录（中文路径直传会出错），再移动
    $tmp = Join-Path $TempDir $name
    & scp @SshOpts "${Remote}:$latest" $tmp | Out-Null
    if ((Get-Item $tmp).Length -ne $remoteSize) {
        Write-Log "FAIL $name 大小不符 local=$((Get-Item $tmp).Length) remote=$remoteSize"
        Remove-Item $tmp -Force
        exit 1
    }

    # 4. gzip 完整性校验（读穿一遍）
    $fs = [IO.File]::OpenRead($tmp)
    try {
        $gz = New-Object IO.Compression.GZipStream($fs, [IO.Compression.CompressionMode]::Decompress)
        $buf = New-Object byte[] 65536
        while ($gz.Read($buf, 0, $buf.Length) -gt 0) { }
        $gz.Dispose()
    } catch {
        $fs.Dispose()
        Write-Log "FAIL $name gzip 校验失败: $_"
        Remove-Item $tmp -Force
        exit 1
    }
    $fs.Dispose()

    Move-Item $tmp $dest -Force
    Write-Log "OK $name ($remoteSize B)"

    # 5. 本地保留最近 30 份
    Get-ChildItem $LocalDir -Filter "whale-*.db.gz" | Sort-Object Name -Descending |
        Select-Object -Skip 30 | ForEach-Object {
            Write-Log "PRUNE $($_.Name)"
            Remove-Item $_.FullName -Force
        }
} catch {
    Write-Log "FAIL $_"
    exit 1
}
