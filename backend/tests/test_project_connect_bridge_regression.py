"""Bridge return must reach a picker even for accounts with existing projects."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.github_app import router


def test_regression_project_connect_bridge_returns_to_projects():
    app = FastAPI()
    app.include_router(router, prefix="/api/aurem-dev")
    response = TestClient(app).get(
        "/api/aurem-dev/github/app/installed?status=success&install_id=42"
    )
    assert response.status_code == 200
    assert "window.location.replace('/projects' + tail)" in response.text
    assert "flow=connect-repo" in response.text