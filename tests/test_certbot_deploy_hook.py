"""A renewed certificate has to reach nginx, and must not take the stack down.

Found by the deploy script's own check on 2026-08-16: the renewal timer was
present, the certificate had 48 days left, and there was no deploy hook -- so a
successful renewal would have left nginx serving the expired certificate until
somebody restarted it. `certbot renew` reporting success is not delivery.

Two defects, both in `renew-cert.sh`:

- `--deploy-hook` was a flag on its own `certbot renew`, which applies to that
  invocation only. The systemd timer runs a plain `certbot renew` and never saw
  it, so the hook lived in the repository and nowhere in the renewal path.
- it named `docker-compose.yml`; production runs `docker-compose.prod.yml`, a
  different compose project, so the `exec` addressed a container that is not the
  one serving the site.

These tests read the shipped hook. They cannot prove certbot will run it -- that
is `certbot renew --dry-run` against the real installation -- but they do keep
the two defects above from coming back, which is what a repository can check.
"""
import os
import stat

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(REPO_ROOT, "scripts", "certbot-deploy-hook.sh")


def read_hook():
    with open(HOOK, encoding="utf-8") as handle:
        return handle.read()


def executable_lines(path):
    """Only what the shell runs.

    The first version of these tests searched the whole file and tripped on the
    comments explaining the defect being fixed -- a check that reads prose is
    not checking behaviour.
    """
    with open(path, encoding="utf-8") as handle:
        return "\n".join(
            line for line in handle.read().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )


def test_the_hook_exists_and_can_be_executed_by_certbot():
    """certbot runs every *executable* file in renewal-hooks/deploy; a
    non-executable one is skipped silently, which is the failure mode this
    whole file is about."""
    assert os.path.exists(HOOK), "the deploy hook is not in the repository"
    mode = os.stat(HOOK).st_mode
    assert mode & stat.S_IXUSR, "the hook is not executable, so certbot would skip it"


def test_it_reloads_and_does_not_restart_the_stack():
    """A reload re-reads the certificate in place. `restart` or `up` would take
    the site down for a file certbot has already written."""
    body = executable_lines(HOOK)
    assert "nginx -s reload" in body, "the hook does not reload nginx"
    for forbidden in ("compose restart", "compose up", "compose down", "systemctl restart"):
        assert forbidden not in body, f"the hook does more than reload: {forbidden!r}"


def test_it_addresses_the_compose_project_that_actually_serves_the_site():
    """The original named docker-compose.yml while production runs the prod file.

    The `exec` then went to a container that is not the one holding the
    certificate, and nothing said so.
    """
    body = executable_lines(HOOK)
    assert "docker-compose.prod.yml" in body
    assert "docker-compose.yml" not in body.replace("docker-compose.prod.yml", "")


def test_the_old_flag_form_is_gone_from_the_renewal_script():
    """`--deploy-hook` as a flag only applies to the invocation carrying it.

    Leaving it there would suggest the path is covered when the systemd timer,
    which runs a bare `certbot renew`, does not carry the flag.
    """
    legacy = os.path.join(REPO_ROOT, "renew-cert.sh")
    if not os.path.exists(legacy):
        return
    body = executable_lines(legacy)
    assert "--deploy-hook" not in body, (
        "renew-cert.sh still passes --deploy-hook as a flag; the timer does not "
        "use it, so the hook belongs in /etc/letsencrypt/renewal-hooks/deploy/"
    )
    assert "docker-compose.yml" not in body.replace("docker-compose.prod.yml", ""), (
        "renew-cert.sh still names the non-production compose file"
    )
