param(
    [string]$Root = "$env:USERPROFILE\.sora-backups",
    [string]$TransportFile,
    [string]$TrustedHostKeyFile
)
# Preparation only: no task registration, server changes, or production reads.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if (-not $TransportFile -or -not $TrustedHostKeyFile) { throw 'TransportFile and TrustedHostKeyFile required' }
if (-not (Test-Path -LiteralPath $TransportFile -PathType Leaf)) { throw 'Transport file missing' }
if (-not (Test-Path -LiteralPath $TrustedHostKeyFile -PathType Leaf)) { throw 'Trusted host key missing' }
$runner = Join-Path (Split-Path -Parent $TransportFile) 'run_msi_backup.ps1'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw 'Runner source missing' }
$trusted = [IO.File]::ReadAllText($TrustedHostKeyFile).Trim()
if ($trusted -notmatch '^109\.120\.147\.12 ssh-ed25519 [A-Za-z0-9+/=]+$') { throw 'Expected one pinned host key from trusted Mac SSH connection' }
$control = Join-Path $Root 'control'
if (Test-Path -LiteralPath $control) { throw 'Refuse to overwrite existing control directory' }
New-Item -ItemType Directory -Path $control -Force | Out-Null
$account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $control /inheritance:r /grant:r "$($account):(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'ACL update failed' }
$identity = Join-Path $control 'pull_ed25519'
$start = New-Object Diagnostics.ProcessStartInfo
$start.FileName = "$env:WINDIR\System32\OpenSSH\ssh-keygen.exe"
$start.Arguments = '-q -t ed25519 -N "" -f "' + $identity + '" -C sora-backup-pull'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$process = [Diagnostics.Process]::Start($start)
$process.StandardInput.Close()
if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Key generation timed out' }
if ($process.ExitCode -ne 0) { throw 'Key generation failed' }
[IO.File]::WriteAllText((Join-Path $control 'known_hosts'), $trusted + "`n", (New-Object Text.UTF8Encoding($false)))
Copy-Item -LiteralPath $TransportFile -Destination (Join-Path $control 'backup_transport.py')
Copy-Item -LiteralPath $runner -Destination (Join-Path $control 'run_msi_backup.ps1')
$pub = [IO.File]::ReadAllText($identity + '.pub').Trim()
if ($pub -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(?: .*)?$') { throw 'Unexpected generated public key' }
[PSCustomObject]@{
    prepared = $true
    scheduled = $false
    public_key = $pub
    private_key_exported = $false
    production_contacted = $false
    root = $Root
} | ConvertTo-Json -Compress
