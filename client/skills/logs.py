from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from client import api
from client.config import API_KEY, SERVER_URL, get_last_task_id, load_last_task
from client.skills.base import Skill

console = Console()


class LogsSkill(Skill):
    def register(self, cli: typer.Typer) -> None:
        @cli.command()
        def logs(
            task_id: Optional[str] = typer.Argument(
                None, help="Task ID, defaults to the latest submitted task"
            ),
            follow: bool = typer.Option(
                False, "--follow", "-f", help="Stream logs in real time"
            ),
        ):
            """View task logs."""
            resolved_task_id = _resolve_task_id(task_id)
            if follow:
                _follow_ws(resolved_task_id)
            else:
                data = api.get_logs(resolved_task_id)
                if data["data"]:
                    console.print(data["data"], end="")
                else:
                    console.print("[dim]No logs yet.[/dim]")


def _resolve_task_id(task_id: Optional[str]) -> str:
    if task_id:
        return task_id

    last_task_id = get_last_task_id()
    if not last_task_id:
        console.print(
            "[red]No recent task found. Run [bold]rds run[/bold] first or pass a task id.[/red]"
        )
        raise typer.Exit(1)

    last_task = load_last_task() or {"task_id": last_task_id}
    command_hint = last_task.get("command")
    if command_hint:
        console.print(f"[dim]Using latest task {last_task_id}: {command_hint}[/dim]")
    else:
        console.print(f"[dim]Using latest task {last_task_id}[/dim]")
    return last_task_id


def _follow_ws(task_id: str) -> None:
    try:
        from websockets.sync.client import connect
    except ImportError:
        console.print("[red]websockets package required for --follow[/red]")
        raise typer.Exit(1)

    ws_url = SERVER_URL.replace("http://", "ws://").replace("https://", "wss://")
    url = f"{ws_url}/tasks/{task_id}/logs/ws"
    headers = {"X-API-Key": API_KEY}

    try:
        with connect(url, additional_headers=headers) as ws:
            for message in ws:
                print(message, end="", flush=True)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        console.print(f"[red]WebSocket error: {e}[/red]")
