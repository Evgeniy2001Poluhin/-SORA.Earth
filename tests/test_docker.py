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


def test_docker_compose_services():
    import yaml
    with open("docker-compose.yml") as f:
        dc = yaml.safe_load(f)
    services = dc["services"]
    assert "app" in services
    assert "nginx" in services
    assert "mlflow" in services
    assert services["app"]["ports"] == ["8000:8000"]


def test_nginx_config():
    content = open("nginx/nginx.conf").read()
    assert "upstream sora_app" in content
    assert "proxy_pass" in content
    assert "websocket" in content.lower() or "upgrade" in content.lower()
