"""HTTP fetching with an on-disk cache.

Every network access of the ingestion jobs goes through :class:`Fetcher`, so
re-runs cost nothing, sources are hit politely, and tests can inject an
``httpx`` transport instead of touching the network.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_USER_AGENT = "asknews/0.1 (+https://asknews.jp; archive ingestion)"
# How long a listing page (an index of releases, a calendar) is trusted
# before it is fetched again; the documents it points to are cached for good.
INDEX_MAX_AGE = timedelta(hours=6)


class FetchError(Exception):
    """A request failed after retries, or the server answered with an error."""

    def __init__(self, url: str, status: int | None, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.status = status


@dataclass(frozen=True, slots=True)
class Fetched:
    url: str
    status: int
    content: bytes
    content_type: str | None
    from_cache: bool

    @property
    def text(self) -> str:
        return self.content.decode(_charset(self.content_type), errors="replace")

    def json_body(self) -> dict[str, Any]:
        data = json.loads(self.text)
        if not isinstance(data, dict):
            raise FetchError(self.url, self.status, "expected a JSON object")
        return data


def _charset(content_type: str | None) -> str:
    if content_type:
        for part in content_type.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset" and value:
                return value.strip('"').lower()
    return "utf-8"


class Fetcher:
    """GET with caching, per-host pacing and bounded retries.

    Responses are cached by URL (plus any request headers that affect the
    answer, such as ``Accept``). A cached response is returned without any
    network access unless ``refresh=True`` or it is older than ``max_age``.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        min_interval: float = 0.5,
        timeout: float = 30.0,
        retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.retries = retries
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        refresh: bool = False,
        max_age: timedelta | None = None,
    ) -> Fetched:
        meta_path, body_path = self._cache_paths(url, headers)
        if not refresh and meta_path.exists() and body_path.exists():
            meta = json.loads(meta_path.read_text("utf-8"))
            age = time.time() - float(meta.get("fetched_at", 0))
            if max_age is None or age <= max_age.total_seconds():
                return Fetched(
                    url, meta["status"], body_path.read_bytes(), meta["content_type"], True
                )
        response = self._request(url, headers)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(response.content)
        meta_path.write_text(
            json.dumps(
                {
                    "url": url,
                    "status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "fetched_at": time.time(),
                }
            ),
            "utf-8",
        )
        return Fetched(
            url, response.status_code, response.content, response.headers.get("content-type"), False
        )

    def _request(self, url: str, headers: dict[str, str] | None) -> httpx.Response:
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            self._pace(host)
            try:
                response = self._client.get(url, headers=headers)
            except httpx.TransportError as exc:
                if attempt >= self.retries:
                    raise FetchError(url, None, f"transport error: {exc}") from exc
                retry_after = None
            else:
                if response.status_code < 400:
                    return response
                transient = response.status_code == 429 or response.status_code >= 500
                if not transient or attempt >= self.retries:
                    raise FetchError(url, response.status_code, f"HTTP {response.status_code}")
                retry_after = _retry_after(response.headers.get("retry-after"))
            attempt += 1
            self._sleep(retry_after if retry_after is not None else min(2.0**attempt, 10.0))

    def _pace(self, host: str) -> None:
        last = self._last_request.get(host)
        now = time.monotonic()
        if last is not None and now - last < self.min_interval:
            self._sleep(self.min_interval - (now - last))
        self._last_request[host] = time.monotonic()

    def _cache_paths(self, url: str, headers: dict[str, str] | None) -> tuple[Path, Path]:
        key_material = url + "\n" + json.dumps(sorted((headers or {}).items()))
        digest = hashlib.sha256(key_material.encode("utf-8")).hexdigest()[:32]
        host = urlsplit(url).netloc or "unknown"
        base = self.cache_dir / host / digest
        return base.with_suffix(".json"), base.with_suffix(".bin")


def _retry_after(value: str | None) -> float | None:
    """Seconds from a Retry-After header given as a delay; a date form is ignored."""
    if value is None or not value.strip().isdigit():
        return None
    return min(float(value.strip()), 60.0)
