"""Step 7: CONNECT install (ECS service deploy + ALB target health smoke probe).

Runs the same ecs:UpdateService / wait services-stable flow that the
GitHub Actions workflow uses, then polls the web target group until at
least one target is healthy.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, ProgressBar, RichLog, Static


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
    RichLog {
        height: 1fr;
        border: round $accent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="deploy-pane"):
            yield Static(
                "[b]Step 7 / 8[/b] — Service deploy + ALB target health", id="title"
            )
            yield RichLog(id="log", highlight=True, markup=True)
            yield ProgressBar(id="progress", total=100, show_eta=False)
            with Horizontal():
                yield Button("Deploy", id="deploy", variant="primary")
                yield Button("Next →", id="next", disabled=True)
        yield Footer()

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
        from connect_bootstrap.aws_helper import boto3_client

        log: RichLog = self.query_one(RichLog)
        progress: ProgressBar = self.query_one(ProgressBar)
        progress.update(progress=0)

        cluster = self.app.state.cluster_name
        web = self.app.state.web_service_name
        worker = self.app.state.worker_service_name
        region = self.app.state.aws_region
        if not (cluster and web and worker):
            log.write(
                "[red]missing cluster/service names from state — re-run terraform apply[/red]"
            )
            return

        ecs = boto3_client("ecs", region_name=region)
        elbv2 = boto3_client("elbv2", region_name=region)

        log.write(f"[b]→ update-service[/b] {web} (force-new-deployment)")
        try:
            ecs.update_service(cluster=cluster, service=web, forceNewDeployment=True)
        except Exception as e:
            log.write(f"[red]update_service failed:[/red] {e}")
            return
        progress.update(progress=20)

        log.write(f"[b]→ update-service[/b] {worker} (force-new-deployment)")
        try:
            ecs.update_service(cluster=cluster, service=worker, forceNewDeployment=True)
        except Exception as e:
            log.write(f"[red]update_service failed:[/red] {e}")
            return
        progress.update(progress=40)

        log.write("[b]→ waiting for services-stable[/b] (timeout 15 min)")
        # boto3 waiter is sync; run in a thread so the UI keeps responding.
        try:
            await asyncio.to_thread(
                self._wait_services_stable, ecs, cluster, [web, worker]
            )
        except Exception as e:
            log.write(f"[red]wait services-stable failed:[/red] {e}")
            return
        progress.update(progress=80)
        log.write("[green]services stable[/green]")

        log.write("[b]→ probing ALB target health[/b]")
        target_groups = await asyncio.to_thread(
            self._target_groups_for_service, elbv2, ecs, cluster, web
        )
        healthy = await asyncio.to_thread(
            self._wait_for_healthy_targets, elbv2, target_groups, deadline_s=120
        )
        progress.update(progress=100)
        if healthy:
            self.app.state.deploy_succeeded = True
            log.write(
                f"[green]✓ at least one target healthy[/green]  →  https://{self.app.state.domain}/health"
            )
            self.query_one("#next", Button).disabled = False
        else:
            log.write("[red]no healthy targets within 2 min — investigate[/red]")

    @staticmethod
    def _wait_services_stable(ecs, cluster: str, services: list[str]) -> None:
        waiter = ecs.get_waiter("services_stable")
        waiter.wait(
            cluster=cluster,
            services=services,
            WaiterConfig={"Delay": 15, "MaxAttempts": 60},  # 15 min
        )

    @staticmethod
    def _target_groups_for_service(elbv2, ecs, cluster: str, service: str) -> list[str]:
        resp = ecs.describe_services(cluster=cluster, services=[service])
        svcs = resp.get("services", [])
        if not svcs:
            return []
        lbs = svcs[0].get("loadBalancers", [])
        return [lb["targetGroupArn"] for lb in lbs if "targetGroupArn" in lb]

    @staticmethod
    def _wait_for_healthy_targets(
        elbv2, tg_arns: list[str], deadline_s: int = 120
    ) -> bool:
        import time

        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            for tg_arn in tg_arns:
                resp = elbv2.describe_target_health(TargetGroupArn=tg_arn)
                healthy = [
                    t
                    for t in resp.get("TargetHealthDescriptions", [])
                    if t.get("TargetHealth", {}).get("State") == "healthy"
                ]
                if healthy:
                    return True
            time.sleep(5)
        return False

    def _advance(self) -> None:
        from connect_bootstrap.screens.status import StatusScreen

        self.app.push_screen(StatusScreen())
