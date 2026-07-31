import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import database
import main
from models import AnalyzeRequest


class _FastAgent:
    def set_persona(self, persona):
        self.persona = persona

    async def analyze(
        self, prompt, report_style=None, owner_address=None,
        progress_callback=None,
    ):
        if progress_callback:
            progress_callback(55, "Writing the report with DeepSeek")
        return {
            "token": {"name": "Dogecoin", "symbol": "DOGE", "chain": "solana"},
            "overall_score": 7.0,
            "risk_level": "medium",
            "persona": self.persona,
        }


class _SlowAgent(_FastAgent):
    async def analyze(self, *args, **kwargs):
        await asyncio.sleep(60)


class AnalysisJobTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tempdir.name) / "analysis-jobs.db"
        database.init_db()
        main._analysis_jobs.clear()
        main._analysis_job_tasks.clear()

    def tearDown(self):
        main._analysis_jobs.clear()
        main._analysis_job_tasks.clear()
        database.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def _job(self, job_id, request):
        now = time.time()
        main._analysis_jobs[job_id] = {
            "job_id": job_id,
            "owner_address": "0xabc",
            "status": "queued",
            "progress": 0,
            "stage": "Queued",
            "source_request": request.model_dump(),
            "created_at": now,
            "updated_at": now,
        }

    def test_background_job_completes_and_preserves_source_request(self):
        request = AnalyzeRequest(
            prompt="dogecoin solana", persona="operator",
            report_style="friendly and concise",
        )
        self._job("complete", request)
        with (
            patch.object(main, "MemeOpsAgent", _FastAgent),
            patch("charts.generate_all_charts", return_value={"chart_1": "data:image/png;base64,a"}),
        ):
            asyncio.run(main._run_analysis_job("complete", request, "0xabc"))
        job = main._analysis_jobs["complete"]
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["progress"], 100)
        self.assertEqual(job["result"]["source_request"]["persona"], "operator")
        self.assertGreater(job["result"]["analysis_id"], 0)

    def test_background_job_can_be_cancelled(self):
        request = AnalyzeRequest(prompt="dogecoin solana", persona="investor")
        self._job("cancel", request)

        async def scenario():
            with patch.object(main, "MemeOpsAgent", _SlowAgent):
                task = asyncio.create_task(
                    main._run_analysis_job("cancel", request, "0xabc")
                )
                await asyncio.sleep(0)
                task.cancel()
                await task

        asyncio.run(scenario())
        self.assertEqual(main._analysis_jobs["cancel"]["status"], "cancelled")
        self.assertEqual(main._analysis_jobs["cancel"]["stage"], "Analysis stopped")


if __name__ == "__main__":
    unittest.main()
