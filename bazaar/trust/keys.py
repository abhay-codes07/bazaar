"""Ed25519 helpers shared by the registry, HTTP signatures and mandates."""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def generate() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def public_bytes(priv_or_pub: Ed25519PrivateKey | Ed25519PublicKey) -> bytes:
    pub = priv_or_pub.public_key() if isinstance(priv_or_pub, Ed25519PrivateKey) else priv_or_pub
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def public_from_bytes(raw: bytes) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(raw)


def sign(priv: Ed25519PrivateKey, data: bytes) -> bytes:
    return priv.sign(data)


def verify(pub: Ed25519PublicKey, sig: bytes, data: bytes) -> bool:
    try:
        pub.verify(sig, data)
        return True
    except InvalidSignature:
        return False


def keyid_for(pub_raw: bytes) -> str:
    import hashlib

    return "ak_" + hashlib.sha256(pub_raw).hexdigest()[:16]
