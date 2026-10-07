from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from asknews.ingest.http import Fetched, Fetcher, FetchError


class Server:
    """Scripted transport: a queue of responses per URL, counting requests."""

    def __init__(self) -> None:
        self.queues: dict[str, list[httpx.Response]] = {}
        self.calls: list[httpx.Request] = []

    def respond(self, url: str, *responses: httpx.Response) -> None:
        self.queues.setdefault(url, []).extend(responses)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            queue = self.queues[str(request.url)]
            return queue.pop(0) if len(queue) > 1 else queue[0]

        return httpx.MockTransport(handler)


def fetcher(tmp_path: Path, server: Server) -> Fetcher:
    return Fetcher(tmp_path, transport=server.transport(), min_interval=0, sleep=lambda _s: None)


def test_second_get_is_served_from_cache(tmp_path: Path) -> None:
    server = Server()
    server.respond(
        "https://example.test/a",
        httpx.Response(200, text="hello", headers={"content-type": "text/plain"}),
    )
    with fetcher(tmp_path, server) as f:
        first = f.get("https://example.test/a")
        second = f.get("https://example.test/a")
    assert isinstance(first, Fetched)
    assert (first.from_cache, second.from_cache) == (False, True)
    assert second.text == "hello"
    assert len(server.calls) == 1


def test_refresh_bypasses_cache(tmp_path: Path) -> None:
    server = Server()
    server.respond(
        "https://example.test/a", httpx.Response(200, text="v1"), httpx.Response(200, text="v2")
    )
    with fetcher(tmp_path, server) as f:
        assert f.get("https://example.test/a").text == "v1"
        assert f.get("https://example.test/a", refresh=True).text == "v2"
        assert f.get("https://example.test/a").text == "v2"


def test_request_headers_are_part_of_the_cache_key(tmp_path: Path) -> None:
    server = Server()
    server.respond("https://example.test/a", httpx.Response(200, text="x"))
    with fetcher(tmp_path, server) as f:
        f.get("https://example.test/a")
        f.get("https://example.test/a", headers={"Accept": "application/json"})
    assert len(server.calls) == 2


def test_server_errors_are_retried_then_succeed(tmp_path: Path) -> None:
    server = Server()
    server.respond("https://example.test/a", httpx.Response(503), httpx.Response(200, text="ok"))
    with fetcher(tmp_path, server) as f:
        assert f.get("https://example.test/a").text == "ok"
    assert len(server.calls) == 2


def test_client_errors_are_not_retried(tmp_path: Path) -> None:
    server = Server()
    server.respond("https://example.test/missing", httpx.Response(404))
    with fetcher(tmp_path, server) as f, pytest.raises(FetchError) as exc:
        f.get("https://example.test/missing")
    assert exc.value.status == 404
    assert len(server.calls) == 1


def test_charset_from_content_type_is_honoured() -> None:
    accented = "caf" + chr(0xE9)
    body = accented.encode("latin-1")
    fetched = Fetched("u", 200, body, "text/html; charset=ISO-8859-1", False)
    assert fetched.text == accented
