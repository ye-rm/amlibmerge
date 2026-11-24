"""HTTP client for Apple Music internal web API (amp-api.music.apple.com)."""

from __future__ import annotations

import random
import re
import time
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from amlibmerge.config import decode_jwt_parts, get_jwt_exp, is_jwt_valid

AMP_API_BASE = "https://amp-api.music.apple.com"
MUSIC_WEB_BASE = "https://music.apple.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_ASSET_JS_RE = re.compile(r"""(?:src|href)=["'](/assets/[^"']+\.js)["']""")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{20,}")


class AppleMusicAPIError(RuntimeError):
    """Raised when an Apple Music API request fails."""

    def __init__(self, status_code: int, message: str, url: str = "") -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} ({url}): {message}")


class AppleMusicAuthError(AppleMusicAPIError):
    """Raised on 401/403 authentication or authorization failures."""


def fetch_web_developer_token(timeout: float = 20.0) -> tuple[str, int | None]:
    """Fetch Apple Music's public web player JWT from music.apple.com JS bundle."""
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(http2=True, timeout=timeout, follow_redirects=True) as client:
        html_text = ""
        for entry_url in (f"{MUSIC_WEB_BASE}/us/browse", MUSIC_WEB_BASE):
            resp = client.get(entry_url, headers=headers)
            if resp.status_code == 200 and "/assets/" in resp.text:
                html_text = resp.text
                break
        if not html_text:
            raise RuntimeError("Failed to fetch music.apple.com HTML to locate JS assets.")

        raw_assets = list(dict.fromkeys(_ASSET_JS_RE.findall(html_text)))
        if not raw_assets:
            raise RuntimeError("No /assets/*.js bundles found on music.apple.com.")

        # Prioritize index bundles first, as Apple embeds the MusicKit JWT there.
        ordered_assets = sorted(
            raw_assets,
            key=lambda p: (0 if "/index" in p.lower() else 1, len(p)),
        )

        for asset_path in ordered_assets[:10]:
            asset_url = urljoin(MUSIC_WEB_BASE, asset_path)
            js_resp = client.get(asset_url, headers={"User-Agent": DEFAULT_USER_AGENT})
            if js_resp.status_code != 200:
                continue
            for candidate in _JWT_RE.findall(js_resp.text):
                parts = decode_jwt_parts(candidate)
                if not parts:
                    continue
                jwt_header, jwt_payload = parts
                if jwt_header.get("alg") == "ES256" and jwt_header.get("kid"):
                    exp = jwt_payload.get("exp")
                    exp_int = int(exp) if isinstance(exp, (int, float)) else None
                    if is_jwt_valid(candidate, exp_int, margin_seconds=60):
                        return candidate, exp_int

    raise RuntimeError(
        "Could not extract a valid ES256 web developer token from music.apple.com JS bundles."
    )


def _merge_query_params(next_url: str, fallback_params: dict[str, Any] | None) -> str:
    """Ensure pagination `next` URLs retain important query params like `include`."""
    if not fallback_params:
        return next_url
    parsed = urlparse(next_url)
    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in fallback_params.items():
        if key == "offset":
            continue
        if value is not None and key not in existing:
            existing[key] = str(value)
    new_query = urlencode(existing)
    return urlunparse(parsed._replace(query=new_query))


class AppleMusicClient:
    """Rate-limited, retrying client for amp-api.music.apple.com (Phase 1 read-only)."""

    def __init__(
        self,
        media_user_token: str,
        developer_token: str | None = None,
        *,
        base_url: str = AMP_API_BASE,
        timeout: float = 30.0,
        min_delay: float = 0.25,
        max_delay: float = 0.6,
        max_retries: int = 4,
    ) -> None:
        self.media_user_token = media_user_token.strip()
        self.developer_token = developer_token.strip() if developer_token else None
        self.developer_token_exp: int | None = (
            get_jwt_exp(self.developer_token) if self.developer_token else None
        )
        self.base_url = base_url.rstrip("/")
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self._last_request_ts = 0.0
        self._client = httpx.Client(http2=True, timeout=timeout, follow_redirects=True)

    def __enter__(self) -> AppleMusicClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def ensure_developer_token(self, force_refresh: bool = False) -> tuple[str, int | None]:
        if not force_refresh and is_jwt_valid(self.developer_token, self.developer_token_exp):
            assert self.developer_token is not None
            return self.developer_token, self.developer_token_exp
        token, exp = fetch_web_developer_token()
        self.developer_token = token
        self.developer_token_exp = exp
        return token, exp

    def _build_headers(self) -> dict[str, str]:
        dev_token, _ = self.ensure_developer_token()
        return {
            "Authorization": f"Bearer {dev_token}",
            "media-user-token": self.media_user_token,
            "Origin": MUSIC_WEB_BASE,
            "Referer": f"{MUSIC_WEB_BASE}/",
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        }

    def _pace(self) -> None:
        if self.max_delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        target_delay = random.uniform(self.min_delay, self.max_delay)
        if self._last_request_ts > 0 and elapsed < target_delay:
            time.sleep(target_delay - elapsed)

    def request_raw(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> tuple[int, dict[str, Any] | None]:
        url = (
            path_or_url
            if path_or_url.startswith("http")
            else f"{self.base_url}/{path_or_url.lstrip('/')}"
        )

        refreshed_jwt = False
        for attempt in range(self.max_retries + 1):
            self._pace()
            headers = self._build_headers()
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            try:
                resp = self._client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                )
                self._last_request_ts = time.monotonic()
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise AppleMusicAPIError(0, f"Network error: {exc}", url) from exc
                time.sleep(min(2**attempt, 10) + random.uniform(0.1, 0.5))
                continue

            if resp.status_code in (202, 204):
                return resp.status_code, (resp.json() if resp.content else None)

            if resp.status_code == 404 and allow_404:
                return 404, None

            if resp.status_code == 401 and not refreshed_jwt:
                # Web developer JWT may have rotated; try refreshing once before failing.
                refreshed_jwt = True
                self.ensure_developer_token(force_refresh=True)
                continue

            if resp.status_code in (401, 403):
                detail = resp.text[:300].strip()
                raise AppleMusicAuthError(
                    resp.status_code,
                    f"Authentication rejected (media-user-token may be expired or invalid). {detail}",
                    url,
                )

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt >= self.max_retries:
                    raise AppleMusicAPIError(resp.status_code, resp.text[:300].strip(), url)
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep_s = float(retry_after) + random.uniform(0.2, 0.8)
                else:
                    sleep_s = min(2 ** (attempt + 1), 20) + random.uniform(0.2, 0.8)
                time.sleep(sleep_s)
                continue

            if resp.status_code >= 400:
                raise AppleMusicAPIError(resp.status_code, resp.text[:300].strip(), url)

            if not resp.content:
                return resp.status_code, None
            return resp.status_code, resp.json()

        raise AppleMusicAPIError(0, "Exhausted retries", url)

    def request_json(
        self,
        method: str,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> dict[str, Any] | None:
        _, payload = self.request_raw(
            method,
            path_or_url,
            params=params,
            json_body=json_body,
            allow_404=allow_404,
        )
        return payload

    def get_storefront(self) -> str:
        payload = self.request_json("GET", "/v1/me/storefront")
        data = (payload or {}).get("data") or []
        if not data or not isinstance(data[0], dict) or not data[0].get("id"):
            raise AppleMusicAPIError(200, "Unexpected /v1/me/storefront response", "/v1/me/storefront")
        return str(data[0]["id"])

    def iter_paginated(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """Yield each raw page JSON payload from a paginated Apple Music endpoint."""
        next_path: str | None = path
        first = True
        while next_path:
            page = self.request_json(
                "GET",
                next_path,
                params=params if first else None,
                allow_404=allow_404,
            )
            first = False
            if not page:
                break
            yield page
            raw_next = page.get("next")
            if isinstance(raw_next, str) and raw_next.strip():
                next_path = _merge_query_params(raw_next.strip(), params)
            else:
                next_path = None

    def iter_library_album_pages(
        self,
        *,
        limit: int = 100,
        include: str = "catalog",
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if include:
            params["include"] = include
        yield from self.iter_paginated("/v1/me/library/albums", params=params)

    def get_library_album_tracks_pages(
        self,
        library_album_id: str,
        *,
        limit: int = 100,
        include: str = "catalog",
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if include:
            params["include"] = include
        yield from self.iter_paginated(
            f"/v1/me/library/albums/{library_album_id}/tracks",
            params=params,
            allow_404=True,
        )

    def get_catalog_album(self, storefront: str, catalog_album_id: str) -> dict[str, Any] | None:
        payload = self.request_json(
            "GET",
            f"/v1/catalog/{storefront}/albums/{catalog_album_id}",
            allow_404=True,
        )
        data = (payload or {}).get("data") or []
        if data and isinstance(data[0], dict):
            return data[0]
        return None

    def iter_library_playlist_pages(self, *, limit: int = 100) -> Iterator[dict[str, Any]]:
        yield from self.iter_paginated("/v1/me/library/playlists", params={"limit": limit})

    def iter_playlist_tracks_pages(
        self,
        playlist_id: str,
        *,
        limit: int = 100,
        include: str = "catalog",
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if include:
            params["include"] = include
        yield from self.iter_paginated(
            f"/v1/me/library/playlists/{playlist_id}/tracks",
            params=params,
            allow_404=True,
        )

    def get_ratings(self, resource_type: str, resource_ids: list[str]) -> list[dict[str, Any]]:
        """Batch-fetch ratings for library-songs or library-albums (chunks of 100)."""
        results: list[dict[str, Any]] = []
        unique_ids = list(dict.fromkeys(i for i in resource_ids if i))
        for start in range(0, len(unique_ids), 100):
            batch = unique_ids[start : start + 100]
            payload = self.request_json(
                "GET",
                f"/v1/me/ratings/{resource_type}",
                params={"ids": ",".join(batch)},
                allow_404=True,
            )
            data = (payload or {}).get("data") or []
            results.extend(item for item in data if isinstance(item, dict))
        return results

    # --- Phase 2: Cloud library mutation & verification methods ---

    def get_library_album(self, library_album_id: str) -> dict[str, Any] | None:
        payload = self.request_json(
            "GET",
            f"/v1/me/library/albums/{library_album_id}",
            params={"include": "catalog"},
            allow_404=True,
        )
        data = (payload or {}).get("data") or []
        if data and isinstance(data[0], dict):
            return data[0]
        return None

    def check_catalog_album_in_library(
        self, storefront: str, catalog_album_id: str
    ) -> list[str]:
        """Return library-albums IDs currently linked to a catalog album ID."""
        payload = self.request_json(
            "GET",
            f"/v1/catalog/{storefront}/albums/{catalog_album_id}",
            params={"include": "library"},
            allow_404=True,
        )
        if not payload:
            return []
        ids: list[str] = []
        for item in payload.get("data") or []:
            rel = ((item.get("relationships") or {}).get("library") or {}).get("data") or []
            for lib_item in rel:
                if isinstance(lib_item, dict) and lib_item.get("id"):
                    ids.append(str(lib_item["id"]))
        for inc in payload.get("included") or []:
            if isinstance(inc, dict) and inc.get("type") == "library-albums" and inc.get("id"):
                cid = str(inc["id"])
                if cid not in ids:
                    ids.append(cid)
        return ids

    def delete_library_album(self, library_album_id: str) -> int:
        """Delete a library album via amp-api DELETE endpoint. Returns HTTP status code."""
        status, _ = self.request_raw(
            "DELETE",
            f"/v1/me/library/albums/{library_album_id}",
            allow_404=True,
        )
        return status

    def delete_library_song(self, library_song_id: str) -> int:
        """Delete a single library song via amp-api DELETE endpoint. Returns HTTP status code."""
        status, _ = self.request_raw(
            "DELETE",
            f"/v1/me/library/songs/{library_song_id}",
            allow_404=True,
        )
        return status

    def add_catalog_album_to_library(self, catalog_album_id: str) -> int:
        """Re-add a full catalog album to the user's cloud library (returns 202 on success)."""
        status, _ = self.request_raw(
            "POST",
            "/v1/me/library",
            params={"ids[albums]": catalog_album_id},
        )
        return status

    def add_tracks_to_playlist(self, playlist_id: str, catalog_song_ids: list[str]) -> list[int]:
        """Append catalog songs to a library playlist in chunks of 100."""
        statuses: list[int] = []
        unique_ids = [cid for cid in catalog_song_ids if cid]
        for start in range(0, len(unique_ids), 100):
            batch = unique_ids[start : start + 100]
            body = {"data": [{"id": cid, "type": "songs"} for cid in batch]}
            status, _ = self.request_raw(
                "POST",
                f"/v1/me/library/playlists/{playlist_id}/tracks",
                json_body=body,
            )
            statuses.append(status)
        return statuses

    def set_song_rating(self, catalog_song_id: str, value: int) -> int:
        """Set a catalog song's rating (1 = love/favorite, -1 = dislike)."""
        body = {"type": "rating", "attributes": {"value": value}}
        status, _ = self.request_raw(
            "PUT",
            f"/v1/me/ratings/songs/{catalog_song_id}",
            json_body=body,
        )
        return status
