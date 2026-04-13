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

Tab completion
--------------
  * First word   → built-in commands + remote executables (fetched async)
  * Path args    → remote filesystem (async, 10 s TTL cache)
  * rpush/rupload → local filesystem paths
"""

from __future__ import annotations

import asyncio
import shlex
import time
from pathlib import Path
from typing import AsyncIterator, Dict, Iterable, List, Optional, Tuple

from rich.console import Console

from client import api
from client.config import save_last_task
from client.skills.submit import _wait_and_print_logs, _resolve_task_id

console = Console()

# ── async remote helpers ──────────────────────────────────────────────────────

# TTL cache: path → (fetched_at, [entries])
_DIR_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_CACHE_TTL = 10.0  # seconds

# Remote executable list (populated once in background at startup)
_remote_cmds: List[str] = []
_remote_cmds_ready = asyncio.Event()


async def _run_remote_task(command: str, timeout: float = 5.0) -> str:
    """Submit a fire-and-forget task and return its stdout as a string."""
    loop = asyncio.get_event_loop()
    task = await loop.run_in_executor(None, lambda: api.create_task(command))
    task_id = task["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = await loop.run_in_executor(None, lambda: api.get_task(task_id))
        if t.get("status") in ("success", "failed", "cancelled"):
            break
        await asyncio.sleep(0.05)
    resp = await loop.run_in_executor(None, lambda: api.get_logs(task_id))
    return (resp.get("data") or "").strip()


async def _fetch_remote_cmds() -> None:
    """Background task: fetch remote executable list via `compgen -c`."""
    global _remote_cmds
    try:
        output = await _run_remote_task("bash -c 'compgen -c' 2>/dev/null | sort -u", timeout=8.0)
        cmds = [c for c in output.splitlines() if c and not c.startswith("_")]
        _remote_cmds = cmds
    except Exception:
        _remote_cmds = []
    _remote_cmds_ready.set()


async def _remote_ls(directory: str) -> List[str]:
    """List a remote directory with TTL caching."""
    now = time.monotonic()
    if directory in _DIR_CACHE:
        ts, entries = _DIR_CACHE[directory]
        if now - ts < _CACHE_TTL:
            return entries
    try:
        raw = await _run_remote_task(
            f"ls -1ap {shlex.quote(directory)} 2>/dev/null", timeout=5.0
        )
        entries = [e for e in raw.splitlines() if e and e not in ("./", "../")]
    except Exception:
        entries = []
    _DIR_CACHE[directory] = (now, entries)
    return entries


async def _complete_remote_path(prefix: str) -> List[str]:
    """Expand a remote path prefix → list of completions."""
    if "/" in prefix:
        dir_part = prefix.rsplit("/", 1)[0] or "/"
        file_part = prefix.rsplit("/", 1)[1]
        base = dir_part + "/"
    else:
        dir_part = "."
        file_part = prefix
        base = ""
    entries = await _remote_ls(dir_part)
    return [base + e for e in entries if e.startswith(file_part)]


# ── prompt_toolkit completer ──────────────────────────────────────────────────

try:
    from prompt_toolkit.completion import Completer, CompleteEvent, Completion, ThreadedCompleter
    from prompt_toolkit.document import Document

    class _RdsCompleterSync(Completer):
        """Synchronous completer — wrapped in ThreadedCompleter so UI never blocks."""

        def get_completions(
            self, document: Document, complete_event: CompleteEvent
        ) -> Iterable[Completion]:
            line = document.text_before_cursor
            try:
                tokens = shlex.split(line)
            except ValueError:
                tokens = line.split()

            new_token = line.endswith(" ") or not line

            # ── first token: built-ins + remote executables ───────────────────
            if not tokens or (len(tokens) == 1 and not new_token):
                word = tokens[0] if tokens else ""
                builtin_candidates = list(_BUILTINS.keys()) + ["exit", "quit"]
                for name in sorted(builtin_candidates):
                    if name.startswith(word):
                        yield Completion(
                            name[len(word):],
                            display=name,
                            display_meta="[built-in]",
                        )
                if _remote_cmds_ready.is_set():
                    seen = set(builtin_candidates)
                    for cmd in _remote_cmds:
                        if cmd.startswith(word) and cmd not in seen:
                            yield Completion(cmd[len(word):], display=cmd)
                return

            cmd = tokens[0]

            # ── rpush / rupload: local filesystem ────────────────────────────
            if cmd in ("rpush", "rupload"):
                import glob
                word = "" if new_token else (tokens[-1] if len(tokens) > 1 else "")
                pattern = (word or ".") + "*"
                for match in sorted(glob.glob(pattern)):
                    suffix = "/" if Path(match).is_dir() else ""
                    display = match + suffix
                    yield Completion(display[len(word):], display=display)
                return

            # ── everything else: remote path completion ───────────────────────
            word = "" if new_token else (tokens[-1] if len(tokens) > 1 else "")
            if word.startswith("/") or word.startswith("./") or word.startswith("../") or not new_token:
                # Run async path fetch synchronously inside the thread
                loop = asyncio.new_event_loop()
                try:
                    matches = loop.run_until_complete(_complete_remote_path(word))
                finally:
                    loop.close()
                for m in matches:
                    yield Completion(m[len(word):], display=m)

    # Wrap in ThreadedCompleter: runs get_completions in a thread pool,
    # keeping the prompt_toolkit event loop (and the UI) fully responsive.
    def _make_completer() -> Completer:
        return ThreadedCompleter(_RdsCompleterSync())

    _HAS_PROMPT_TOOLKIT = True

except ImportError:
    _HAS_PROMPT_TOOLKIT = False

    def _make_completer():  # type: ignore
        return None


# ── helpers ───────────────────────────────────────────────────────────────────


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
  [dim]rds>[/dim] ls -la          →  rds run "ls -la"  (output printed automatically)

[bold]Tab completion[/bold]
  First word    → built-in + remote executables (e.g. nvidia-smi, python)
  Path args     → remote filesystem paths
  rpush/rupload → local filesystem paths
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


# ── dispatch helper ───────────────────────────────────────────────────────────

def _dispatch(line: str) -> None:
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        console.print(f"[red]Parse error:[/red] {e}")
        return

    cmd, *args = tokens

    if cmd in _BUILTINS:
        try:
            _BUILTINS[cmd](args)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
    else:
        try:
            task = api.create_task(line)
            save_last_task(task["id"], task.get("command"))
            console.print(f"[dim]→ remote: {line}  (task {task['id']})[/dim]")
            console.print("[dim]─── output ──────────────────────────────────────────[/dim]")
            _wait_and_print_logs(task["id"])
        except Exception as e:
            console.print(f"[red]Failed to submit task:[/red] {e}")


# ── main REPL (prompt_toolkit) ────────────────────────────────────────────────

async def _repl() -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style

    style = Style.from_dict({"prompt": "ansicyan bold"})
    session: PromptSession = PromptSession(
        completer=_make_completer(),
        history=InMemoryHistory(),
        style=style,
        enable_history_search=True,
    )

    # Kick off remote command list fetch in background
    asyncio.ensure_future(_fetch_remote_cmds())

    console.print(
        "[bold cyan]rds-shell[/bold cyan] [dim]— type [bold]rhelp[/bold] for commands, "
        "[bold]exit[/bold] to quit[/dim]"
    )
    console.print("[dim]Tab completion: commands + remote paths.  "
                  "Remote executables load in background...[/dim]")

    loop = asyncio.get_event_loop()

    with patch_stdout():
        while True:
            try:
                line = await session.prompt_async("rds> ")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Bye![/dim]")
                break

            line = line.strip()
            if not line:
                continue
            if line in ("exit", "quit"):
                console.print("[dim]Bye![/dim]")
                break

            # Run blocking dispatch in thread so the event loop stays alive
            await loop.run_in_executor(None, _dispatch, line)


# ── main REPL (fallback: plain readline) ─────────────────────────────────────

def _repl_fallback() -> None:
    import readline  # noqa: F401 — history + arrow keys

    console.print(
        "[bold cyan]rds-shell[/bold cyan] [dim]— type [bold]rhelp[/bold] for commands, "
        "[bold]exit[/bold] to quit[/dim]"
    )
    console.print("[dim](prompt_toolkit not installed — tab completion unavailable)[/dim]")

    while True:
        try:
            line = input("rds> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye![/dim]")
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            console.print("[dim]Bye![/dim]")
            break
        _dispatch(line)


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if _HAS_PROMPT_TOOLKIT:
        asyncio.run(_repl())
    else:
        _repl_fallback()


if __name__ == "__main__":
    main()
