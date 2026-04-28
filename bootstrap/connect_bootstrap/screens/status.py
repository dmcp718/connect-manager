"""Step 8: status dashboard.

Live view of:
  - Fargate task counts (RUNNING/PENDING/STOPPED) per service
  - ALB target group health
  - CloudWatch alarm states (filtered to alarms in the connect-alerts SNS topic)

Auto-refreshes every 15s while the screen is mounted.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static


REFRESH_INTERVAL_S = 15


class StatusScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    DEFAULT_CSS = """
    StatusScreen {
        align: center middle;
    }
    Vertical#status-pane {
        width: 100%;
        height: 100%;
        padding: 1 2;
    }
    DataTable {
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._refresh_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="status-pane"):
            yield Static("[b]Step 8 / 8[/b] — Status dashboard", id="title")
            yield Static("App URL: [link]https://{}[/link]", id="url")
            yield Static("[b]ECS services[/b]")
            yield DataTable(id="services-tbl")
            yield Static("[b]ALB targets[/b]")
            yield DataTable(id="alb-tbl")
            yield Static("[b]CloudWatch alarms[/b]")
            yield DataTable(id="alarms-tbl")
            yield Static("", id="last-updated")
            with Horizontal():
                yield Button("Refresh", id="refresh", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#services-tbl", DataTable).add_columns(
            "Service", "Desired", "Running", "Pending"
        )
        self.query_one("#alb-tbl", DataTable).add_columns("Target", "State", "Reason")
        self.query_one("#alarms-tbl", DataTable).add_columns(
            "Alarm", "State", "Updated"
        )

        url_w = self.query_one("#url", Static)
        url_w.update(
            f"App URL: [link]https://{self.app.state.domain or '<domain>'}/[/link]"
        )

        # Kick off the refresh loop.
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    def on_unmount(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

    def action_refresh_now(self) -> None:
        self.run_worker(self._refresh(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.run_worker(self._refresh(), exclusive=True)

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await self._refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Don't let one failure kill the loop.
                pass
            try:
                await asyncio.sleep(REFRESH_INTERVAL_S)
            except asyncio.CancelledError:
                return

    async def _refresh(self) -> None:
        from connect_bootstrap.aws_helper import boto3_client

        region = self.app.state.aws_region
        cluster = self.app.state.cluster_name
        web = self.app.state.web_service_name
        worker = self.app.state.worker_service_name

        if not (cluster and web and worker):
            self.query_one("#last-updated", Static).update(
                "[red]missing state — re-run apply[/red]"
            )
            return

        ecs = boto3_client("ecs", region_name=region)
        elbv2 = boto3_client("elbv2", region_name=region)
        cw = boto3_client("cloudwatch", region_name=region)

        await asyncio.gather(
            asyncio.to_thread(self._refresh_services, ecs, cluster, [web, worker]),
            asyncio.to_thread(self._refresh_alb, ecs, elbv2, cluster, web),
            asyncio.to_thread(self._refresh_alarms, cw),
        )

        from datetime import datetime, timezone

        self.query_one("#last-updated", Static).update(
            f"updated {datetime.now(timezone.utc).strftime('%H:%M:%SZ')}"
        )

    def _refresh_services(self, ecs, cluster: str, services: list[str]) -> None:
        tbl = self.query_one("#services-tbl", DataTable)
        tbl.clear()
        try:
            resp = ecs.describe_services(cluster=cluster, services=services)
        except Exception as e:
            tbl.add_row("error", str(e)[:40], "", "")
            return
        for s in resp.get("services", []):
            tbl.add_row(
                s.get("serviceName", "?"),
                str(s.get("desiredCount", 0)),
                str(s.get("runningCount", 0)),
                str(s.get("pendingCount", 0)),
            )

    def _refresh_alb(self, ecs, elbv2, cluster: str, web: str) -> None:
        tbl = self.query_one("#alb-tbl", DataTable)
        tbl.clear()
        try:
            svcs = ecs.describe_services(cluster=cluster, services=[web]).get(
                "services", []
            )
            if not svcs:
                return
            for lb in svcs[0].get("loadBalancers", []):
                tg = lb.get("targetGroupArn")
                if not tg:
                    continue
                resp = elbv2.describe_target_health(TargetGroupArn=tg)
                for t in resp.get("TargetHealthDescriptions", []):
                    target_id = t.get("Target", {}).get("Id", "?")
                    state = t.get("TargetHealth", {}).get("State", "?")
                    reason = t.get("TargetHealth", {}).get("Reason", "")
                    tbl.add_row(target_id, state, reason)
        except Exception as e:
            tbl.add_row("error", str(e)[:40], "")

    def _refresh_alarms(self, cw) -> None:
        tbl = self.query_one("#alarms-tbl", DataTable)
        tbl.clear()
        try:
            resp = cw.describe_alarms(AlarmNamePrefix="connect-", MaxRecords=50)
            for a in resp.get("MetricAlarms", []):
                tbl.add_row(
                    a.get("AlarmName", "?"),
                    a.get("StateValue", "?"),
                    str(a.get("StateUpdatedTimestamp", "")),
                )
        except Exception as e:
            tbl.add_row("error", str(e)[:40], "")
