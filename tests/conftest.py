import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="elbrus-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["LLM_MODE"] = "stub"
os.environ["DATA_DIR"] = "data_synthetic"
os.environ["SEED_ON_START"] = "false"
os.environ["BACKGROUND_JOBS"] = "false"
os.environ["RSS_ENABLED"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from llm_stub import transport  # noqa: E402

from app.llm.client import get_llm  # noqa: E402
from app.seed import seed  # noqa: E402

get_llm().transport = transport


@pytest.fixture(scope="session", autouse=True)
def seeded():
    return seed(reset=True, warm=True)


@pytest.fixture(scope="session")
def client(seeded):
    from app.main import app

    with TestClient(app) as c:
        yield c


def login(client, user: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"login": user})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def cfo_a(client):
    return login(client, "cfo_a")


@pytest.fixture()
def ceo_b(client):
    return login(client, "ceo_b")
