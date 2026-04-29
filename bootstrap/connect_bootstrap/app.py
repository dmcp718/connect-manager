"""Main Textual app + screen navigation for connect-bootstrap."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Static


def _resolve_repo_root() -> Path:
    """Find the repo root by walking up from cwd looking for terraform/main.tf.

    The TUI is run from arbitrary cwds (project root, bootstrap/, etc.). The
    previous default of Path.cwd() picked up bootstrap/ and tried to write
    bootstrap/terraform/terraform.tfvars. Walk upward looking for the real
    terraform/ dir; fall back to the package's source-tree parent for
    editable installs; final fallback is cwd so the error is at least visible.
    """
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "terraform" / "main.tf").is_file():
            return candidate
    pkg_root = Path(__file__).resolve().parents[2]
    if (pkg_root / "terraform" / "main.tf").is_file():
        return pkg_root
    return Path.cwd()


@dataclass
class WizardState:
    """State that flows between screens.

    Populated incrementally as the operator advances through the wizard.
    Persisted in-memory only — re-running connect-bootstrap starts fresh.
    """

    # Resolved at startup
    repo_root: Path = field(default_factory=_resolve_repo_root)
    terraform_dir: Optional[Path] = None

    # From auth screen
    aws_account_id: Optional[str] = None
    aws_region: str = "us-east-1"
    aws_profile: Optional[str] = None
    caller_arn: Optional[str] = None

    # From tfvars form
    domain: Optional[str] = None
    route53_zone_id: Optional[str] = None
    github_repo: Optional[str] = None
    alarms_email: Optional[str] = None
    env: str = "prod"

    # From apply
    apply_succeeded: bool = False
    cluster_name: Optional[str] = None
    web_service_name: Optional[str] = None
    worker_service_name: Optional[str] = None
    migrate_task_definition_family: Optional[str] = None
    alb_dns_name: Optional[str] = None
    secrets_kms_key_arn: Optional[str] = None
    rds_master_secret_arn: Optional[str] = None

    # From secrets
    secrets_seeded: bool = False

    # Final
    deploy_succeeded: bool = False


class HomeScreen(Static):
    """Landing card shown before the operator picks a step."""

    DEFAULT_CSS = """
    HomeScreen {
        align: center middle;
        padding: 2 4;
        border: round $accent;
        background: $panel;
    }
    """

    def render(self) -> str:
        return (
            "[b]connect-bootstrap[/b] — operator wizard for the aws-fargate stack.\n\n"
            "Press [b]n[/b] to start the wizard, [b]q[/b] to quit, [b]?[/b] for help."
        )


class BootstrapApp(App):
    """Top-level Textual app.

    Each wizard step is a Screen pushed onto the stack. The state attribute
    is read-write across screens (single-threaded; no locking required).
    """

    TITLE = "connect-bootstrap"
    SUB_TITLE = "aws-fargate operator wizard"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("n", "next_step", "Next"),
        Binding("?", "help", "Help"),
    ]

    CSS = """
    Screen {
        background: $background;
    }
    Footer {
        background: $primary 30%;
    }
    """

    state: WizardState

    def __init__(self) -> None:
        super().__init__()
        self.state = WizardState()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Container(HomeScreen(), id="home-container")
        yield Footer()

    def action_next_step(self) -> None:
        # Lazy imports keep startup fast; screens loaded only on entry.
        from connect_bootstrap.screens.deps import DepsCheckScreen

        self.push_screen(DepsCheckScreen())

    def action_help(self) -> None:
        self.notify(
            "Use the on-screen buttons or the keybinds in the footer. "
            "n = next step, q = quit. Esc on any screen returns to the home view.",
            title="connect-bootstrap help",
            timeout=8,
        )
