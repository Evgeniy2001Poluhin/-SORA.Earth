# Deployment Notes

## Models Directory Permissions (Production)

Before deploying to production, ensure the `models/` directory has correct ownership:

```bash
# On production server (45.137.60.67)
cd /opt/sora_earth_ai_platform
sudo chown -R 1000:1000 ./models
```

This is required because:
- Docker containers run as user `app` (uid=1000, gid=1000)
- The `models/` directory is mounted as a volume from the host
- Without correct ownership, containers cannot read/write model files

**Run this once after initial deployment or if models directory permissions get reset.**

## Verification

After setting permissions, verify:
```bash
ls -la models/ | head -5
# Should show: drwxr-xr-x ... 1000 1000 ... (or app app)
```
