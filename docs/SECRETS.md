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
| `COPILOT_LLM_API_KEY` | optional Co-Pilot prose rewrite | **no** — off by default | environment | whoever owns the chosen provider |
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
written to report. **25 occurrences, redacted here.**

What the fragments are is not the same as what they prove. They are shorter than
a complete key, which is why no scanner rule for a full key matched them — but a
fragment being short says nothing about whether the credential it came from was
ever revoked, and key formats differ enough between vendors and eras that
inferring anything from length is unsound. Treat them as identifying material
belonging to a credential of **unknown validity**.

They entered in commit `bb5aeb4`. **They remain in history**, so:

- **owner verification is required** before any claim that this risk is closed;
- **rotation, not redaction, is the remedy** — the fragments are gone from the
  tree, which is not the same as the credential being dead;
- **rewriting history is neither done nor sufficient.** It invalidates every
  outstanding branch and pull request, and it does not revoke anything. A secret
  that reached a remote is in every clone, fork and cache already.

| status | |
|---|---|
| active valid credentials detected by the scanner | 0 |
| historical secret fragments | 25, in 4 documents, commit `bb5aeb4` |
| credential validity | **unknown** |
| owner rotation verification | **required** |
| rotations performed here | none |
| history rewritten | no |

"No active credentials detected" means the scanner found none it can recognise.
It is not a demonstration that none exist.

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

## The Co-Pilot's LLM: three modes, one of them the default

The Co-Pilot computes its verdict, probability, confidence and drivers from the
model and from templates. An LLM only rewrites the prose of `executive_summary`
and answers follow-up questions. Nothing a caller relies on depends on a service
being reachable, and a test asserts that contract.

| mode | configuration | external dependency |
|---|---|---|
| `smart_template` | **default**, nothing to set | none |
| self-hosted | `COPILOT_LLM_BASE_URL=http://ollama:11434/v1` | none leaves the host |
| managed | any OpenAI-compatible endpoint over https | the provider |

There is **no default endpoint**. Enabling the LLM means naming where it lives,
so a forgotten key can never start an outbound call by itself — which the
previous arrangement allowed: the gate was the presence of `OPENAI_API_KEY`, and
the endpoint defaulted to `api.openai.com`.

### Endpoint validation

The base URL comes from deployment configuration only — no request influences it.
It is still validated, because a misconfigured or tampered endpoint is the case
this protects against, not a hostile provider:

| refused | why |
|---|---|
| credentials in the URL | a URL travels wherever it is logged, and the credential with it |
| a query string or fragment | a base URL is a prefix, not a request; anything appended is smuggled into every call |
| any scheme but http/https | |
| no host | |
| plain `http://` to a remote host | the request carries project data and the key |

Plain `http://` is accepted only to `localhost`, `127.0.0.1`, `::1`, `ollama` or
`host.docker.internal` — the addresses where the hop never leaves the machine.

### Addressing a self-hosted Ollama

The address depends on where the application runs, and getting it wrong is the
usual first mistake: **`localhost` inside a container is the container**, not the
host.

| application | Ollama | base URL |
|---|---|---|
| on the host | on the host | `http://localhost:11434/v1` |
| Compose service | Compose service | `http://ollama:11434/v1` |
| in Docker | on the host | `http://host.docker.internal:11434/v1` |

The OpenAI client requires *some* value for the API key; Ollama ignores it.

`host.docker.internal` resolves on its own only under Docker Desktop. On Linux
Docker Engine — which is what production runs — it has to be granted:

```yaml
services:
  backend:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Without that line the third row simply fails to resolve, and the Co-Pilot falls
back to `smart_template` with nothing obviously wrong.

### What this validation is and is not

It is **configuration hardening for a deployment-controlled endpoint**. The base
URL comes from the environment and no request influences it, so there is no
user-driven SSRF vector to prevent; what it guards against is a mistyped or
tampered configuration.

It is deliberately not a general SSRF firewall. It does not restrict ports,
disable redirects, re-check the destination after DNS resolution, or exclude
cloud metadata addresses. Adding those would turn a provider adapter into a
network policy engine, and the threat it would answer — an operator who can
already set environment variables — has better options available to it.

### OpenAI and OpenRouter are retired

Not because their endpoints are unreachable. `api.openai.com` answers — it
returns 401. The reason is that **the accounts cannot be administered from
here**: a key cannot be created, rotated or revoked. A credential you cannot
revoke is a standing risk, and what it bought was nicer wording.

The `openai` package stays in requirements. It is a client for the OpenAI *wire
format*, which is what Ollama and the managed alternatives speak; it is not a
binding to that company.

```
DEPENDENCY_STATUS      OpenAI/OpenRouter retired from configuration
CREDENTIAL_CONSUMPTION none — no code path reads those variables
PROVIDER_REVOCATION    unverified; the consoles are not reachable from here
OWNER_FOLLOW_UP        if account access returns, locate and revoke the
                       corresponding credentials and review usage logs
```

Retired variables are **ignored with a warning, not rejected**. Refusing to
start over a forgotten line in an old deployment file would turn a no-op into an
outage during whatever change surfaced it.

Note for scope: other foreign services remain in use and are reachable —
Open-Meteo, OpenAQ, the World Bank and OECD all answer, and unlike the LLM they
are functional dependencies of the environmental pipeline. This change is about
one provider that cannot be administered, not about foreign services generally.

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
| `COPILOT_LLM_API_KEY` | chosen provider | set, restart | the Co-Pilot health endpoint reports `smart_template_with_llm_rewrite` |

Rotating the database password and the backup credentials in the wrong order
leaves backups failing silently until someone looks. Change them together and
confirm a backup completes before walking away.

**Rotating the backup recipient key does not re-encrypt existing backups.** Keep
the old private identity for as long as any backup encrypted to it is still
within retention, or those backups become unreadable.

## If a secret is exposed

0. **Establish identity.** Find the credential a fragment belongs to, in the
   OpenAI and OpenRouter consoles. If identity cannot be *disproved*, treat the
   credential as compromised — the burden runs that way round, not the other.
1. **Contain.** Revoke at the provider before anything else. A rotated-but-not-revoked key is still a live key.
2. **Rotate**, following the table above.
3. **Invalidate sessions** if the signing secret was involved.
4. **Look for use** — provider logs, application logs, unexpected spend.
5. **Assess reach.** A secret in git history is in every clone and every fork. Rotation is the fix; history rewriting is cleanup, and needs owner approval because it invalidates every outstanding branch and pull request.
6. **Redeploy and verify.**
7. **Record the rotation evidence outside Git** — the provider's own audit
   trail, not a file in the repository. Writing it here is how these four
   documents became a finding.
8. **Write down** what was exposed, for how long, and what was done — without reproducing the value, which is how these four documents became a finding in the first place.
