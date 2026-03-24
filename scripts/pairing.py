#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone


def _b64_encode(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64_decode(token: str) -> dict:
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def build_invite(peer_id: str, endpoint: str, ttl_minutes: int = 10) -> str:
    nonce = secrets.token_hex(8)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    signature = hashlib.sha256(f"{peer_id}:{endpoint}:{nonce}:{expires_at}".encode("utf-8")).hexdigest()
    return _b64_encode(
        {
            "peerId": peer_id,
            "endpoint": endpoint,
            "nonce": nonce,
            "expiresAt": expires_at,
            "signature": signature,
        }
    )


def parse_invite(token: str) -> dict:
    payload = _b64_decode(token)
    expected = hashlib.sha256(
        f"{payload['peerId']}:{payload['endpoint']}:{payload['nonce']}:{payload['expiresAt']}".encode("utf-8")
    ).hexdigest()
    if payload.get("signature") != expected:
        raise SystemExit("Invalid invite signature")
    expires_at = datetime.fromisoformat(payload["expiresAt"])
    if expires_at < datetime.now(timezone.utc):
        raise SystemExit("Invite expired")
    return payload
