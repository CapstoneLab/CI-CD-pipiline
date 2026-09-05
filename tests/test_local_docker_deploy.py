from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.steps.local_docker_deploy import (
    _app_compose_service,
    _container_name,
    _port_range_from_env,
    _prepare_runtime_context,
    _replace_container,
    _router_default_config,
    _router_service_config,
    _select_port,
)
from app.utils.shell import CommandResult


def test_port_selection_is_stable_and_avoids_reserved_port() -> None:
    service = "example/api"
    first = _select_port(service, 10000, 10010, set())

    assert _select_port(service, 10000, 10010, set()) == first
    assert _select_port(service, 10000, 10010, {first}) != first


def test_port_range_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_PORT_START", "20000")
    monkeypatch.setenv("DEPLOY_PORT_END", "10000")

    with pytest.raises(ValueError, match="deployment port range"):
        _port_range_from_env()


def test_container_name_is_docker_safe_and_service_specific() -> None:
    first = _container_name("My Org", "Frontend@Service", "my org/frontend@service")
    second = _container_name("My Org", "Backend@Service", "my org/backend@service")

    assert first.startswith("cicd-app-my-org-frontend-service-")
    assert first != second
    assert len(first) <= 63


def test_app_compose_service_only_publishes_on_loopback() -> None:
    service = _app_compose_service(
        container_name="cicd-app-example",
        image_name="cicd-managed/example:123",
        service_key="example/api",
        owner="example",
        repo_name="api",
        runtime="node",
        artifact_hash="123",
        bind_address="127.0.0.1",
        host_port=12000,
        container_port=3000,
    )

    assert service["ports"] == ["127.0.0.1:12000:3000"]


def test_router_preserves_proxy_metadata_and_websocket_upgrade() -> None:
    default_config = _router_default_config()
    route = _router_service_config(
        "/services/Example/API", "cicd-app-example", 3000, "example/api"
    )

    assert "map $http_upgrade $cicd_connection_upgrade" in default_config
    assert "proxy_set_header Connection $cicd_connection_upgrade" in route
    assert "proxy_set_header Host $http_host" in route
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for" in route
    assert "proxy_set_header X-Forwarded-Proto $cicd_forwarded_proto" in route


def test_backend_router_route_forwards_websocket_upgrade() -> None:
    route_path = Path(__file__).resolve().parents[1] / "deployments/router/routes/capstone-back.conf"
    route = route_path.read_text(encoding="utf-8")

    assert "location ^~ /api/" in route
    assert "proxy_http_version 1.1;" in route
    assert "proxy_set_header Upgrade $http_upgrade;" in route
    assert "proxy_set_header Connection $cicd_connection_upgrade;" in route
    assert "proxy_read_timeout 3600s;" in route


def test_prepare_node_context_uses_fallback_artifact(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    fallback = artifacts / "dist-server"
    fallback.mkdir(parents=True)
    (fallback / "package.json").write_text(
        json.dumps({"scripts": {"start": "node server.js"}}), encoding="utf-8"
    )
    (fallback / "server.js").write_text("require('http').createServer().listen(3000)\n", encoding="utf-8")
    (artifacts / "build_meta.json").write_text(
        json.dumps({"fallback_directory": "dist-server"}), encoding="utf-8"
    )

    spec = _prepare_runtime_context(
        repo_dir=tmp_path / "repo",
        artifacts_dir=artifacts,
        context_dir=tmp_path / "context",
        runtime="node",
        python_entry=None,
        public_path="/services/example/api",
    )

    assert spec.container_port == 3000
    assert (spec.context_dir / "app/server.js").is_file()
    dockerfile = (spec.context_dir / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["npm", "start"]' in dockerfile


def test_prepare_frontend_context_adds_spa_fallback(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    output = artifacts / "dist"
    output.mkdir(parents=True)
    (output / "index.html").write_text(
        '<script src="/assets/app.js"></script><main>ok</main>', encoding="utf-8"
    )

    spec = _prepare_runtime_context(
        repo_dir=tmp_path / "repo",
        artifacts_dir=artifacts,
        context_dir=tmp_path / "context",
        runtime="react",
        python_entry=None,
        public_path="/services/example/web",
    )

    assert spec.container_port == 80
    index_html = (spec.context_dir / "app/index.html").read_text(encoding="utf-8")
    assert 'src="/services/example/web/assets/app.js"' in index_html
    nginx_config = (spec.context_dir / "default.conf").read_text(encoding="utf-8")
    assert "try_files $uri $uri/ /index.html" in nginx_config


def test_failed_docker_run_does_not_remove_name_collision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def fake_docker(args: list[str], **_kwargs) -> CommandResult:
        commands.append(args)
        return CommandResult(exit_code=1, output="name is already in use")

    monkeypatch.setattr("app.steps.local_docker_deploy._docker", fake_docker)

    deployed = _replace_container(
        current_container="",
        container_name="cicd-app-example",
        image_name="cicd-managed/example:123",
        service_key="example/api",
        owner="example",
        repo_name="api",
        runtime="node",
        artifact_hash="123",
        bind_address="0.0.0.0",
        host_port=12000,
        container_port=3000,
        network="capstone-internal",
        health_host="127.0.0.1",
        cwd=tmp_path,
        log_file=tmp_path / "deploy.log",
    )

    assert deployed is False
    assert commands[0][0] == "run"
    assert not any(command[:2] == ["rm", "-f"] for command in commands)
