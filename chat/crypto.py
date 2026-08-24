"""
At-rest encryption for user-supplied provider keys (BYOK, Fix C).

Keys are NEVER stored or logged in plaintext. The Fernet key is derived from
the deployment's SECRET_KEY (sha256), so no extra secret to manage — with the
documented trade-off that rotating SECRET_KEY invalidates stored provider keys
(users just paste them again). Set CHAT_KEY_ENCRYPTION_SECRET to decouple the
two if you prefer independent rotation.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet() -> Fernet:
    secret = os.environ.get("CHAT_KEY_ENCRYPTION_SECRET") or settings.SECRET_KEY
    digest = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str | None:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None  # e.g. SECRET_KEY rotated — treat as no key stored
