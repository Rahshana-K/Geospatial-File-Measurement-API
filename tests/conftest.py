import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", max_upload_mb=1, max_unzipped_mb=5, max_zip_members=20)


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))
