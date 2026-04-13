"""rds deploy — sync local source code to the remote server and restart it.

Usage
-----
    rds deploy [--src DIR] [--dest DIR] [--restart-cmd CMD]

Defaults
--------
    --src          auto-detected as the root of the installed package
    --dest         same absolute path on the remote machine (mirror mode)
    --restart-cmd  "pkill -f rds-server; sleep 1; nohup rds-server > ~/rds-server.log 2>&1 &"
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from client.config import SERVER_URL
from client.skills.base import Skill

console = Console()

# Root of the local source tree: .../remote_device_server/
_LOCAL_SRC = Path(__file__).parent.parent.parent.resolve()

# Default restart command executed on the remote host after sync
_DEFAULT_RESTART = (
    "pkill -f rds-server; sleep 1; "
    "nohup rds-server > ~/rds-server.log 2>&1 & "
    "sleep 2 && tail -5 ~/rds-server.log"
)


def _extract_host(server_url: str) -> Optional[str]:
    """Parse 'http://HOST:PORT' → 'HOST'."""
    try:
        host = server_url.split("://", 1)[1].split(":")[0].split("/")[0]
        return host if host else None
    except Exception:
        return None


class DeploySkill(Skill):
    def register(self, cli: typer.Typer) -> None:
        @cli.command()
        def deploy(
            src: Optional[Path] = typer.Option(
                None, "--src", "-s",
                help="Local source directory to sync (default: auto-detected package root)",
            ),
            dest: Optional[str] = typer.Option(
                None, "--dest", "-d",
                help="Destination path on remote host (default: same as local src)",
            ),
            restart_cmd: str = typer.Option(
                _DEFAULT_RESTART, "--restart-cmd",
                help="Shell command to run on remote after sync to restart the server",
            ),
            ssh_user: Optional[str] = typer.Option(
                None, "--user", "-u",
                help="SSH username for the remote host (default: current OS user)",
            ),
            dry_run: bool = typer.Option(
                False, "--dry-run", help="Print rsync command without executing"
            ),
        ):
            """Sync local source code to the remote server and restart it."""

            # ── resolve source ────────────────────────────────────────────────
            local_src = Path(src).resolve() if src else _LOCAL_SRC
            if not local_src.is_dir():
                console.print(f"[red]Source directory not found: {local_src}[/red]")
                raise typer.Exit(1)

            # ── resolve remote host ───────────────────────────────────────────
            host = _extract_host(SERVER_URL)
            if not host:
                console.print(f"[red]Cannot parse host from SERVER_URL: {SERVER_URL}[/red]")
                raise typer.Exit(1)

            remote_dest = dest or str(local_src)  # mirror: same path on remote

            ssh_target = f"{ssh_user}@{host}" if ssh_user else host
            rsync_dest = f"{ssh_target}:{remote_dest}"

            # ── check rsync available ─────────────────────────────────────────
            if not shutil.which("rsync"):
                console.print("[red]rsync not found. Please install rsync.[/red]")
                raise typer.Exit(1)

            # ── build rsync command ───────────────────────────────────────────
            rsync_cmd = [
                "rsync", "-avz", "--delete",
                "--exclude=__pycache__",
                "--exclude=*.pyc",
                "--exclude=*.egg-info",
                "--exclude=.git",
                "--exclude=user_data",   # don't overwrite server data
                f"{local_src}/",         # trailing slash = sync contents
                rsync_dest,
            ]

            console.print(f"[dim]Syncing  {local_src}/[/dim]")
            console.print(f"[dim]      →  {rsync_dest}[/dim]")

            if dry_run:
                console.print("[yellow]dry-run:[/yellow] " + " ".join(rsync_cmd))
                return

            # ── rsync ─────────────────────────────────────────────────────────
            console.print("[dim]Running rsync...[/dim]")
            result = subprocess.run(rsync_cmd)
            if result.returncode != 0:
                console.print(f"[red]rsync failed (exit {result.returncode})[/red]")
                raise typer.Exit(result.returncode)
            console.print("[green]✓ Sync complete[/green]")

            # ── remote install + restart via ssh ─────────────────────────────
            install_and_restart = (
                f"cd {remote_dest} && "
                f"pip install -e . -q && "
                f"{restart_cmd}"
            )
            ssh_cmd = ["ssh", ssh_target, install_and_restart]

            console.print("[dim]Installing & restarting on remote...[/dim]")
            result = subprocess.run(ssh_cmd)
            if result.returncode != 0:
                console.print(f"[red]remote install/restart failed (exit {result.returncode})[/red]")
                raise typer.Exit(result.returncode)
            console.print("[green]✓ Remote install/restart complete[/green]")
