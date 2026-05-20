import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 确保项目根在 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Base


@pytest.fixture(scope="session")
def engine():
    return create_engine("sqlite:///:memory:")


@pytest.fixture(scope="session")
def tables(engine):
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db(engine, tables):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()
