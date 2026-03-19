from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from client import api
from client.skills.base import Skill

console = Console()

REMOTE_PROJECT_ROOT = "/mnt/dolphinfs/ssd_pool/docker/user/hadoop-perception/wangjiawei43"


class KernelBenchSkill(Skill):
    """Push a CUDA kernel project to remote A100 and run benchmarks."""

    def register(self, cli: typer.Typer) -> None:
        @cli.command()
        def bench(
            project_dir: Path = typer.Argument(
                ..., help="Local project directory (e.g. ~/sparse-mask-attention)"
            ),
            mode: str = typer.Option(
                "all",
                "--mode", "-m",
                help="Benchmark mode: correctness | perf | compare | all",
            ),
            run_cmd: Optional[str] = typer.Option(
                None,
                "--cmd",
                help="Custom command (overrides default 'python run.py --mode <mode>')",
            ),
            follow: bool = typer.Option(
                True, "--follow/--no-follow", "-f",
                help="Stream logs in real time",
            ),
            conda: Optional[str] = typer.Option(
                None, "--conda", "-c",
                help="Conda environment on remote machine",
            ),
        ):
            """Push a CUDA kernel project to remote GPU and run benchmarks.

            Examples:
                rds bench ~/sparse-mask-attention
                rds bench ~/sparse-mask-attention --mode compare
                rds bench ~/sparse-mask-attention --cmd "python run.py --mode perf"
                rds bench ~/my-kernel --cmd "pytest tests/ -v" --conda walle
            """
            project_dir = project_dir.expanduser().resolve()
            if not project_dir.is_dir():
                console.print(f"[red]Not a directory: {project_dir}[/red]")
                raise typer.Exit(1)

            # 1. Push code
            console.print(f"[dim]Packing {project_dir.name}...[/dim]")
            from client.skills.push import _pack_directory
            tar_path = _pack_directory(project_dir)

            console.print("[dim]Uploading to remote A100...[/dim]")
            result = api.upload_file(tar_path)
            upload_id = result["upload_id"]
            tar_path.unlink()
            console.print(f"[green]Uploaded:[/green] {upload_id}")

            # 2. Build command
            if run_cmd is None:
                run_cmd = f"python run.py --mode {mode}"

            # 3. Install deps + run
            full_cmd = f"cd {REMOTE_PROJECT_ROOT} && pip install -r requirements.txt -q 2>/dev/null; {run_cmd}"

            task = api.create_task(full_cmd, conda_env=conda, upload_id=upload_id)
            task_id = task["id"]
            console.print(f"[green]Task submitted:[/green] {task_id}")
            console.print(f"  command: {run_cmd}")

            # 4. Follow logs
            if follow:
                console.print("[dim]--- streaming output ---[/dim]")
                _follow_logs(task_id)
            else:
                console.print(f"  Use [bold]rds logs {task_id} -f[/bold] to follow output")


def _follow_logs(task_id: str) -> None:
    """Poll logs until task finishes (works without websockets too)."""
    offset = 0
    while True:
        data = api.get_logs(task_id, offset=offset)
        if data["data"]:
            print(data["data"], end="", flush=True)
            offset = data["offset"]

        task_info = api.get_task(task_id)
        if task_info["status"] in ("success", "failed", "cancelled"):
            # Drain remaining logs
            data = api.get_logs(task_id, offset=offset)
            if data["data"]:
                print(data["data"], end="", flush=True)

            status = task_info["status"]
            color = "green" if status == "success" else "red"
            console.print(f"\n[{color}]Task {status}[/{color}] (exit_code={task_info.get('exit_code')})")
            break

        time.sleep(1)
