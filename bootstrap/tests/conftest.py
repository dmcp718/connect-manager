"""Shared fixtures for bootstrap TUI tests.

Each screen reaches out to subprocesses (`run_capture`/`run_stream`) or
boto3 (`boto3_client`). For deterministic snapshots the fixtures below
patch those out at the module-import sites so screen workers settle to a
predictable state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


# ── Subprocess mocks ──────────────────────────────────────────────────────────


_VERSION_OUTPUT = {
    "aws": "aws-cli/2.13.0 Python/3.11.0 Linux/x86_64",
    "terraform": "Terraform v1.6.0\non linux_amd64",
    "jq": "jq-1.6",
    "gh": "gh version 2.40.1 (2024-01-15)",
    "docker": "Docker version 24.0.5, build ced0996",
}


async def fake_run_capture(
    argv, cwd=None, env=None, timeout=30.0
) -> tuple[int, str, str]:
    """Deterministic stand-in for `run_capture`.

    Returns canned `--version` output for the dependency-check probes and
    a fixed `sts get-caller-identity` JSON for the auth screen. Anything
    else returns rc=0 with empty streams.
    """
    args = list(argv)
    if not args:
        return 0, "", ""

    head = args[0]
    rest = args[1:]

    if head == "aws" and rest[:2] == ["sts", "get-caller-identity"]:
        return (
            0,
            '{"UserId":"AIDA12345EXAMPLE","Account":"123456789012",'
            '"Arn":"arn:aws:iam::123456789012:user/test-operator"}',
            "",
        )

    if head in _VERSION_OUTPUT:
        return 0, _VERSION_OUTPUT[head], ""

    return 0, "", ""


async def fake_run_stream(argv, cwd=None, env=None):
    """Deterministic stand-in for `run_stream` — emits a single line then exits."""
    yield "stdout", f"$ {' '.join(argv)}"
    yield "stdout", "(mocked output)"
    yield "exit", "0"


# ── boto3 mocks ───────────────────────────────────────────────────────────────


def _mock_ecs() -> MagicMock:
    m = MagicMock(name="ecs")
    m.describe_clusters.return_value = {
        "clusters": [
            {
                "clusterName": "connect-prod",
                "status": "ACTIVE",
                "registeredContainerInstancesCount": 0,
                "runningTasksCount": 2,
                "pendingTasksCount": 0,
                "activeServicesCount": 2,
            }
        ],
        "failures": [],
    }
    m.describe_services.return_value = {
        "services": [
            {
                "serviceName": "connect-prod-web",
                "status": "ACTIVE",
                "desiredCount": 2,
                "runningCount": 2,
                "pendingCount": 0,
                "deployments": [{"status": "PRIMARY", "rolloutState": "COMPLETED"}],
                "loadBalancers": [
                    {
                        "targetGroupArn": "arn:aws:elasticloadbalancing:us-east-1:"
                        "123456789012:targetgroup/connect-prod-web/abcdef0123456789",
                        "containerName": "web",
                        "containerPort": 8000,
                    }
                ],
            }
        ]
    }
    m.get_waiter.return_value.wait.return_value = None
    m.list_tasks.return_value = {"taskArns": []}
    m.describe_tasks.return_value = {"tasks": []}
    m.update_service.return_value = {"service": {"serviceName": "connect-prod-web"}}
    return m


def _mock_elbv2() -> MagicMock:
    m = MagicMock(name="elbv2")
    m.describe_target_health.return_value = {
        "TargetHealthDescriptions": [
            {
                "Target": {"Id": "10.0.1.10", "Port": 8000},
                "TargetHealth": {"State": "healthy"},
            },
            {
                "Target": {"Id": "10.0.2.20", "Port": 8000},
                "TargetHealth": {"State": "healthy"},
            },
        ]
    }
    return m


def _mock_cloudwatch() -> MagicMock:
    m = MagicMock(name="cloudwatch")
    m.describe_alarms.return_value = {
        "MetricAlarms": [
            {
                "AlarmName": "connect-prod-web-5xx",
                "StateValue": "OK",
                "StateUpdatedTimestamp": "2026-04-28T12:00:00Z",
            }
        ]
    }
    return m


def _mock_secretsmanager() -> MagicMock:
    m = MagicMock(name="secretsmanager")
    m.describe_secret.return_value = {
        "Name": "/connect/prod/jwt",
        "ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:/connect/prod/jwt-AbCdEf",
    }
    m.put_secret_value.return_value = {"VersionId": "v1"}
    return m


def fake_boto3_client(service_name: str, **kwargs: Any) -> MagicMock:
    factories = {
        "ecs": _mock_ecs,
        "elbv2": _mock_elbv2,
        "cloudwatch": _mock_cloudwatch,
        "secretsmanager": _mock_secretsmanager,
    }
    factory = factories.get(service_name)
    if factory is None:
        return MagicMock(name=service_name)
    return factory()


# ── Pytest fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def patch_externals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace subprocess + boto3 entry points so screen workers are deterministic.

    Each screen module imports `run_capture` / `run_stream` at module
    scope, so we patch the names *in those modules* — patching the source
    in `connect_bootstrap.shell` would not affect already-imported refs.
    `boto3_client` is imported lazily inside methods, so patching it at
    its source module is enough.
    """
    from connect_bootstrap import aws_helper
    from connect_bootstrap.screens import apply, auth, deps

    monkeypatch.setattr(deps, "run_capture", fake_run_capture)
    monkeypatch.setattr(auth, "run_capture", fake_run_capture)
    monkeypatch.setattr(apply, "run_capture", fake_run_capture)
    monkeypatch.setattr(apply, "run_stream", fake_run_stream)
    monkeypatch.setattr(aws_helper, "boto3_client", fake_boto3_client)

    # DepsCheckScreen probes via shutil.which to decide if a binary is
    # installed. Pretend everything we care about is present so the test
    # host doesn't need the real toolchain.
    fake_which_targets = {"aws", "terraform", "jq", "gh", "docker"}

    def fake_which(name: str, *args, **kwargs):
        if name in fake_which_targets:
            return f"/usr/bin/{name}"
        import shutil as _real

        return (
            _real.which.__wrapped__(name)
            if hasattr(_real.which, "__wrapped__")
            else None
        )

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("connect_bootstrap.screens.deps.shutil.which", fake_which)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    """A throwaway repo_root with a minimal terraform.tfvars.example.

    `TfvarsFormScreen` reads tfvars on compose; we hand it a deterministic
    file so the snapshot doesn't depend on the operator's real values.
    """
    tf = tmp_path / "terraform"
    tf.mkdir()
    (tf / "terraform.tfvars.example").write_text(
        'env             = "prod"\n'
        'aws_region      = "us-east-1"\n'
        'domain          = "connect.example.com"\n'
        'route53_zone_id = "Z0123456789ABCDEFGHIJ"\n'
        'github_repo     = "dmcp718/connect-manager"\n'
        'alarms_email    = "alerts@example.com"\n'
        'vpc_cidr        = "10.20.0.0/16"\n'
    )
    return tmp_path
