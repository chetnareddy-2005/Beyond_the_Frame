from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import pytest

from backend.auth import AuthManager
from backend import sector_registry
from backend.headquarters_sectors import headquarters_database, load_headquarters_sectors


class FakeFernet:
    def __init__(self, key):
        self.key = key

    @staticmethod
    def generate_key():
        return b"test-document-key"

    def encrypt(self, value):
        return b"encrypted:" + value

    def decrypt(self, value):
        if not value.startswith(b"encrypted:"):
            raise ValueError("invalid test ciphertext")
        return value[len(b"encrypted:"):]


def _approved_roster(tmp_path, monkeypatch):
    monkeypatch.setenv("BTF_PRIVATE_STORAGE_ROOT", str(tmp_path.parent / f"{tmp_path.name}-private"))
    monkeypatch.setenv("BTF_REGISTRY_ENCRYPTION_KEY", "test-master-key")
    monkeypatch.setenv("BTF_REGISTRY_AUDIT_KEY", "test-audit-key-with-more-than-32-random-bytes")
    monkeypatch.setattr(sector_registry, "_fernet", lambda: (FakeFernet, "test-master-key"))
    load_headquarters_sectors(tmp_path)
    connection = sector_registry._db(tmp_path)
    sector_id = connection.execute("SELECT sector_id FROM headquarters_sectors ORDER BY row_index,column_index LIMIT 1").fetchone()[0]
    request_id = "REG_" + "a" * 32
    now = datetime.now(timezone.utc)
    submitted = (now - timedelta(days=5)).isoformat().replace("+00:00", "Z")
    decided = (now - timedelta(days=4)).isoformat().replace("+00:00", "Z")
    connection.execute("""INSERT INTO sector_registry_requests
        (request_id,sector_id,status,head_phone,head_email,sector_address,base_camp_lat,base_camp_lon,
        resolved_address_string,location_capture_method,submitted_at,decided_at,assigned_admin_id,decided_by)
        VALUES(?,?,'approved','+15555550123','head@example.test','Test address',1,2,
        'Test address','MANUAL_MAP_CLICK',?,?,5,5)""", (request_id, sector_id, submitted, decided))
    connection.executemany("""INSERT INTO sector_registry_members
        (request_id,member_role,member_index,full_name,member_id,rank) VALUES(?,?,?,?,?,?)""", [
        (request_id, "head", 0, "Old Head", "1234567", "Captain"),
        (request_id, "technical", 1, "Tech One", "2345678", "Engineer"),
        (request_id, "technical", 2, "Tech Two", "3456789", "Engineer"),
    ])
    storage = sector_registry._storage(tmp_path)
    storage.mkdir(parents=True, exist_ok=True)
    old_path = storage / "old-head.workid.enc"
    old_key = FakeFernet.generate_key()
    old_bytes = b"%PDF-1.7 old encrypted document"
    old_path.write_bytes(FakeFernet(old_key).encrypt(old_bytes))
    master = FakeFernet(os.environ["BTF_REGISTRY_ENCRYPTION_KEY"].encode("ascii"))
    wrapped = master.encrypt(old_key)
    expiry = (now + timedelta(days=20)).isoformat().replace("+00:00", "Z")
    connection.execute("""INSERT INTO registry_documents
        (request_id,encrypted_path,wrapped_key,content_type,submitted_at,expires_at)
        VALUES(?,?,?,?,?,?)""", (request_id, str(old_path), wrapped, "application/pdf", submitted, expiry))
    connection.commit()
    connection.close()
    auth = AuthManager(tmp_path / "auth.sqlite3", bootstrap_username="hq", bootstrap_password="secure-admin-password")
    account = auth.create_sector_account(sector_id, username="shared-login@example.test")
    return sector_id, request_id, auth, account, old_bytes


def _payload(role="head", member_index=0, member_id="7654321", document_bytes=b"\x89PNG\r\n\x1a\nreplacement"):
    return {
        "member_role": role,
        "member_index": member_index,
        "full_name": "New Roster Member",
        "member_id": member_id,
        "rank": "Major",
        "head_phone": "+15555550999",
        "head_email": "new-contact@example.test",
        "document": {
            "filename": "replacement.png",
            "content_type": "image/png",
            "document_base64": base64.b64encode(document_bytes).decode("ascii"),
        },
    }


def test_head_replacement_preserves_history_encrypts_documents_and_keeps_login_identity(tmp_path, monkeypatch):
    sector_id, request_id, auth, account, old_bytes = _approved_roster(tmp_path, monkeypatch)
    result = sector_registry.replace_sector_personnel(tmp_path, sector_id, _payload(), 5)

    assert result["head_contact_updated"] is True
    assert result["shared_login_unchanged"] is True
    assert auth.account_profile(account["user_id"])["username"] == "shared-login@example.test"
    roster = sector_registry.list_approved_sector_users(tmp_path)[0]
    head = next(member for member in roster["members"] if member["member_role"] == "head")
    assert (head["full_name"], head["member_id"], head["rank"]) == ("New Roster Member", "7654321", "Major")
    assert roster["head_email"] == "new-contact@example.test"
    new_document, content_type = sector_registry.get_work_id(tmp_path, request_id, 5, "head")
    assert new_document == b"\x89PNG\r\n\x1a\nreplacement"
    assert content_type == "image/png"
    history = sector_registry.list_personnel_history(tmp_path, sector_id)
    assert len(history) == 1
    assert history[0]["full_name"] == "Old Head"
    assert history[0]["document_available"] is True
    historical_document, historical_type = sector_registry.get_personnel_history_work_id(tmp_path, history[0]["history_id"], 5)
    assert historical_document == old_bytes
    assert historical_type == "application/pdf"
    with pytest.raises(sector_registry.RegistryError) as denied:
        sector_registry.get_personnel_history_work_id(tmp_path, history[0]["history_id"], 6)
    assert denied.value.status == 404
    entries = sector_registry.audit_entries(tmp_path, 5)
    assert any(entry["action"] == "sector_personnel_replaced" and entry["detail"] == "head:0" for entry in entries)
    assert any(entry["action"] == "personnel_history_document_retrieved" for entry in entries)


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        (_payload(member_id="12345"), "invalid_member_id"),
        (_payload(document_bytes=b"not a png"), "invalid_document_signature"),
        (_payload(member_id="2345678"), "duplicate_member_id"),
    ],
)
def test_personnel_replacement_rejects_invalid_ids_documents_and_duplicates(tmp_path, monkeypatch, payload, error_code):
    sector_id, _request_id, _auth, _account, _old_bytes = _approved_roster(tmp_path, monkeypatch)
    with pytest.raises(sector_registry.RegistryError) as caught:
        sector_registry.replace_sector_personnel(tmp_path, sector_id, payload, 5)
    assert caught.value.code == error_code
    assert sector_registry.list_personnel_history(tmp_path, sector_id) == []


def test_personnel_replacement_updates_technical_member_and_respects_document_retention(tmp_path, monkeypatch):
    sector_id, request_id, _auth, _account, _old_bytes = _approved_roster(tmp_path, monkeypatch)
    payload = _payload(role="technical", member_index=1)
    result = sector_registry.replace_sector_personnel(tmp_path, sector_id, payload, 5)
    assert result["member_role"] == "technical"
    roster = sector_registry.list_approved_sector_users(tmp_path)[0]
    current = next(member for member in roster["members"] if member["member_index"] == 1)
    assert current["full_name"] == "New Roster Member"
    document, _content_type = sector_registry.get_work_id(tmp_path, request_id, 5, "technical1")
    assert document.endswith(b"replacement")
    assert sector_registry.purge_documents(tmp_path) == 1
