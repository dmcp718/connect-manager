"""Snapshot tests for every Bootstrap TUI screen.

Each test wraps a single screen in a minimal `App` subclass, primes
`WizardState` with whatever fields that screen expects, and hands the
app to `pytest-textual-snapshot`. The first run with `--snapshot-update`
seeds baseline `.svg` files under `__snapshots__/`; subsequent runs
must match byte-for-byte.

External side effects (subprocess + boto3) are stubbed via the
auto-applied `patch_externals` fixture in `conftest.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Type

import pytest
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header

from connect_bootstrap.app import WizardState
from connect_bootstrap.screens.apply import ApplyScreen
from connect_bootstrap.screens.auth import AwsAuthScreen
from connect_bootstrap.screens.cluster import ClusterScreen
from connect_bootstrap.screens.deploy import DeployScreen
from connect_bootstrap.screens.deps import DepsCheckScreen
from connect_bootstrap.screens.secrets import SecretsScreen
from connect_bootstrap.screens.status import StatusScreen
from connect_bootstrap.screens.tfvars_form import TfvarsFormScreen


TERMINAL_SIZE = (120, 40)


class _ScreenHarness(App):
    """Minimal App that hosts a single screen for snapshot capture."""

    state: WizardState

    def __init__(self, screen_cls: Type[Screen], state: WizardState) -> None:
        super().__init__()
        self.state = state
        self._screen_cls = screen_cls

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Footer()

    def on_mount(self) -> None:
        self.push_screen(self._screen_cls())


def _state(repo_root: Path | None = None, **overrides) -> WizardState:
    s = WizardState()
    if repo_root is not None:
        s.repo_root = repo_root
    s.aws_account_id = "123456789012"
    s.aws_region = "us-east-1"
    s.caller_arn = "arn:aws:iam::123456789012:user/test-operator"
    s.domain = "connect.example.com"
    s.route53_zone_id = "Z0123456789ABCDEFGHIJ"
    s.cluster_name = "connect-prod"
    s.web_service_name = "connect-prod-web"
    s.worker_service_name = "connect-prod-worker"
    s.alb_dns_name = "connect-prod-alb-1234567890.us-east-1.elb.amazonaws.com"
    s.apply_succeeded = True
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


@pytest.fixture(autouse=True)
def freeze_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin datetime.now so 'last updated' timestamps and the textual Header
    clock render identical bytes across runs."""
    fixed_naive = datetime(2026, 4, 28, 12, 0, 0)
    fixed_aware = fixed_naive.replace(tzinfo=timezone.utc)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_aware if tz else fixed_naive

    # status.py imports datetime inside _refresh — patch the module reference.
    monkeypatch.setattr(
        "connect_bootstrap.screens.status.datetime",
        _FrozenDateTime,
        raising=False,
    )
    # Textual's Header clock renders datetime.now().time() in
    # textual.widgets._header — patch it so the wall-clock corner is stable.
    monkeypatch.setattr(
        "textual.widgets._header.datetime",
        _FrozenDateTime,
        raising=True,
    )


# ── Tests: 9 screens ─────────────────────────────────────────────────────────


def test_home_screen(snap_compare):
    """Initial App view — HomeScreen card before any wizard step."""
    from connect_bootstrap.app import BootstrapApp

    app = BootstrapApp()
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_deps_check_screen(snap_compare):
    app = _ScreenHarness(DepsCheckScreen, _state())
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_aws_auth_screen(snap_compare):
    app = _ScreenHarness(AwsAuthScreen, _state())
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_tfvars_form_screen(snap_compare, repo_root: Path):
    app = _ScreenHarness(TfvarsFormScreen, _state(repo_root=repo_root))
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_apply_screen(snap_compare, repo_root: Path):
    app = _ScreenHarness(ApplyScreen, _state(repo_root=repo_root))
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_secrets_screen(snap_compare):
    app = _ScreenHarness(SecretsScreen, _state())
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_cluster_screen(snap_compare):
    app = _ScreenHarness(ClusterScreen, _state())
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_deploy_screen(snap_compare):
    app = _ScreenHarness(DeployScreen, _state())
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)


def test_status_screen(snap_compare):
    app = _ScreenHarness(StatusScreen, _state())
    assert snap_compare(app, terminal_size=TERMINAL_SIZE)
