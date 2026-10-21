# Local stand ownership

Initialize a **new, empty** working directory before using the stand:

```sh
export GSTAND_WORK="$HOME/.sora-gstand/my-new-run"
bash tests/grafana_stand/stand.sh init
bash tests/grafana_stand/stand.sh names
bash tests/grafana_stand/stand.sh up-base
bash tests/grafana_stand/stand.sh grafana a "$PWD/grafana/provisioning"
python3 tests/grafana_stand/check.py states
```

The marker assigns a random run id. Every container, network and volume has both
that id in its name and an `online.sora.gstand.run` label. An existing resource
with the same name but no matching label is refused, never adopted or removed.
Ports remain fixed and loopback-only: two stands cannot run simultaneously on
these ports. Docker reports that collision; the helper does not free ports
or remove another stand.

Daemon operations accept only a local Unix-socket endpoint. A remote
`DOCKER_HOST` is rejected before invoking Docker; context metadata is inspected
locally and an SSH/TCP context is refused before contacting that daemon.

`grafana` now accepts a variant such as `a`, **not an arbitrary container and
volume name**. The volume name is derived from this run and variant. `stop a`
and `rm a` inspect ownership before acting. `teardown` preflights all resources
selected by this run's label before removing any. It retains generated files.
Pre-existing, unlabelled resources from the old helper require a separate manual
review; the helper will not clean them up.

`override fast` creates `$GSTAND_WORK/outputs/fast`; `provisioning-from-main old`
creates a fresh output and extracts the locally known `origin/main` there.
Neither fetches from the network, deletes a directory or overwrites an existing
output. Use the printed provisioning path with `grafana`. That command also
refuses foreign provisioning paths and symlinks. Repository provisioning is
its only permitted source outside this run's outputs.

The old `admin.env` is not loaded or executed. The disposable password is now in
`$GSTAND_WORK/admin-password`; `check.py` validates the initialized directory and
only reads this file, never generating a missing password. Historical
`RESULTS.md` describes the previous helper, including its missing `control`
driver; these safety changes do **not** recreate or independently validate
scenario C.

This prevents accidental foreign-resource cleanup and existing symlink escapes.
It is not a security boundary against a malicious process with the same user or
Docker-daemon access: such a process can change labels, private paths or files
between checks and operations. Do not share the stand work directory or daemon
with untrusted actors. Stub-only tests validate command selection and refusal;
they do not prove Grafana runtime behavior or notification delivery.
