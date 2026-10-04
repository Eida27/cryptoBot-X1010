from decimal import Decimal

import pytest

from crypto_bot.domain.clock import FakeClock
from crypto_bot.domain.enums import Mode
from crypto_bot.storage.database import Database
from crypto_bot.storage.repository import Repository


@pytest.fixture
def clock():
    return FakeClock(3600001)


@pytest.fixture
def repo(tmp_path):
    result = Repository(Database(tmp_path / "paper.sqlite3"))
    result.create_run(Mode.PAPER, Decimal("20"), {"config": "test"}, 0)
    yield result
    result.db.close()
