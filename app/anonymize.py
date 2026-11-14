import hashlib
import hmac

from . import config


def anon_id(did: str, key: str | None = None) -> str:
    """Public pseudonym: acct_ + first 12 hex chars of HMAC-SHA256(key, did)."""
    k = config.ANON_HMAC_KEY if key is None else key
    if not k:
        raise RuntimeError("ANON_HMAC_KEY is not set")
    return "acct_" + hmac.new(k.encode(), did.encode(), hashlib.sha256).hexdigest()[:12]
