"""Secure HQ-owned sector registry workflow (documents fail closed without encryption keys)."""
from __future__ import annotations
import base64, hashlib, hmac, json, math, os, re, secrets, sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import time, threading
from backend.headquarters_sectors import _ClosingConnection, headquarters_database, load_headquarters_sectors, normalize_polygon, set_sector_geometry, list_sector_geometries

RETENTION_DAYS=30
_GEOCODE_LOCK=threading.Lock()
_GEOCODE_CACHE={}
_GEOCODE_LAST_REQUEST=0.0
MAX_WORK_ID_BYTES=5*1024*1024
ALLOWED_TYPES={"image/jpeg","image/png","application/pdf"}

class RegistryError(ValueError):
    def __init__(self, code:str, message:str, status:int=400):
        super().__init__(message); self.code,self.status=code,status

def _stamp(value=None):
    return (value or datetime.now(timezone.utc)).isoformat(timespec="seconds").replace("+00:00","Z")

def _storage(project_root):
    project=Path(project_root).resolve()
    configured=os.environ.get("BTF_PRIVATE_STORAGE_ROOT","").strip()
    root=Path(configured).expanduser().resolve() if configured else project.parent/"secure_private_storage"
    if root==project or project in root.parents: raise RegistryError("private_storage_inside_project","Private storage must be outside the project folder.",500)
    return root/"work_ids"

def _fernet():
    key=os.environ.get("BTF_REGISTRY_ENCRYPTION_KEY","").strip()
    if not key: raise RegistryError("encryption_unconfigured","Configure BTF_REGISTRY_ENCRYPTION_KEY before accepting work-ID uploads.",503)
    try:
        from cryptography.fernet import Fernet
        Fernet(key.encode("ascii"))  # validate configuration before accepting or reviewing documents
        return Fernet,key
    except ImportError as e: raise RegistryError("encryption_unavailable","Install the cryptography package before accepting work-ID uploads.",503) from e
    except Exception as e: raise RegistryError("invalid_encryption_key","The work-ID encryption key is invalid.",503) from e

def _audit_key():
    secret=os.environ.get("BTF_REGISTRY_AUDIT_KEY","").strip().encode("utf-8")
    if len(secret)<32: raise RegistryError("audit_key_unconfigured","Configure BTF_REGISTRY_AUDIT_KEY with at least 32 random bytes before accepting or reviewing work IDs.",503)
    return secret

def require_document_security():
    """Fail closed for public intake and every HQ registry review operation."""
    _fernet()
    _audit_key()

def _db(root):
    load_headquarters_sectors(root)  # Ensure sector lifecycle columns exist before registry queries.
    c=sqlite3.connect(headquarters_database(root),timeout=15,factory=_ClosingConnection); c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON"); c.execute("PRAGMA secure_delete=ON"); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA busy_timeout=15000")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS sector_registry_requests(request_id TEXT PRIMARY KEY,sector_id TEXT NOT NULL REFERENCES headquarters_sectors(sector_id),status TEXT NOT NULL CHECK(status IN('pending','approved','rejected')),head_phone TEXT NOT NULL DEFAULT '',head_email TEXT NOT NULL DEFAULT '',sector_address TEXT NOT NULL DEFAULT '',chosen_lat REAL,chosen_lon REAL,base_camp_lat REAL NOT NULL,base_camp_lon REAL NOT NULL,resolved_address_string TEXT NOT NULL,automated_locked_lat REAL,automated_locked_lon REAL,location_capture_method TEXT NOT NULL CHECK(location_capture_method IN('DEVICE_HARDWARE','ADDRESS_GEOCODING','MANUAL_MAP_CLICK')),submitted_at TEXT NOT NULL,decided_at TEXT,assigned_admin_id INTEGER NOT NULL,decided_by INTEGER,decision_note TEXT NOT NULL DEFAULT '',is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN(0,1))); 
    CREATE TABLE IF NOT EXISTS sector_registry_members(request_id TEXT NOT NULL REFERENCES sector_registry_requests(request_id) ON DELETE CASCADE,member_role TEXT NOT NULL CHECK(member_role IN('head','technical')),member_index INTEGER NOT NULL,full_name TEXT NOT NULL,member_id TEXT NOT NULL,rank TEXT NOT NULL,PRIMARY KEY(request_id,member_role,member_index),UNIQUE(request_id,member_id));
    CREATE TABLE IF NOT EXISTS registry_documents(request_id TEXT PRIMARY KEY REFERENCES sector_registry_requests(request_id) ON DELETE CASCADE,encrypted_path TEXT,wrapped_key BLOB,content_type TEXT NOT NULL,submitted_at TEXT NOT NULL,expires_at TEXT NOT NULL,purged_at TEXT,purge_reason TEXT,CHECK((encrypted_path IS NULL)=(wrapped_key IS NULL)));
    CREATE TABLE IF NOT EXISTS registry_member_documents(request_id TEXT NOT NULL REFERENCES sector_registry_requests(request_id) ON DELETE CASCADE,member_role TEXT NOT NULL CHECK(member_role IN('technical')),member_index INTEGER NOT NULL,encrypted_path TEXT NOT NULL,wrapped_key BLOB NOT NULL,content_type TEXT NOT NULL,submitted_at TEXT NOT NULL,expires_at TEXT NOT NULL,purged_at TEXT,purge_reason TEXT,PRIMARY KEY(request_id,member_role,member_index));
    CREATE TABLE IF NOT EXISTS sector_personnel_history(history_id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL REFERENCES sector_registry_requests(request_id) ON DELETE CASCADE,sector_id TEXT NOT NULL,member_role TEXT NOT NULL CHECK(member_role IN('head','technical')),member_index INTEGER NOT NULL,full_name TEXT NOT NULL,member_id TEXT NOT NULL,rank TEXT NOT NULL,head_phone TEXT NOT NULL DEFAULT '',head_email TEXT NOT NULL DEFAULT '',document_path TEXT,wrapped_key BLOB,content_type TEXT,expires_at TEXT,changed_at TEXT NOT NULL,changed_by INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS registry_audit_log(audit_id INTEGER PRIMARY KEY AUTOINCREMENT,admin_id INTEGER NOT NULL,request_id TEXT NOT NULL,action TEXT NOT NULL,occurred_at TEXT NOT NULL,previous_digest TEXT NOT NULL,entry_digest TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS sector_password_reset_requests(reset_id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES sector_registry_requests(request_id),sector_number TEXT NOT NULL,initiated_by_user_id INTEGER NOT NULL,head_member_id TEXT NOT NULL,created_at TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN('pending','approved','rejected','completed')),verification_hash BLOB,verification_expires_at TEXT,verified_at TEXT,verification_attempts INTEGER NOT NULL DEFAULT 0,decided_by INTEGER,decided_at TEXT);
    CREATE INDEX IF NOT EXISTS registry_pending_idx ON sector_registry_requests(status,submitted_at);
    CREATE INDEX IF NOT EXISTS registry_expiry_idx ON registry_documents(expires_at,purged_at);
    CREATE INDEX IF NOT EXISTS personnel_history_expiry_idx ON sector_personnel_history(expires_at);
    """)
    request_columns={str(row[1]) for row in c.execute("PRAGMA table_info(sector_registry_requests)").fetchall()}
    if "is_active" not in request_columns:
        c.execute("ALTER TABLE sector_registry_requests ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN(0,1))")
    audit_columns={str(row[1]) for row in c.execute("PRAGMA table_info(registry_audit_log)").fetchall()}
    if "detail" not in audit_columns:
        c.execute("ALTER TABLE registry_audit_log ADD COLUMN detail TEXT NOT NULL DEFAULT ''")
    index=c.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='registry_one_active_request_per_sector'").fetchone()
    if index and "is_active" not in str(index[0]).casefold():
        c.execute("DROP INDEX registry_one_active_request_per_sector")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS registry_one_active_request_per_sector ON sector_registry_requests(sector_id) WHERE status IN('pending','approved') AND is_active=1")
    # Remove the retired phone/OTP challenge table and its old phone identifiers.
    c.execute("DROP TABLE IF EXISTS registry_phone_challenges")
    if "head_email" not in request_columns: c.execute("ALTER TABLE sector_registry_requests ADD COLUMN head_email TEXT NOT NULL DEFAULT ''")
    if "sector_address" not in request_columns: c.execute("ALTER TABLE sector_registry_requests ADD COLUMN sector_address TEXT NOT NULL DEFAULT ''")
    for name, definition in (("chosen_lat", "REAL"), ("chosen_lon", "REAL"),
                             ("automated_locked_lat", "REAL"), ("automated_locked_lon", "REAL"),
                             ("location_capture_method", "TEXT"),
                             ("base_camp_lat", "REAL"), ("base_camp_lon", "REAL"),
                             ("resolved_address_string", "TEXT")):
        if name not in request_columns:
            c.execute(f"ALTER TABLE sector_registry_requests ADD COLUMN {name} {definition}")
    c.execute("UPDATE sector_registry_requests SET base_camp_lat=chosen_lat WHERE base_camp_lat IS NULL AND chosen_lat IS NOT NULL")
    c.execute("UPDATE sector_registry_requests SET base_camp_lon=chosen_lon WHERE base_camp_lon IS NULL AND chosen_lon IS NOT NULL")
    c.execute("UPDATE sector_registry_requests SET resolved_address_string=sector_address WHERE resolved_address_string IS NULL AND sector_address<>''")
    c.execute("UPDATE sector_registry_requests SET location_capture_method=CASE WHEN location_capture_method='browser_geolocation' THEN 'DEVICE_HARDWARE' WHEN location_capture_method IN('manual_pin','hq_review_pin') THEN 'MANUAL_MAP_CLICK' ELSE location_capture_method END WHERE location_capture_method IN('browser_geolocation','manual_pin','hq_review_pin')")
    reset_columns={str(row[1]) for row in c.execute("PRAGMA table_info(sector_password_reset_requests)").fetchall()}
    for name,definition in (("verification_hash","BLOB"),("verification_expires_at","TEXT"),("verified_at","TEXT"),("verification_attempts","INTEGER NOT NULL DEFAULT 0")):
        if name not in reset_columns: c.execute(f"ALTER TABLE sector_password_reset_requests ADD COLUMN {name} {definition}")
    return c

def _normalized_address(value):
    return " ".join(value.casefold().split())

def _geocode_token(address,lat,lon,resolved,expires):
    data=json.dumps({"address":_normalized_address(address),"lat":round(lat,7),"lon":round(lon,7),"resolved":resolved,"expires":expires},separators=(",",":"),ensure_ascii=False).encode("utf-8")
    signature=hmac.new(_audit_key(),data,hashlib.sha256).digest()
    return base64.urlsafe_b64encode(data+b"."+signature).decode("ascii").rstrip("=")

def _verify_geocode_token(token,address,lat,lon,resolved):
    try:
        raw=base64.urlsafe_b64decode(token+"="*((4-len(token)%4)%4))
        if len(raw)<33 or raw[-33:-32]!=b".": return False
        data,signature=raw[:-33],raw[-32:]
        if not hmac.compare_digest(signature,hmac.new(_audit_key(),data,hashlib.sha256).digest()): return False
        record=json.loads(data.decode("utf-8"))
        return (record.get("expires",0)>=int(time.time()) and record.get("address")==_normalized_address(address)
                and record.get("lat")==round(lat,7) and record.get("lon")==round(lon,7) and record.get("resolved")==resolved)
    except Exception:
        return False

def geocode_address(address):
    """Resolve the entered address server-side and sign the result for submission."""
    global _GEOCODE_LAST_REQUEST
    address=_text(address,"Sector address",240);_audit_key();cache_key=_normalized_address(address)
    with _GEOCODE_LOCK:
        cached=_GEOCODE_CACHE.get(cache_key)
        if cached is None:
            delay=1.0-(time.monotonic()-_GEOCODE_LAST_REQUEST)
            if delay>0: time.sleep(delay)
            query=urlencode({"q":address,"format":"jsonv2","limit":"1","addressdetails":"1"})
            request=Request("https://nominatim.openstreetmap.org/search?"+query,headers={"User-Agent":"BeyondTheFrame-SectorRegistry/1.0","Accept":"application/json"})
            try:
                with urlopen(request,timeout=8) as response: records=json.loads(response.read(256_000).decode("utf-8"))
            except HTTPError as error: raise RegistryError("geocoder_unavailable","Address lookup is temporarily unavailable. Please try another location method.",503) from error
            except (URLError,TimeoutError,ValueError,json.JSONDecodeError) as error: raise RegistryError("geocoder_unavailable","Address lookup is temporarily unavailable. Please try another location method.",503) from error
            finally: _GEOCODE_LAST_REQUEST=time.monotonic()
            if not isinstance(records,list) or not records: raise RegistryError("address_not_found","Address could not be geocoded. Please verify or use alternative capture methods.",422)
            try:
                lat,lon=float(records[0]["lat"]),float(records[0]["lon"]);resolved=_text(records[0].get("display_name"),"Resolved address",500)
            except (KeyError,TypeError,ValueError): raise RegistryError("address_not_found","Address could not be geocoded. Please verify or use alternative capture methods.",422) from None
            if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180: raise RegistryError("address_not_found","Address could not be geocoded. Please verify or use alternative capture methods.",422)
            cached={"latitude":lat,"longitude":lon,"resolved_address_string":resolved}
            if len(_GEOCODE_CACHE)>=256: _GEOCODE_CACHE.pop(next(iter(_GEOCODE_CACHE)))
            _GEOCODE_CACHE[cache_key]=cached
        expires=int(time.time())+1800
        return {**cached,"verification_token":_geocode_token(address,cached["latitude"],cached["longitude"],cached["resolved_address_string"],expires),"expires_at":expires}

def _text(value,field,limit=120):
    if not isinstance(value,str): raise RegistryError("invalid_registration",f"{field} is required.")
    value=" ".join(value.split())
    if not value or len(value)>limit or any(ord(x)<32 for x in value): raise RegistryError("invalid_registration",f"{field} must contain 1 to {limit} valid characters.")
    return value

def _resolve_sector_id(project_root, sector_value):
    """Resolve a manually entered HQ sector name/ID to its canonical geometry ID."""
    value=_text(sector_value,"Sector name or number",80).casefold()
    sectors=load_headquarters_sectors(project_root)["sectors"]
    matches=[item for item in sectors if value in {
        str(item["id"]).casefold(),
        str(item["name"]).casefold(),
        f"{item['name']} ({item['id']})".casefold(),
    }]
    if len(matches)!=1: raise RegistryError("unknown_sector","Enter a sector name or ID that is currently configured by Headquarters.")
    return str(matches[0]["id"])

def _validated(payload):
    if not isinstance(payload,dict): raise RegistryError("invalid_registration","Registration details are required.")
    sector=_text(payload.get("sector_id"),"Sector name or number",80); email=_text(payload.get("head_email"),"Sector-head email",254).casefold()
    address=_text(payload.get("sector_address"),"Sector address",240)
    phone_raw=_text(payload.get("head_phone"),"Sector-head phone number",32)
    phone=re.sub(r"[\s().-]","",phone_raw)
    if not re.fullmatch(r"\+?[1-9][0-9]{7,14}",phone): raise RegistryError("invalid_phone","Enter a valid phone number with country code (8–15 digits).")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email): raise RegistryError("invalid_email","Enter a valid sector-head email address.")
    members=payload.get("members")
    if not isinstance(members,list) or len(members)!=3: raise RegistryError("invalid_members","Provide one sector head and two technical members.")
    result=[]; roles=[]; ids=set()
    for i,row in enumerate(members):
        if not isinstance(row,dict) or row.get("role") not in {"head","technical"}: raise RegistryError("invalid_members","Member role must be head or technical.")
        role=row["role"]; roles.append(role); mid=_text(row.get("member_id"),"Member ID",7)
        if not re.fullmatch(r"[0-9]{7}",mid): raise RegistryError("invalid_member_id","Every member ID must contain exactly 7 digits.")
        if mid.casefold() in ids: raise RegistryError("duplicate_member_id","Member IDs must be unique.")
        ids.add(mid.casefold()); result.append((role,i,_text(row.get("full_name"),"Name"),mid,_text(row.get("rank"),"Rank",80)))
    if roles != ["head","technical","technical"]: raise RegistryError("invalid_members","Provide the sector head first, followed by technical member 1 and technical member 2.")
    return sector,email,phone,address,result

def _location_fields(payload,address):
    """Require a verified coordinate pair and exactly one supported capture method."""
    lat=payload.get("base_camp_lat");lon=payload.get("base_camp_lon");method=payload.get("location_capture_method")
    if method not in {"DEVICE_HARDWARE","ADDRESS_GEOCODING","MANUAL_MAP_CLICK"}: raise RegistryError("invalid_location_method","Choose exactly one base-camp location method.")
    if lat is None or lon is None: raise RegistryError("location_required","Capture a base-camp pin using the selected location method before submitting.")
    if isinstance(lat,bool) or isinstance(lon,bool): raise RegistryError("invalid_location","Base-camp coordinates must be valid numbers.")
    try: lat,lon=float(lat),float(lon)
    except (TypeError,ValueError): raise RegistryError("invalid_location","Base-camp coordinates must include latitude and longitude.") from None
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180: raise RegistryError("invalid_location","Base-camp coordinates are outside valid latitude/longitude ranges.")
    if method=="ADDRESS_GEOCODING":
        resolved=_text(payload.get("resolved_address_string"),"Resolved address",500)
        token=payload.get("location_verification_token")
        if not isinstance(token,str) or not _verify_geocode_token(token,address,lat,lon,resolved): raise RegistryError("address_not_verified","Resolve the current sector address before submitting.")
    else: resolved=address
    return lat,lon,resolved,method

def _document_signature_matches(content_type: str, raw: bytes) -> bool:
    """Check that the uploaded bytes begin with the declared supported file signature."""
    signatures = {
        "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        "application/pdf": lambda data: data.startswith(b"%PDF-"),
    }
    validator = signatures.get(content_type)
    return bool(validator and validator(raw))
def submit_request(root,payload,admin_id,filename,content_type,document_b64):
    require_document_security()
    Fernet,master_key=_fernet()
    sector,email,phone,address,members=_validated(payload)
    base_lat, base_lon, resolved_address, location_method = _location_fields(payload,address)
    sector=_resolve_sector_id(root,sector)
    docs=payload.get("documents")
    if not isinstance(docs,dict): docs={"head":{"filename":filename,"content_type":content_type,"document_base64":document_b64}}
    required={"head","technical1","technical2"}
    if set(docs)!=required: raise RegistryError("documents_required","Upload the head and both technical members’ work IDs.")
    now=datetime.now(timezone.utc); expires=now+timedelta(days=RETENTION_DAYS); request_id="REG_"+secrets.token_hex(16)
    storage=_storage(root); storage.mkdir(parents=True,exist_ok=True); staged=[]
    prepared=[]
    # Validate every supplied document before writing any encrypted payload so an
    # invalid later member cannot leave an orphaned file in private storage.
    for key in ("head","technical1","technical2"):
        item=docs[key]
        if not isinstance(item,dict): raise RegistryError("invalid_document","Each member work-ID file is required.")
        fname=item.get("filename"); ctype=item.get("content_type"); encoded=item.get("document_base64")
        if ctype not in ALLOWED_TYPES: raise RegistryError("invalid_document_type","Each work ID must be a JPEG, PNG, or PDF.")
        if not isinstance(fname,str) or len(fname)>200 or Path(fname).name!=fname: raise RegistryError("invalid_filename","A work-ID filename is invalid.")
        try: raw=base64.b64decode(encoded,validate=True)
        except Exception as e: raise RegistryError("invalid_document","A work-ID upload is invalid.") from e
        if not raw or len(raw)>MAX_WORK_ID_BYTES: raise RegistryError("document_size","Each work-ID file must be at most 5 MiB.")
        if not _document_signature_matches(ctype, raw): raise RegistryError("invalid_document_signature","The uploaded file contents do not match the declared JPEG, PNG, or PDF type.")
        prepared.append((key,raw,ctype))
    try:
        for key,raw,ctype in prepared:
            key_bytes=Fernet.generate_key(); path=storage/(request_id+"_"+key+".workid.enc")
            path.write_bytes(Fernet(key_bytes).encrypt(raw)); wrapped=Fernet(master_key.encode("ascii")).encrypt(key_bytes)
            staged.append((key,path,wrapped,ctype))
    except Exception:
        for _,path,_,_ in staged: path.unlink(missing_ok=True)
        raise
    try:
        with _db(root) as c:
            c.execute("INSERT INTO sector_registry_requests(request_id,sector_id,status,head_phone,head_email,sector_address,chosen_lat,chosen_lon,base_camp_lat,base_camp_lon,resolved_address_string,location_capture_method,submitted_at,assigned_admin_id) VALUES(?,?,'pending',?,?,?,?,?,?,?,?,?,?,?)",(request_id,sector,phone,email,address,base_lat,base_lon,base_lat,base_lon,resolved_address,location_method,_stamp(now),admin_id))
            c.executemany("INSERT INTO sector_registry_members(request_id,member_role,member_index,full_name,member_id,rank) VALUES(?,?,?,?,?,?)",[(request_id,*m) for m in members])
            for key,path,wrapped,ctype in staged:
                if key=="head": c.execute("INSERT INTO registry_documents(request_id,encrypted_path,wrapped_key,content_type,submitted_at,expires_at) VALUES(?,?,?,?,?,?)",(request_id,str(path),wrapped,ctype,_stamp(now),_stamp(expires)))
                else: c.execute("INSERT INTO registry_member_documents(request_id,member_role,member_index,encrypted_path,wrapped_key,content_type,submitted_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",(request_id,"technical",int(key[-1])-1,str(path),wrapped,ctype,_stamp(now),_stamp(expires)))
    except Exception:
        for _,path,_,_ in staged: path.unlink(missing_ok=True)
        raise
    return {"request_id":request_id,"sector_id":sector,"status":"pending","submitted_at":_stamp(now),"documents_expires_at":_stamp(expires)}

def list_requests(root,admin_id,status="pending"):
    require_document_security()
    with _db(root) as c:
        rows=c.execute("""SELECT r.request_id,r.sector_id,s.name sector_name,r.status,r.head_email,r.head_phone,r.sector_address,r.base_camp_lat,r.base_camp_lon,r.resolved_address_string,r.location_capture_method,r.submitted_at,r.decided_at,r.decision_note,(d.purged_at IS NULL AND d.encrypted_path IS NOT NULL AND d.expires_at>?) document_available FROM sector_registry_requests r JOIN headquarters_sectors s USING(sector_id) LEFT JOIN registry_documents d USING(request_id) WHERE r.assigned_admin_id=? AND (?='all' OR r.status=?) ORDER BY r.submitted_at DESC""",(_stamp(),admin_id,status,status)).fetchall()
        result=[]
        for r in rows:
            members=[dict(x) for x in c.execute("SELECT member_role,member_index,full_name,member_id,rank FROM sector_registry_members WHERE request_id=? ORDER BY member_index",(r["request_id"],))]
            extra=c.execute("SELECT member_index,purged_at,encrypted_path,expires_at,wrapped_key FROM registry_member_documents WHERE request_id=?",(r["request_id"],)).fetchall()
            docs={"head":bool(r["document_available"]),**{f"technical{int(x['member_index'])+1}":bool(x["encrypted_path"] and x["wrapped_key"] and not x["purged_at"] and x["expires_at"]>_stamp()) for x in extra}}
            result.append({**dict(r),"document_available":bool(r["document_available"]),"documents":docs,"members":members})
        return result

def list_approved_sector_users(root):
    """Return approved rosters and safe document availability metadata."""
    database = headquarters_database(root)
    if not database.exists():
        return []
    with _db(root) as c:
        rows = c.execute("""SELECT r.request_id,r.sector_id,s.name sector_name,r.head_email,
            r.head_phone,r.sector_address,r.submitted_at,r.decided_at,r.decided_by
            FROM sector_registry_requests r JOIN headquarters_sectors s USING(sector_id)
            WHERE r.status='approved' AND r.is_active=1 AND s.is_active=1 ORDER BY s.row_index,s.column_index,r.decided_at DESC""").fetchall()
        result=[]
        for row in rows:
            request_id=row["request_id"]
            head=c.execute("SELECT encrypted_path,wrapped_key,expires_at,purged_at FROM registry_documents WHERE request_id=?",(request_id,)).fetchone()
            documents={"head":bool(head and head["encrypted_path"] and head["wrapped_key"] and not head["purged_at"] and head["expires_at"]>_stamp())}
            tech=c.execute("SELECT member_index,encrypted_path,wrapped_key,expires_at,purged_at FROM registry_member_documents WHERE request_id=?",(request_id,)).fetchall()
            for item in tech:
                key=f"technical{int(item['member_index'])+1}"
                documents[key]=bool(item["encrypted_path"] and item["wrapped_key"] and not item["purged_at"] and item["expires_at"]>_stamp())
            members=[dict(member) for member in c.execute(
                "SELECT member_role,member_index,full_name,member_id,rank FROM sector_registry_members WHERE request_id=? ORDER BY member_index",(request_id,)).fetchall()]
            for member in members:
                key="head" if member["member_role"]=="head" else f"technical{int(member['member_index'])+1}"
                member["document_key"]=key
                member["document_available"]=documents.get(key,False)
            result.append({**dict(row),"documents":documents,"members":members})
        return result


def replace_sector_personnel(root, sector_id, payload, admin_id):
    """Replace one active roster member while retaining an encrypted history snapshot."""
    require_document_security()
    Fernet, master_key = _fernet()
    if not isinstance(sector_id, str) or not re.fullmatch(r"S[1-9][0-9]*", sector_id.upper()):
        raise RegistryError("invalid_sector", "Choose a valid active sector.")
    if not isinstance(payload, dict):
        raise RegistryError("invalid_personnel_change", "Personnel replacement details are required.")
    role = payload.get("member_role")
    raw_index = payload.get("member_index")
    if role == "head":
        member_index = 0
    elif role == "technical" and raw_index in {1, 2, "1", "2"}:
        member_index = int(raw_index)
    else:
        raise RegistryError("invalid_member_role", "Choose the sector head or technical member 1 or 2.")
    full_name = _text(payload.get("full_name"), "Name")
    member_id = _text(payload.get("member_id"), "Work ID", 7)
    if not re.fullmatch(r"[0-9]{7}", member_id):
        raise RegistryError("invalid_member_id", "Work ID must contain exactly 7 digits.")
    rank = _text(payload.get("rank"), "Rank", 80)
    document = payload.get("document")
    if not isinstance(document, dict):
        raise RegistryError("documents_required", "Upload the replacement member's work-ID document.")
    filename = document.get("filename")
    content_type = document.get("content_type")
    encoded = document.get("document_base64")
    if content_type not in ALLOWED_TYPES:
        raise RegistryError("invalid_document_type", "Work ID must be a JPEG, PNG, or PDF.")
    if not isinstance(filename, str) or len(filename) > 200 or Path(filename).name != filename:
        raise RegistryError("invalid_filename", "The work-ID filename is invalid.")
    try:
        raw_document = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError) as error:
        raise RegistryError("invalid_document", "The work-ID upload is invalid.") from error
    if not raw_document or len(raw_document) > MAX_WORK_ID_BYTES:
        raise RegistryError("document_size", "The work-ID file must be at most 5 MiB.")
    if not _document_signature_matches(content_type, raw_document):
        raise RegistryError("invalid_document_signature", "The uploaded file contents do not match the declared JPEG, PNG, or PDF type.")

    requested_phone = payload.get("head_phone")
    requested_email = payload.get("head_email")
    if role == "head":
        if requested_phone is not None and str(requested_phone).strip():
            phone_raw = _text(requested_phone, "Sector-head phone number", 32)
            phone = re.sub(r"[\s().-]", "", phone_raw)
            if not re.fullmatch(r"\+?[1-9][0-9]{7,14}", phone):
                raise RegistryError("invalid_phone", "Enter a valid phone number with country code (8–15 digits).")
        else:
            phone = None
        if requested_email is not None and str(requested_email).strip():
            email = _text(requested_email, "Sector-head email", 254).casefold()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise RegistryError("invalid_email", "Enter a valid sector-head contact email address.")
        else:
            email = None
    else:
        phone = email = None

    now = datetime.now(timezone.utc)
    submitted = _stamp(now)
    expires = _stamp(now + timedelta(days=RETENTION_DAYS))
    request_id = None
    storage = _storage(root)
    storage.mkdir(parents=True, exist_ok=True)
    new_path = storage / f"PERSONNEL_{secrets.token_hex(16)}_{role}{member_index}.workid.enc"
    document_key = Fernet.generate_key()
    new_wrapped_key = Fernet(master_key.encode("ascii")).encrypt(document_key)
    new_path.write_bytes(Fernet(document_key).encrypt(raw_document))
    try:
        with _db(root) as c:
            request = c.execute("""SELECT r.request_id,r.head_phone,r.head_email
                FROM sector_registry_requests r JOIN headquarters_sectors s USING(sector_id)
                WHERE r.sector_id=? AND r.status='approved' AND r.is_active=1 AND s.is_active=1
                ORDER BY r.decided_at DESC LIMIT 1""", (sector_id.upper(),)).fetchone()
            if request is None:
                raise RegistryError("approved_sector_not_found", "No active approved roster exists for this sector.", 404)
            request_id = str(request["request_id"])
            member = c.execute("""SELECT full_name,member_id,rank FROM sector_registry_members
                WHERE request_id=? AND member_role=? AND member_index=?""",
                (request_id, role, member_index)).fetchone()
            if member is None:
                raise RegistryError("member_not_found", "The selected member is not in the current sector roster.", 404)
            duplicate = c.execute("""SELECT 1 FROM sector_registry_members
                WHERE request_id=? AND member_id=? AND NOT(member_role=? AND member_index=?)""",
                (request_id, member_id, role, member_index)).fetchone()
            if duplicate:
                raise RegistryError("duplicate_member_id", "Work ID is already assigned to another roster member.")

            head_phone = phone if role == "head" and phone is not None else request["head_phone"]
            head_email = email if role == "head" and email is not None else request["head_email"]
            if role == "head":
                old_document = c.execute("""SELECT encrypted_path,wrapped_key,content_type,expires_at,purged_at
                    FROM registry_documents WHERE request_id=?""", (request_id,)).fetchone()
                if old_document and old_document["encrypted_path"] and old_document["wrapped_key"] and not old_document["purged_at"]:
                    prior_document = (old_document["encrypted_path"], old_document["wrapped_key"], old_document["content_type"], old_document["expires_at"])
                else:
                    prior_document = (None, None, None, None)
            else:
                document_index = member_index - 1
                old_document = c.execute("""SELECT encrypted_path,wrapped_key,content_type,expires_at,purged_at
                    FROM registry_member_documents WHERE request_id=? AND member_role='technical' AND member_index=?""",
                    (request_id, document_index)).fetchone()
                if old_document and old_document["encrypted_path"] and old_document["wrapped_key"] and not old_document["purged_at"]:
                    prior_document = (old_document["encrypted_path"], old_document["wrapped_key"], old_document["content_type"], old_document["expires_at"])
                else:
                    prior_document = (None, None, None, None)
            c.execute("""INSERT INTO sector_personnel_history
                (request_id,sector_id,member_role,member_index,full_name,member_id,rank,head_phone,head_email,
                document_path,wrapped_key,content_type,expires_at,changed_at,changed_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (request_id, sector_id.upper(), role, member_index, member["full_name"], member["member_id"], member["rank"],
                 request["head_phone"], request["head_email"], *prior_document, submitted, admin_id))
            c.execute("""UPDATE sector_registry_members SET full_name=?,member_id=?,rank=?
                WHERE request_id=? AND member_role=? AND member_index=?""",
                (full_name, member_id, rank, request_id, role, member_index))
            if role == "head":
                c.execute("UPDATE sector_registry_requests SET head_phone=?,head_email=? WHERE request_id=?",
                          (head_phone, head_email, request_id))
                c.execute("""INSERT INTO registry_documents(request_id,encrypted_path,wrapped_key,content_type,submitted_at,expires_at,purged_at,purge_reason)
                    VALUES(?,?,?,?,?,?,NULL,NULL) ON CONFLICT(request_id) DO UPDATE SET encrypted_path=excluded.encrypted_path,
                    wrapped_key=excluded.wrapped_key,content_type=excluded.content_type,submitted_at=excluded.submitted_at,
                    expires_at=excluded.expires_at,purged_at=NULL,purge_reason=NULL""",
                    (request_id, str(new_path), new_wrapped_key, content_type, submitted, expires))
            else:
                c.execute("""INSERT INTO registry_member_documents
                    (request_id,member_role,member_index,encrypted_path,wrapped_key,content_type,submitted_at,expires_at,purged_at,purge_reason)
                    VALUES(?,'technical',?,?,?,?,?,?,NULL,NULL) ON CONFLICT(request_id,member_role,member_index) DO UPDATE SET
                    encrypted_path=excluded.encrypted_path,wrapped_key=excluded.wrapped_key,content_type=excluded.content_type,
                    submitted_at=excluded.submitted_at,expires_at=excluded.expires_at,purged_at=NULL,purge_reason=NULL""",
                    (request_id, document_index, str(new_path), new_wrapped_key, content_type, submitted, expires))
            _audit(c, admin_id, request_id, "sector_personnel_replaced", f"{role}:{member_index}")
        return {
            "sector_id": sector_id.upper(),
            "request_id": request_id,
            "member_role": role,
            "member_index": member_index,
            "changed_at": submitted,
            "head_contact_updated": role == "head" and (phone is not None or email is not None),
            "shared_login_unchanged": True,
        }
    except Exception:
        new_path.unlink(missing_ok=True)
        raise


def list_personnel_history(root, sector_id):
    with _db(root) as c:
        rows = c.execute("""SELECT history_id,sector_id,member_role,member_index,full_name,rank,changed_at,changed_by,
            (document_path IS NOT NULL AND wrapped_key IS NOT NULL AND expires_at>?) AS document_available
            FROM sector_personnel_history WHERE sector_id=? ORDER BY changed_at DESC,history_id DESC""",
            (_stamp(), str(sector_id).upper())).fetchall()
        return [{**dict(row), "document_available": bool(row["document_available"])} for row in rows]


def get_personnel_history_work_id(root, history_id, admin_id):
    require_document_security()
    Fernet, master_key = _fernet()
    with _db(root) as c:
        row = c.execute("""SELECT request_id,document_path,wrapped_key,content_type,expires_at,changed_by
            FROM sector_personnel_history WHERE history_id=?""", (history_id,)).fetchone()
        if row is None or row["changed_by"] != admin_id:
            raise RegistryError("document_not_found", "Historical work-ID document is unavailable to this reviewer.", 404)
        if not row["document_path"] or not row["wrapped_key"] or not row["expires_at"] or row["expires_at"] <= _stamp():
            raise RegistryError("document_expired", "Historical work-ID document has been removed or expired.", 410)
        path = Path(row["document_path"]).resolve()
        store = _storage(root).resolve()
        if store not in path.parents:
            raise RegistryError("invalid_document_path", "Stored member document failed its integrity check.", 500)
        _audit(c, admin_id, row["request_id"], "personnel_history_document_retrieved", str(history_id))
        document_key = Fernet(master_key.encode("ascii")).decrypt(bytes(row["wrapped_key"]))
        return Fernet(document_key).decrypt(path.read_bytes()), row["content_type"]

def list_active_approved_sectors(root):
    """Return only public identifiers and names for active approved sectors."""
    database = headquarters_database(root)
    if not database.exists():
        return []
    with _db(root) as c:
        rows = c.execute("""SELECT DISTINCT r.sector_id,s.name AS sector_name
            FROM sector_registry_requests r JOIN headquarters_sectors s USING(sector_id)
            WHERE r.status='approved' AND r.is_active=1 AND s.is_active=1
            ORDER BY s.row_index,s.column_index,r.decided_at DESC""").fetchall()
    return [{"id": str(row["sector_id"]), "name": str(row["sector_name"])} for row in rows]

def _audit(c,admin,request_id,action="work_id_retrieved",detail=""):
    secret=_audit_key()
    prev=c.execute("SELECT entry_digest FROM registry_audit_log ORDER BY audit_id DESC LIMIT 1").fetchone()
    previous=prev[0] if prev else "0"*64; at=_stamp()
    data=f"{previous}|{admin}|{request_id}|{action}|{at}".encode("utf-8")
    if detail:
        data += b"|" + detail.encode("utf-8")
    digest=hmac.new(secret,data,hashlib.sha256).hexdigest()
    c.execute("INSERT INTO registry_audit_log(admin_id,request_id,action,occurred_at,previous_digest,entry_digest,detail) VALUES(?,?,?,?,?,?,?)",
              (admin,request_id,action,at,previous,digest,detail))

def get_work_id(root,request_id,admin_id,member_key="head"):
    require_document_security(); Fernet,master_key=_fernet()
    with _db(root) as c:
        request=c.execute("SELECT assigned_admin_id,status,submitted_at FROM sector_registry_requests WHERE request_id=?",(request_id,)).fetchone()
        if request is None or request["assigned_admin_id"]!=admin_id: raise RegistryError("document_not_found","Work-ID document is unavailable to this reviewer.",404)
        if request["status"] not in {"pending","approved"}: raise RegistryError("document_expired","Work-ID document has been removed.",410)
        if member_key=="head": row=c.execute("SELECT encrypted_path,wrapped_key,content_type,purged_at,expires_at FROM registry_documents WHERE request_id=?",(request_id,)).fetchone()
        elif member_key in {"technical1","technical2"}: row=c.execute("SELECT encrypted_path,wrapped_key,content_type,purged_at,expires_at FROM registry_member_documents WHERE request_id=? AND member_role='technical' AND member_index=?",(request_id,int(member_key[-1])-1)).fetchone()
        else: raise RegistryError("invalid_document_member","Unknown member document.")
        if row is None or row["purged_at"] or not row["encrypted_path"] or not row["wrapped_key"] or row["expires_at"]<=_stamp(): raise RegistryError("document_expired","Work-ID document has been removed or expired.",410)
        _audit(c,admin_id,request_id); path=Path(row["encrypted_path"]).resolve(); store=_storage(root).resolve()
        if store not in path.parents: raise RegistryError("invalid_document_path","Stored document failed its integrity check.",500)
        document_key=Fernet(master_key.encode("ascii")).decrypt(bytes(row["wrapped_key"]))
        plaintext=Fernet(document_key).decrypt(path.read_bytes())
        return plaintext,row["content_type"]

def purge_documents(root,now=None):
    moment=_stamp(now); c=_db(root)
    try:
        rows=c.execute("""SELECT d.request_id,d.encrypted_path FROM registry_documents d
            JOIN sector_registry_requests r USING(request_id) WHERE d.purged_at IS NULL
            AND (d.expires_at<=? OR r.status='rejected' OR
                 (r.status='approved' AND d.submitted_at<=COALESCE(r.decided_at,d.submitted_at)))""",(moment,)).fetchall()
        for row in rows:
            if row["encrypted_path"]:
                path=Path(row["encrypted_path"]).resolve(); store=_storage(root).resolve()
                if store not in path.parents: raise RegistryError("invalid_document_path","Stored document failed its integrity check.",500)
                path.unlink(missing_ok=True)
            request=c.execute("SELECT status FROM sector_registry_requests WHERE request_id=?",(row["request_id"],)).fetchone()
            reason=request["status"] if request and request["status"] in {"approved","rejected"} else "retention_expired"
            c.execute("UPDATE registry_documents SET encrypted_path=NULL,wrapped_key=NULL,purged_at=?,purge_reason=? WHERE request_id=?",(moment,reason,row["request_id"]))
        member_rows=c.execute("""SELECT d.request_id,d.member_role,d.member_index,d.encrypted_path,r.status
            FROM registry_member_documents d JOIN sector_registry_requests r USING(request_id)
            WHERE d.purged_at IS NULL AND
            (d.expires_at<=? OR r.status='rejected' OR
             (r.status='approved' AND d.submitted_at<=COALESCE(r.decided_at,d.submitted_at)))""",(moment,)).fetchall()
        for row in member_rows:
            path=Path(row["encrypted_path"]).resolve(); store=_storage(root).resolve()
            if store not in path.parents: raise RegistryError("invalid_document_path","Stored member document failed its integrity check.",500)
            path.unlink(missing_ok=True)
            reason=row["status"] if row["status"] in {"approved","rejected"} else "retention_expired"
            c.execute("""UPDATE registry_member_documents SET encrypted_path='',wrapped_key=x'',purged_at=?,purge_reason=?
                WHERE request_id=? AND member_role=? AND member_index=?""",
                (moment,reason,row["request_id"],row["member_role"],row["member_index"]))
        history_rows=c.execute("""SELECT history_id,document_path FROM sector_personnel_history
            WHERE document_path IS NOT NULL AND expires_at<=?""",(moment,)).fetchall()
        for row in history_rows:
            path=Path(row["document_path"]).resolve(); store=_storage(root).resolve()
            if store not in path.parents: raise RegistryError("invalid_document_path","Stored historical document failed its integrity check.",500)
            path.unlink(missing_ok=True)
            c.execute("UPDATE sector_personnel_history SET document_path=NULL,wrapped_key=NULL WHERE history_id=?",(row["history_id"],))
        c.commit()
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return len(rows)
    except Exception: c.rollback(); raise
    finally: c.close()

def send_email(address, subject, body):
    """Send only from explicitly configured SMTP; missing config never reports sent."""
    import smtplib
    from email.message import EmailMessage
    host=os.environ.get("BTF_SMTP_HOST","").strip()
    try: port=int(os.environ.get("BTF_SMTP_PORT","587"))
    except ValueError: return {"mode":"unconfigured","sent":False}
    username=os.environ.get("BTF_SMTP_USERNAME","").strip()
    password=os.environ.get("BTF_SMTP_PASSWORD","")
    sender=os.environ.get("BTF_SMTP_FROM","").strip()
    if not (host and sender and username and password):
        return {"mode":"unconfigured","sent":False}
    message=EmailMessage(); message["From"]=sender; message["To"]=address
    message["Subject"]=subject; message.set_content(body)
    try:
        with smtplib.SMTP(host,port,timeout=15) as server:
            server.starttls(); server.login(username,password); server.send_message(message)
        return {"mode":"smtp","sent":True}
    except Exception:
        return {"mode":"smtp","sent":False,"error":"provider_delivery_failed"}

def send_invitation_email(address, account):
    body=(f"Your request for a Beyond the Frame account for {account['sector_number']} has been granted.\n\n"
          f"Login email: {address}\nTemporary password: {account['temporary_password']}\n\n"
          "Please sign in using your registered email and this one-time temporary password. You will be asked to set your own password before you can continue using the account.\n\n"
          "For your security, do not share this temporary password.")
    return send_email(address,"Beyond the Frame sector invitation",body)

def send_email_code(address, code):
    return send_email(address,"Beyond the Frame verification code",
                      f"Your password-reset code is {code}. It expires in five minutes.")

def decide_request(root,auth,request_id,admin_id,decision,note="",geometry=None):
    require_document_security()
    if decision not in {"approved","rejected"}: raise RegistryError("invalid_decision","Decision must be approved or rejected.")
    normalized_geometry = None
    if decision == "approved":
        try:
            normalized_geometry = normalize_polygon(geometry)
        except (ValueError, AttributeError) as error:
            raise RegistryError("invalid_sector_geometry", str(error)) from error
    account=None
    try:
        with _db(root) as c:
            row=c.execute("SELECT * FROM sector_registry_requests WHERE request_id=? AND assigned_admin_id=?",(request_id,admin_id)).fetchone()
            if row is None: raise RegistryError("request_not_found","Registry request not found for this reviewer.",404)
            if row["status"]!="pending": raise RegistryError("request_already_decided","This request has already been decided.",409)
            if decision=="approved":
                if row["base_camp_lat"] is None or row["base_camp_lon"] is None or row["location_capture_method"] not in {"DEVICE_HARDWARE","ADDRESS_GEOCODING","MANUAL_MAP_CLICK"}:
                    raise RegistryError("location_required","This older request has no verified base-camp pin. Ask the sector head to submit a new request with one of the three location methods.",409)
                set_sector_geometry(c, row["sector_id"], normalized_geometry)
                account=auth.create_sector_account(row["sector_id"], username=row["head_email"])
                if not account["created"]:
                    raise RegistryError("sector_account_exists","A shared account already exists for this sector; HQ must reconcile the account before approval.",409)
            c.execute("UPDATE sector_registry_requests SET status=?,decided_at=?,decided_by=?,decision_note=? WHERE request_id=?",(decision,_stamp(),admin_id," ".join(note.split())[:500],request_id))
            c.commit()
    except Exception:
        # Registry and authentication databases cannot share one SQLite transaction;
        # compensate an account created for a decision that did not commit.
        if account and account.get("created"):
            auth.delete_sector_account(row["sector_id"])
        raise
    purge_documents(root)
    delivery=send_invitation_email(row["head_email"],account) if account else {"mode":"not_sent"}
    return {"request_id":request_id,"decision":decision,"account":account,"delivery":delivery}

def decommission_sector(root, sector_id, admin_id, reason):
    """Disable a sector while retaining request, member, document, and audit rows."""
    if not isinstance(sector_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", sector_id.strip()):
        raise RegistryError("invalid_sector_id", "A valid sector ID is required.")
    if not isinstance(reason, str):
        raise RegistryError("decommission_reason_required", "Enter a reason for decommissioning this sector.")
    reason = " ".join(reason.split())
    if not 5 <= len(reason) <= 500 or any(ord(ch) < 32 for ch in reason):
        raise RegistryError("invalid_decommission_reason", "The reason must contain 5 to 500 valid characters.")
    sector_id = sector_id.strip().upper()
    _audit_key()
    c = _db(root)
    try:
        c.execute("BEGIN IMMEDIATE")
        sector = c.execute("SELECT is_active FROM headquarters_sectors WHERE sector_id=?", (sector_id,)).fetchone()
        if sector is None:
            raise RegistryError("sector_not_found", "The configured sector was not found.", 404)
        request = c.execute("""SELECT request_id FROM sector_registry_requests
            WHERE sector_id=? AND status='approved' AND is_active=1
            ORDER BY decided_at DESC LIMIT 1""", (sector_id,)).fetchone()
        if not sector["is_active"]:
            previous = c.execute("""SELECT request_id FROM sector_registry_requests
                WHERE sector_id=? AND status='approved' AND is_active=0 ORDER BY decided_at DESC LIMIT 1""", (sector_id,)).fetchone()
            if previous is None:
                raise RegistryError("sector_not_active", "This sector is already inactive.", 409)
            c.commit()
            return {"sector_id": sector_id, "request_id": previous["request_id"], "decommissioned": True, "already_decommissioned": True}
        if request is None:
            raise RegistryError("approved_request_not_found", "Only a sector with an approved registry can be decommissioned.", 409)
        now = _stamp()
        c.execute("UPDATE headquarters_sectors SET is_active=0,decommissioned_at=?,decommission_reason=?,updated_at=CURRENT_TIMESTAMP WHERE sector_id=?",
                  (now, reason, sector_id))
        c.execute("UPDATE sector_registry_requests SET is_active=0 WHERE request_id=?", (request["request_id"],))
        c.execute("UPDATE authorized_drones SET status='revoked',updated_at=CURRENT_TIMESTAMP WHERE sector_id=? AND status='approved'", (sector_id,))
        _audit(c, admin_id, request["request_id"], "sector_decommissioned", reason)
        c.commit()
        return {"sector_id": sector_id, "request_id": request["request_id"], "decommissioned": True,
                "already_decommissioned": False, "decommissioned_at": now, "reason": reason}
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def list_decommissioned_sectors(root):
    database = headquarters_database(root)
    if not database.exists():
        return []
    with _db(root) as c:
        rows=c.execute("""SELECT a.audit_id,r.sector_id,s.name sector_name,r.head_email,
            a.detail reason,a.occurred_at decommissioned_at,a.admin_id
            FROM registry_audit_log a JOIN sector_registry_requests r ON r.request_id=a.request_id
            JOIN headquarters_sectors s ON s.sector_id=r.sector_id
            WHERE a.action='sector_decommissioned' ORDER BY a.audit_id DESC""").fetchall()
        return [dict(row) for row in rows]


def request_reset(root,request_id,sector_number,user_id,head_member_id):
    if not isinstance(request_id,str) or not isinstance(sector_number,str) or not isinstance(head_member_id,str):
        raise RegistryError("invalid_reset_request","Sector request and registered head ID are required.")
    with _db(root) as c:
        row=c.execute("""SELECT r.head_email,m.member_id FROM sector_registry_requests r JOIN sector_registry_members m ON m.request_id=r.request_id AND m.member_role='head' WHERE r.request_id=? AND r.sector_id=? AND r.status='approved' AND r.is_active=1""",(request_id,sector_number)).fetchone()
        if row is None or not hmac.compare_digest(str(row["member_id"]).casefold(),head_member_id.strip().casefold()): raise RegistryError("head_verification_failed","Registered sector-head verification failed.",403)
        pending=c.execute("SELECT reset_id FROM sector_password_reset_requests WHERE request_id=? AND initiated_by_user_id=? AND status='pending' AND verified_at IS NOT NULL",(request_id,user_id)).fetchone()
        if pending: raise RegistryError("reset_already_pending","A password reset is already waiting for HQ review.",429)
        reset="RST_"+secrets.token_hex(16); created=_stamp()
        c.execute("INSERT INTO sector_password_reset_requests(reset_id,request_id,sector_number,initiated_by_user_id,head_member_id,created_at,status,verified_at) VALUES(?,?,?,?,?,?,'pending',?)",(reset,request_id,sector_number,user_id,row["member_id"],created,created))
    return {"reset_id":reset,"status":"pending_hq_review","email":row["head_email"]}

def list_resets(root):
    with _db(root) as c:
        rows=c.execute("""SELECT x.reset_id,x.request_id,x.sector_number,x.head_member_id,x.created_at,
          r.head_email FROM sector_password_reset_requests x JOIN sector_registry_requests r USING(request_id)
          WHERE x.status='pending' AND x.verified_at IS NOT NULL AND r.is_active=1 ORDER BY x.created_at""").fetchall()
        return [dict(row) for row in rows]

def decide_reset(root,auth,reset_id,admin_id,approve):
    with _db(root) as c:
        row=c.execute("SELECT x.*,r.head_email FROM sector_password_reset_requests x JOIN sector_registry_requests r USING(request_id) WHERE x.reset_id=? AND x.status='pending' AND x.verified_at IS NOT NULL AND r.is_active=1",(reset_id,)).fetchone()
        if row is None: raise RegistryError("reset_not_found","Pending password-reset request not found.",404)
        c.execute("UPDATE sector_password_reset_requests SET status=?,decided_by=?,decided_at=? WHERE reset_id=?",("approved" if approve else "rejected",admin_id,_stamp(),reset_id))
    if not approve: return {"reset_id":reset_id,"status":"rejected"}
    account=auth.reset_sector_password(row["sector_number"]); auth.invalidate_user_sessions(account["user_id"])
    delivery=send_invitation_email(row["head_email"],account)
    with _db(root) as c: c.execute("UPDATE sector_password_reset_requests SET status='completed' WHERE reset_id=?",(reset_id,))
    return {"reset_id":reset_id,"status":"completed","account":account,"delivery":delivery}

def audit_entries(root,admin_id):
    with _db(root) as c:
        return [dict(r) for r in c.execute("SELECT * FROM registry_audit_log WHERE admin_id=? ORDER BY audit_id DESC LIMIT 500",(admin_id,))]



def active_sector_geometries(root):
    with _db(root) as c:
        active_ids = {str(row[0]) for row in c.execute("""SELECT DISTINCT r.sector_id
            FROM sector_registry_requests r JOIN headquarters_sectors s USING(sector_id)
            WHERE r.status='approved' AND r.is_active=1 AND s.is_active=1""")}
    return list_sector_geometries(root, active_ids)
