#!/usr/bin/env bash
# Explicit installation only. Default --check does not change the host.
# Production invocation needs the owner's separately scoped installation GO.
set -euo pipefail
mode="${1:-}"
recipient="${2:-}"
reader_key="${3:-}"
case "$mode" in --check|--apply) ;; *) echo 'usage: bash scripts/install_backup_export.sh --check|--apply recipient.pem reader.pub' >&2; exit 2 ;; esac
[ "$#" -eq 3 ] || exit 2
[ "$(id -u)" -eq 0 ] || { echo 'root required' >&2; exit 2; }
bundle="$(cd "$(dirname "$0")/.." && pwd)"
for tool in install useradd getent systemctl openssl python3; do command -v "$tool" >/dev/null; done
python3 -c 'import cryptography'
openssl pkey -pubin -in "$recipient" -noout >/dev/null 2>&1
python3 - "$reader_key" <<'PY'
import base64, pathlib, sys
raw=pathlib.Path(sys.argv[1]).read_text().strip()
fields=raw.split()
assert '\n' not in raw and '\r' not in raw and len(fields)>=2
assert fields[0]=='ssh-ed25519'
value=base64.b64decode(fields[1], validate=True)
assert len(value)==51 and value[:19]==b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'
PY
for file in scripts/backup_transport.py scripts/backup_crypt.sh scripts/backup_crypt.py infra/systemd/sora-backup-export.service infra/systemd/sora-backup-export.timer; do
    [ -f "$bundle/$file" ] && [ ! -L "$bundle/$file" ] || exit 2
done
if getent passwd sora-backup-reader >/dev/null; then echo 'reader account already exists; inspect before installation' >&2; exit 2; fi
if getent group sora-backup-reader >/dev/null; then echo 'reader group already exists; inspect before installation' >&2; exit 2; fi
for path in /usr/local/lib/sora-backup /etc/sora-backup /var/lib/sora-backup-export /var/lib/sora-backup-reader /etc/systemd/system/sora-backup-export.service /etc/systemd/system/sora-backup-export.timer; do
    if [ -e "$path" ] || [ -L "$path" ]; then echo "refusing to replace $path" >&2; exit 2; fi
done
echo 'Plan: dedicated reader, root-owned forced command, encrypted export only, timer 03:45 UTC.'
echo 'No application deployment, database migration, SSH daemon restart, or backup deletion.'
if [ "$mode" = '--check' ]; then echo 'BACKUP_EXPORT_PREFLIGHT_OK'; exit 0; fi
useradd --system --user-group --home-dir /var/lib/sora-backup-reader --no-create-home --shell /bin/sh sora-backup-reader
install -d -m 0755 -o root -g root /usr/local/lib/sora-backup /var/lib/sora-backup-reader /var/lib/sora-backup-reader/.ssh
install -d -m 0700 -o root -g root /etc/sora-backup
install -d -m 0750 -o root -g sora-backup-reader /var/lib/sora-backup-export
for name in backup_transport.py backup_crypt.sh backup_crypt.py; do
    install -m 0644 -o root -g root "$bundle/scripts/$name" "/usr/local/lib/sora-backup/$name"
done
install -m 0644 -o root -g root "$recipient" /etc/sora-backup/recipient.pem
python3 - "$reader_key" <<'PY'
from pathlib import Path
import sys
fields=Path(sys.argv[1]).read_text().split()
line='restrict,command="/usr/bin/python3 /usr/local/lib/sora-backup/backup_transport.py serve --root /var/lib/sora-backup-export" '+fields[0]+' '+fields[1]+'\n'
p=Path('/var/lib/sora-backup-reader/.ssh/authorized_keys')
with p.open('x') as f: f.write(line)
p.chmod(0o644)
PY
install -m 0644 -o root -g root "$bundle/infra/systemd/sora-backup-export.service" /etc/systemd/system/
install -m 0644 -o root -g root "$bundle/infra/systemd/sora-backup-export.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sora-backup-export.timer
echo 'BACKUP_EXPORT_INSTALLED; service runs on timer; verify restricted SSH access before acceptance.'
