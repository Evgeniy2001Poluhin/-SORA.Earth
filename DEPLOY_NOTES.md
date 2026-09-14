# Deployment Notes

## Models Directory Permissions (Production)

**Nothing to do. Do not chown `models/`.**

This section used to instruct `sudo chown -R 1000:1000 ./models`, so that the
containers could write there. That is the defect in #191, not the fix for it:
`models/` is the host's Git checkout, and a container able to write it dirties
the working tree, after which `scripts/deploy_production.sh` correctly refuses
every later deployment. The chown also did not survive — any `git checkout`
restores the files as whoever ran it.

`models/` is the seed: an immutable bootstrap that only a commit changes. It is
mounted `:ro` into backend and scheduler, and the containers need no ownership
of it — the files are world-readable. Retraining writes to
`runtime/staged/<run_id>/` on the `runtime_models` volume, which the containers
do own.

The one directory that still needs uid 1000 is `/app/runtime`, and
`Dockerfile.prod` creates it after `USER app` so that the named volume
initialises owned by the right user. No manual step.

## Verification

After setting permissions, verify:
```bash
ls -la models/ | head -5
# Should show: drwxr-xr-x ... 1000 1000 ... (or app app)
```
