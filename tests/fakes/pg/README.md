Stand-ins for the PostgreSQL client tools, so the backup and drill scripts can
be run whole without a server.

`pg_lib.sh` runs these from PATH when `PG_CONTAINER` is empty, so the scripts
under test are unmodified: they call the same commands they would in
production. Faking at this boundary is what makes an offline test of
`backup_run.sh` and `backup_offsite_drill.sh` an execution of those scripts
rather than a description of them.

Behaviour is steered by environment variables so a test can make a step fail
without editing anything:

    FAKE_PG_DUMP_BYTES        what pg_dump emits (default: a fixed archive)
    FAKE_PG_RESTORE_FAIL      pg_restore exits non-zero
    FAKE_PG_LIST_FAIL         pg_restore --list refuses the archive
    FAKE_PG_FINGERPRINT       what psql answers for fingerprint queries
    FAKE_PG_LOG               a file every invocation is appended to
