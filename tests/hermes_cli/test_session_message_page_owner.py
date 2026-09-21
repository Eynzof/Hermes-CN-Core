from pathlib import Path

import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    "serving_profile",
    [
        "work",
        # A serving home that is not a named profile (custom HERMES_HOME). On POSIX that home
        # sits under the platform default (~/.hermes) so ``profile=default`` still resolves to
        # the real default; on Windows the platform default is %LOCALAPPDATA%\hermes, which a
        # test cannot host hermetically (redirecting LOCALAPPDATA trips the live-state.db guard).
        pytest.param(
            None,
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason="POSIX-only layout: custom HERMES_HOME under the platform default home",
            ),
        ),
    ],
)
def test_message_pages_identify_the_serving_profile(tmp_path, monkeypatch, serving_profile):
    from hermes_state import SessionDB

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    work_home = default_home / "profiles" / "work" if serving_profile else default_home / "custom-home"
    default_home.mkdir(parents=True)
    work_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(work_home))
    # conftest redirects this legacy override independently of HERMES_HOME.
    monkeypatch.setattr("hermes_state.DEFAULT_DB_PATH", work_home / "state.db")

    for home, count in ((default_home, 1), (work_home, 199)):
        db = SessionDB(db_path=home / "state.db")
        try:
            db.create_session(session_id="same-id", source="desktop")
            db.append_messages_batch("same-id", [
                {"role": "user", "content": f"message-{index}"}
                for index in range(count)
            ])
        finally:
            db.close()

    from hermes_cli.web_routers.sessions import manage_router

    app = FastAPI()
    app.include_router(manage_router)
    with TestClient(app) as client:
        query = "limit=120&order=latest&include_compacted=true"
        tail = client.get(f"/api/sessions/same-id/messages?{query}").json()
        older = client.get(f"/api/sessions/same-id/messages?{query}&offset=120").json()
        default = client.get(f"/api/sessions/same-id/messages?{query}&profile=default").json()

    assert tail["profile"] == older["profile"] == (serving_profile or "default")
    assert default["profile"] == "default"
    assert len(tail["messages"]) == 120
    assert len(older["messages"]) == 79
    assert len(default["messages"]) == 1
    assert [row["content"] for row in older["messages"] + tail["messages"]] == [
        f"message-{index}" for index in range(199)
    ]
