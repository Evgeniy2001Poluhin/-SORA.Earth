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
    # Bound to loopback, and asserted as an absence as well as a presence.
    # "8000:8000" binds every interface. On a laptop that is harmless; on the
    # production host it published the API to the internet unencrypted, past
    # nginx and its TLS. Checking only for the new string would let the old one
    # come back alongside it, so the unrestricted form is named and forbidden.
    ports = services["app"].get("ports", [])
    assert "127.0.0.1:8000:8000" in ports, ports
    assert "8000:8000" not in ports, f"port 8000 is bound to every interface: {ports}"


def test_nginx_config():
    content = open("nginx/nginx.conf").read()
    assert "upstream sora_backend" in content
    assert "proxy_pass" in content
    assert "websocket" in content.lower() or "upgrade" in content.lower()
