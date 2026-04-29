"""Step 3: terraform.tfvars form.

Reads existing terraform.tfvars (if present) for defaults, presents a form,
writes the result back to disk on Save.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from connect_bootstrap import tfvars


class TfvarsFormScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("n", "advance", "Next"),
        Binding("ctrl+s", "save_now", "Save"),
    ]

    DEFAULT_CSS = """
    TfvarsFormScreen {
        align: center middle;
    }
    VerticalScroll#form-pane {
        width: 90;
        height: 90%;
        padding: 1 2;
        border: round $accent;
    }
    Input {
        width: 1fr;
    }
    """

    # Field name → (label, default, required, placeholder).
    # NOTE: aws_region is intentionally NOT in this list. It is captured on
    # Step 2 (auth screen) where it is actually verified against AWS via
    # sts get-caller-identity, and injected into the tfvars file on _save().
    FIELDS: tuple[tuple[str, str, str, bool, str], ...] = (
        ("env", "env (deployment name suffix)", "prod", False, "prod"),
        ("domain", "domain (FQDN)", "", True, "connect.example.com"),
        ("route53_zone_id", "route53_zone_id", "", True, "Z0123456789ABCDEFGHIJ"),
        (
            "github_repo",
            "github_repo (Path A only — blank = skip GitHub Actions OIDC)",
            "",
            False,
            "owner/repo or leave empty",
        ),
        ("alarms_email", "alarms_email", "", False, "alerts@example.com"),
        ("vpc_cidr", "vpc_cidr", "10.20.0.0/16", False, "10.20.0.0/16"),
        ("rds_instance_class", "rds_instance_class", "db.t4g.micro", False, ""),
        ("rds_multi_az", "rds_multi_az (true/false)", "false", False, "false"),
        (
            "elasticache_node_type",
            "elasticache_node_type",
            "cache.t4g.micro",
            False,
            "",
        ),
        ("worker_max_count", "worker_max_count", "8", False, "8"),
    )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="form-pane"):
            yield Static("[b]Step 3 / 8[/b] — terraform.tfvars", id="title")
            yield Static(
                "Defaults pre-populated from existing tfvars + tfvars.example."
            )
            yield Static(
                f"aws_region = [b]{self.app.state.aws_region}[/b] (inherited from Step 2)"
            )
            existing = self._load_existing()
            for name, label, default, _required, placeholder in self.FIELDS:
                yield Label(label)
                value = str(existing.get(name, default))
                yield Input(value=value, placeholder=placeholder, id=f"f_{name}")
            yield Static("", id="status")
            with Horizontal():
                yield Button("Save", id="save", variant="primary")
                yield Button("Next →", id="next", disabled=True)
        yield Footer()

    def _terraform_dir(self) -> Path:
        return self.app.state.repo_root / "terraform"

    def _load_existing(self) -> dict:
        existing = tfvars.parse(self._terraform_dir() / "terraform.tfvars")
        if not existing:
            existing = tfvars.parse(self._terraform_dir() / "terraform.tfvars.example")
        return existing

    def action_save_now(self) -> None:
        self._save()

    def action_advance(self) -> None:
        if not self.query_one("#next", Button).disabled:
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._save()
        elif event.button.id == "next":
            self._advance()

    def _save(self) -> None:
        status = self.query_one("#status", Static)
        values: dict = {}
        missing: list[str] = []
        for name, _label, _default, required, _ph in self.FIELDS:
            inp = self.query_one(f"#f_{name}", Input)
            v = inp.value.strip()
            if not v:
                if required:
                    missing.append(name)
                continue
            # Coerce types loosely to match tfvars decoding.
            if v.lower() in ("true", "false"):
                values[name] = v.lower() == "true"
            elif v.isdigit():
                values[name] = int(v)
            else:
                values[name] = v

        if missing:
            status.update(f"[red]Missing required:[/red] {', '.join(missing)}")
            return

        # aws_region is sourced from Step 2 (auth) where it was verified
        # against AWS. Inject it so the written tfvars file matches the
        # account/region the operator just authenticated to.
        values["aws_region"] = self.app.state.aws_region

        target = self._terraform_dir() / "terraform.tfvars"
        try:
            tfvars.write(target, values)
        except OSError as e:
            status.update(f"[red]write failed:[/red] {e}")
            return

        # Stash on app state for later screens.
        self.app.state.terraform_dir = self._terraform_dir()
        self.app.state.env = str(values.get("env", "prod"))
        self.app.state.domain = str(values.get("domain", ""))
        self.app.state.route53_zone_id = str(values.get("route53_zone_id", ""))
        self.app.state.github_repo = str(values.get("github_repo", "")) or None
        self.app.state.alarms_email = str(values.get("alarms_email", "")) or None

        status.update(f"[green]✓[/green] wrote {target}")
        self.query_one("#next", Button).disabled = False

    def _advance(self) -> None:
        from connect_bootstrap.screens.apply import ApplyScreen

        self.app.push_screen(ApplyScreen())
