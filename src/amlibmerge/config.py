"""Configuration, credential storage, and JWT helpers."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from amlibmerge.models import Credentials


def get_config_dir() -> Path:
    custom = os.environ.get("AMLIBMERGE_HOME")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".amlibmerge"


def get_credentials_path() -> Path:
    return get_config_dir() / "credentials.json"


def mask_token(token: str | None, keep: int = 6) -> str:
    if not token:
        return "<unset>"
    if len(token) <= keep * 2 + 3:
        return "*" * len(token)
    return f"{token[:keep]}...{token[-keep:]} (len={len(token)})"


def decode_jwt_parts(token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Decode JWT header and payload without signature verification."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        decoded: list[dict[str, Any]] = []
        for segment in parts[:2]:
            padded = segment + "=" * (-len(segment) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            obj = json.loads(raw.decode("utf-8"))
            if not isinstance(obj, dict):
                return None
            decoded.append(obj)
        return decoded[0], decoded[1]
    except Exception:
        return None


def get_jwt_exp(token: str) -> int | None:
    parts = decode_jwt_parts(token)
    if not parts:
        return None
    exp = parts[1].get("exp")
    if isinstance(exp, (int, float)):
        return int(exp)
    return None


def is_jwt_valid(token: str | None, exp: int | None = None, margin_seconds: int = 300) -> bool:
    if not token:
        return False
    effective_exp = exp if exp is not None else get_jwt_exp(token)
    if effective_exp is None:
        return True
    return time.time() + margin_seconds < effective_exp


def save_credentials(creds: Credentials) -> Path:
    path = get_credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.model_dump_json(indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_credentials() -> Credentials | None:
    env_user_token = os.environ.get("AM_MEDIA_USER_TOKEN", "").strip()
    env_dev_token = os.environ.get("AM_DEVELOPER_TOKEN", "").strip() or None

    path = get_credentials_path()
    file_creds: Credentials | None = None
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        file_creds = Credentials.model_validate(raw)

    if env_user_token:
        dev_token = env_dev_token or (file_creds.developer_token if file_creds else None)
        dev_exp = get_jwt_exp(dev_token) if dev_token else None
        return Credentials(
            media_user_token=env_user_token,
            developer_token=dev_token,
            developer_token_exp=dev_exp,
            storefront=file_creds.storefront if file_creds else None,
        )

    if file_creds and env_dev_token:
        file_creds.developer_token = env_dev_token
        file_creds.developer_token_exp = get_jwt_exp(env_dev_token)
    return file_creds


def clear_credentials() -> bool:
    path = get_credentials_path()
    if path.exists():
        path.unlink()
        return True
    return False
