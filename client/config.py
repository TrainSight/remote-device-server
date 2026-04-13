from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

RDS_DIR = Path.home() / ".rds"
CONFIG_FILE = RDS_DIR / "config"
LAST_TASK_FILE = RDS_DIR / "last_task.json"


# ── config file helpers ───────────────────────────────────────────────────────

def _load_config() -> Dict[str, str]:
    config: Dict[str, str] = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    return config


def _save_config(config: Dict[str, str]) -> None:
    _ensure_rds_dir()
    lines = [f"{k}={v}" for k, v in config.items()]
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_rds_dir() -> None:
    RDS_DIR.mkdir(parents=True, exist_ok=True)


# ── username bootstrap ────────────────────────────────────────────────────────

def _bootstrap_username(cfg: Dict[str, str]) -> str:
    """Interactively ask the user to choose a username on first run.

    The value is persisted to ~/.rds/config so the prompt only appears once.
    Falls back to the OS login name if stdin is not a tty.
    """
    default = getpass.getuser()

    # Non-interactive fallback (e.g. pipes / scripts)
    if not sys.stdin.isatty():
        cfg["username"] = default
        _save_config(cfg)
        return default

    print()
    print("┌─────────────────────────────────────────────────┐")
    print("│  rds — first time setup                         │")
    print("│                                                  │")
    print("│  Choose a username to identify your workspace   │")
    print("│  on the remote server.  This is saved once to   │")
    print("│  ~/.rds/config and won't be asked again.        │")
    print("└─────────────────────────────────────────────────┘")
    try:
        answer = input(f"  Username [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""

    username = answer if answer else default
    # Sanitise: keep only alphanumeric, dash, underscore, dot
    username = "".join(c for c in username if c.isalnum() or c in "-_.")
    if not username:
        username = default

    cfg["username"] = username
    _save_config(cfg)
    print(f"  ✓ Username set to '{username}' (saved to ~/.rds/config)\n")
    return username


# ── last-task helpers ─────────────────────────────────────────────────────────

def save_last_task(task_id: str, command: Optional[str] = None) -> None:
    _ensure_rds_dir()
    payload: Dict[str, Any] = {"task_id": task_id}
    if command:
        payload["command"] = command
    LAST_TASK_FILE.write_text(json.dumps(payload), encoding="utf-8")


def load_last_task() -> Optional[Dict[str, str]]:
    if not LAST_TASK_FILE.exists():
        return None

    try:
        payload = json.loads(LAST_TASK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None

    last_task: Dict[str, str] = {"task_id": task_id}
    command = payload.get("command")
    if isinstance(command, str) and command:
        last_task["command"] = command
    return last_task


def get_last_task_id() -> Optional[str]:
    last_task = load_last_task()
    if not last_task:
        return None
    return last_task["task_id"]


# ── module-level initialisation ───────────────────────────────────────────────

_cfg = _load_config()

SERVER_URL = os.environ.get("RDS_SERVER_URL", _cfg.get("server_url", ""))
API_KEY    = os.environ.get("RDS_API_KEY",    _cfg.get("api_key",    ""))

if not SERVER_URL or not API_KEY:
    print(
        "Error: RDS_SERVER_URL and RDS_API_KEY must be configured.\n"
        "\n"
        "Option 1 - config file (~/.rds/config):\n"
        "  mkdir -p ~/.rds\n"
        "  echo 'server_url=http://YOUR_SERVER_IP:PORT' >> ~/.rds/config\n"
        "  echo 'api_key=YOUR_API_KEY' >> ~/.rds/config\n"
        "\n"
        "Option 2 - environment variables:\n"
        "  export RDS_SERVER_URL=http://YOUR_SERVER_IP:PORT\n"
        "  export RDS_API_KEY=YOUR_API_KEY",
        file=sys.stderr,
    )
    sys.exit(1)

# Resolve username: env var > config file > interactive bootstrap
USERNAME: str = os.environ.get("RDS_USERNAME", _cfg.get("username", ""))
if not USERNAME:
    USERNAME = _bootstrap_username(_cfg)
