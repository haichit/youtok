import base64
import hashlib
import json
from datetime import datetime

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from sqlalchemy.orm import Session

from youtok.config import settings
from youtok.db.crud import create_license, get_license_by_hash
from youtok.db.models import License
from youtok.license.machine_id import get_machine_id


class InvalidLicense(Exception):
    pass


def _pad_b32(s: str) -> str:
    pad = (8 - len(s) % 8) % 8
    return s + "=" * pad


def verify_key(key: str) -> dict:
    if not key.startswith("YOUTOK-"):
        raise InvalidLicense("Key must start with YOUTOK-")

    parts = key[7:].rsplit("-", 1)
    if len(parts) != 2:
        raise InvalidLicense("Invalid key format")

    payload_b32, sig_b32 = parts
    payload_bytes = base64.b32decode(_pad_b32(payload_b32))
    sig_bytes = base64.b32decode(_pad_b32(sig_b32))

    pub_key_pem = settings.public_key_path.read_bytes()
    pub_key = load_pem_public_key(pub_key_pem)

    pub_key.verify(
        sig_bytes,
        payload_bytes,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )

    payload = json.loads(payload_bytes)

    if payload.get("exp"):
        exp_dt = datetime.fromisoformat(payload["exp"].rstrip("Z"))
        if exp_dt < datetime.utcnow():
            raise InvalidLicense("Key has expired")

    return payload


def activate(key: str, db: Session) -> License:
    payload = verify_key(key)
    key_hash = hashlib.sha256(key.encode()).hexdigest()

    existing = get_license_by_hash(db, key_hash)
    if existing:
        raise InvalidLicense("Key already activated")

    machine_id = get_machine_id()

    expires_at = None
    if payload.get("exp"):
        expires_at = datetime.fromisoformat(payload["exp"].rstrip("Z"))

    lic = create_license(
        db,
        key_hash=key_hash,
        email=payload["email"],
        machine_id=machine_id,
        expires_at=expires_at,
        max_jobs_per_day=payload.get("max_jobs_per_day"),
        features_json=json.dumps(payload.get("features", ["base"])),
        status="active",
    )

    cache = {
        "license_id": lic.id,
        "machine_id": machine_id,
        "activated_at": datetime.utcnow().isoformat(),
    }
    settings.license_cache_path.write_text(
        json.dumps(cache, indent=2), encoding="utf-8"
    )

    return lic


def is_activated() -> bool:
    if not settings.license_cache_path.exists():
        return False

    try:
        cache = json.loads(
            settings.license_cache_path.read_text(encoding="utf-8")
        )
        current_mid = get_machine_id()
        return cache.get("machine_id") == current_mid
    except Exception:
        return False
