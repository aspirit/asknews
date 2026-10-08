from __future__ import annotations

import hashlib
import re

from fastapi.testclient import TestClient

from asknews.answer.llm import ScriptedLLM
from asknews.web.assets import STATIC_DIR, asset, version

Api = tuple[TestClient, dict[str, int], ScriptedLLM]


def test_asset_url_carries_the_content_hash() -> None:
    digest = hashlib.sha256((STATIC_DIR / "ask.js").read_bytes()).hexdigest()[:8]
    assert version("ask.js") == digest
    assert asset("ask.js") == f"/static/ask.js?v={digest}"


def test_pages_reference_versioned_scripts_and_styles(api: Api) -> None:
    client, _, _ = api
    home = client.get("/").text
    assert re.search(r'href="/static/app\.css\?v=[0-9a-f]{8}"', home)
    assert re.search(r'src="/static/app\.js\?v=[0-9a-f]{8}"', home)
    ask = client.get("/ask").text
    assert f'src="{asset("ask.js")}"' in ask
    assert client.get(asset("ask.js")).status_code == 200


def test_static_files_are_served_by_the_app(api: Api) -> None:
    client: TestClient = api[0]
    assert client.get("/static/app.js").headers["content-type"].startswith("text/javascript")
