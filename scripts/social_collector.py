"""Run one supported social collection cycle.

Local:
    python scripts/social_collector.py

Railway Cron:
    python scripts/social_collector.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from config import load_project_env  # noqa: E402

load_project_env()

from database import init_db  # noqa: E402
from social import SocialCollector  # noqa: E402


async def main() -> None:
    init_db()
    result = await SocialCollector().run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["asset_count"] and result["success_count"] == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
