from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Root of the installed server package  →  .../remote_device_server/server/
_SERVER_DIR = Path(__file__).parent
# Default data directory sits next to the server/ package: .../remote_device_server/user_data/
_DEFAULT_DATA_DIR = _SERVER_DIR.parent / "user_data"


@dataclass
class Settings:
    api_key: str = "change-me-to-a-strong-random-key"
    host: str = "0.0.0.0"
    port: int = 44401
    workspace_root: str = field(default_factory=lambda: str(_DEFAULT_DATA_DIR / "workspace"))
    log_root: str = field(default_factory=lambda: str(_DEFAULT_DATA_DIR / "logs"))
    max_concurrent_tasks: int = 4
    db_path: str = field(default_factory=lambda: str(_DEFAULT_DATA_DIR / "rds.db"))

    def __post_init__(self):
        _INT_FIELDS = {"port", "max_concurrent_tasks"}

        for name in ("api_key", "host", "port", "workspace_root",
                      "log_root", "max_concurrent_tasks", "db_path"):
            env_key = "RDS_" + name.upper()
            val = os.environ.get(env_key)
            if val is not None:
                if name in _INT_FIELDS:
                    setattr(self, name, int(val))
                else:
                    setattr(self, name, val)

        self.port = int(self.port)
        self.max_concurrent_tasks = int(self.max_concurrent_tasks)
        self.workspace_root = Path(self.workspace_root)
        self.log_root = Path(self.log_root)
        self.db_path = Path(self.db_path)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.log_root.mkdir(parents=True, exist_ok=True)

    def get_client_workspace(self, username: str) -> Path:
        """Return (and create) a dedicated workspace directory for a user."""
        path = self.workspace_root / username
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
