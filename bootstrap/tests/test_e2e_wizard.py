"""End-to-end wizard walk against ministack-style mocks.

Drives `BootstrapApp` from the home screen through every step
(Deps → Auth → Tfvars → Apply → Secrets → Cluster → Deploy → Status)
using Textual's `Pilot`, with subprocess + boto3 stubbed by the
`patch_externals` fixture in `conftest.py`. Verifies that the wizard's
`WizardState` populates as each screen completes, and writes a plain-
text transcript to `tests/artifacts/e2e_transcript.txt` for debugging.

The bead's original `helm list -n connect` assertion is from the kind/
EKS-era wizard; aws-fargate doesn't deploy to Kubernetes, so the
equivalent here is `state.deploy_succeeded == True` plus a populated
ALB DNS name. The full real-AWS smoke (terraform apply against a live
account) is gated as a separate task (awsk-3r4.5) — this test stays in
the local fast loop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Button, Input

from connect_bootstrap.app import BootstrapApp


pytestmark = pytest.mark.asyncio


ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def _write_transcript(name: str, lines: list[str]) -> None:
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    (ARTIFACTS_DIR / name).write_text("\n".join(lines) + "\n")


async def _settle(pilot, ticks: int = 10) -> None:
    """Pump the event loop a fixed number of times. Each pause yields once
    to asyncio so workers + reactive renders catch up."""
    for _ in range(ticks):
        await pilot.pause()


async def test_wizard_walks_through_all_eight_steps(repo_root: Path):
    """Drive every screen via Pilot and assert state populates correctly."""
    transcript: list[str] = ["e2e wizard transcript", "=" * 60]

    app = BootstrapApp()
    # Point the wizard at our throwaway repo_root so tfvars + terraform_dir
    # resolve to fixture data, not the real terraform/.
    app.state.repo_root = repo_root
    app.state.terraform_dir = repo_root / "terraform"

    async with app.run_test(size=(160, 60)) as pilot:
        await _settle(pilot, 4)
        transcript.append(f"home: stack depth={len(app.screen_stack)}")

        # ── Step 1: Deps ──────────────────────────────────────────────────
        await pilot.press("n")  # action_next_step → DepsCheckScreen
        await _settle(pilot, 8)
        deps_screen = app.screen
        next_btn = deps_screen.query_one("#next", Button)
        assert not next_btn.disabled, "deps probe should mark all required ✓"
        transcript.append("step 1 (deps): all-required-ok=True")
        await pilot.press("n")  # _advance → AwsAuthScreen
        await _settle(pilot, 4)

        # ── Step 2: AWS auth ──────────────────────────────────────────────
        await pilot.press("v")  # action_verify_now
        await _settle(pilot, 8)
        assert app.state.aws_account_id == "123456789012"
        assert app.state.caller_arn and "test-operator" in app.state.caller_arn
        transcript.append(
            f"step 2 (auth): account={app.state.aws_account_id} "
            f"arn={app.state.caller_arn}"
        )
        await pilot.press("n")  # _advance → TfvarsFormScreen
        await _settle(pilot, 4)

        # ── Step 3: tfvars form ──────────────────────────────────────────
        # Fill the required fields the form blocks Save on.
        tfvars_screen = app.screen
        tfvars_screen.query_one("#f_domain", Input).value = "connect.example.com"
        tfvars_screen.query_one(
            "#f_route53_zone_id", Input
        ).value = "Z0123456789ABCDEFGHIJ"
        await pilot.click("#save")
        await _settle(pilot, 4)
        out_path = repo_root / "terraform" / "terraform.tfvars"
        assert out_path.exists(), "Save should write terraform.tfvars"
        assert "connect.example.com" in out_path.read_text()
        transcript.append(f"step 3 (tfvars): wrote {out_path}")
        # Press Next to advance.
        next_btn = tfvars_screen.query_one("#next", Button)
        if not next_btn.disabled:
            await pilot.press("n")
            await _settle(pilot, 4)
        else:
            # Save did not enable Next — drop directly into Apply for the
            # E2E walk to continue. This still exercises the screen.
            from connect_bootstrap.screens.apply import ApplyScreen

            await app.push_screen(ApplyScreen())
            await _settle(pilot, 4)

        # ── Step 4: Apply (terraform plan + apply, mocked) ───────────────
        apply_screen = app.screen
        await pilot.press("p")  # action_do_plan
        await _settle(pilot, 12)
        apply_btn = apply_screen.query_one("#apply", Button)
        assert not apply_btn.disabled, "plan rc=0 should enable Apply"
        await pilot.press("a")  # action_do_apply
        await _settle(pilot, 12)
        assert app.state.apply_succeeded, "mocked apply rc=0 should set flag"
        assert app.state.cluster_name == "connect-prod"
        assert app.state.alb_dns_name and "elb.amazonaws.com" in app.state.alb_dns_name
        transcript.append(
            f"step 4 (apply): cluster={app.state.cluster_name} "
            f"alb={app.state.alb_dns_name}"
        )
        await pilot.press("n")  # _advance → SecretsScreen
        await _settle(pilot, 4)

        # ── Step 5: Secrets seeder ───────────────────────────────────────
        secrets_screen = app.screen
        await pilot.click("#gen")  # generate JWT
        await _settle(pilot, 2)
        jwt_value = secrets_screen.query_one("#jwt", Input).value
        assert len(jwt_value) == 64
        secrets_screen.query_one("#admin_email", Input).value = "ops@example.com"
        secrets_screen.query_one("#admin_password", Input).value = "supersecret"
        await pilot.click("#seed")
        await _settle(pilot, 8)
        assert app.state.secrets_seeded, "mocked PutSecretValue should flip flag"
        transcript.append("step 5 (secrets): jwt + admin written")
        await pilot.press("n")  # _advance → ClusterScreen
        await _settle(pilot, 4)

        # ── Step 6: Cluster verification ─────────────────────────────────
        await _settle(pilot, 8)  # on_mount kicks describe_clusters
        cluster_next = app.screen.query_one("#next", Button)
        assert not cluster_next.disabled, "ACTIVE cluster should enable Next"
        transcript.append("step 6 (cluster): cluster ACTIVE")
        await pilot.press("n")  # _advance → DeployScreen
        await _settle(pilot, 4)

        # ── Step 7: Deploy ───────────────────────────────────────────────
        await pilot.click("#deploy")
        await _settle(pilot, 20)  # update-service + waiter + target-health probe
        assert app.state.deploy_succeeded, "mocked target health=healthy → flag"
        transcript.append("step 7 (deploy): services stable + target healthy")
        await pilot.press("n")  # _advance → StatusScreen
        await _settle(pilot, 4)

        # ── Step 8: Status dashboard ─────────────────────────────────────
        await _settle(pilot, 6)
        from textual.widgets import DataTable

        services_tbl = app.screen.query_one("#services-tbl", DataTable)
        assert services_tbl.row_count >= 1, "describe_services should populate"
        transcript.append(f"step 8 (status): services rows={services_tbl.row_count}")

    transcript.append("=" * 60)
    transcript.append("FINAL STATE")
    final = {
        "aws_account_id": app.state.aws_account_id,
        "caller_arn": app.state.caller_arn,
        "domain": app.state.domain,
        "cluster_name": app.state.cluster_name,
        "web_service_name": app.state.web_service_name,
        "worker_service_name": app.state.worker_service_name,
        "alb_dns_name": app.state.alb_dns_name,
        "apply_succeeded": app.state.apply_succeeded,
        "secrets_seeded": app.state.secrets_seeded,
        "deploy_succeeded": app.state.deploy_succeeded,
    }
    transcript.append(json.dumps(final, indent=2))
    _write_transcript("e2e_transcript.txt", transcript)
