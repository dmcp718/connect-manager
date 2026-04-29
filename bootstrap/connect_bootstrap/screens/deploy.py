"""Step 7: CONNECT install — delegates to scripts/deploy.sh.

The Path B deploy script (scripts/deploy.sh) was smoke-validated end-to-end
against real AWS in awsk-3r4.5: ECR login, multi-arch build + push,
register migrate task def + run-task, wait for clean exit, then register
new web/worker task defs and update-service. Reimplementing that here
would diverge — the TUI live-streams the script's output instead.

Pre-conditions (enforced by the screen):
  - terraform apply has populated cluster_name + web/worker service names
    on WizardState.
  - docker is on $PATH (deps screen warns if missing, but it's optional
    there because local dev doesn't need it).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

from connect_bootstrap.shell import run_stream


class DeployScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("n", "advance", "Next"),
        Binding("d", "do_deploy", "Deploy"),
    ]

    DEFAULT_CSS = """
    DeployScreen {
        align: center middle;
    }
    Vertical#deploy-pane {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    Horizontal#tag-row {
        height: auto;
    }
    Input {
        width: 1fr;
    }
    RichLog {
        height: 1fr;
        border: round $accent;
    }
    Horizontal#buttons {
        height: auto;
    }
    """

    DEPLOY_SCRIPT_RELATIVE = "scripts/deploy.sh"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="deploy-pane"):
            yield Static(
                "[b]Step 7 / 8[/b] — Build images, run migrate, deploy services",
                id="title",
            )
            yield Static(
                "Runs scripts/deploy.sh: ECR login → buildx multi-arch → push → "
                "migrate task → update-service → wait services-stable.",
            )
            with Horizontal(id="tag-row"):
                yield Label("Image tag (blank = git short SHA):")
                yield Input(placeholder="e.g. v0.1.0", id="tag")
            yield RichLog(id="log", highlight=True, markup=True)
            yield Static("", id="status")
            with Horizontal(id="buttons"):
                yield Button("Deploy", id="deploy", variant="primary")
                yield Button("Next →", id="next", disabled=True)
        yield Footer()

    def _script_path(self) -> Path:
        return self.app.state.repo_root / self.DEPLOY_SCRIPT_RELATIVE

    def _preflight(self) -> str | None:
        """Return a human-readable error if the screen can't run, else None."""
        s = self.app.state
        if not s.cluster_name:
            return "cluster_name missing — re-run terraform apply"
        if not s.web_service_name or not s.worker_service_name:
            return "web/worker service names missing — re-run terraform apply"
        if not self._script_path().is_file():
            return f"deploy script not found at {self._script_path()}"
        if shutil.which("docker") is None:
            return "docker is not on $PATH — install docker (and buildx) and retry"
        return None

    def action_do_deploy(self) -> None:
        self.run_worker(self._deploy(), exclusive=True)

    def action_advance(self) -> None:
        if not self.query_one("#next", Button).disabled:
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "deploy":
            self.run_worker(self._deploy(), exclusive=True)
        elif event.button.id == "next":
            self._advance()

    async def _deploy(self) -> None:
        log: RichLog = self.query_one(RichLog)
        status = self.query_one("#status", Static)

        err = self._preflight()
        if err:
            status.update(f"[red]{err}[/red]")
            return

        s = self.app.state
        env = {
            **os.environ,
            "AWS_REGION": s.aws_region,
            "AWS_DEFAULT_REGION": s.aws_region,
            "CLUSTER_NAME": s.cluster_name or f"connect-{s.env}",
            "WEB_SERVICE": s.web_service_name or f"connect-{s.env}-web",
            "WORKER_SERVICE": s.worker_service_name or f"connect-{s.env}-worker",
            "MIGRATE_TASK_DEF_FAMILY": s.migrate_task_definition_family
            or f"connect-{s.env}-migrate",
            "WEB_TASK_DEF_FAMILY": f"connect-{s.env}-web",
            "WORKER_TASK_DEF_FAMILY": f"connect-{s.env}-worker",
        }

        argv: list[str] = ["bash", str(self._script_path())]
        tag = self.query_one("#tag", Input).value.strip()
        if tag:
            argv.append(tag)

        log.clear()
        log.write(f"[b]$ {' '.join(argv)}[/b]")
        status.update("running deploy.sh — this typically takes 8–15 min")

        rc = -1
        async for label, line in run_stream(
            argv,
            cwd=str(self.app.state.repo_root),
            env=env,
        ):
            if label == "exit":
                rc = int(line)
                break
            log.write(("[red]" + line + "[/red]") if label == "stderr" else line)

        if rc == 0:
            self.app.state.deploy_succeeded = True
            domain = self.app.state.domain or "<domain>"
            status.update(
                f"[green]✓ deploy complete[/green] — try [link]https://{domain}/health[/link]"
            )
            self.query_one("#next", Button).disabled = False
        else:
            status.update(
                f"[red]deploy.sh exited {rc}[/red] — fix the underlying issue and retry"
            )

    def _advance(self) -> None:
        from connect_bootstrap.screens.status import StatusScreen

        self.app.push_screen(StatusScreen())
