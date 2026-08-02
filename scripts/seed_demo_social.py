"""Seed one clearly-labelled synthetic Top-N social snapshot for demos."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from config import load_project_env  # noqa: E402

load_project_env()

from database import init_db  # noqa: E402
from social import seed_demo_social_snapshots  # noqa: E402


def main() -> None:
    init_db()
    limit = max(1, min(int(os.getenv("DEMO_SOCIAL_DATA_LIMIT", "10")), 100))
    print(
        json.dumps(
            seed_demo_social_snapshots(limit), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
