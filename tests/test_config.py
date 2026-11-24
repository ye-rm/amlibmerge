"""Unit tests for config, JWT parsing, and credential storage."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from amlibmerge.config import (
    clear_credentials,
    get_jwt_exp,
    is_jwt_valid,
    load_credentials,
    mask_token,
    save_credentials,
)
from amlibmerge.models import Credentials


def _make_fake_jwt(exp: int) -> str:
    h = base64.urlsafe_b64encode(json.dumps({"alg": "ES256", "kid": "TEST"}).encode()).rstrip(b"=")
    p = base64.urlsafe_b64encode(json.dumps({"iss": "TEAM", "exp": exp}).encode()).rstrip(b"=")
    return f"{h.decode()}.{p.decode()}.sig1234567890"


def test_jwt_exp_and_validity() -> None:
    future_exp = int(time.time()) + 3600
    past_exp = int(time.time()) - 60
    future_jwt = _make_fake_jwt(future_exp)
    past_jwt = _make_fake_jwt(past_exp)

    assert get_jwt_exp(future_jwt) == future_exp
    assert is_jwt_valid(future_jwt) is True
    assert is_jwt_valid(past_jwt) is False
    assert mask_token("abcdefghijklmnopqrstuvwxyz").startswith("abcdef...")


def test_credentials_save_load_env_override_and_clear(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AMLIBMERGE_HOME", str(tmp_path))
    monkeypatch.delenv("AM_MEDIA_USER_TOKEN", raising=False)
    monkeypatch.delenv("AM_DEVELOPER_TOKEN", raising=False)

    assert load_credentials() is None

    saved = Credentials(media_user_token="tok_file_1234567890", storefront="cn")
    save_credentials(saved)

    loaded = load_credentials()
    assert loaded is not None
    assert loaded.media_user_token == "tok_file_1234567890"
    assert loaded.storefront == "cn"

    monkeypatch.setenv("AM_MEDIA_USER_TOKEN", "tok_env_9999999999")
    env_loaded = load_credentials()
    assert env_loaded is not None
    assert env_loaded.media_user_token == "tok_env_9999999999"
    assert env_loaded.storefront == "cn"

    monkeypatch.delenv("AM_MEDIA_USER_TOKEN", raising=False)
    assert clear_credentials() is True
    assert load_credentials() is None
