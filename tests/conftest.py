from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from schemagate.app import create_app
from schemagate.loader import CaseLoader
from schemagate.runner import build_result

CASES_DIR = ROOT / "fixtures" / "cases"
INVALID_DIR = ROOT / "fixtures" / "invalid"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def cases_dir() -> Path:
    return CASES_DIR


@pytest.fixture(scope="session")
def invalid_dir() -> Path:
    return INVALID_DIR


@pytest.fixture(scope="session")
def loader() -> CaseLoader:
    return CaseLoader(CASES_DIR)


@pytest.fixture(scope="session")
def events_case(loader: CaseLoader):
    return loader.load_case("lake_events")


@pytest.fixture(scope="session")
def orders_case(loader: CaseLoader):
    return loader.load_case("lake_orders")


@pytest.fixture(scope="session")
def events_result(events_case):
    return build_result(events_case)


@pytest.fixture(scope="session")
def orders_result(orders_case):
    return build_result(orders_case)


@pytest.fixture
def copied_case(tmp_path: Path):
    fixtures = tmp_path / "cases"
    fixtures.mkdir()
    def copy(case_id: str) -> tuple[Path, Path]:
        destination = fixtures / case_id
        shutil.copytree(CASES_DIR / case_id, destination)
        return fixtures, destination
    return copy


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        fixtures_dir=CASES_DIR,
        db_path=tmp_path / "ledger.db",
        reports_dir=tmp_path / "reports",
        testing=True,
    )


@pytest.fixture
def client(app):
    return app.test_client()
