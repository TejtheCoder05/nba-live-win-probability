from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_model_artifact import EXPECTED_SHA256, MODEL_PATH
from src.api.config import AppConfig

ROOT = Path(__file__).resolve().parent.parent


def test_production_artifacts_are_minimal_and_complete() -> None:
    production_files = {
        path.name for path in (ROOT / "artifacts" / "win_probability_v1").iterdir() if not path.name.startswith(".")
    }
    required = {"best_model.pt", "model_config.json", "preprocessing.json"}
    assert required <= production_files
    assert MODEL_PATH.stat().st_size < 25_000


def test_frozen_model_hash_matches_ci_contract() -> None:
    import hashlib

    assert hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest() == EXPECTED_SHA256


def test_dockerfile_uses_production_server_and_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith("FROM python:3.12.10-slim-bookworm")
    assert "download.pytorch.org/whl/cpu" in dockerfile
    assert "USER app" in dockerfile
    assert 'CMD ["gunicorn"' in dockerfile
    assert "scripts/verify_runtime.py" in dockerfile


def test_dockerignore_excludes_training_data_but_keeps_runtime_inputs() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "data\n" in ignored
    assert "artifacts/**" in ignored
    assert "!artifacts/win_probability_v1/best_model.pt" in ignored
    assert "!tests/fixtures/live/playbyplay_0022000001.json" in ignored


def test_railway_config_uses_docker_healthcheck_and_safe_restart() -> None:
    config = json.loads((ROOT / "railway.json").read_text(encoding="utf-8"))
    assert config["$schema"] == "https://railway.com/railway.schema.json"
    assert config["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile",
    }
    assert config["deploy"] == {
        "healthcheckPath": "/api/health",
        "healthcheckTimeout": 120,
        "restartPolicyType": "ON_FAILURE",
        "restartPolicyMaxRetries": 10,
        "drainingSeconds": "30",
    }


def test_github_ci_has_required_triggers_and_checks() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for required in (
        "pull_request:",
        "branches:",
        "python -m pytest -q",
        "python -m compileall -q src scripts",
        "python -m pip check",
        "git diff --check",
        "python scripts/verify_model_artifact.py",
        "docker build --tag nba-win-probability:ci .",
        "python scripts/verify_container.py --image nba-win-probability:ci",
    ):
        assert required in workflow


def test_container_acceptance_requires_production_lifecycle_logs() -> None:
    verifier = (ROOT / "scripts" / "verify_container.py").read_text(encoding="utf-8")
    for required in (
        "Frozen predictor loaded with 10 features",
        "Application initialized mode=replay",
        "Started game poller game_id=0022000001",
        "Replay control game_id=0022000001 action=start",
        "Official FINAL product override applied game_id=0022000001",
    ):
        assert required in verifier


def test_production_config_rejects_debug_and_uses_cloud_port() -> None:
    config = AppConfig.from_env(
        {
            "NBA_APP_MODE": "replay",
            "NBA_APP_ENV": "production",
            "NBA_APP_DEBUG": "false",
            "HOST": "0.0.0.0",
            "PORT": "10000",
        }
    )
    assert not config.debug
    assert (config.host, config.port) == ("0.0.0.0", 10000)
