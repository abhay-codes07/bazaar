"""RFC 9421 HTTP Message Signatures (Ed25519) — the Web Bot Auth / Visa TAP shape.

Covered components: ``@method``, ``@authority``, ``@path``, ``content-digest`` (when a body is
present). Parameters: ``created``, ``expires``, ``nonce``, ``keyid``, ``alg``, ``tag``.
Verification performs the seven checks from the Cloudflare/Visa write-up: headers present, key
lookup, freshness, nonce replay, tag, signature-base reconstruction, Ed25519 verify.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bazaar.trust import keys

TAG_BROWSE = "agent-browse"
TAG_PAY = "agent-pay"
MAX_SKEW_SECONDS = 300
_LABEL = "sig1"


def content_digest(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


def _signature_base(method: str, authority: str, path: str, digest: str | None, params: str) -> bytes:
    lines = [f'"@method": {method.upper()}', f'"@authority": {authority}', f'"@path": {path}']
    if digest is not None:
        lines.append(f'"content-digest": {digest}')
    lines.append(f'"@signature-params": {params}')
    return "\n".join(lines).encode()


def _params(components: list[str], keyid: str, created: int, expires: int, nonce: str, tag: str) -> str:
    comps = " ".join(f'"{c}"' for c in components)
    return f'({comps});created={created};expires={expires};nonce="{nonce}";keyid="{keyid}";alg="ed25519";tag="{tag}"'


def sign_request(priv: Ed25519PrivateKey, keyid: str, method: str, authority: str, path: str, body: bytes = b"", tag: str = TAG_BROWSE, now: int | None = None, ttl: int = 120) -> dict[str, str]:
    """Return the headers to attach: Signature-Input, Signature, and Content-Digest when a body exists."""
    created = now or int(time.time())
    expires = created + ttl
    nonce = secrets.token_urlsafe(12)
    components = ["@method", "@authority", "@path"]
    digest = None
    headers: dict[str, str] = {}
    if body:
        digest = content_digest(body)
        components.append("content-digest")
        headers["Content-Digest"] = digest
    params = _params(components, keyid, created, expires, nonce, tag)
    base = _signature_base(method, authority, path, digest, params)
    sig = keys.sign(priv, base)
    headers["Signature-Input"] = f"{_LABEL}={params}"
    headers["Signature"] = f"{_LABEL}=:{base64.b64encode(sig).decode()}:"
    return headers


@dataclass
class VerifiedSignature:
    keyid: str
    tag: str
    created: int
    nonce: str


class SignatureError(Exception):
    def __init__(self, step: str, detail: str):
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


_PARAM_RX = re.compile(r'\((?P<comps>[^)]*)\);created=(?P<created>\d+);expires=(?P<expires>\d+);nonce="(?P<nonce>[^"]+)";keyid="(?P<keyid>[^"]+)";alg="(?P<alg>[^"]+)";tag="(?P<tag>[^"]+)"')


class NonceCache:
    def __init__(self, ttl: int = MAX_SKEW_SECONDS * 2):
        self._seen: dict[str, float] = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    def check_and_add(self, nonce: str, now: float | None = None) -> bool:
        now = now or time.time()
        with self._lock:
            for k in [k for k, t in self._seen.items() if t < now - self._ttl]:
                del self._seen[k]
            if nonce in self._seen:
                return False
            self._seen[nonce] = now
            return True


def verify_request(headers: dict[str, str], method: str, authority: str, path: str, body: bytes, lookup_public_key, nonces: NonceCache, now: int | None = None, required_tag: str | None = None) -> VerifiedSignature:
    """``lookup_public_key(keyid)`` → Ed25519PublicKey | None. Raises :class:`SignatureError` naming the failed step."""
    h = {k.lower(): v for k, v in headers.items()}
    # 1. headers present
    if "signature-input" not in h or "signature" not in h:
        raise SignatureError("headers", "Signature-Input / Signature missing")
    si = h["signature-input"]
    if not si.startswith(f"{_LABEL}="):
        raise SignatureError("headers", "unknown signature label")
    params = si[len(_LABEL) + 1 :]
    m = _PARAM_RX.fullmatch(params)
    if not m:
        raise SignatureError("headers", "malformed Signature-Input")
    if m.group("alg") != "ed25519":
        raise SignatureError("headers", "unsupported alg")
    # 2. key lookup
    pub = lookup_public_key(m.group("keyid"))
    if pub is None:
        raise SignatureError("key", f"unknown keyid {m.group('keyid')}")
    # 3. freshness
    now = now or int(time.time())
    created, expires = int(m.group("created")), int(m.group("expires"))
    if created > now + MAX_SKEW_SECONDS or expires < now or expires - created > 3600:
        raise SignatureError("freshness", "signature expired or created in the future")
    # 4. nonce replay
    if not nonces.check_and_add(m.group("nonce"), now):
        raise SignatureError("replay", "nonce already used")
    # 5. tag
    tag = m.group("tag")
    if required_tag and tag != required_tag:
        raise SignatureError("tag", f"expected {required_tag}, got {tag}")
    # 6. reconstruct base
    comps = [c.strip('"') for c in m.group("comps").split()]
    digest = None
    if "content-digest" in comps:
        digest = h.get("content-digest")
        if digest is None or digest != content_digest(body):
            raise SignatureError("digest", "content-digest missing or does not match body")
    elif body:
        raise SignatureError("digest", "body present but not covered by the signature")
    base = _signature_base(method, authority, path, digest, params)
    # 7. verify
    sig_hdr = h["signature"]
    if not sig_hdr.startswith(f"{_LABEL}=:") or not sig_hdr.endswith(":"):
        raise SignatureError("signature", "malformed Signature header")
    sig = base64.b64decode(sig_hdr[len(_LABEL) + 2 : -1])
    if not keys.verify(pub, sig, base):
        raise SignatureError("signature", "Ed25519 verification failed")
    return VerifiedSignature(keyid=m.group("keyid"), tag=tag, created=created, nonce=m.group("nonce"))
