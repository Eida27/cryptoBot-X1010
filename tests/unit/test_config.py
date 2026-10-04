from decimal import Decimal
from pathlib import Path

import pytest


def test_paper_is_default_and_live_is_disabled():
    from crypto_bot.config import Settings
    from crypto_bot.domain.enums import Mode

    settings = Settings()
    assert settings.mode is Mode.PAPER
    assert not settings.live_trading_enabled
    assert not settings.entries_enabled


@pytest.mark.parametrize(
    "changes",
    [
        {"leverage": "3"},
        {"initial_capital_usdt": "0"},
        {"initial_capital_usdt": "NaN"},
        {"initial_capital_usdt": "Infinity"},
        {"database": "state/shared.sqlite3"},
        {"rest_url": "https://evil.example"},
        {"entries_enabled": True},
        {"mode": "LIVE", "host_profile": "local"},
        {"initial_capital_usdt": None},
        {"symbols": ["DOGEUSDT"]},
    ],
)
def test_unsafe_configuration_is_rejected(changes):
    from crypto_bot.config import ConfigurationError, Settings, validate_mode

    with pytest.raises(ConfigurationError):
        validate_mode(Settings(**changes))


def test_secrets_are_redacted_and_environment_is_explicit(tmp_path):
    from crypto_bot.config import load_settings

    config = tmp_path / "paper.toml"
    config.write_text('initial_capital_usdt = "16.00"\n')
    settings = load_settings(config, {"CBOT_API_SECRET": "NEVER_SHOW_THIS"})
    assert "NEVER_SHOW_THIS" not in repr(settings)
    assert "NEVER_SHOW_THIS" not in str(settings.safe_dict())
    assert settings.database.name == "paper.sqlite3"
    assert settings.initial_capital_usdt == Decimal("16.00")


def test_mode_databases_cannot_be_shared():
    from crypto_bot.config import ConfigurationError, Settings, validate_mode

    with pytest.raises(ConfigurationError):
        validate_mode(Settings(mode="DEMO", database=Path("state/paper.sqlite3")))
