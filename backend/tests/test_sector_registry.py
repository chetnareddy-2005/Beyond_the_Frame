from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend import sector_registry
from backend.headquarters_sectors import load_headquarters_sectors, headquarters_database



def _mock_document_keys(monkeypatch):
    monkeypatch.setattr(sector_registry, "_fernet", lambda: (object(), "test-fernet-key"))

def _request(root, status, expires_at, filename="demo.enc", sector="S1"):
    load_headquarters_sectors(root)
    initialized = sector_registry._db(root)
    initialized.close()
    storage = sector_registry._storage(root)
    storage.mkdir(parents=True, exist_ok=True)
    path = storage / filename
    path.write_bytes(b"ciphertext")
    with sqlite3.connect(headquarters_database(root)) as connection:
        connection.execute(
            """INSERT INTO sector_registry_requests
               (request_id,sector_id,status,head_phone,head_email,sector_address,base_camp_lat,base_camp_lon,
                resolved_address_string,location_capture_method,submitted_at,assigned_admin_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (filename, sector, status, "+15555550123", "head@example.test", "Test address", 1.0, 2.0,
             "Test address", "MANUAL_MAP_CLICK", "2026-01-01T00:00:00Z", 1),
        )
        connection.execute(
            """INSERT INTO registry_documents
               (request_id,encrypted_path,wrapped_key,content_type,submitted_at,expires_at)
               VALUES(?,?,?,?,?,?)""",
            (filename, str(path), b"wrapped", "image/png", "2026-01-01T00:00:00Z", expires_at),
        )
    return path


def test_work_id_upload_fails_closed_without_encryption_key(tmp_path, monkeypatch):
    monkeypatch.delenv("BTF_REGISTRY_ENCRYPTION_KEY", raising=False)
    with pytest.raises(sector_registry.RegistryError) as caught:
        sector_registry.submit_request(tmp_path, {}, 1, "id.png", "image/png", "eA==")
    assert caught.value.status == 503
    assert caught.value.code == "encryption_unconfigured"


def test_retention_purge_removes_final_and_expired_documents(tmp_path, monkeypatch):
    monkeypatch.setenv("BTF_PRIVATE_STORAGE_ROOT", str(tmp_path.parent / "private"))
    old_expiry = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    expired = _request(tmp_path, "pending", old_expiry, "expired.enc")
    decided = _request(tmp_path, "approved", "2027-01-01T00:00:00Z", "decided.enc", "S2")
    fresh = _request(tmp_path, "pending", "2027-01-01T00:00:00Z", "fresh.enc", "S3")
    count = sector_registry.purge_documents(tmp_path)
    assert count == 2
    assert not expired.exists()
    assert not decided.exists()
    assert fresh.exists()
    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        rows = dict(connection.execute("SELECT request_id,purged_at FROM registry_documents"))
        assert rows["expired.enc"] is not None
        assert rows["decided.enc"] is not None
        assert rows["fresh.enc"] is None


def test_registration_requires_exactly_one_head_and_two_technical_members():
    payload = {
        "sector_id": "S1", "head_email": "head@example.test",
        "head_phone": "+919876543210",
        "sector_address": "Test sector, Example District",
        "members": [
            {"role": "head", "full_name": "Head", "member_id": "1234567", "rank": "Officer"},
            {"role": "technical", "full_name": "Tech 1", "member_id": "2345678", "rank": "Tech"},
            {"role": "technical", "full_name": "Tech 2", "member_id": "3456789", "rank": "Tech"},
        ],
    }
    sector, email, phone, address, members = sector_registry._validated(payload)
    assert (sector, email, phone, address, len(members)) == ("S1", "head@example.test", "+919876543210", "Test sector, Example District", 3)
    payload["members"][2]["role"] = "head"
    with pytest.raises(sector_registry.RegistryError):
        sector_registry._validated(payload)

def test_login_sector_list_contains_only_active_hq_approved_sectors(tmp_path):
    load_headquarters_sectors(tmp_path)
    connection = sector_registry._db(tmp_path)
    sector_ids = [row[0] for row in connection.execute(
        "SELECT sector_id FROM headquarters_sectors ORDER BY row_index,column_index LIMIT 3"
    ).fetchall()]
    approved_id, pending_id, inactive_id = sector_ids
    common = ("", 0.0, 0.0, "Test address", "MANUAL_MAP_CLICK", "2026-01-01T00:00:00Z", 1)
    connection.execute(
        """INSERT INTO sector_registry_requests
           (request_id,sector_id,status,head_phone,head_email,base_camp_lat,base_camp_lon,
            resolved_address_string,location_capture_method,submitted_at,assigned_admin_id)
           VALUES(?,?,?,'',?,?,?,?,?,?,?)""",
        ("approved-request", approved_id, "approved", *common),
    )
    connection.execute(
        """INSERT INTO sector_registry_requests
           (request_id,sector_id,status,head_phone,head_email,base_camp_lat,base_camp_lon,
            resolved_address_string,location_capture_method,submitted_at,assigned_admin_id)
           VALUES(?,?,?,'',?,?,?,?,?,?,?)""",
        ("pending-request", pending_id, "pending", *common),
    )
    connection.execute(
        """INSERT INTO sector_registry_requests
           (request_id,sector_id,status,head_phone,head_email,base_camp_lat,base_camp_lon,
            resolved_address_string,location_capture_method,submitted_at,assigned_admin_id,is_active)
           VALUES(?,?,?,'',?,?,?,?,?,?,?,0)""",
        ("inactive-request", inactive_id, "approved", *common),
    )
    connection.execute("UPDATE headquarters_sectors SET is_active=0 WHERE sector_id=?", (inactive_id,))
    connection.commit()
    connection.close()

    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        approved_name = connection.execute(
            "SELECT name FROM headquarters_sectors WHERE sector_id=?", (approved_id,)
        ).fetchone()[0]
    assert sector_registry.list_active_approved_sectors(tmp_path) == [
        {"id": approved_id, "name": approved_name}
    ]

def test_head_initiated_reset_goes_to_hq_and_emails_new_one_time_password(tmp_path, monkeypatch):
    from backend.auth import AuthManager

    monkeypatch.setenv("BTF_PRIVATE_STORAGE_ROOT", str(tmp_path.parent / "private"))
    monkeypatch.delenv("BTF_SMTP_HOST", raising=False)
    request_id = "REG_" + "a" * 32
    _request(tmp_path, "approved", "2027-01-01T00:00:00Z", request_id + ".enc")
    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        connection.execute("UPDATE sector_registry_requests SET request_id=?,head_email=? WHERE request_id=?", (request_id, "head@example.com", request_id + ".enc"))
        connection.execute("UPDATE registry_documents SET request_id=? WHERE request_id=?", (request_id, request_id + ".enc"))
        connection.execute("INSERT INTO sector_registry_members(request_id,member_role,member_index,full_name,member_id,rank) VALUES(?,?,?,?,?,?)", (request_id, "head", 0, "Registered Head", "HEAD-01", "Officer"))
    auth = AuthManager(tmp_path / "auth.sqlite3", bootstrap_username="hq", bootstrap_password="a-secure-admin-password")
    account = auth.create_sector_account("S1", username="head@example.com")
    sector_user_id = account["user_id"]
    assert auth.authenticate("head@example.com", account["temporary_password"], "S1", "sector")
    token = auth.create_session(sector_user_id, "head@example.com")
    request = sector_registry.request_reset(tmp_path, request_id, "S1", sector_user_id, "head-01")
    assert request["status"] == "pending_hq_review"
    result = sector_registry.decide_reset(tmp_path, auth, request["reset_id"], 1, True)
    assert result["status"] == "completed"
    assert auth.get_session(token) is None
    assert auth.account_profile(sector_user_id)["must_change_password"] is True
    assert result["account"]["username"] == "head@example.com"

def test_hq_approval_creates_email_login_and_uses_local_invitation_fallback(tmp_path, monkeypatch):
    from backend.auth import AuthManager

    monkeypatch.setenv("BTF_PRIVATE_STORAGE_ROOT", str(tmp_path.parent / "private"))
    monkeypatch.delenv("BTF_SMTP_HOST", raising=False)
    _mock_document_keys(monkeypatch)
    monkeypatch.setenv("BTF_REGISTRY_AUDIT_KEY", "test-audit-key-material-that-is-at-least-32-bytes")
    load_headquarters_sectors(tmp_path)
    initialized = sector_registry._db(tmp_path)
    initialized.close()
    request_id = "REG_" + "b" * 32
    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        connection.execute(
            """INSERT INTO sector_registry_requests
               (request_id,sector_id,status,head_phone,head_email,sector_address,base_camp_lat,base_camp_lon,
                resolved_address_string,location_capture_method,submitted_at,assigned_admin_id)
               VALUES(?,?,'pending','','head@example.com','Test address',1,2,'Test address','MANUAL_MAP_CLICK',?,?)""",
            (request_id, "S1", "2026-10-24T00:00:00Z", 1),
        )
    auth = AuthManager(tmp_path / "auth.sqlite3", bootstrap_username="hq", bootstrap_password="a-secure-admin-password")
    result = sector_registry.decide_request(tmp_path, auth, request_id, 1, "approved", geometry=[[17.37,78.48],[17.37,78.49],[17.38,78.49],[17.38,78.48]])
    assert result["decision"] == "approved"
    assert result["account"]["created"] is True
    assert result["account"]["username"] == "head@example.com"
    assert auth.authenticate("HEAD@example.com", result["account"]["temporary_password"], "S1", "sector")
    assert auth.account_profile(result["account"]["user_id"])["must_change_password"] is True
    assert auth.change_password(result["account"]["user_id"], result["account"]["temporary_password"], "North-2026!")
    assert auth.authenticate("head@example.com", "North-2026!", "S1", "sector")
    assert result["delivery"]["mode"] == "unconfigured"
    assert result["delivery"]["sent"] is False
    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        assert connection.execute("SELECT status FROM sector_registry_requests WHERE request_id=?", (request_id,)).fetchone()[0] == "approved"


def test_approval_does_not_finalize_when_sector_account_already_exists(tmp_path, monkeypatch):
    from backend.auth import AuthManager

    _mock_document_keys(monkeypatch)
    monkeypatch.setenv("BTF_REGISTRY_AUDIT_KEY", "test-audit-key-material-that-is-at-least-32-bytes")
    load_headquarters_sectors(tmp_path)
    initialized = sector_registry._db(tmp_path)
    initialized.close()
    request_id = "REG_" + "c" * 32
    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        connection.execute(
            """INSERT INTO sector_registry_requests
               (request_id,sector_id,status,head_phone,head_email,sector_address,base_camp_lat,base_camp_lon,
                resolved_address_string,location_capture_method,submitted_at,assigned_admin_id)
               VALUES(?,?,'pending','','head@example.com','Test address',1,2,'Test address','MANUAL_MAP_CLICK',?,?)""",
            (request_id, "S1", "2026-10-24T00:00:00Z", 1),
        )
    auth = AuthManager(tmp_path / "auth.sqlite3", bootstrap_username="hq", bootstrap_password="a-secure-admin-password")
    auth.create_sector_account("S1")
    with pytest.raises(sector_registry.RegistryError) as caught:
        sector_registry.decide_request(tmp_path, auth, request_id, 1, "approved", geometry=[[17.37,78.48],[17.37,78.49],[17.38,78.49],[17.38,78.48]])
    assert caught.value.code == "sector_account_exists"
    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        assert connection.execute("SELECT status FROM sector_registry_requests WHERE request_id=?", (request_id,)).fetchone()[0] == "pending"



def test_document_queue_fails_closed_without_audit_key(tmp_path, monkeypatch):
    _mock_document_keys(monkeypatch)
    monkeypatch.delenv("BTF_REGISTRY_AUDIT_KEY", raising=False)
    with pytest.raises(sector_registry.RegistryError) as caught:
        sector_registry.list_requests(tmp_path, 1)
    assert caught.value.status == 503
    assert caught.value.code == "audit_key_unconfigured"


def test_email_sender_fails_closed_without_smtp_configuration(monkeypatch):
    for key in ("BTF_SMTP_HOST", "BTF_SMTP_USERNAME", "BTF_SMTP_PASSWORD", "BTF_SMTP_FROM"):
        monkeypatch.delenv(key, raising=False)
    result = sector_registry.send_email("head@example.com", "Invite", "body")
    assert result == {"mode": "unconfigured", "sent": False}


def test_email_sender_requires_explicit_sender_address(monkeypatch):
    import smtplib
    monkeypatch.setenv("BTF_SMTP_HOST", "mail.example.test")
    monkeypatch.setenv("BTF_SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("BTF_SMTP_PASSWORD", "local-test-secret")
    monkeypatch.delenv("BTF_SMTP_FROM", raising=False)

    class UnexpectedSMTP:
        def __init__(self, *args, **kwargs):
            raise AssertionError("SMTP must not be opened without an explicit sender")

    monkeypatch.setattr(smtplib, "SMTP", UnexpectedSMTP)
    result = sector_registry.send_email("head@example.test", "Invite", "body")
    assert result == {"mode": "unconfigured", "sent": False}


def test_manual_sector_name_email_registration_and_three_member_documents_end_to_end(tmp_path, monkeypatch):
    import base64
    class FakeFernet:
        def __init__(self, key):
            self.key = key
        @staticmethod
        def generate_key():
            return b"per-document-test-key"
        def encrypt(self, value):
            return b"encrypted:" + value
        def decrypt(self, value):
            if not value.startswith(b"encrypted:"):
                raise ValueError("invalid test ciphertext")
            return value[len(b"encrypted:"):]

    monkeypatch.setattr(sector_registry, "_fernet", lambda: (FakeFernet, "test-master-key"))
    monkeypatch.setenv("BTF_REGISTRY_AUDIT_KEY", "test-audit-key-material-that-is-at-least-32-bytes")
    monkeypatch.setenv("BTF_PRIVATE_STORAGE_ROOT", str(tmp_path.parent / "private"))
    load_headquarters_sectors(tmp_path)
    assert sector_registry._resolve_sector_id(tmp_path, "sEcToR 1") == "S1"

    documents = {
        key: {"filename": key + ".png", "content_type": "image/png", "document_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + key.encode()).decode()}
        for key in ("head", "technical1", "technical2")
    }
    result = sector_registry.submit_request(tmp_path, {
        "sector_id": "Sector 1 (S1)",
        "head_email": "head@example.test",
        "head_phone": "+919876543210",
        "sector_address": "Test sector, Example District",
        "base_camp_lat": 17.37,
        "base_camp_lon": 78.48,
        "location_capture_method": "MANUAL_MAP_CLICK",
        "members": [
            {"role": "head", "full_name": "Head One", "member_id": "1234567", "rank": "Officer"},
            {"role": "technical", "full_name": "Tech One", "member_id": "2345678", "rank": "Technician"},
            {"role": "technical", "full_name": "Tech Two", "member_id": "3456789", "rank": "Technician"},
        ],
        "documents": documents,
    }, 1, None, None, None)
    assert result["sector_id"] == "S1"
    request = sector_registry.list_requests(tmp_path, 1)[0]
    assert request["documents"] == {"head": True, "technical1": True, "technical2": True}
    for key in ("head", "technical1", "technical2"):
        contents, content_type = sector_registry.get_work_id(tmp_path, result["request_id"], 1, key)
        assert contents == b"\x89PNG\r\n\x1a\n" + key.encode()
        assert content_type == "image/png"
    with sqlite3.connect(headquarters_database(tmp_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM registry_audit_log WHERE request_id=?", (result["request_id"],)).fetchone()[0] == 3


def test_work_id_signature_must_match_declared_type():
    valid_png = b"\x89PNG\r\n\x1a\n" + b"test"
    valid_jpeg = b"\xff\xd8\xff" + b"test"
    valid_pdf = b"%PDF-1.7\n" + b"test"
    assert sector_registry._document_signature_matches("image/png", valid_png)
    assert sector_registry._document_signature_matches("image/jpeg", valid_jpeg)
    assert sector_registry._document_signature_matches("application/pdf", valid_pdf)
    assert not sector_registry._document_signature_matches("image/png", b"not a PNG")
    assert not sector_registry._document_signature_matches("application/pdf", valid_png)

def test_manual_sector_name_must_match_hq_configuration(tmp_path):
    load_headquarters_sectors(tmp_path)
    with pytest.raises(sector_registry.RegistryError) as caught:
        sector_registry._resolve_sector_id(tmp_path, "Unconfigured Sector")
    assert caught.value.code == "unknown_sector"


def test_invitation_email_uses_configured_smtp_without_logging_password(monkeypatch):
    import smtplib
    monkeypatch.setenv("BTF_SMTP_HOST", "mail.example.test")
    monkeypatch.setenv("BTF_SMTP_PORT", "587")
    monkeypatch.setenv("BTF_SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("BTF_SMTP_PASSWORD", "local-test-secret")
    monkeypatch.setenv("BTF_SMTP_FROM", "Beyond the Frame <sender@example.test>")
    captured = {}
    class FakeSMTP:
        def __init__(self, host, port, timeout): captured["connection"]=(host,port,timeout)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self): captured["tls"] = True
        def login(self, username, password): captured["login"]=(username,password)
        def send_message(self, message): captured["message"]=message
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    result=sector_registry.send_invitation_email("head@example.com", {"sector_number":"S1","username":"head@example.com","temporary_password":"one-time-secret"})
    assert result == {"mode":"smtp","sent":True}
    assert captured["message"]["To"] == "head@example.com"
    assert "one-time-secret" in captured["message"].get_content()
    assert captured["tls"] is True
