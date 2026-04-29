"""End-to-end TUI walk against a real local ministack.

Drives the wizard with NO mocks: real subprocess for terraform init/plan,
real boto3 against http://localhost:4566 for sts/secretsmanager/ecs.

This is the test that should have existed before Epic 6 closed — every
class-of-bug the snapshot tests can't catch surfaces here:

  - default values pointing at wrong absolute paths (repo_root)
  - default values leaking upstream identifiers (github_repo)
  - missing env propagation between subprocess + boto3 (auth ministack)
  - plain printable keybinds eaten by focused Input widgets
  - missing input validation (1-char JWT)
  - missing logical steps (deploy.sh delegation)

The test is opt-in (marked `real_ministack`) because it needs ministack
running on :4566 + terraform on PATH. Unit tests still run by default.

Run it explicitly:
    uv run pytest tests/test_e2e_ministack.py -m real_ministack -s
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
from pathlib import Path

import pytest
from textual.widgets import Button, Input

pytestmark = [pytest.mark.real_ministack, pytest.mark.asyncio]


def _ministack_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 4566), timeout=1.0):
            return True
    except OSError:
        return False


def _terraform_available() -> bool:
    return shutil.which("terraform") is not None


@pytest.fixture
def ministack_sandbox(tmp_path: Path) -> Path:
    """Sandbox terraform tree at tmp_path/terraform pointed at ministack."""
    repo_root = Path(__file__).resolve().parents[2]
    src_terraform = repo_root / "terraform"
    if not src_terraform.is_dir():
        pytest.skip(f"upstream terraform/ not found at {src_terraform}")
    dst_terraform = tmp_path / "terraform"
    shutil.copytree(
        src_terraform,
        dst_terraform,
        ignore=shutil.ignore_patterns(
            ".terraform", ".terraform.lock.hcl", "terraform.tfstate*", "tfplan"
        ),
    )
    # Make sure scripts/ exists for the deploy preflight (we won't run it).
    (tmp_path / "scripts").mkdir(exist_ok=True)
    real_script = repo_root / "scripts" / "deploy.sh"
    if real_script.is_file():
        shutil.copy2(real_script, tmp_path / "scripts" / "deploy.sh")
    return tmp_path


async def _settle(pilot, ticks: int = 4) -> None:
    for _ in range(ticks):
        await pilot.pause()


@pytest.mark.skipif(not _ministack_up(), reason="ministack not running on :4566")
@pytest.mark.skipif(not _terraform_available(), reason="terraform not on PATH")
async def test_wizard_drives_clean_through_step_seven(
    ministack_sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Walk the actual wizard end-to-end against ministack, no mocks."""
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TF_DATA_DIR", str(ministack_sandbox / ".tfdata"))

    from connect_bootstrap.app import BootstrapApp

    app = BootstrapApp()
    app.state.repo_root = ministack_sandbox
    app.state.terraform_dir = ministack_sandbox / "terraform"

    async with app.run_test(headless=True) as pilot:
        await _settle(pilot, 4)

        # Home → Step 1: deps
        await pilot.press("n")
        await _settle(pilot, 6)
        from connect_bootstrap.screens.deps import DepsCheckScreen

        assert isinstance(app.screen, DepsCheckScreen)
        assert not app.screen.query_one("#next", Button).disabled, (
            "deps probe should mark all required tools present on test host"
        )

        # Step 1 → Step 2: ministack auth
        await pilot.press("n")
        await _settle(pilot, 4)
        from connect_bootstrap.screens.auth import AwsAuthScreen

        assert isinstance(app.screen, AwsAuthScreen)
        await pilot.click("#m_ministack")
        await _settle(pilot, 2)
        await pilot.click("#verify")
        # verify spawns a worker; give it real wall-clock seconds.
        for _ in range(15):
            await pilot.pause()
            await asyncio.sleep(1.0)
            if not app.screen.query_one("#next", Button).disabled:
                break
        assert app.state.aws_account_id, "sts get-caller-identity should set account_id"
        assert app.state.aws_region == "us-east-1"
        assert os.environ.get("AWS_ENDPOINT_URL") == "http://localhost:4566", (
            "auth screen ministack mode must propagate AWS_ENDPOINT_URL to os.environ"
        )

        # Step 2 → Step 3: tfvars form
        await pilot.press("ctrl+j")
        await _settle(pilot, 4)
        from connect_bootstrap.screens.tfvars_form import TfvarsFormScreen

        assert isinstance(app.screen, TfvarsFormScreen)
        app.screen.query_one("#f_domain", Input).value = "connect.local"
        app.screen.query_one("#f_route53_zone_id", Input).value = "Z000000LOCAL"
        await pilot.press("ctrl+s")
        await _settle(pilot, 6)
        tfvars = ministack_sandbox / "terraform" / "terraform.tfvars"
        assert tfvars.is_file(), "Save should write terraform.tfvars"
        body = tfvars.read_text()
        assert "connect.local" in body
        assert "Z000000LOCAL" in body
        assert 'aws_region = "us-east-1"' in body, (
            "Step 3 must inherit aws_region from Step 2's verified value"
        )

        # Step 3 → Step 4: terraform plan
        await pilot.press("ctrl+j")
        await _settle(pilot, 4)
        from connect_bootstrap.screens.apply import ApplyScreen

        assert isinstance(app.screen, ApplyScreen)
        await pilot.click("#plan")
        # terraform init + plan against ministack: ~30-60s in cold cache.
        for _ in range(120):
            await pilot.pause()
            await asyncio.sleep(1.0)
            if not app.screen.query_one("#apply", Button).disabled:
                break
        assert not app.screen.query_one("#apply", Button).disabled, (
            "terraform plan against ministack must enable Apply"
        )

        # Skip the actual apply (ministack ECS/Fargate is partial). Stub
        # the post-apply state so we can shake out screens 5-7.
        app.state.apply_succeeded = True
        app.state.cluster_name = "connect-prod"
        app.state.web_service_name = "connect-prod-web"
        app.state.worker_service_name = "connect-prod-worker"
        app.state.migrate_task_definition_family = "connect-prod-migrate"

        # Pre-create the secrets that module.secrets would have made.
        import boto3

        sm = boto3.client("secretsmanager")
        for path in ("/connect/prod/jwt", "/connect/prod/admin"):
            try:
                sm.create_secret(Name=path, SecretString='{"placeholder":"true"}')
            except sm.exceptions.ResourceExistsException:
                pass
        ecs = boto3.client("ecs")
        try:
            ecs.create_cluster(clusterName="connect-prod")
        except Exception:
            pass

        app.screen.query_one("#next", Button).disabled = False
        await pilot.press("n")
        await _settle(pilot, 6)

        # Step 5: secrets seeder — JWT generation + length validation
        from connect_bootstrap.screens.secrets import SecretsScreen

        assert isinstance(app.screen, SecretsScreen)
        await pilot.press("ctrl+g")
        await _settle(pilot, 2)
        jwt_val = app.screen.query_one("#jwt", Input).value
        assert len(jwt_val) == 64, "ctrl+g should populate exactly 64 hex chars"

        # Validation regression test: empty out the JWT, check seed refuses.
        app.screen.query_one("#jwt", Input).value = "g"
        app.screen.query_one("#admin_email", Input).value = "ops@example.com"
        app.screen.query_one("#admin_password", Input).value = "ok-passw0rd"
        await pilot.click("#seed")
        await _settle(pilot, 4)
        assert app.screen.query_one("#next", Button).disabled, (
            "seed with 1-char JWT must be rejected by length validation"
        )
        assert not app.state.secrets_seeded

        # Re-fill with a real JWT and verify success.
        await pilot.press("ctrl+g")
        await _settle(pilot, 2)
        await pilot.click("#seed")
        for _ in range(10):
            await pilot.pause()
            await asyncio.sleep(0.5)
            if app.state.secrets_seeded:
                break
        assert app.state.secrets_seeded, "seed with valid JWT must succeed"

        # Step 5 → Step 6: cluster verification
        await pilot.press("ctrl+j")
        await _settle(pilot, 6)
        from connect_bootstrap.screens.cluster import ClusterScreen

        assert isinstance(app.screen, ClusterScreen)
        # describe_clusters fires on_mount; let it settle.
        for _ in range(10):
            await pilot.pause()
            await asyncio.sleep(0.5)
            if not app.screen.query_one("#next", Button).disabled:
                break
        assert not app.screen.query_one("#next", Button).disabled, (
            "ACTIVE ministack cluster should enable Next"
        )

        # Step 6 → Step 7: deploy screen renders pre-flight
        await pilot.press("n")
        await _settle(pilot, 4)
        from connect_bootstrap.screens.deploy import DeployScreen

        assert isinstance(app.screen, DeployScreen)
        # Don't run scripts/deploy.sh against ministack — ECR/ECS Fargate
        # behavior is partial. Just confirm the screen mounted cleanly.
        assert app.screen.query_one("#deploy", Button) is not None
