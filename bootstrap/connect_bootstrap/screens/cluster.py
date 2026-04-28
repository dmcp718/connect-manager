"""Step 6: ECS cluster verification.

Replaces the EKS kubeconfig wrapper from the K8s plan. Runs
ecs:DescribeClusters and shows registered services / task counts.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static


class ClusterScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("n", "advance", "Next"),
        Binding("r", "refresh_now", "Refresh"),
    ]

    DEFAULT_CSS = """
    ClusterScreen {
        align: center middle;
    }
    Vertical#cluster-pane {
        width: 90;
        padding: 1 2;
        border: round $accent;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="cluster-pane"):
            yield Static("[b]Step 6 / 8[/b] — ECS cluster verification", id="title")
            yield DataTable(id="cluster-tbl")
            yield Static("", id="status")
            yield Button("Refresh", id="refresh", variant="primary")
            yield Button("Next →", id="next", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        tbl = self.query_one(DataTable)
        tbl.add_columns("Field", "Value")
        self.run_worker(self._refresh(), exclusive=True)

    def action_refresh_now(self) -> None:
        self.run_worker(self._refresh(), exclusive=True)

    def action_advance(self) -> None:
        if not self.query_one("#next", Button).disabled:
            self._advance()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.run_worker(self._refresh(), exclusive=True)
        elif event.button.id == "next":
            self._advance()

    async def _refresh(self) -> None:
        from connect_bootstrap.aws_helper import boto3_client

        tbl: DataTable = self.query_one(DataTable)
        tbl.clear()
        status = self.query_one("#status", Static)

        cluster_name = self.app.state.cluster_name
        if not cluster_name:
            status.update("[red]cluster_name missing — re-run terraform apply[/red]")
            return

        ecs = boto3_client("ecs", region_name=self.app.state.aws_region)
        try:
            resp = ecs.describe_clusters(
                clusters=[cluster_name],
                include=["STATISTICS", "SETTINGS"],
            )
        except Exception as e:
            status.update(f"[red]describe_clusters failed:[/red] {e}")
            return

        clusters = resp.get("clusters", [])
        if not clusters:
            status.update(f"[red]cluster '{cluster_name}' not found[/red]")
            return
        c = clusters[0]

        rows: list[tuple[str, str]] = [
            ("Cluster name", c.get("clusterName", "")),
            ("Cluster ARN", c.get("clusterArn", "")),
            ("Status", c.get("status", "")),
            ("Registered tasks", str(c.get("registeredContainerInstancesCount", 0))),
            ("Running tasks", str(c.get("runningTasksCount", 0))),
            ("Pending tasks", str(c.get("pendingTasksCount", 0))),
            ("Active services", str(c.get("activeServicesCount", 0))),
        ]
        for k, v in rows:
            tbl.add_row(k, v)

        if c.get("status") == "ACTIVE":
            status.update("[green]✓[/green] cluster ACTIVE — proceed to deploy")
            self.query_one("#next", Button).disabled = False
        else:
            status.update(f"[yellow]cluster status = {c.get('status')}[/yellow]")

    def _advance(self) -> None:
        from connect_bootstrap.screens.deploy import DeployScreen

        self.app.push_screen(DeployScreen())
