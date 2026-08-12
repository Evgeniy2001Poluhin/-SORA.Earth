"""Which database the suite is allowed to use.

Kept as a pure function so the rule can be asserted directly. Testing it
through `conftest.py` would mean testing an import side effect, and the one
case that matters -- an ambient `DATABASE_URL` pointing somewhere real -- is
exactly the case nobody wants to reproduce by running the suite against it.

The rule, in one line: the tests take a database only from `TEST_DATABASE_URL`,
and otherwise make their own.

An earlier version used `os.environ.setdefault("DATABASE_URL", ...)`, which
keeps whatever is already there. That is safe against *overwriting* CI's
configuration and unsafe in the direction that matters: a shell with a
production URL exported -- from a deploy, a psql session, a sourced .env --
would hand the suite production, and the suite writes. `create_all` would even
add missing tables to it.

`CI=true` is deliberately not accepted as permission either. CI can be handed a
wrong secret as easily as a laptop can, and "this is an automated run" says
nothing about which database the variable points at. The permission has to be
specific to testing, which is what a separate variable name gives.
"""
from __future__ import annotations

import os
import tempfile
from typing import Callable, Mapping, Optional, Tuple


def choose_database_url(
    env: Mapping[str, str],
    make_temp_dir: Callable[[], str] = lambda: tempfile.mkdtemp(prefix="sora-tests-"),
) -> Tuple[str, Optional[str]]:
    """Return `(database_url, temp_dir_to_clean_up)`.

    `temp_dir_to_clean_up` is None when the caller chose the database, so the
    teardown removes only what this process created.

    A file rather than `:memory:`: in-memory SQLite gives every connection its
    own database, so anything crossing a connection boundary would see an empty
    schema -- swapping one class of false result for another.
    """
    explicit = env.get("TEST_DATABASE_URL")
    if explicit:
        return explicit, None

    directory = make_temp_dir()
    return f"sqlite:///{os.path.join(directory, 'test.db')}", directory
