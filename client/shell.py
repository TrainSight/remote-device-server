"""rds-shell — a lightweight interactive shell for remote-device-server.

Usage
-----
Just run `rds-shell` (or `python -m client.shell`).  You will land in a REPL
where:

  * Every command that is NOT a built-in short-alias is forwarded to the
    remote machine via `rds run <cmd>` and its output is streamed back
    automatically (no need to call `rds logs`).

  * Built-in short aliases:

        rlogs  [task_id]           – view logs of latest (or given) task
        rlogs  -f [task_id]        – follow/stream logs
        rinfo  [task_id]           – show task details
        rps    [-n N]              – list recent tasks (default 20)
        rcancel [task_id]          – cancel a task
        rpush  <dir> [--run CMD]   – push local dir, optionally run command
        rupload <file>             – upload a single file
        rdownload <remote> <local> – download file from server
        renvs                      – list conda environments on server
        rmonitor                   – show GPU / CPU snapshot
        rhealth                    – check server connectivity
        rhelp                      – show this help message
        exit / quit / Ctrl-D       – leave rds-shell
"""

from __future__ import annotations

import shlex
import sys
import readline  # noqa: F401  — enables arrow-key history in input()

from pathlib import Path
from typing import Optional

from rich.console import Console

from client import api
from client.config import save_last_task
from client.skills.submit import _wait_and_print_logs, _resolve_task_id

console = Console()

# ── helpers ──────────────────────────────────────────────────────────────────


def _print_help() -> None:
    console.print(
        """
[bold cyan]rds-shell[/bold cyan] — remote device shell

[bold]Built-in commands[/bold]
  [green]rlogs[/green]  [task_id]              view output of latest (or given) task
  [green]rlogs[/green]  -f [task_id]           follow / stream logs live
  [green]rinfo[/green]  [task_id]              show task details
  [green]rps[/green]    [-n N]                 list recent tasks (default 20)
  [green]rcancel[/green] [task_id]             cancel a running task
  [green]rpush[/green]  <dir> [--run CMD]      push local dir and optionally run cmd
  [green]rupload[/green] <file>                upload a single local file
  [green]rdownload[/green] <remote> <local>    download file from server
  [green]renvs[/green]                         list conda envs on server
  [green]rmonitor[/green]                      GPU / CPU snapshot
  [green]rhealth[/green]                       check server connectivity
  [green]rhelp[/green]                         show this message
  [green]exit[/green] / [green]quit[/green] / Ctrl-D         leave rds-shell

[bold]Everything else[/bold] is forwarded to the remote machine:
  [dim]rds-shell>[/dim] ls -la          →  rds run "ls -la"  (output printed automatically)
"""
    )


# ── built-in handlers ─────────────────────────────────────────────────────────


def _cmd_rlogs(args: list[str]) -> None:
    follow = "-f" in args
    args = [a for a in args if a != "-f"]
    task_id = args[0] if args else None

    try:
        resolved = _resolve_task_id(task_id)
    except SystemExit:
        return

    if follow:
        _follow_ws(resolved)
    else:
        data = api.get_logs(resolved)
        text = data.get("data") or ""
        if text:
            print(text, end="")
        else:
            console.print("[dim]No logs yet.[/dim]")


def _follow_ws(task_id: str) -> None:
    from client.config import API_KEY, SERVER_URL

    try:
        from websockets.sync.client import connect
    except ImportError:
        console.print("[red]websockets package required for --follow[/red]")
        return

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


def _cmd_rinfo(args: list[str]) -> None:
    task_id = args[0] if args else None
    try:
        resolved = _resolve_task_id(task_id)
    except SystemExit:
        return
    t = api.get_task(resolved)
    for k, v in t.items():
        if v is not None:
            console.print(f"  [bold]{k}:[/bold] {v}")


def _cmd_rps(args: list[str]) -> None:
    limit = 20
    if "-n" in args:
        idx = args.index("-n")
        try:
            limit = int(args[idx + 1])
        except (IndexError, ValueError):
            pass
    data = api.list_tasks(limit=limit)
    for t in data["tasks"]:
        color = {
            "running": "blue", "success": "green",
            "failed": "red", "pending": "yellow", "cancelled": "dim",
        }.get(t["status"], "white")
        console.print(
            f"[{color}]{t['status']:>10}[/{color}]  {t['id']}  {t['command'][:60]}"
        )
    console.print(f"\n[dim]Total: {data['total']}[/dim]")


def _cmd_rcancel(args: list[str]) -> None:
    task_id = args[0] if args else None
    try:
        resolved = _resolve_task_id(task_id)
    except SystemExit:
        return
    result = api.cancel_task(resolved)
    console.print(f"[yellow]{result['status']}[/yellow]")


def _cmd_rpush(args: list[str]) -> None:
    import tarfile

    if not args:
        console.print("[red]Usage: rpush <dir> [--run CMD] [--conda ENV][/red]")
        return

    path = Path(args[0]).resolve()
    if not path.is_dir():
        console.print(f"[red]Not a directory: {path}[/red]")
        return

    run_cmd: Optional[str] = None
    conda: Optional[str] = None
    i = 1
    while i < len(args):
        if args[i] == "--run" and i + 1 < len(args):
            run_cmd = args[i + 1]; i += 2
        elif args[i] == "--conda" and i + 1 < len(args):
            conda = args[i + 1]; i += 2
        else:
            i += 1

    console.print(f"[dim]Packing {path}...[/dim]")
    tar_path = Path(f"/tmp/rds_upload_{path.name}.tar.gz")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(path, arcname=".")

    console.print("[dim]Uploading...[/dim]")
    result = api.upload_file(tar_path)
    upload_id = result["upload_id"]
    console.print(f"[green]Uploaded:[/green] {upload_id}")
    tar_path.unlink(missing_ok=True)

    if run_cmd:
        task = api.create_task(run_cmd, conda_env=conda, upload_id=upload_id)
        save_last_task(task["id"], task.get("command"))
        console.print(f"[green]Task submitted:[/green] {task['id']}")
        console.print("[dim]─── output ──────────────────────────────────────────[/dim]")
        _wait_and_print_logs(task["id"])


def _cmd_rupload(args: list[str]) -> None:
    if not args:
        console.print("[red]Usage: rupload <file>[/red]")
        return
    p = Path(args[0]).resolve()
    if not p.is_file():
        console.print(f"[red]Not a file: {p}[/red]")
        return
    result = api.upload_file(p)
    console.print(f"[green]Uploaded:[/green] {result['upload_id']} ({result['filename']})")


def _cmd_rdownload(args: list[str]) -> None:
    if len(args) < 2:
        console.print("[red]Usage: rdownload <remote_path> <local_path>[/red]")
        return
    api.download_file(args[0], Path(args[1]).resolve())
    console.print(f"[green]Downloaded:[/green] {args[1]}")


def _cmd_renvs(_args: list[str]) -> None:
    envs = api.list_envs()
    if not envs:
        console.print("[dim]No conda environments found.[/dim]")
        return
    for env in envs:
        console.print(f"  {env['name']:20s} {env['path']}")


def _cmd_rmonitor(_args: list[str]) -> None:
    from client.skills.monitor import _print_snapshot
    data = api.get_monitor()
    _print_snapshot(data)


def _cmd_rhealth(_args: list[str]) -> None:
    try:
        result = api.health()
        console.print(f"[green]Server OK[/green]: {result}")
    except Exception as e:
        console.print(f"[red]Connection failed:[/red] {e}")


# ── dispatch table ────────────────────────────────────────────────────────────

_BUILTINS: dict = {
    "rlogs":     _cmd_rlogs,
    "rinfo":     _cmd_rinfo,
    "rps":       _cmd_rps,
    "rcancel":   _cmd_rcancel,
    "rpush":     _cmd_rpush,
    "rupload":   _cmd_rupload,
    "rdownload": _cmd_rdownload,
    "renvs":     _cmd_renvs,
    "rmonitor":  _cmd_rmonitor,
    "rhealth":   _cmd_rhealth,
    "rhelp":     lambda _: _print_help(),
}


# ── main REPL ─────────────────────────────────────────────────────────────────

def main() -> None:
    console.print(
        "[bold cyan]rds-shell[/bold cyan] [dim]— type [bold]rhelp[/bold] for commands, "
        "[bold]exit[/bold] to quit[/dim]"
    )

    while True:
        try:
            line = input("[dim]rds>[/dim] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye![/dim]")
            break

        if not line:
            continue

        if line in ("exit", "quit"):
            console.print("[dim]Bye![/dim]")
            break

        # parse into tokens (respects quotes)
        try:
            tokens = shlex.split(line)
        except ValueError as e:
            console.print(f"[red]Parse error:[/red] {e}")
            continue

        cmd, *args = tokens

        if cmd in _BUILTINS:
            try:
                _BUILTINS[cmd](args)
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
        else:
            # forward entire line as a remote command
            try:
                task = api.create_task(line)
                save_last_task(task["id"], task.get("command"))
                console.print(
                    f"[dim]→ remote: {line}  (task {task['id']})[/dim]"
                )
                console.print("[dim]─── output ──────────────────────────────────────────[/dim]")
                _wait_and_print_logs(task["id"])
            except Exception as e:
                console.print(f"[red]Failed to submit task:[/red] {e}")


if __name__ == "__main__":
    main()
