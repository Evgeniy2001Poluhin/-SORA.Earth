# 🔐 Security Key Rotation Guide

**CRITICAL: Exposed API keys detected in .env file. Follow this guide immediately.**

---

## ⚠️ Status: KEYS EXPOSED (But not in git history ✅)

**Good news:** `.env` is NOT in git history (verified via `git log`).

**Bad news:** Local `.env` file contains exposed keys that may have been:
- Shared via chat/screenshot
- Copied to other systems
- Visible in IDE screenshots
- Backed up to cloud services

**Action required:** Rotate ALL keys immediately as precaution.

---

## 🚨 STEP 1: Rotate OpenAI API Keys

### 1a. OpenAI (sk-proj-hEHc0Ymh...)

```bash
# 1. Go to https://platform.openai.com/api-keys
# 2. Find key "sk-proj-hEHc0Ymh..." or matching name
# 3. Click "Revoke" → Confirm deletion
# 4. Click "Create new secret key"
# 5. Copy new key immediately (shown only once!)
# 6. Update .env:
#    OPENAI_API_KEY=sk-proj-NEW_KEY_HERE
```

**Current exposed key (first 20 chars):**
```
sk-proj-hEHc0Ymhlb3ZvqRE4wg0...
```

### 1b. OpenRouter (sk-or-v1-6d4048dd...)

```bash
# 1. Go to https://openrouter.ai/keys
# 2. Find key "sk-or-v1-6d4048dd..." or matching name
# 3. Click "Revoke" or delete key
# 4. Click "Create new key"
# 5. Copy new key
# 6. Update .env:
#    OPENAI_API_KEY=sk-or-v1-NEW_KEY_HERE
#    OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

**Current exposed key (first 20 chars):**
```
sk-or-v1-6d4048dd01c6c91e...
```

---

## 🔒 STEP 2: Rotate Production Secrets

### 2a. Generate New JWT Secret

```bash
# Generate 64-char random hex string
openssl rand -hex 32

# Copy output and update .env:
# SORA_JWT_SECRET=<paste_64_char_hex_here>
```

### 2b. Generate New Admin Token

```bash
# Generate 64-char random hex string
openssl rand -hex 32

# Copy output and update .env:
# SORA_ADMIN_TOKEN=<paste_64_char_hex_here>
```

### 2c. Set Strong Default Passwords

```bash
# Generate 3 strong passwords (16+ chars each)
openssl rand -base64 24  # For admin
openssl rand -base64 24  # For analyst
openssl rand -base64 24  # For viewer

# Update .env:
# SORA_DEFAULT_ADMIN_PASSWORD=<paste_password_1>
# SORA_DEFAULT_ANALYST_PASSWORD=<paste_password_2>
# SORA_DEFAULT_VIEWER_PASSWORD=<paste_password_3>

# STORE THESE IN PASSWORD MANAGER (1Password, Bitwarden, etc.)
```

---

## 🛡️ STEP 3: Update Production Server

**IF you have a production deployment at 45.137.60.67:**

```bash
# 1. SSH to production
ssh root@45.137.60.67

# 2. Backup current .env
cd /opt/sora_earth_ai_platform
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)

# 3. Generate new secrets ON PRODUCTION (don't copy from local!)
openssl rand -hex 32  # JWT secret
openssl rand -hex 32  # Admin token
openssl rand -base64 24  # Admin password
openssl rand -base64 24  # Analyst password
openssl rand -base64 24  # Viewer password

# 4. Edit .env with new values
nano .env

# 5. Restart services to pick up new secrets
docker compose -f docker-compose.prod.yml restart backend scheduler

# 6. Verify no errors in logs
docker compose -f docker-compose.prod.yml logs -f backend | head -50
```

---

## ✅ STEP 4: Verification Checklist

After rotation, verify:

```bash
# Local development
cd ~/sora_earth_ai_platform

# 1. Check .env has new keys (no sk-proj-hEHc0Ymh or sk-or-v1-6d4048dd)
grep "OPENAI_API_KEY" .env
# Should show NEW key or be empty/commented

# 2. Check .env permissions (should be 600)
ls -la .env
# If not: chmod 600 .env

# 3. Verify .env is in .gitignore
grep "^\.env$" .gitignore
# Should output: .env

# 4. Double-check .env not in git
git ls-files | grep "^\.env$"
# Should be empty (no output)

# 5. Test app starts with new secrets
docker-compose up -d
docker-compose logs -f app | head -20
# Should NOT see "CRITICAL: Production deployment detected with development JWT secret"
```

---

## 📋 STEP 5: Update .env File Template

Your `.env` file should now look like this:

```bash
# Environment
SORA_ENV=development  # or production

# Database
POSTGRES_PASSWORD=<STRONG_32_CHAR_PASSWORD>
DATABASE_URL=postgresql://sora:<SAME_PASSWORD>@postgres:5432/sora_earth

# Security (ALL ROTATED)
SORA_JWT_SECRET=<NEW_64_CHAR_HEX_FROM_openssl_rand_hex_32>
SORA_ADMIN_TOKEN=<NEW_64_CHAR_HEX_FROM_openssl_rand_hex_32>

# Default user passwords (REQUIRED for production)
SORA_DEFAULT_ADMIN_PASSWORD=<STRONG_PASSWORD_16_CHARS_MIN>
SORA_DEFAULT_ANALYST_PASSWORD=<STRONG_PASSWORD_16_CHARS_MIN>
SORA_DEFAULT_VIEWER_PASSWORD=<STRONG_PASSWORD_16_CHARS_MIN>

# External APIs (ROTATED)
OPENAI_API_KEY=<NEW_OPENAI_KEY_OR_EMPTY>
# OPENAI_API_KEY=<NEW_OPENROUTER_KEY_OR_EMPTY>
# OPENAI_BASE_URL=https://openrouter.ai/api/v1
# LLM_MODEL=openai/gpt-4o-mini

# Optional
OPENAQ_API_KEY=<YOUR_KEY_OR_EMPTY>
MLFLOW_TRACKING_URI=http://host.docker.internal:5556
SENTRY_DSN=
GRAFANA_PASSWORD=<STRONG_PASSWORD>
```

---

## 🔍 STEP 6: Audit Cloud Backups

Check if `.env` was backed up to:

1. **iCloud/Google Drive/Dropbox** (if project folder synced)
   - Search for `.env` in cloud storage
   - Delete any found copies
   - Purge from trash/deleted items

2. **Time Machine / Windows Backup**
   - Old backups may contain exposed keys
   - Consider those backups compromised

3. **IDE settings sync** (VS Code Settings Sync, JetBrains)
   - Should NOT sync `.env` (it's in .gitignore)
   - But verify sync logs just in case

4. **Docker volumes**
   ```bash
   # Check if .env was copied into Docker volumes
   docker volume ls
   docker volume inspect <volume_name> | grep Mountpoint
   # Manually check mountpoint for .env copies
   ```

---

## 📞 STEP 7: Monitor for Unauthorized Usage

### OpenAI Usage Dashboard
- Go to https://platform.openai.com/usage
- Check for unexpected API calls in last 7 days
- If suspicious activity → contact OpenAI support

### OpenRouter Usage
- Go to https://openrouter.ai/activity
- Review recent requests
- Report suspicious activity to support@openrouter.ai

### Database Access Logs
```bash
# On production server
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U sora -d sora_earth -c \
  "SELECT * FROM pg_stat_activity WHERE usename='sora';"
```

---

## ⚡ Quick Summary (TL;DR)

```bash
# === ACTIONS REQUIRED ===

1. Revoke old OpenAI key (sk-proj-hEHc0Ymh...)
2. Revoke old OpenRouter key (sk-or-v1-6d4048dd...)
3. Generate new keys and update .env locally
4. If production exists: rotate keys there too (SSH + regenerate)
5. Verify .env permissions: chmod 600 .env
6. Test app starts with new secrets
7. Monitor API usage dashboards for next 7 days

# === TIME ESTIMATE ===
15-30 minutes total
```

---

## ✅ Checklist

- [ ] Revoked old OpenAI API key
- [ ] Revoked old OpenRouter API key
- [ ] Generated new JWT secret (64 chars)
- [ ] Generated new admin token (64 chars)
- [ ] Set strong default user passwords
- [ ] Updated local .env file
- [ ] Updated production .env (if applicable)
- [ ] Verified .env permissions (600)
- [ ] Verified .env in .gitignore
- [ ] Tested app startup
- [ ] Stored new passwords in password manager
- [ ] Checked cloud backups for leaked .env
- [ ] Monitored API usage dashboards

---

## 📚 Additional Resources

- [OWASP Secrets Management Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html)
- [OpenAI Security Best Practices](https://platform.openai.com/docs/guides/safety-best-practices)
- [Rotating AWS/GCP Secrets](https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets.html)

---

**Questions?** Contact security team or refer to CLAUDE.md for architecture details.
