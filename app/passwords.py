"""Argon2id password hashing, importable without the rest of app.auth.

app/auth.py refuses to import in production without a JWT signing key, which
is right for a process that issues tokens and wrong for the one-shot bootstrap
that only hashes the initial passwords. So the hasher is here, with no import
of anything that reads a secret.
"""
import os

from argon2 import PasswordHasher

#
# Argon2id at the argon2-cffi defaults: time_cost=3, memory_cost=64 MiB,
# parallelism=4, which costs roughly 150 ms to hash and 130 ms to verify on
# commodity hardware. The cost is deliberately not configurable: the only
# reason to expose it would be to lower it, and a weak setting is
# indistinguishable from a strong one until a table leaks.
#
# Only Argon2id is accepted. Two SHA-256 formats used to be verified as well,
# "so that no existing account is locked out" -- but no store ever held one:
# accounts were rebuilt from the environment on every import, hashed with
# Argon2id. They now live in the `users` table, whose CHECK constraint refuses
# anything that is not `$argon2id$`, so there is nothing to migrate and the
# fallback is gone rather than kept as an unreachable way in.
# OWASP's floor for Argon2id. Raising the parameters as hardware improves is
# expected and is why they are read from the environment at all; lowering them
# past this point is refused outright, because a weak setting is
# indistinguishable from a strong one until a table leaks.
ARGON2_FLOOR = {"time_cost": 2, "memory_cost": 19 * 1024, "parallelism": 1}
ARGON2_DEFAULTS = {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 4}
# A ceiling as well as a floor: memory_cost is allocated per concurrent
# verification, so a mistyped value denies service just as effectively as a
# weak one -- in the opposite direction and just as quietly.
# memory_cost is the total for one hash operation. parallelism splits that
# total across lanes -- it does not multiply it. Measured on this build, m=256
# MiB costs ~256 MiB of peak RSS at p=1, 2, 4 and 8 alike.
#
# So the multiplier is concurrency, not parallelism: N verifications in flight
# hold roughly N x memory_cost at once. Nothing in the app caps N -- the rate
# limiter bounds requests per key per minute, not simultaneous ones -- and no
# container in docker-compose.prod.yml declares a memory limit, so a mistyped
# cost is bounded only by host RAM.
#
# 256 MiB is therefore a configuration guard rather than a proof of safety: it
# keeps a typo from reserving gigabytes per verification, while eight
# concurrent logins at the ceiling would still hold ~2 GiB. Bounding N belongs
# with a declared container limit and an admission bound, and should come
# before this ceiling is raised.
ARGON2_CEILING = {"time_cost": 10, "memory_cost": 256 * 1024, "parallelism": 8}


def _argon2_params() -> dict:
    params = {}
    for name, default in ARGON2_DEFAULTS.items():
        raw = os.getenv(f"SORA_ARGON2_{name.upper()}")
        if raw is None:
            params[name] = default
            continue
        try:
            value = int(raw)
        except ValueError:
            raise RuntimeError(
                f"SORA_ARGON2_{name.upper()} must be an integer, got {raw!r}"
            ) from None
        if value < ARGON2_FLOOR[name]:
            raise RuntimeError(
                f"SORA_ARGON2_{name.upper()}={value} is below the floor "
                f"{ARGON2_FLOOR[name]}. Refusing to weaken password hashing."
            )
        if value > ARGON2_CEILING[name]:
            raise RuntimeError(
                f"SORA_ARGON2_{name.upper()}={value} exceeds the ceiling "
                f"{ARGON2_CEILING[name]}. A cost this high denies service."
            )
        params[name] = value
    return params


_password_hasher = PasswordHasher(**_argon2_params())

ARGON2_PREFIX = "$argon2"


def _hash_password(password: str) -> str:
    return _password_hasher.hash(password)
