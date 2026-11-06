param([string]$Root = "$env:USERPROFILE\.sora-backups", [switch]$Apply)
# Register only after owner GO and server install. USB copy deferred by owner.
$ErrorActionPreference = 'Stop'
$name = 'SoraEncryptedBackupPull'
$runner = Join-Path $Root 'control\run_msi_backup.ps1'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw 'Runner missing' }
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { throw 'Task already exists; refusing replacement' }
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
# 04:00 UTC = 07:00 Moscow. Convert for the actual Windows timezone.
$utc = [DateTime]::SpecifyKind((Get-Date).ToUniversalTime().Date.AddHours(4), [DateTimeKind]::Utc)
$dailyAt = [TimeZoneInfo]::ConvertTimeFromUtc($utc, [TimeZoneInfo]::Local)
$daily = New-ScheduledTaskTrigger -Daily -At $dailyAt
$retry = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 30)
$login = New-ScheduledTaskTrigger -AtLogOn -User $account
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$full = [IO.Path]::GetFullPath($Root)
if ($full -notmatch '^[A-Za-z]:\\' -or $full -match '["\r\n]') { throw 'Local drive path required' }
$linux = '/mnt/' + $full.Substring(0,1).ToLowerInvariant() + '/' + $full.Substring(3).Replace('\','/')
$arguments = '-d SoraCodeRunner -u root -- python3 "' + $linux + '/control/backup_transport.py" pull --root "' + $linux + '/archives" --host sora-backup-reader@109.120.147.12 --identity "' + $linux + '/control/pull_ed25519" --known-hosts "' + $linux + '/control/known_hosts" --ssh-binary /mnt/c/Windows/System32/OpenSSH/ssh.exe'
$action = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument $arguments
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
if (-not $Apply) { Write-Output 'MSI_BACKUP_TASK_PLAN_ONLY; daily 04:00 UTC, logon, retry 30m; requires logged-in owner'; exit 0 }
Register-ScheduledTask -TaskName $name -Action $action -Trigger @($daily,$retry,$login) -Settings $settings -Principal $principal | Out-Null
Write-Output 'MSI_BACKUP_TASK_INSTALLED; no immediate pull was started'
