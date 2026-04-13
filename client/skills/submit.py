from __future__ import annotations

import time
from typing import Optional

import typer
from rich.console import Console

from client import api
from client.config import save_last_task
from client.skills.base import Skill
from client.skills.task_utils import resolve_task_id

console = Console()

# Terminal statuses — once reached, the task won't change any more
_DONE_STATUSES = {"success", "failed", "cancelled"}


def _wait_and_print_logs(task_id: str, poll_interval: float = 0.1) -> None:
    """Stream task logs in near-real-time by polling every poll_interval seconds."""
    printed_offset = 0  # byte offset of logs already printed
    done = False

    while not done:
        # --- fetch any new log data first (minimise perceived latency) ---
        try:
            log_resp = api.get_logs(task_id, offset=printed_offset)
            new_data: str = log_resp.get("data") or ""
            if new_data:
                print(new_data, end="", flush=True)
                printed_offset += len(new_data.encode())
        except Exception:
            pass  # log fetch failure is non-fatal

        # --- check task status ---
        try:
            task = api.get_task(task_id)
            status = task.get("status", "unknown")
        except Exception as e:
            console.print(f"[red]Error polling task status: {e}[/red]")
            break

        if status in _DONE_STATUSES:
            done = True
            # Final flush: catch any logs written between the last poll and completion
            try:
                log_resp = api.get_logs(task_id, offset=printed_offset)
                tail: str = log_resp.get("data") or ""
                if tail:
                    print(tail, end="", flush=True)
            except Exception:
                pass

            color = "green" if status == "success" else "red"
            console.print(f"\n[{color}]Task {status}[/{color}]  ({task_id})")
        else:
            time.sleep(poll_interval)


class SubmitSkill(Skill):
    def register(self, cli: typer.Typer) -> None:
        @cli.command()
        def run(
            command: str = typer.Argument(..., help="Shell command to execute"),
            conda: Optional[str] = typer.Option(None, "--conda", "-c", help="Conda environment name"),
            workdir: Optional[str] = typer.Option(None, "--workdir", "-w", help="Working directory on server"),
            no_wait: bool = typer.Option(
                False, "--no-wait", "-n", help="Return immediately without waiting for output"
            ),
        ):
            """Submit a task for remote execution and stream its output."""
            task = api.create_task(command, conda_env=conda, working_dir=workdir)
            save_last_task(task["id"], task.get("command"))
            console.print(f"[green]Task submitted:[/green] {task['id']}")
            console.print(f"  status: {task['status']}")
            console.print(f"  command: {task['command']}")

            if no_wait:
                console.print("  tip: use [bold]rds logs[/bold] to view the latest task logs")
                return

            console.print("[dim]─── output ──────────────────────────────────────────[/dim]")
            _wait_and_print_logs(task["id"])

        @cli.command()
        def cancel(
            task_id: Optional[str] = typer.Argument(
                None, help="Task ID, defaults to the latest submitted task"
            )
        ):
            """Cancel a running task."""
            resolved_task_id = resolve_task_id(task_id, "cancel")
            result = api.cancel_task(resolved_task_id)
            console.print(f"[yellow]{result['status']}[/yellow]")

        @cli.command(name="ps")
        def list_tasks(
            limit: int = typer.Option(20, "--limit", "-n"),
        ):
            """List recent tasks."""
            data = api.list_tasks(limit=limit)
            for t in data["tasks"]:
                status_color = {
                    "running": "blue",
                    "success": "green",
                    "failed": "red",
                    "pending": "yellow",
                    "cancelled": "dim",
                }.get(t["status"], "white")
                console.print(
                    f"[{status_color}]{t['status']:>10}[/{status_color}]  "
                    f"{t['id']}  {t['command'][:60]}"
                )
            console.print(f"\n[dim]Total: {data['total']}[/dim]")

        @cli.command()
        def info(
            task_id: Optional[str] = typer.Argument(
                None, help="Task ID, defaults to the latest submitted task"
            )
        ):
            """Show task details."""
            resolved_task_id = resolve_task_id(task_id, "show info for")
            t = api.get_task(resolved_task_id)
            for k, v in t.items():
                if v is not None:
                    console.print(f"  [bold]{k}:[/bold] {v}")
