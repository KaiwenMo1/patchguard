from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from patchguard.api_app import AnalysisStore, create_app
from patchguard.models import ChangedFile, PullRequestInfo, RiskReport

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def api_client(app) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    )


async def test_api_submit_poll_and_fetch_report(tmp_path) -> None:
    store = AnalysisStore(tmp_path / "api_runs")
    app = create_app(store=store, report_service_factory=lambda: FakeReportService())
    async with api_client(app) as client:
        submit = await client.post(
            "/api/analyze-pr",
            json={"pr_url": "https://github.com/owner/repo/pull/123"},
        )

        assert submit.status_code == 202
        analysis_id = submit.json()["analysis_id"]

        status_response = await wait_for_terminal_status(client, analysis_id)
        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["status"] == "completed"
        assert status_payload["pr_url"] == "https://github.com/owner/repo/pull/123"

        report_response = await client.get(f"/api/report/{analysis_id}")
        assert report_response.status_code == 200
        report_payload = report_response.json()
        assert report_payload["pr"]["owner"] == "owner"
        assert report_payload["changed_files"][0]["filename"] == "src/app.py"
        assert report_payload["risk_score"] == 0


async def test_api_forwards_safety_options_to_report_service(tmp_path) -> None:
    fake_service = FakeReportService()
    store = AnalysisStore(tmp_path / "api_runs")
    app = create_app(store=store, report_service_factory=lambda: fake_service)
    async with api_client(app) as client:
        response = await client.post(
            "/api/analyze-pr",
            json={
                "pr_url": "https://github.com/owner/repo/pull/123",
                "skip_llm": True,
                "skip_docker": True,
            },
        )

        assert response.status_code == 202
        await wait_for_terminal_status(client, response.json()["analysis_id"])
    assert fake_service.received_options["skip_llm"] is True
    assert fake_service.received_options["skip_docker"] is True


async def test_api_records_failed_background_analysis(tmp_path) -> None:
    store = AnalysisStore(tmp_path / "api_runs")
    app = create_app(store=store, report_service_factory=lambda: FailingReportService())
    async with api_client(app) as client:
        submit = await client.post(
            "/api/analyze-pr",
            json={"pr_url": "https://github.com/owner/repo/pull/123"},
        )

        analysis_id = submit.json()["analysis_id"]
        status_payload = (await wait_for_terminal_status(client, analysis_id)).json()
        assert status_payload["status"] == "failed"
        assert "boom" in status_payload["error"]

        report_response = await client.get(f"/api/report/{analysis_id}")
        assert report_response.status_code == 404


async def test_api_report_returns_conflict_before_completion(tmp_path) -> None:
    store = AnalysisStore(tmp_path / "api_runs")
    record = store.create("https://github.com/owner/repo/pull/123")
    app = create_app(store=store, report_service_factory=lambda: FakeReportService())
    async with api_client(app) as client:
        response = await client.get(f"/api/report/{record.analysis_id}")

    assert response.status_code == 409
    assert "not finished" in response.json()["detail"]


async def test_api_allows_local_dashboard_origin(tmp_path) -> None:
    store = AnalysisStore(tmp_path / "api_runs")
    app = create_app(store=store, report_service_factory=lambda: FakeReportService())
    async with api_client(app) as client:
        response = await client.options(
            "/api/analyze-pr",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


async def wait_for_terminal_status(
    client: httpx.AsyncClient,
    analysis_id: str,
    *,
    attempts: int = 100,
) -> httpx.Response:
    for _ in range(attempts):
        response = await client.get(f"/api/analysis/{analysis_id}")
        if response.json()["status"] in {"completed", "failed", "partial"}:
            return response
        await asyncio.sleep(0.01)
    raise AssertionError(f"Analysis {analysis_id} did not finish")


class FakeReportService:
    def __init__(self) -> None:
        self.received_options = {}

    def analyze(
        self,
        pr_url: str,
        output_path: str | Path,
        *,
        workspaces_dir=None,  # noqa: ANN001
        cleanup_workspace: bool = False,
        skip_llm: bool = False,
        skip_docker: bool = False,
        status_callback=None,  # noqa: ANN001
    ) -> RiskReport:
        self.received_options = {
            "cleanup_workspace": cleanup_workspace,
            "skip_llm": skip_llm,
            "skip_docker": skip_docker,
        }
        if status_callback:
            for status in [
                "fetching_pr",
                "cloning",
                "analyzing_diff",
                "running_existing_tests",
                "scanning_security",
            ]:
                status_callback(status)
        report = RiskReport(
            pr=PullRequestInfo(
                owner="owner",
                repo="repo",
                number=123,
                url=pr_url,
                title="Fake PR",
            ),
            changed_files=[
                ChangedFile(
                    filename="src/app.py",
                    status="modified",
                    additions=1,
                    deletions=1,
                    changes=2,
                )
            ],
        )
        report.status = "complete"
        report.report_path = str(output_path)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return report


class FailingReportService:
    def analyze(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("boom")
