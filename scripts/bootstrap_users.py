"""Create the three built-in accounts in an empty `users` table, once.

A deployment step, run after `migrate` and before any application container is
recreated (scripts/deploy_production.sh). It is separate from `migrate` on
purpose: that service owns the schema and is given DATABASE_URL and nothing
else; this one is given the three bootstrap passwords and nothing else needs
them -- the backend no longer reads SORA_DEFAULT_*_PASSWORD at all.

    python3 scripts/bootstrap_users.py          passwords from the environment
    python3 scripts/bootstrap_users.py --dev    the published development
                                                passwords; refused in production

What it does to a table that already holds accounts: nothing. No password,
role, block or session version is touched, so changing a SORA_DEFAULT_*
variable after the first deployment has no effect, and an account an
administrator blocked or reset stays that way. It still requires an active
administrator to exist and fails otherwise, which stops the deployment before
the new code serves a site nobody can administer.

Exit codes:

    0   created, or already initialised with an active administrator
    1   refused: passwords missing, no active administrator, or the database
        unreachable

Neither a password nor a hash is printed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dev", action="store_true",
                        help="use the development passwords (refused when SORA_ENV=production)")
    args = parser.parse_args(argv)

    from sqlalchemy.exc import SQLAlchemyError

    from app import accounts
    from app.database import SessionLocal
    from app.passwords import _hash_password

    production = os.getenv("SORA_ENV", "development") == "production"
    if args.dev:
        if production:
            print("bootstrap_users: --dev is refused when SORA_ENV=production", file=sys.stderr)
            return 1
        passwords = dict(accounts.DEV_PASSWORDS)
    else:
        passwords = {role: os.getenv(f"SORA_DEFAULT_{role.upper()}_PASSWORD", "")
                     for role in accounts.BOOTSTRAP_ACCOUNTS}

    try:
        with SessionLocal() as session:
            outcome = accounts.bootstrap(session, passwords, _hash_password)
    except accounts.AccountError as exc:
        print(f"bootstrap_users: refused: {exc.message}", file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"bootstrap_users: database unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"bootstrap_users: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
