from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from asknews.answer.llm import ScriptedLLM

pytestmark = pytest.mark.integration
Api = tuple[TestClient, dict[str, int], ScriptedLLM]


def test_health(api: Api) -> None:
    client, _, _ = api
    assert client.get("/healthz").json()["status"] == "ok"


def test_document_list_filters_and_detail(api: Api) -> None:
    client, ids, _ = api
    everything = client.get("/api/documents").json()
    assert [d["kind"] for d in everything] == ["minutes", "press_conference", "statement"]
    videos = client.get("/api/documents", params={"videos": "true"}).json()
    assert [d["id"] for d in videos] == [ids["presser"]]
    assert videos[0]["media"] == {
        "provider": "brightcove",
        "id": "6379710022112",
        "duration_sec": 3132,
    }
    assert videos[0]["speaker"] == "Jerome H. Powell"
    assert client.get("/api/documents", params={"kind": "poem"}).status_code == 422
    detail = client.get(f"/api/documents/{ids['presser']}").json()
    assert detail["segments"][0]["start_sec"] == 14.5
    assert client.get("/api/documents/999999").status_code == 404


def test_ask_answers_with_renumbered_citations(api: Api) -> None:
    client, _, llm = api
    llm.replies.append('{"paragraphs": ["The Fed lowered rates [1]."], "followups": ["Why?"]}')
    body = client.get(
        "/api/ask", params={"q": "What did the Fed decide about the federal funds rate?"}
    ).json()
    assert body["status"] == "answered"
    assert body["paragraphs"] == ["The Fed lowered rates [1]."]
    assert body["citations"][0]["n"] == 1
    assert body["citations"][0]["url"].startswith("/doc/")
    assert body["followups"] == ["Why?"]
    assert body["request_id"] is not None


def test_ask_declines_out_of_scope_and_validates_input(api: Api) -> None:
    client, _, _ = api
    assert (
        client.get("/api/ask", params={"q": "Best ramen in Tokyo?"}).json()["reason"]
        == "out_of_scope"
    )
    assert client.get("/api/ask", params={"q": "x"}).status_code == 422


def test_citations_only(api: Api) -> None:
    client, _, _ = api
    body = client.get("/api/ask/citations", params={"q": "federal funds rate decision"}).json()
    assert body
    assert body[0]["n"] == 1


def test_meetings_bundle_documents_by_date(api: Api) -> None:
    client, _, _ = api
    body = client.get("/api/meetings").json()
    assert len(body) == 1
    meeting = body[0]
    assert meeting["key"] == "fed:20250917"
    assert meeting["decision"]["text"] == "4 to 4-1/4 percent"
    assert (meeting["decision"]["change_bp"], meeting["decision"]["action"]) == (-25, "lower")
    assert meeting["decision"]["dissenters"] == []  # the fixture statement records no dissent
    status = {p["role"]: p["status"] for p in meeting["parts"]}
    assert status == {
        "statement": "present",
        "press_conference": "present",
        "minutes": "present",
        "outlook": "missing",  # a September meeting publishes projections
    }


def test_rankings(api: Api) -> None:
    client, _, _ = api
    body = client.get("/api/rankings", params={"days": 3650}).json()
    assert body["speakers"][0]["slug"] == "powell"
    assert body["speakers"][0]["press_conferences"] == 1
    assert body["speakers"][0]["minutes_spoken"] == 52
    assert any(t["slug"] == "policy-rate" and t["documents"] >= 1 for t in body["topics"])
    assert client.get("/api/briefs").json() == []


def test_prominence_prefers_core_publishers_and_decisions(api: Api) -> None:
    from asknews.archive.documents import prominent

    client, _, _ = api
    with client.app.state.runtime.pool.connection() as conn:  # type: ignore[attr-defined]
        ranked = prominent(conn, limit=10)
    assert [d.kind for d in ranked[:2]] == ["press_conference", "statement"]
