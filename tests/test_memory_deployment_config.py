from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MEMORY_COMPOSE_FILE = REPOSITORY_ROOT / "memory" / "compose.yml"


def docker_compose_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    return (
        subprocess.run(
            [docker, "compose", "version"],
            capture_output=True,
            check=False,
            text=True,
        ).returncode
        == 0
    )


def render_memory_service_environment(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        **os.environ,
        "MEMORY_LLM_API_KEY": "test-key",
        "MEMORY_LLM_BASE_URL": "https://provider.example/v1",
        "MEMORY_LLM_MODEL": "global-model",
        "MEMORY_LLM_REASONING_EFFORT": "low",
        "MEMORY_EMBEDDING_API_KEY": "test-embedding-key",
    }
    for key in (
        "MEMORY_RETAIN_LLM_MODEL",
        "MEMORY_CONSOLIDATION_LLM_MODEL",
        "MEMORY_REFLECT_LLM_MODEL",
    ):
        environment.pop(key, None)
    environment.update(overrides or {})
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(MEMORY_COMPOSE_FILE.parent),
            "--file",
            str(MEMORY_COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        check=True,
        env=environment,
        text=True,
    )

    rendered = json.loads(completed.stdout)
    return rendered["services"]["memory-api"]["environment"]


@pytest.mark.skipif(not docker_compose_available(), reason="Docker Compose is required")
def test_memory_compose_renders_per_operation_models_with_shared_effort():
    service_environment = render_memory_service_environment(
        {
            "MEMORY_RETAIN_LLM_MODEL": "retain-model",
            "MEMORY_CONSOLIDATION_LLM_MODEL": "consolidation-model",
            "MEMORY_REFLECT_LLM_MODEL": "reflect-model",
        }
    )

    assert service_environment["HINDSIGHT_API_LLM_MODEL"] == "global-model"
    assert service_environment["HINDSIGHT_API_RETAIN_LLM_MODEL"] == "retain-model"
    assert (
        service_environment["HINDSIGHT_API_CONSOLIDATION_LLM_MODEL"]
        == "consolidation-model"
    )
    assert service_environment["HINDSIGHT_API_REFLECT_LLM_MODEL"] == "reflect-model"
    assert service_environment["HINDSIGHT_API_LLM_REASONING_EFFORT"] == "low"


@pytest.mark.skipif(not docker_compose_available(), reason="Docker Compose is required")
def test_memory_operation_models_fall_back_to_the_global_model():
    service_environment = render_memory_service_environment()

    assert service_environment["HINDSIGHT_API_RETAIN_LLM_MODEL"] == "global-model"
    assert (
        service_environment["HINDSIGHT_API_CONSOLIDATION_LLM_MODEL"]
        == "global-model"
    )
    assert service_environment["HINDSIGHT_API_REFLECT_LLM_MODEL"] == "global-model"
