"""Step 1: dependency check (aws, terraform, jq, gh).

Reports each binary's presence + version. Operator must resolve any missing
ones before advancing — there's no auto-install.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from connect_bootstrap.shell import run_capture


@dataclass
class Dep:
    name: str
    version_argv: tuple[str, ...]
    minimum: str  # human-readable hint only; no parsing
    required: bool = True


DEPS: tuple[Dep, ...] = (
    Dep("aws", ("aws", "--version"), "v2.13+"),
    Dep("terraform", ("terraform", "version"), "1.5+"),
    Dep("jq", ("jq", "--version"), "1.6+"),
    Dep(
        "gh", ("gh", "--version"), "2.40+", required=False
    ),  # only needed for AWS_ROLE_ARN push
    Dep("docker", ("docker", "--version"), "24+", required=False),  # for local dev only
)


class DepsCheckScreen(Screen):
    """Lists each dependency, runs --version, marks ✓ / ✗ / —."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("n", "advance", "Next"),
        Binding("r", "refresh_now", "Recheck"),
    ]

    DEFAULT_CSS = """
    DepsCheckScreen {
        align: center middle;
    }
    Vertical#deps-pane {
        width: 80;
        padding: 1 2;
        border: round $accent;
    }
    DataTable {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="deps-pane"):
            yield Static("[b]Step 1 / 8[/b] — Dependency check", id="title")
            yield DataTable(id="deps-table")
            yield Static("", id="hint")
            yield Button("Recheck", id="recheck", variant="primary")
            yield Button("Next →", id="next", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Required", "Tool", "Status", "Version", "Hint")
        self.run_worker(self._refresh(), exclusive=True)

    async def _refresh(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        all_required_ok = True
        for dep in DEPS:
            status, version = await self._probe(dep)
            row_required = "yes" if dep.required else "no"
            row_status = status
            table.add_row(
                row_required, dep.name, row_status, version, f"need {dep.minimum}"
            )
            if dep.required and not status.startswith("✓"):
                all_required_ok = False

        hint = self.query_one("#hint", Static)
        next_btn = self.query_one("#next", Button)
        if all_required_ok:
            hint.update(
                "All required deps present. Press [b]n[/b] or click [b]Next →[/b]."
            )
            next_btn.disabled = False
        else:
            hint.update(
                "[red]Required dep missing.[/red] Install it, then press [b]r[/b] to recheck."
            )
            next_btn.disabled = True

    async def _probe(self, dep: Dep) -> tuple[str, str]:
        if shutil.which(dep.name) is None:
            return "✗ not found", "-"
        try:
            rc, out, err = await run_capture(list(dep.version_argv), timeout=5.0)
        except Exception as e:  # subprocess.TimeoutError, FileNotFoundError, etc.
            return "✗ probe error", str(e)[:40]
        if rc != 0:
            return "✗ exit nonzero", out.splitlines()[0] if out else err.splitlines()[
                0
            ] if err else ""
        first_line = (out or err).splitlines()[0] if (out or err) else "?"
        return "✓ ok", first_line[:40]

    def action_refresh_now(self) -> None:
        self.run_worker(self._refresh(), exclusive=True)

    def action_advance(self) -> None:
        next_btn = self.query_one("#next", Button)
        if not next_btn.disabled:
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "recheck":
            self.run_worker(self._refresh(), exclusive=True)
        elif event.button.id == "next":
            self._advance()

    def _advance(self) -> None:
        from connect_bootstrap.screens.auth import AwsAuthScreen

        self.app.push_screen(AwsAuthScreen())
