import os

import pytest
from google_fixtures import google_db  # noqa: F401


@pytest.mark.parametrize("scenario", ["read", "research", "update"])
async def test_google_worker_graph_uses_python_without_approval(google_db, scenario):  # noqa: F811
    if os.environ.get("ARTIFACT_RUNNER_DOCKER") != "1":
        pytest.skip("Requires Docker runtime image")
    from scripts.google_fixture import run_google_scenario

    report = await run_google_scenario(google_db, scenario)
    assert report["status"] == "completed", report
    assert report["search_calls"] == (1 if scenario == "research" else 0)
    assert report["approvals"] == 0
    assert report["writes"] == (0 if scenario == "read" else 1)
    assert set(report["tools"]) <= {"run_python_code", "search_web"}
