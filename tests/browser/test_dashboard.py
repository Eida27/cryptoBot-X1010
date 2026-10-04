import socket
import threading
import time
from decimal import Decimal as D
from pathlib import Path

import pytest
import uvicorn
from argon2 import PasswordHasher
from playwright.sync_api import expect, sync_playwright

from crypto_bot.config import Settings
from crypto_bot.domain.enums import PositionPhase, PositionSide
from crypto_bot.domain.models import Position
from crypto_bot.web.routes import create_app


@pytest.mark.parametrize("mode", ["PAPER", "DEMO", "LIVE"])
@pytest.mark.parametrize("width", [1440, 390])
def test_dashboard_real_browser(repo, mode, width):
    repo.db.connection.execute("UPDATE runs SET mode=?", (mode,))
    repo.save_position(
        Position(
            "SOLUSDT",
            PositionSide.LONG,
            D("0.1"),
            D("100"),
            D("1"),
            1,
            D("98"),
            D("104"),
            D("60"),
            PositionPhase.OPEN,
            "owned",
        )
    )
    app = create_app(Settings(password_hash=PasswordHasher().hash("browser password")), repo)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": 1000})
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            page.goto(f"http://127.0.0.1:{port}/")
            page.get_by_label("Password", exact=True).fill("browser password")
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.get_by_role("heading", name="Account overview").wait_for()
            assert page.locator(".mode").inner_text() == mode
            assert "PHP 100" in page.inner_text("main")
            assert "OPEN" in page.locator("[data-position]").inner_text()
            assert page.locator("[data-state]").inner_text() == "PAUSED"
            page.get_by_role("button", name="Pause new entries", exact=True).click()
            expect(page.locator("#command-result")).to_contain_text("PENDING")
            assert repo.pending_commands()[0].action == "pause"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            Path("artifacts/browser").mkdir(parents=True, exist_ok=True)
            page.screenshot(path=f"artifacts/browser/{mode.lower()}-{width}.png", full_page=True)
            page.get_by_role("link", name="Diagnostics", exact=True).click()
            assert "No completed eligible signal" in page.inner_text("main")
            if mode == "PAPER" and width == 390:
                repo.db.connection.execute("UPDATE web_sessions SET last_seen_ms=0")
                page.evaluate("poll()")
                page.locator("#session-warning").wait_for(state="visible")
            assert not errors
            assert all(url.startswith(f"http://127.0.0.1:{port}/") for url in requests)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive()
