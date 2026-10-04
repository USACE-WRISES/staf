import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    import fixture
    return fixture.build(tmp_path_factory.mktemp("hrslim"))


@pytest.fixture(scope="session")
def dataset(data_dir):
    from hrslim import Dataset
    return Dataset(data_dir)


@pytest.fixture(scope="session")
def data_dir2(tmp_path_factory):
    import fixture2
    return fixture2.build(tmp_path_factory.mktemp("hrslim2"))


@pytest.fixture(scope="session")
def dataset2(data_dir2):
    from hrslim import Dataset
    return Dataset(data_dir2)
