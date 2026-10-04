import re

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from crypto_bot.config import Settings


@pytest.fixture
def client(repo):
    from crypto_bot.web.routes import create_app

    settings = Settings(password_hash=PasswordHasher().hash("test password"))
    with TestClient(create_app(settings, repo), base_url="http://127.0.0.1:8000") as session:
        yield session


def login(client):
    page = client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/login",
        data={"password": "test password", "csrf": csrf},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert response.status_code == 200
    return re.search(r'name="csrf" value="([^"]+)"', response.text).group(1)


def test_sessions_host_origin_and_csrf_guard_controls(client, repo):
    assert client.get("/api/state").status_code == 401
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    csrf = login(client)
    assert (
        client.post(
            "/commands/pause", data={"csrf": csrf}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/commands/pause", data={}, headers={"Origin": "http://127.0.0.1:8000"}
        ).status_code
        == 403
    )
    assert client.get("/commands/pause").status_code == 405
    request_id = "a" * 32
    for _ in range(2):
        assert (
            client.post(
                "/commands/pause",
                data={"csrf": csrf, "request_id": request_id},
                headers={"Origin": "http://127.0.0.1:8000"},
            ).status_code
            == 202
        )
    assert len(repo.pending_commands()) == 1
    assert (
        client.post(
            "/commands/close-and-pause",
            data={"csrf": csrf, "request_id": "b" * 32},
            headers={"Origin": "http://127.0.0.1:8000"},
        ).status_code
        == 202
    )
    assert [c.action for c in repo.pending_commands()] == ["pause", "close-and-pause"]
    cookie = client.cookies.get("cbot_session")
    assert cookie and cookie not in str(
        repo.db.connection.execute("SELECT * FROM web_sessions").fetchall()
    )


def test_expiry_redaction_and_escape(client, repo):
    login(client)
    repo.db.connection.execute(
        "INSERT INTO audit_events(run_id,at_ms,kind,payload) VALUES(?,1,'VENUE',?)",
        (repo.current_trial().run_id, '{"message":"<script>alert(1)</script>","api_key":"hidden"}'),
    )
    assert "hidden" not in client.get("/api/state").text
    page = client.get("/diagnostics")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text
    repo.db.connection.execute("UPDATE web_sessions SET last_seen_ms=0")
    assert client.get("/api/state").status_code == 401


def test_login_throttle_and_cookie_policy(client):
    page = client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    for _ in range(5):
        assert (
            client.post(
                "/login",
                data={"password": "wrong", "csrf": csrf},
                headers={"Origin": "http://127.0.0.1:8000"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/login",
            data={"password": "wrong", "csrf": csrf},
            headers={"Origin": "http://127.0.0.1:8000"},
        ).status_code
        == 429
    )


def test_loss_halt_stays_latched_when_resume_is_queued(client, repo):
    csrf = login(client)
    repo.latch_halt(repo.current_trial().run_id, "TRIAL_LOSS")
    client.post(
        "/commands/resume",
        data={"csrf": csrf, "request_id": "c" * 32},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert repo.current_trial().halt_reason == "TRIAL_LOSS"
    assert "TRIAL_LOSS" in client.get("/api/state").text


def test_background_polling_does_not_extend_idle_session(client, repo):
    login(client)
    repo.db.connection.execute("UPDATE web_sessions SET last_seen_ms=last_seen_ms-10000")
    before = repo.db.connection.execute("SELECT last_seen_ms FROM web_sessions").fetchone()[0]
    assert client.get("/api/state").status_code == 200
    assert (
        repo.db.connection.execute("SELECT last_seen_ms FROM web_sessions").fetchone()[0] == before
    )
