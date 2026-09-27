"""API validation and security-sensitive file handling."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.services import storage
from app.services.storage import UploadValidationError, detect_type, path_for_key, sanitize_filename


def png_bytes(size: tuple[int, int] = (40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


# --- storage primitives ---------------------------------------------------------

@pytest.mark.parametrize("key", ["../etc/passwd", "..\\..\\win.ini", "/abs/path.pdf", "abc.pdf",
                                 "0123456789abcdef0123456789abcdef.exe", "0123456789abcdef0123456789abcdef.pdf/../x"])
def test_path_for_key_rejects_traversal_and_bad_keys(key: str) -> None:
    with pytest.raises(ValueError):
        path_for_key(key)


def test_path_for_key_stays_inside_root(storage_dir: Path) -> None:
    p = path_for_key("0123456789abcdef0123456789abcdef.pdf")
    assert p.parent == storage_dir.resolve()


@pytest.mark.parametrize(("name", "expected"), [
    ("../../etc/passwd", "passwd"),
    ("C:\\Windows\\system32\\evil.pdf", "evil.pdf"),
    ("statement<script>.pdf", "statement_script_.pdf"),
    ("", "statement"),
    ("\x00\x01.pdf", "pdf"),
])
def test_sanitize_filename(name: str, expected: str) -> None:
    assert sanitize_filename(name) == expected


def test_detect_type_uses_magic_bytes_not_extension() -> None:
    assert detect_type(b"%PDF-1.7 ...").kind == "pdf"  # type: ignore[union-attr]
    assert detect_type(png_bytes()).mime == "image/png"  # type: ignore[union-attr]
    assert detect_type(b"MZ\x90\x00 windows exe") is None
    assert detect_type(b"<html><script>alert(1)</script>") is None


def test_store_upload_rejects_empty_oversize_and_fake_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(UploadValidationError, match="empty"):
        storage.store_upload(io.BytesIO(b""))
    with pytest.raises(UploadValidationError, match="could not be opened"):
        storage.store_upload(io.BytesIO(b"%PDF-1.4 this is not really a pdf"))
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    with pytest.raises(UploadValidationError, match="larger than"):
        storage.store_upload(io.BytesIO(b"%PDF-" + b"0" * (2 * 1024 * 1024)))


def test_store_upload_rejects_decompression_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "max_image_pixels", 1000)
    with pytest.raises(UploadValidationError, match="resolution"):
        storage.store_upload(io.BytesIO(png_bytes((100, 100))))


# --- API --------------------------------------------------------------------

def test_requests_without_session_are_rejected() -> None:
    from app.main import app

    with TestClient(app) as anon:
        assert anon.get("/api/statements").status_code == 401


def test_csrf_header_required_for_mutations(client: TestClient) -> None:
    r = client.post("/api/statements/demo", headers={"x-spendlens-client": ""})
    assert r.status_code == 403


def test_upload_rejects_unsupported_type(client: TestClient) -> None:
    r = client.post("/api/statements", files={"file": ("evil.pdf", b"#!/bin/sh\nrm -rf /", "application/pdf")})
    assert r.status_code == 422 and "Unsupported file type" in r.json()["detail"]


def test_upload_image_stores_under_random_key(client: TestClient, storage_dir: Path) -> None:
    r = client.post("/api/statements", files={"file": ("../../my statement.png", png_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["display_name"] == "my statement.png"
    assert body["document"]["kind"] == "image"
    assert "storage_key" not in r.text and str(storage_dir) not in r.text
    files = list(storage_dir.glob("*"))
    assert len(files) == 1 and files[0].name.endswith(".png") and "statement" not in files[0].name


def test_other_sessions_cannot_access_statement(client: TestClient, other_client: TestClient) -> None:
    sid = client.post("/api/statements", files={"file": ("a.png", png_bytes(), "image/png")}).json()["id"]
    for path in (f"/api/statements/{sid}", f"/api/statements/{sid}/file", f"/api/statements/{sid}/events",
                 f"/api/statements/{sid}/pages"):
        assert other_client.get(path).status_code == 404
    assert other_client.delete(f"/api/statements/{sid}").status_code == 404
    assert client.get(f"/api/statements/{sid}").status_code == 200


def test_file_endpoint_security_headers(client: TestClient) -> None:
    sid = client.post("/api/statements", files={"file": ("a.png", png_bytes(), "image/png")}).json()["id"]
    r = client.get(f"/api/statements/{sid}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert "no-store" in r.headers["cache-control"]


def test_delete_removes_file_and_rows(client: TestClient, storage_dir: Path) -> None:
    sid = client.post("/api/statements", files={"file": ("a.png", png_bytes(), "image/png")}).json()["id"]
    assert len(list(storage_dir.glob("*"))) == 1
    assert client.delete(f"/api/statements/{sid}").status_code == 204
    assert list(storage_dir.glob("*")) == []
    assert client.get(f"/api/statements/{sid}").status_code == 404
    actions = [e["action"] for e in client.get("/api/session/audit").json()]
    assert actions[:2] == ["statement.delete", "statement.upload"]


def test_delete_all_data_and_retention(client: TestClient, storage_dir: Path) -> None:
    for _ in range(2):
        client.post("/api/statements", files={"file": ("a.png", png_bytes(), "image/png")})
    assert client.put("/api/session/retention", json={"days": 3}).status_code == 422
    assert client.put("/api/session/retention", json={"days": 30}).json()["retention_days"] == 30
    assert client.delete("/api/session/data").json() == {"deleted_statements": 2}
    assert client.get("/api/statements").json() == [] and list(storage_dir.glob("*")) == []


def test_retention_purge_deletes_expired(client: TestClient, storage_dir: Path) -> None:
    from datetime import timedelta

    from app.db.session import db_session
    from app.models import Statement, utcnow
    from app.services.statements import purge_expired

    sid = client.post("/api/statements", files={"file": ("a.png", png_bytes(), "image/png")}).json()["id"]
    with db_session() as db:
        db.get(Statement, __import__("uuid").UUID(sid)).expires_at = utcnow() - timedelta(minutes=1)  # type: ignore[union-attr]
    with db_session() as db:
        assert purge_expired(db) == 1
    assert list(storage_dir.glob("*")) == []


def test_transaction_query_validation(client: TestClient) -> None:
    assert client.get("/api/transactions?min_amount=-5").status_code == 422
    assert client.get("/api/transactions?min_amount=10&max_amount=5").status_code == 422
    assert client.get("/api/transactions?currency=DOLLARS").status_code == 422
    assert client.get("/api/transactions?sort=password").status_code == 422
    assert client.get("/api/transactions?limit=100000").status_code == 422
    ok = client.get("/api/transactions?search=%25_%5C")  # LIKE wildcards are escaped, not interpreted
    assert ok.status_code == 200 and ok.json()["total"] == 0
