"""Offline HMAC credentials and evidence authentication.

Local shared-key authentication only: not OIDC, public-key signatures, or KMS.
Tokens bind identity, issuer, audience, and expiry; headers cannot change them.
"""
from __future__ import annotations

import base64
import hmac
import json
from hashlib import sha256
from time import time


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def require_key(key: bytes):
    if len(key) < 32:
        raise ValueError("Use a random local secret of at least 32 bytes")


def issue_token(key: bytes, tenant: str, subject: str, *, ttl: int = 900, now=None):
    require_key(key)
    if not tenant or not subject or not 1 <= ttl <= 3600:
        raise ValueError("Identity and TTL (1..3600 seconds) required")
    issued = int(time() if now is None else now)
    payload = {"iss": "rewindguard-local", "aud": "rewindguard-api", "tenant_id": tenant,
               "subject_id": subject, "iat": issued, "exp": issued + ttl}
    encoded = base64.urlsafe_b64encode(canonical(payload)).rstrip(b"=")
    signature = hmac.new(key, b"rgx-local-v1." + encoded, sha256).hexdigest()
    return "rgx-local-v1." + encoded.decode() + "." + signature


def verify_token(key: bytes, token: str, *, now=None):
    require_key(key)
    if len(token) > 4096:
        raise ValueError("Invalid token")
    try:
        prefix, encoded, signature = token.split(".")
        expected = hmac.new(key, (prefix + "." + encoded).encode(), sha256).hexdigest()
        if prefix != "rgx-local-v1" or not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid signature")
        payload = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        current = time() if now is None else now
        if (payload["iss"] != "rewindguard-local" or payload["aud"] != "rewindguard-api"
                or type(payload["exp"]) is not int or type(payload["iat"]) is not int
                or not payload["iat"] <= current < payload["exp"]
                or not 0 < payload["exp"] - payload["iat"] <= 3600
                or not isinstance(payload["tenant_id"], str) or not payload["tenant_id"]
                or not isinstance(payload["subject_id"], str) or not payload["subject_id"]):
            raise ValueError("Invalid claims")
        return payload
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid token") from error


class LocalEvidenceSigner:
    """Authenticates a complete certificate, including its integrity digest."""
    def __init__(self, key: bytes, key_id: str = "local-v1"):
        require_key(key)
        self.key, self.key_id = key, key_id

    def sign(self, certificate):
        from dataclasses import asdict
        if not certificate.verify_integrity():
            raise ValueError("Certificate integrity mismatch")
        payload = {"algorithm": "HMAC-SHA256-LOCAL", "key_id": self.key_id,
                   "certificate": asdict(certificate)}
        return {**payload, "signature": hmac.new(self.key, canonical(payload), sha256).hexdigest()}

    def verify(self, envelope):
        try:
            if set(envelope) != {"algorithm", "key_id", "certificate", "signature"}:
                return False
            payload = {k: v for k, v in envelope.items() if k != "signature"}
            return (payload["algorithm"] == "HMAC-SHA256-LOCAL" and payload["key_id"] == self.key_id
                    and hmac.compare_digest(hmac.new(self.key, canonical(payload), sha256).hexdigest(),
                                            envelope["signature"]))
        except (TypeError, ValueError, KeyError):
            return False
