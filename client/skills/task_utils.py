from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from client.config import get_last_task_id, load_last_task

console = Console()


def resolve_task_id(task_id: Optional[str], action: str = "use") -> str:
    if task_id:
        return task_id

    last_task_id = get_last_task_id()
    if not last_task_id:
        console.print(
            f"[red]No recent task found. Run [bold]rds run[/bold] first or pass a task id to {action}.[/red]"
        )
        raise typer.Exit(1)

    last_task = load_last_task() or {"task_id": last_task_id}
    command_hint = last_task.get("command")
    if command_hint:
        console.print(f"[dim]Using latest task {last_task_id}: {command_hint}[/dim]")
    else:
        console.print(f"[dim]Using latest task {last_task_id}[/dim]")
    return last_task_id
