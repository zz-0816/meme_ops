"""Small project-local .env loader.

The project intentionally avoids making python-dotenv a runtime requirement.
Real process environment variables always take precedence over values in .env.
"""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
