import hashlib
import hmac
import re

import pytest

from app import config
from app.anonymize import anon_id


def test_format_and_value():
    a = anon_id("did:plc:abc", key="k")
    assert re.fullmatch(r"acct_[0-9a-f]{12}", a)
    assert a == "acct_" + hmac.new(b"k", b"did:plc:abc", hashlib.sha256).hexdigest()[:12]


def test_deterministic_and_key_dependent():
    assert anon_id("did:plc:abc", key="k") == anon_id("did:plc:abc", key="k")
    assert anon_id("did:plc:abc", key="k") != anon_id("did:plc:abc", key="k2")
    assert anon_id("did:plc:abc", key="k") != anon_id("did:plc:abd", key="k")


def test_missing_key_raises(monkeypatch):
    monkeypatch.setattr(config, "ANON_HMAC_KEY", "")
    with pytest.raises(RuntimeError):
        anon_id("did:plc:abc")
