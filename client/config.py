from __future__ import annotations

import os
from pathlib import Path


def _load_config() -> dict[str, str]:
    config: dict[str, str] = {}
    config_file = Path.home() / ".rds" / "config"
    if config_file.exists():
        for line in config_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    return config


_cfg = _load_config()

SERVER_URL = os.environ.get("RDS_SERVER_URL", _cfg.get("server_url", "http://10.164.56.75:44401"))
API_KEY = os.environ.get("RDS_API_KEY", _cfg.get("api_key", "change-me-to-a-strong-random-key"))
