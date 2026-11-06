#!/usr/bin/env bash
# Destructive OS installation checks: ONLY in a disposable, network-isolated
# Debian container with SORA_BACKUP_INSTALL_TEST=1. Never run on the host/VPS.
# systemctl activation is stubbed; Unix permissions and OpenSSH are real.
set -euo pipefail
[ "${SORA_BACKUP_INSTALL_TEST:-}" = 1 ] && [ -f /.dockerenv ] && [ "$(id -u)" = 0 ] \
  || { echo 'requires explicit disposable container stand' >&2; exit 2; }
repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"
stand="$(mktemp -d)"
sshd_pid=''
cleanup() { [ -z "$sshd_pid" ] || kill "$sshd_pid" 2>/dev/null || true; }
trap cleanup EXIT
mkdir -p "$stand/bin" /run/sshd
cat > "$stand/bin/systemctl" <<'SH'
#!/bin/sh
case "$*" in 'daemon-reload'|'enable --now sora-backup-export.timer') echo "$*" >> "$SORA_SYSTEMCTL_LOG" ;; *) exit 1 ;; esac
SH
chmod +x "$stand/bin/systemctl"
export SORA_SYSTEMCTL_LOG="$stand/systemctl.log"
export PATH="$stand/bin:$PATH"

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$stand/recovery.pem" 2>/dev/null
openssl pkey -in "$stand/recovery.pem" -pubout -out "$stand/recipient.pem" 2>/dev/null
ssh-keygen -q -t ed25519 -N '' -f "$stand/client"
bash scripts/install_backup_export.sh --check "$stand/recipient.pem" "$stand/client.pub"
! getent passwd sora-backup-reader >/dev/null
[ ! -e /usr/local/lib/sora-backup ] && [ ! -e /etc/sora-backup ]
[ ! -e /var/lib/sora-backup-export ] && [ ! -e "$SORA_SYSTEMCTL_LOG" ]
echo 'INSTALL_PREFLIGHT_NO_SIDE_EFFECTS_OK'

bash scripts/install_backup_export.sh --apply "$stand/recipient.pem" "$stand/client.pub"
grep -qx 'daemon-reload' "$SORA_SYSTEMCTL_LOG"
grep -qx 'enable --now sora-backup-export.timer' "$SORA_SYSTEMCTL_LOG"
/usr/bin/systemd-analyze verify /etc/systemd/system/sora-backup-export.service /etc/systemd/system/sora-backup-export.timer
sha256sum /usr/local/lib/sora-backup/* /var/lib/sora-backup-reader/.ssh/authorized_keys > "$stand/before.sha256"
if bash scripts/install_backup_export.sh --apply "$stand/recipient.pem" "$stand/client.pub" > "$stand/repeat.log" 2>&1; then
    echo 'installer overwrote existing installation' >&2; exit 1
fi
sha256sum -c "$stand/before.sha256" >/dev/null
echo 'INSTALL_REPEAT_REFUSED_WITHOUT_OVERWRITE_OK'

install -d -m 0700 /var/backups/sora
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="/var/backups/sora/sora_earth_${stamp}.dump"
printf 'SORA_ONLY_SYNTHETIC_EXPORT\000\377\n' > "$dump"
sha256sum "$dump" > "$dump.sha256"
# Execute the real unit's command, without claiming systemd/cgroup execution.
python3 - <<'PY'
import configparser, shlex, subprocess
p=configparser.ConfigParser(interpolation=None)
p.read('/etc/systemd/system/sora-backup-export.service')
subprocess.run(shlex.split(p['Service']['ExecStart']),check=True)
PY
if runuser -u sora-backup-reader -- cat "$dump" > /dev/null 2>&1; then
    echo 'reader could read plaintext' >&2; exit 1
fi
if runuser -u sora-backup-reader -- touch /var/lib/sora-backup-export/should-not-write 2>/dev/null; then
    echo 'reader could write export' >&2; exit 1
fi
grep -qF 'SORA_ONLY_SYNTHETIC_EXPORT' "$dump"
if grep -rFq 'SORA_ONLY_SYNTHETIC_EXPORT' /var/lib/sora-backup-export; then
    echo 'plaintext present in encrypted export' >&2; exit 1
fi
echo 'EXPORT_REAL_UNIX_PERMISSIONS_OK'

ssh-keygen -q -t ed25519 -N '' -f "$stand/host"
printf '[127.0.0.1]:2222 ' > "$stand/known_hosts"
cat "$stand/host.pub" >> "$stand/known_hosts"
cat > "$stand/sshd.conf" <<CONF
Port 2222
ListenAddress 127.0.0.1
HostKey $stand/host
PidFile $stand/sshd.pid
UsePAM yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
StrictModes yes
AllowUsers sora-backup-reader
AuthorizedKeysFile .ssh/authorized_keys
CONF
/usr/sbin/sshd -t -f "$stand/sshd.conf"
/usr/sbin/sshd -D -e -f "$stand/sshd.conf" > "$stand/sshd.log" 2>&1 &
sshd_pid=$!
ssh_args=(-F /dev/null -i "$stand/client" -p 2222 -o BatchMode=yes -o IdentitiesOnly=yes
    -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$stand/known_hosts" -o ConnectTimeout=5)
target=sora-backup-reader@127.0.0.1
ready=0
for _ in $(seq 1 20); do
    if ssh "${ssh_args[@]}" "$target" list > "$stand/list.json" 2> "$stand/client.log"; then ready=1; break; fi
    sleep 0.25
done
if [ "$ready" != 1 ]; then cat "$stand/client.log" "$stand/sshd.log" >&2; exit 1; fi
python3 - "$stand/list.json" "$stamp" <<'PY'
import json,sys
assert json.load(open(sys.argv[1]))==[sys.argv[2]]
PY
ssh "${ssh_args[@]}" "$target" "manifest $stamp" > "$stand/manifest.json"
ssh "${ssh_args[@]}" "$target" "part $stamp payload.enc" > "$stand/received.enc"
cmp "$stand/received.enc" "/var/lib/sora-backup-export/$stamp/payload.enc"
for request in id 'sh -c id' 'manifest ../' "part $stamp ../recovery.pem"; do
    if ssh "${ssh_args[@]}" "$target" "$request" > "$stand/rejected.out" 2> "$stand/rejected.err"; then
        echo 'forced command accepted forbidden request' >&2; exit 1
    fi
    [ ! -s "$stand/rejected.out" ]
done
if timeout 10 ssh "${ssh_args[@]}" -N -o ExitOnForwardFailure=yes -R 0:127.0.0.1:22 "$target" > "$stand/forward.out" 2> "$stand/forward.err"; then
    echo 'forwarding accepted' >&2; exit 1
else
    rc=$?
    [ "$rc" != 124 ] || { echo 'forwarding test timed out' >&2; exit 1; }
    grep -Eiq 'forwarding failed|administratively prohibited' "$stand/forward.err"
fi
echo 'REAL_OPENSSH_FORCED_COMMAND_AND_FORWARDING_OK'
echo 'BACKUP_INSTALLATION_STAND_PASSED'
