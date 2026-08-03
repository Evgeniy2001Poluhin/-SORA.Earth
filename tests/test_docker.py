import pytest
import os


def test_dockerfile_exists():
    assert os.path.isfile("Dockerfile")


def test_dockerfile_multistage():
    content = open("Dockerfile").read()
    assert content.count("FROM ") >= 2, "Should use multi-stage build"


def test_dockerfile_healthcheck():
    content = open("Dockerfile").read()
    assert "HEALTHCHECK" in content


def test_dockerignore_exists():
    assert os.path.isfile(".dockerignore")
    content = open(".dockerignore").read()
    assert "venv" in content
    assert "__pycache__" in content


def test_docker_compose_exists():
    assert os.path.isfile("docker-compose.yml")


@pytest.mark.integration
def test_docker_compose_services():
    import yaml
    with open("docker-compose.yml") as f:
        dc = yaml.safe_load(f)
    services = dc["services"]
    assert "app" in services
    assert "postgres" in services
    assert "redis" in services
    assert "prometheus" in services
    assert "grafana" in services
    # Every published port of the API must bind loopback -- stated as a property
    # over all entries, not as a list of forbidden strings.
    #
    # The first version of this assertion banned the exact string "8000:8000".
    # That is the wrong shape: "9000:8000", "0.0.0.0:9000:8000" and a bare
    # "8000" all publish to every interface and all would have passed it. On the
    # production host that is what put the API on the internet unencrypted, past
    # nginx and its TLS.
    ports = services["app"].get("ports", [])
    assert ports, "the app service publishes nothing; expected a loopback mapping"

    for entry in ports:
        if isinstance(entry, dict):          # long syntax
            host_ip = entry.get("host_ip")
        else:                                # short syntax: [IP:][HOST:]CONTAINER
            parts = str(entry).split(":")
            host_ip = parts[0] if len(parts) >= 3 else None
        assert host_ip == "127.0.0.1", (
            f"app port {entry!r} does not bind loopback (host_ip={host_ip!r}); "
            "it would publish the API on every interface"
        )


def test_nginx_config():
    content = open("nginx/nginx.conf").read()
    assert "upstream sora_backend" in content
    assert "proxy_pass" in content
    assert "websocket" in content.lower() or "upgrade" in content.lower()
