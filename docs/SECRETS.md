# Secrets

Inventory, what a scan of this repository actually found, and how each secret is
rotated.

## Inventory

| variable | consumer | required | source | rotation owner |
|---|---|---|---|---|
| `SORA_JWT_SECRET` | `app/auth.py` — signs access and refresh tokens | **production** | environment | platform |
| `SORA_ADMIN_TOKEN` | admin-only endpoints | **production** | environment | platform |
| `POSTGRES_PASSWORD` | application and backup connections | **production** | environment / Docker secret | platform |
| `SORA_DEFAULT_{ADMIN,ANALYST,VIEWER}_PASSWORD` | seeds the built-in accounts | production | environment | platform |
| `SENTRY_DSN` | error reporting | optional | environment | platform |
| `OPENAI_API_KEY` | RAG endpoints | optional | environment | account holder |
| `OPENAQ_API_KEY` | air-quality ingestion | optional | environment | account holder |
| `TELEGRAM_BOT_TOKEN`, `SMTP_PASSWORD` | alert delivery | optional | environment | platform |
| `BACKUP_S3_ACCESS_KEY_FILE`, `BACKUP_S3_SECRET_KEY_FILE` | backup upload | backup only | **file**, never argv | platform |
| `BACKUP_RECIPIENT_KEY` | public key; encrypts backups | backup only | file | platform |
| `BACKUP_IDENTITY_KEY` | private key; **restore only** | restore only | file, never on the backup host | platform |

Two of these are asymmetric on purpose: the machine that writes backups holds
only the public half and cannot read what it wrote.

## What the scan found

`gitleaks` v8.28.0, pinned by digest, over the tracked tree and all 572 commits.

**Real key material, in tracked documentation.** Four files — the security
audit and rotation guides — quoted the OpenAI and OpenRouter keys they had been
written to report. Truncated prefixes rather than working keys, but a prefix is
still identifying material, and a document that reports a leak by reproducing it
is its own finding. 25 occurrences, redacted here.

They entered in commit `bb5aeb4`. **They remain in history**, and removing them
requires a rewrite, which is an owner decision and is not done here. The
documents themselves record that rotation was still outstanding.

**False positives, from one broad rule.** `generic-api-key` matched three
What-If form field names — `co2_reduction_tons` and friends — in the source and
again in every build artefact that inlines them. A custom rule written for this
repository initially matched inside `gfm-task-list-item` in `package-lock.json`,
because `ta|sk-|list` looks like a key prefix without a word boundary. Both are
pinned to the exact strings rather than excluded by path.

**One test credential**, renamed to `not-a-real-credential-invalid-by-design`
rather than allowlisted while looking plausible. An exception that needs a
comment explaining it is not a secret is one that outlives the reason for it.

### Claims from the earlier audit that did not hold

Checked before acting on them:

| claim | finding |
|---|---|
| a `.env` entered git history | **no** — only `.env.example`, in two commits |
| `k8s/deployment.yaml` carries a JWT placeholder | **no** — it uses `secretKeyRef` |
| hardcoded database credentials in `scripts/` | **none found** |

## Scanning

`.gitleaks.toml` holds the rules and a deliberately small allowlist. Nothing
suppresses a rule globally; every exception is pinned to a path or an exact
string and says why.

Excluded by path: dependencies, build artefacts, and `.claude/worktrees/` —
sibling checkouts of this same repository, which multiply every finding by the
number of worktrees and hide the total. The first scan reported 71 findings that
were 10 distinct ones counted six times.

`secret-scan.yml` runs on every pull request over the working tree, and weekly
over the whole history. It is a separate workflow rather than a job in `ci.yml`
because six open pull requests already edit that file.

**The report shows rule, path and line — never the value, never the matching
line, and it is not uploaded as an artifact.** A scanner that publishes what it
finds has moved the secret rather than contained it.

Locally:

```bash
gitleaks detect --no-git --config=.gitleaks.toml
```

## Startup validation

`app/secret_validation.py` refuses to start production when a required secret is
missing, is a placeholder from the example configuration, still carries the
development prefix, or is too short to be worth signing with.

A placeholder is the case that matters. Missing fails immediately and visibly; a
placeholder starts, serves traffic, and signs tokens anyone holding the same
public example can forge.

Every fault is reported at once rather than one per restart, and the message
names variables, never values — a validation error that echoes the secret puts
it in every log that captured the failure.

Development is not policed. Requiring eight variables to run a test pushes
people towards copying a real value into their shell.

## Rotation

The same shape for each, differing only in where the new value goes.

| secret | generate | apply | verify |
|---|---|---|---|
| `SORA_JWT_SECRET` | `openssl rand -hex 32` | set, restart the app | existing sessions are invalidated — expected, and the reason to do it in a window |
| `SORA_ADMIN_TOKEN` | `openssl rand -hex 24` | set, restart | an admin endpoint rejects the old token |
| `POSTGRES_PASSWORD` | `openssl rand -base64 32` | `ALTER ROLE … PASSWORD`, then update the app and the backup job **together** | both connect; a backup completes |
| S3 credentials | provider console | write to the credential files, no restart needed | a backup uploads and its manifest appears |
| `BACKUP_RECIPIENT_KEY` | `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072` | public half to the backup host, private half **elsewhere** | a new backup restores in a drill |
| `OPENAI_API_KEY` and other vendor keys | vendor console | set, restart | the endpoint that uses it answers |

Rotating the database password and the backup credentials in the wrong order
leaves backups failing silently until someone looks. Change them together and
confirm a backup completes before walking away.

**Rotating the backup recipient key does not re-encrypt existing backups.** Keep
the old private identity for as long as any backup encrypted to it is still
within retention, or those backups become unreadable.

## If a secret is exposed

1. **Contain.** Revoke at the provider before anything else. A rotated-but-not-revoked key is still a live key.
2. **Rotate**, following the table above.
3. **Invalidate sessions** if the signing secret was involved.
4. **Look for use** — provider logs, application logs, unexpected spend.
5. **Assess reach.** A secret in git history is in every clone and every fork. Rotation is the fix; history rewriting is cleanup, and needs owner approval because it invalidates every outstanding branch and pull request.
6. **Redeploy and verify.**
7. **Write down** what was exposed, for how long, and what was done — without reproducing the value, which is how these four documents became a finding in the first place.
