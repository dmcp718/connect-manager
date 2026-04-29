"""Step 5: Secrets Manager seeder.

Module.secrets ships connect/<env>/jwt + connect/<env>/admin as empty
secrets. This screen prompts for the actual values (with a 'generate JWT'
button) and writes them via PutSecretValue.

DATABASE_URL and VALKEY_URL are populated by the rds/elasticache modules
themselves — no operator input needed.
"""

from __future__ import annotations

import json
import secrets as py_secrets

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static


class SecretsScreen(Screen):
    # NOTE: bindings use ctrl-modifier keys because plain printable keys
    # (g, n) get consumed by whichever Input has focus and never reach the
    # screen — the operator wouldn't be able to advance from the secrets
    # form once they'd clicked into any field.
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("ctrl+j", "advance", "Next"),
        Binding("ctrl+g", "gen_jwt", "Generate JWT"),
    ]

    DEFAULT_CSS = """
    SecretsScreen {
        align: center middle;
    }
    Vertical#secrets-pane {
        width: 90;
        padding: 1 2;
        border: round $accent;
    }
    Input {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="secrets-pane"):
            yield Static("[b]Step 5 / 8[/b] — Secrets Manager seeder", id="title")
            yield Static(
                "Click [b]Generate[/b] (or press [b]Ctrl+G[/b]) for a 32-byte JWT key."
            )

            yield Label("JWT signing key (32+ random bytes, hex):")
            yield Input(password=True, id="jwt", placeholder="64 hex chars")
            with Horizontal():
                yield Button("Generate", id="gen", variant="default")
                yield Static(" ")

            yield Label("Admin email (for the bootstrap admin user):")
            yield Input(id="admin_email", placeholder="admin@example.com")

            yield Label("Admin password:")
            yield Input(password=True, id="admin_password")

            yield Static("", id="status")
            with Horizontal():
                yield Button("Seed", id="seed", variant="primary")
                yield Button("Next →", id="next", disabled=True)
        yield Footer()

    def action_gen_jwt(self) -> None:
        self.query_one("#jwt", Input).value = py_secrets.token_hex(32)

    def action_advance(self) -> None:
        if not self.query_one("#next", Button).disabled:
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "gen":
            self.action_gen_jwt()
        elif event.button.id == "seed":
            self.run_worker(self._seed(), exclusive=True)
        elif event.button.id == "next":
            self._advance()

    async def _seed(self) -> None:
        status = self.query_one("#status", Static)
        jwt = self.query_one("#jwt", Input).value.strip()
        admin_email = self.query_one("#admin_email", Input).value.strip()
        admin_password = self.query_one("#admin_password", Input).value

        missing = [
            name
            for name, val in (
                ("jwt", jwt),
                ("admin_email", admin_email),
                ("admin_password", admin_password),
            )
            if not val
        ]
        if missing:
            status.update(f"[red]Missing:[/red] {', '.join(missing)}")
            return

        # JWT must be at least 32 bytes of entropy (64 hex chars). Anything
        # shorter and Fernet (which derives its key from this value) becomes
        # trivially brute-forceable.
        if len(jwt) < 64 or not all(c in "0123456789abcdefABCDEF" for c in jwt):
            status.update(
                "[red]JWT must be 64+ hex characters (32+ bytes).[/red] "
                "Click [b]Generate[/b] to make one."
            )
            return
        if len(admin_password) < 8:
            status.update("[red]Admin password must be at least 8 characters.[/red]")
            return

        from connect_bootstrap.aws_helper import boto3_client

        sm = boto3_client("secretsmanager", region_name=self.app.state.aws_region)
        env = self.app.state.env
        try:
            sm.put_secret_value(
                SecretId=f"/connect/{env}/jwt",
                SecretString=json.dumps({"value": jwt}),
            )
            sm.put_secret_value(
                SecretId=f"/connect/{env}/admin",
                SecretString=json.dumps(
                    {"email": admin_email, "password": admin_password}
                ),
            )
        except Exception as e:
            status.update(f"[red]put_secret_value failed:[/red] {e}")
            return

        self.app.state.secrets_seeded = True
        status.update("[green]✓[/green] connect/jwt + connect/admin seeded")
        self.query_one("#next", Button).disabled = False

    def _advance(self) -> None:
        from connect_bootstrap.screens.cluster import ClusterScreen

        self.app.push_screen(ClusterScreen())
