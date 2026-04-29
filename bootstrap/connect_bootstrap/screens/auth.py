"""Step 2: AWS authentication.

Operator picks how to authenticate, the screen verifies via
aws sts get-caller-identity, and the resolved account/region/arn flows
into WizardState.
"""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)

from connect_bootstrap.shell import run_capture


class AwsAuthScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("n", "advance", "Next"),
        Binding("v", "verify_now", "Verify"),
    ]

    DEFAULT_CSS = """
    AwsAuthScreen {
        align: center middle;
    }
    Vertical#auth-pane {
        width: 80;
        padding: 1 2;
        border: round $accent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="auth-pane"):
            yield Static("[b]Step 2 / 8[/b] — AWS authentication", id="title")
            yield Label("Pick a credential source:")
            with RadioSet(id="method"):
                yield RadioButton(
                    "Existing profile / env vars (current shell)",
                    value=True,
                    id="m_env",
                )
                yield RadioButton(
                    "AWS SSO login (aws sso login --profile <name>)", id="m_sso"
                )
                yield RadioButton(
                    "ministack (AWS_ENDPOINT_URL=http://localhost:4566)",
                    id="m_ministack",
                )
            yield Label("Profile name (optional, used for SSO):")
            yield Input(placeholder="connect-prod", id="profile")
            yield Label("Region:")
            yield Input(value="us-east-1", id="region")
            yield Static("", id="status")
            with Horizontal():
                yield Button("Verify", id="verify", variant="primary")
                yield Button("Next →", id="next", disabled=True)
        yield Footer()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        # ministack auto-fills the right env-var hint; no validation needed
        # until the user clicks Verify.
        if event.pressed.id == "m_ministack":
            self.query_one("#region", Input).value = "us-east-1"

    def action_verify_now(self) -> None:
        self.run_worker(self._verify(), exclusive=True)

    def action_advance(self) -> None:
        next_btn = self.query_one("#next", Button)
        if not next_btn.disabled:
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "verify":
            self.run_worker(self._verify(), exclusive=True)
        elif event.button.id == "next":
            self._advance()

    async def _verify(self) -> None:
        status = self.query_one("#status", Static)
        method = self.query_one("#method", RadioSet).pressed_button
        profile = self.query_one("#profile", Input).value.strip()
        region = self.query_one("#region", Input).value.strip() or "us-east-1"

        env: dict[str, str] = {"AWS_REGION": region, "AWS_DEFAULT_REGION": region}
        if method and method.id == "m_sso" and profile:
            env["AWS_PROFILE"] = profile
            status.update(f"Running [b]aws sso login --profile {profile}[/b] …")
            sso_rc, _, sso_err = await run_capture(
                ["aws", "sso", "login", "--profile", profile], env=env, timeout=120.0
            )
            if sso_rc != 0:
                status.update(
                    f"[red]SSO login failed:[/red] {sso_err.splitlines()[0] if sso_err else 'see terminal'}"
                )
                return
        elif method and method.id == "m_ministack":
            env["AWS_ENDPOINT_URL"] = "http://localhost:4566"
            env.setdefault(
                "AWS_ACCESS_KEY_ID", os.environ.get("AWS_ACCESS_KEY_ID", "test")
            )
            env.setdefault(
                "AWS_SECRET_ACCESS_KEY", os.environ.get("AWS_SECRET_ACCESS_KEY", "test")
            )
            # Propagate to the python process env too — boto3 clients in the
            # later screens (secrets, cluster, deploy, status) inherit os.environ
            # and would otherwise hit real AWS instead of ministack.
            os.environ["AWS_ENDPOINT_URL"] = env["AWS_ENDPOINT_URL"]
            os.environ["AWS_ACCESS_KEY_ID"] = env["AWS_ACCESS_KEY_ID"]
            os.environ["AWS_SECRET_ACCESS_KEY"] = env["AWS_SECRET_ACCESS_KEY"]
        # Always sync region into os.environ so later boto3 clients agree
        # with the value the operator just verified.
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region

        status.update("Running [b]aws sts get-caller-identity[/b] …")
        rc, out, err = await run_capture(
            ["aws", "sts", "get-caller-identity", "--output", "json"],
            env=env,
            timeout=15.0,
        )
        if rc != 0:
            status.update(
                f"[red]get-caller-identity failed:[/red] {err.splitlines()[0] if err else out.splitlines()[0] if out else ''}"
            )
            return

        import json

        try:
            ident = json.loads(out)
        except json.JSONDecodeError as e:
            status.update(f"[red]bad json:[/red] {e}")
            return

        # Stash on app state for later screens.
        self.app.state.aws_account_id = ident.get("Account")
        self.app.state.aws_region = region
        self.app.state.aws_profile = profile or None
        self.app.state.caller_arn = ident.get("Arn")

        status.update(
            f"[green]✓[/green] {ident.get('Arn')} — account {ident.get('Account')} — region {region}"
        )
        self.query_one("#next", Button).disabled = False

    def _advance(self) -> None:
        from connect_bootstrap.screens.tfvars_form import TfvarsFormScreen

        self.app.push_screen(TfvarsFormScreen())
