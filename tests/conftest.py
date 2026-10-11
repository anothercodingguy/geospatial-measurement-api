import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        upload_dir=tmp_path / "uploads",
        max_upload_bytes=2 * 1024 * 1024,
        max_uncompressed_bytes=10 * 1024 * 1024,
    )


@pytest.fixture
def client(settings) -> TestClient:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def upload(client):
    """POST a file and return the response."""

    def _upload(filename: str, data: bytes):
        return client.post("/api/files/", files={"file": (filename, data)})

    return _upload
