"""Step 4: terraform plan + confirm gate + terraform apply.

Live-streams subprocess output into a scrolling RichLog. Parses the plan
summary line ('Plan: N to add, M to change, K to destroy') and gates the
apply on operator confirmation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, RichLog, Static

from connect_bootstrap.shell import run_capture, run_stream


@dataclass
class PlanSummary:
    add: int = 0
    change: int = 0
    destroy: int = 0


_PLAN_LINE = re.compile(r"Plan:\s*(\d+) to add,\s*(\d+) to change,\s*(\d+) to destroy")


class ApplyScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("p", "do_plan", "Plan"),
        Binding("a", "do_apply", "Apply"),
        Binding("n", "advance", "Next"),
    ]

    DEFAULT_CSS = """
    ApplyScreen {
        align: center middle;
    }
    Vertical#apply-pane {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    RichLog {
        height: 1fr;
        border: round $accent;
    }
    Horizontal#buttons {
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._plan_summary: PlanSummary | None = None
        self._applied: bool = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="apply-pane"):
            yield Static("[b]Step 4 / 8[/b] — terraform plan + apply", id="title")
            yield RichLog(id="log", highlight=True, markup=True)
            yield Static("", id="summary")
            with Horizontal(id="buttons"):
                yield Button("Run plan", id="plan", variant="primary")
                yield Button("Apply", id="apply", disabled=True)
                yield Button("Next →", id="next", disabled=True)
        yield Footer()

    def _terraform_dir(self) -> Path:
        return self.app.state.terraform_dir or self.app.state.repo_root / "terraform"

    def action_do_plan(self) -> None:
        self.run_worker(self._run_plan(), exclusive=True)

    def action_do_apply(self) -> None:
        if not self.query_one("#apply", Button).disabled:
            self.run_worker(self._run_apply(), exclusive=True)

    def action_advance(self) -> None:
        if not self.query_one("#next", Button).disabled:
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "plan":
            self.run_worker(self._run_plan(), exclusive=True)
        elif event.button.id == "apply":
            self.run_worker(self._run_apply(), exclusive=True)
        elif event.button.id == "next":
            self._advance()

    async def _run_plan(self) -> None:
        log: RichLog = self.query_one(RichLog)
        log.clear()
        log.write("[b]$ terraform init -input=false[/b]")
        rc, out, err = await run_capture(
            ["terraform", "init", "-input=false"], cwd=str(self._terraform_dir()), timeout=120.0
        )
        for line in (out + err).splitlines():
            log.write(line)
        if rc != 0:
            log.write("[red]init failed[/red]")
            return

        log.write("[b]$ terraform plan -no-color -out=tfplan[/b]")
        plan_out: list[str] = []
        async for label, line in run_stream(
            ["terraform", "plan", "-no-color", "-out=tfplan"],
            cwd=str(self._terraform_dir()),
        ):
            if label == "exit":
                rc = int(line)
                break
            plan_out.append(line)
            log.write(("[red]" + line + "[/red]") if label == "stderr" else line)

        summary = self._parse_summary(plan_out)
        self._plan_summary = summary
        summary_w = self.query_one("#summary", Static)
        summary_w.update(
            f"[b]Plan summary:[/b] +{summary.add}  ~{summary.change}  −{summary.destroy}"
            + ("  [green]ready to apply[/green]" if rc == 0 else "  [red]plan failed[/red]")
        )
        if rc == 0:
            self.query_one("#apply", Button).disabled = False

    async def _run_apply(self) -> None:
        log: RichLog = self.query_one(RichLog)
        log.write("[b]$ terraform apply -no-color -input=false tfplan[/b]")
        async for label, line in run_stream(
            ["terraform", "apply", "-no-color", "-input=false", "tfplan"],
            cwd=str(self._terraform_dir()),
        ):
            if label == "exit":
                rc = int(line)
                break
            log.write(("[red]" + line + "[/red]") if label == "stderr" else line)

        if rc != 0:
            log.write("[red]apply failed — fix and re-plan[/red]")
            return

        # Pull useful outputs into WizardState.
        await self._capture_outputs(log)
        self._applied = True
        self.app.state.apply_succeeded = True
        self.query_one("#next", Button).disabled = False
        log.write("[green]apply succeeded — press n to continue[/green]")

    async def _capture_outputs(self, log: RichLog) -> None:
        rc, out, _err = await run_capture(
            ["terraform", "output", "-json"], cwd=str(self._terraform_dir()), timeout=30.0
        )
        if rc != 0:
            log.write("[red]terraform output -json failed; outputs not captured[/red]")
            return
        try:
            outputs = json.loads(out)
        except json.JSONDecodeError as e:
            log.write(f"[red]output JSON decode failed: {e}[/red]")
            return

        def _v(name: str) -> str | None:
            v = outputs.get(name, {}).get("value")
            return v if isinstance(v, str) else None

        self.app.state.cluster_name = _v("cluster_name")
        self.app.state.web_service_name = _v("web_service_name")
        self.app.state.worker_service_name = _v("worker_service_name")
        self.app.state.migrate_task_definition_family = _v("migrate_task_definition_family")
        self.app.state.alb_dns_name = _v("alb_dns_name")
        self.app.state.secrets_kms_key_arn = _v("secrets_kms_key_arn")
        self.app.state.rds_master_secret_arn = _v("rds_master_secret_arn")

        log.write(
            f"[b]Outputs captured:[/b] cluster={self.app.state.cluster_name}  "
            f"alb={self.app.state.alb_dns_name}"
        )

    @staticmethod
    def _parse_summary(lines: list[str]) -> PlanSummary:
        for line in lines:
            m = _PLAN_LINE.search(line)
            if m:
                return PlanSummary(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return PlanSummary()

    def _advance(self) -> None:
        from connect_bootstrap.screens.secrets import SecretsScreen

        self.app.push_screen(SecretsScreen())
