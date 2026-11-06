param([string]$Root = "$env:USERPROFILE\.sora-backups")
# Invoke WSL's installed Python, with the existing Windows SSH binary.
# No model, worker queue, decryption key or dynamically generated shell code.
$ErrorActionPreference = 'Stop'
$control = Join-Path $Root 'control'
$pathArgs = @($control, (Join-Path $control 'pull_ed25519'), (Join-Path $control 'known_hosts'), $Root)
$linuxPaths = @()
foreach ($path in $pathArgs) {
    $full = [IO.Path]::GetFullPath($path)
    if ($full -notmatch '^[A-Za-z]:\\' -or $full -match '["\r\n]') { throw 'Local drive path required' }
    $linuxPaths += '/mnt/' + $full.Substring(0,1).ToLowerInvariant() + '/' + $full.Substring(3).Replace('\','/')
}
& wsl.exe -d SoraCodeRunner -u root -- python3 ($linuxPaths[0] + '/backup_transport.py') pull `
    --root ($linuxPaths[3] + '/archives') --host sora-backup-reader@109.120.147.12 `
    --identity $linuxPaths[1] --known-hosts $linuxPaths[2] `
    --ssh-binary /mnt/c/Windows/System32/OpenSSH/ssh.exe
exit $LASTEXITCODE
