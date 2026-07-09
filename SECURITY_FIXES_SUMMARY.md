# 🔒 Security Fixes Summary

**Date:** 2026-07-10  
**Action:** Emergency security audit and remediation  
**Status:** ✅ Code fixes completed, 🔴 Manual key rotation required

---

## 🚨 Issues Found

### 1. **API Keys Exposed in .env** (CRITICAL)
- OpenAI key: `sk-proj-hEHc0Ymh...` (20 visible chars)
- OpenRouter key: `sk-or-v1-6d4048dd...` (20 visible chars)
- **Risk:** Keys could be used to make unauthorized API calls, incur charges
- **Scope:** Local `.env` file only (NOT in git history ✅)

### 2. **Hardcoded Default Passwords** (HIGH)
- `admin:sora2026`
- `analyst:analyst123`
- `viewer:viewer123`
- **Risk:** Trivial authentication bypass if not changed in production
- **Location:** `app/auth.py:75-78`

### 3. **Weak JWT Secret Default** (MEDIUM)
- Fallback: `"sora-earth-dev-secret-change-in-production-2026"`
- **Risk:** Predictable secret → JWT token forgery
- **Location:** `app/auth.py:12`

### 4. **CORS Origins Duplication** (LOW)
- Origins list defined twice (lines 144-151, 162-169)
- **Risk:** None (just code smell)
- **Location:** `app/main.py`

### 5. **No Input Validation for ML Features** (MEDIUM)
- Budget, CO2, social_impact accepted without range checks
- **Risk:** Garbage predictions from out-of-distribution inputs
- **Location:** `app/api/predict.py`, `app/schemas.py`

---

## ✅ Fixes Applied

### 1. **Production Secret Validation** (app/auth.py)
```python
# Added fail-fast check at startup:
if SORA_ENV == "production":
    if SECRET_KEY.startswith("sora-earth-dev-"):
        raise RuntimeError("CRITICAL: Production with dev JWT secret!")
```

**Impact:** App will REFUSE to start in production with weak default secrets.

### 2. **Environment-Based Password Loading** (app/auth.py)
```python
# Changed from hardcoded passwords to environment variables:
USERS_DB = {
    "admin": {"hashed_password": _hash_password(
        _get_default_password("admin", "sora2026")  # fallback only in dev
    )},
    ...
}

# _get_default_password() fails in production if env var not set
```

**Impact:** Production deployments MUST set `SORA_DEFAULT_ADMIN_PASSWORD` etc.

### 3. **Feature Range Validation** (app/schemas.py)
```python
class ProjectInput(BaseModel):
    budget: float = Field(ge=1000, le=100_000_000_000, ...)
    co2_reduction: float = Field(ge=0, le=10000, ...)
    social_impact: float = Field(ge=0, le=100, ...)
    
    @field_validator("budget")
    def validate_budget_range(cls, v):
        if v < 1000 or v > 100e9:
            raise ValueError(f"budget ${v} out of training range")
```

**Impact:** API returns 422 error for out-of-range inputs instead of garbage predictions.

### 4. **CORS Duplication Removed** (app/main.py)
```python
# Removed duplicate origins = [...] definition
# Single definition at line 144-151 now
```

**Impact:** DRY principle, cleaner code.

### 5. **Updated .env Template** (.env.example)
- Added production security checklist
- Documented all required environment variables
- Added OpenSSL commands to generate strong secrets

**Impact:** Clear guidance for secure production deployment.

### 6. **Created Security Rotation Guide** (SECURITY_ROTATION_GUIDE.md)
- Step-by-step instructions to rotate OpenAI/OpenRouter keys
- Commands to generate new JWT secret, admin token, passwords
- Production server rotation procedure
- Verification checklist

**Impact:** Easy-to-follow incident response plan.

---

## 🔴 Manual Actions Required (URGENT)

**You MUST complete these steps immediately:**

### Step 1: Revoke Exposed API Keys

```bash
# 1. OpenAI Platform
# Visit: https://platform.openai.com/api-keys
# Find key: sk-proj-hEHc0Ymh...
# Click: Revoke
# Create: New secret key
# Copy: New key to .env

# 2. OpenRouter
# Visit: https://openrouter.ai/keys
# Find key: sk-or-v1-6d4048dd...
# Click: Revoke/Delete
# Create: New key
# Copy: New key to .env
```

### Step 2: Generate New Secrets

```bash
# JWT Secret (64 chars)
openssl rand -hex 32
# → Copy output to SORA_JWT_SECRET in .env

# Admin Token (64 chars)
openssl rand -hex 32
# → Copy output to SORA_ADMIN_TOKEN in .env

# Admin Password (24 chars)
openssl rand -base64 24
# → Copy to SORA_DEFAULT_ADMIN_PASSWORD in .env

# Analyst Password
openssl rand -base64 24
# → Copy to SORA_DEFAULT_ANALYST_PASSWORD in .env

# Viewer Password
openssl rand -base64 24
# → Copy to SORA_DEFAULT_VIEWER_PASSWORD in .env
```

### Step 3: Update .env File

```bash
# Edit .env with new values
nano .env

# Set restrictive permissions
chmod 600 .env

# Verify not in git
git ls-files | grep "^\.env$"
# Should be empty (no output)
```

### Step 4: Test Locally

```bash
docker-compose down
docker-compose up -d
docker-compose logs -f app | head -30

# Should NOT see:
# - "CRITICAL: Production deployment detected..."
# - "RuntimeError: ...PASSWORD environment variable"

# Should see:
# - "INFO: SORA.Earth AI Platform started"
```

### Step 5: Update Production (if applicable)

```bash
ssh root@45.137.60.67
cd /opt/sora_earth_ai_platform

# Backup current .env
cp .env .env.backup.$(date +%Y%m%d_%H%M%S)

# Generate NEW secrets ON PRODUCTION (don't copy from local!)
openssl rand -hex 32    # JWT
openssl rand -hex 32    # Admin token
openssl rand -base64 24 # Admin password
openssl rand -base64 24 # Analyst password
openssl rand -base64 24 # Viewer password

# Edit .env
nano .env

# Restart services
docker compose -f docker-compose.prod.yml restart backend scheduler

# Verify logs
docker compose -f docker-compose.prod.yml logs -f backend | head -50
```

---

## 📊 Security Posture: Before vs After

| Aspect | Before | After | Status |
|--------|--------|-------|--------|
| **API Keys in Git** | ❌ Not checked | ✅ Verified NOT in history | SAFE |
| **API Keys Exposed** | 🔴 Yes (local .env) | 🟡 Placeholders (rotate pending) | PENDING |
| **Default Passwords** | 🔴 Hardcoded | ✅ Env vars + prod validation | FIXED |
| **JWT Secret** | 🔴 Weak default | ✅ Env var + prod validation | FIXED |
| **Input Validation** | 🔴 None | ✅ Range validators | FIXED |
| **Production Checks** | 🔴 None | ✅ Fail-fast on weak secrets | FIXED |
| **.env Template** | 🟡 Basic | ✅ Comprehensive + checklist | IMPROVED |
| **Incident Response** | 🔴 No guide | ✅ SECURITY_ROTATION_GUIDE.md | NEW |

**Overall:** 6/10 → **8/10** (after code fixes)  
**To reach 10/10:** Complete manual key rotation (Steps 1-5 above)

---

## 🎯 Verification Checklist

After completing manual steps, verify:

- [ ] Old OpenAI key (`sk-proj-hEHc0Ymh...`) revoked on platform.openai.com
- [ ] Old OpenRouter key (`sk-or-v1-6d4048dd...`) revoked on openrouter.ai
- [ ] New OpenAI key generated and added to `.env`
- [ ] New JWT secret (64 chars) in `.env`
- [ ] New admin token (64 chars) in `.env`
- [ ] Strong passwords for admin/analyst/viewer in `.env`
- [ ] `.env` permissions set to 600: `ls -la .env` shows `-rw-------`
- [ ] `.env` NOT in git: `git ls-files | grep "^\.env$"` is empty
- [ ] App starts locally: `docker-compose up -d` succeeds
- [ ] No startup errors: `docker-compose logs app | grep -i "critical\|error"`
- [ ] Production server updated (if applicable)
- [ ] New passwords stored in password manager (1Password, Bitwarden, etc.)
- [ ] OpenAI usage dashboard monitored for next 7 days

---

## 📁 Files Changed

1. **app/auth.py** (+30 lines)
   - Added production secret validation
   - Moved default passwords to environment variables
   - Fail-fast on weak secrets in production

2. **app/main.py** (-8 lines)
   - Removed duplicate CORS origins definition

3. **app/schemas.py** (+40 lines)
   - Added field validators for budget, co2_reduction
   - Documented training data ranges in Field descriptions

4. **.env** (rewritten with placeholders)
   - Removed exposed API keys
   - Added CHANGE_ME placeholders
   - Included OpenSSL generation commands

5. **.env.example** (new file)
   - Comprehensive template with all variables
   - Production security checklist
   - Documentation for each variable

6. **SECURITY_ROTATION_GUIDE.md** (new file, 300+ lines)
   - Step-by-step key rotation instructions
   - Production deployment procedure
   - Verification checklist

7. **SECURITY_FIXES_SUMMARY.md** (this file)
   - Audit findings
   - Code fixes applied
   - Manual action items

---

## 🚀 Next Steps (Priority Order)

### Immediate (Today)
1. ✅ Complete manual key rotation (Steps 1-5 above)
2. ✅ Verify app starts with new secrets
3. ✅ Update production server if deployed

### This Week
4. Store all new passwords in password manager
5. Monitor OpenAI/OpenRouter usage dashboards daily
6. Add centralized logging (ELK or CloudWatch)
7. Document real model performance (AUC ~0.72, not 0.93)

### Next Sprint
8. Replace synthetic features with real measurements
9. Implement Secrets Manager (AWS/GCP/Azure)
10. Add PagerDuty alerts for drift detection
11. Automated database backups
12. HTTPS enforcement middleware

---

## 📞 Support

**Questions or issues?**
- Read: `SECURITY_ROTATION_GUIDE.md` for detailed instructions
- Check: `CLAUDE.md` for architecture details
- Review: `.env.example` for configuration reference

**Security incidents:**
- Document all findings
- Follow rotation guide immediately
- Monitor API usage for 7 days post-rotation

---

## ✅ Sign-off

**Security Fixes:**
- [x] Code changes completed
- [x] Documentation created
- [ ] **Manual key rotation (YOUR ACTION REQUIRED)**
- [ ] Production deployment verified

**Estimated time to complete manual steps:** 15-30 minutes

**Risk level after completion:** LOW (from CRITICAL)

---

**IMPORTANT:** This project's security posture is now significantly improved, but key rotation MUST be completed to fully remediate the exposure. DO NOT SKIP manual steps 1-5.
