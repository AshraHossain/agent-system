"""Read-only search API: results, input bounds, auth and the non-loopback refusal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from platewatch.api import (
    SECURITY_HEADERS,
    build_app,
    hash_token,
    parse_digests,
    serve,
    verify_token,
)
from platewatch.contracts import BoundingBox, PlateRecord
from platewatch.core.store import PlateStore

TOKEN = "s3cret-token"


def _rec(pid: str, text: str) -> PlateRecord:
    return PlateRecord(
        id=pid,
        image_sha256=f"sha-{pid}",
        source=f"{pid}.png",
        box=BoundingBox(x_min=0, y_min=0, x_max=5, y_max=5),
        raw_text=text,
        plate_text=text,
        detection_confidence=1,
        ocr_confidence=1,
        confidence=1,
        tags=[],
    )


@pytest.fixture
def store() -> PlateStore:
    s = PlateStore()
    s.add(_rec("1", "AB12CDE"))
    s.add(_rec("2", "KAA1000"))
    return s


@pytest.fixture
def client(store: PlateStore) -> TestClient:
    return TestClient(build_app(store))


# --- search ---------------------------------------------------------------------------


def test_search_returns_the_matching_plate(client: TestClient):
    body = client.get("/plates/search", params={"q": "AB12 CDE"}).json()
    assert [h["plate_text"] for h in body] == ["AB12CDE"]
    assert body[0]["exact"] is True and body[0]["sightings"] == 1


def test_search_tolerates_ocr_confusions(client: TestClient):
    body = client.get("/plates/search", params={"q": "KAAIOOO"}).json()
    assert "KAA1000" in [h["plate_text"] for h in body]


def test_search_with_no_match_is_an_empty_list(client: TestClient):
    r = client.get("/plates/search", params={"q": "ZZ99ZZZ"})
    assert r.status_code == 200 and r.json() == []


def test_query_that_normalises_to_nothing_is_empty_not_an_error(client: TestClient):
    r = client.get("/plates/search", params={"q": "---"})
    assert r.status_code == 200 and r.json() == []


@pytest.mark.parametrize(
    "params",
    [
        {},  # q is required
        {"q": ""},  # below min_length
        {"q": "A" * 65},  # above max_length
        {"q": "AB", "limit": 0},  # below the floor
        {"q": "AB", "limit": 51},  # above the cap
        {"q": "AB", "limit": "many"},  # not an integer
    ],
)
def test_bad_input_is_rejected_with_422(client: TestClient, params: dict):
    assert client.get("/plates/search", params=params).status_code == 422


def test_security_headers_are_present(client: TestClient):
    headers = client.get("/plates/search", params={"q": "AB12CDE"}).headers
    for key, value in SECURITY_HEADERS.items():
        assert headers[key] == value


# --- read-only ------------------------------------------------------------------------


def test_no_route_accepts_a_write_method(client: TestClient):
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)("/plates/search", params={"q": "AB12CDE"})
        assert r.status_code == 405, f"{method} must not be routed"


def test_search_does_not_change_the_store(client: TestClient, store: PlateStore):
    before = store.count()
    client.get("/plates/search", params={"q": "AB12CDE"})
    client.get("/plates/search", params={"q": "NOPE123"})
    assert store.count() == before


# --- probes ---------------------------------------------------------------------------


def test_probes_report_status_and_size(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready", "plates": 2}


def test_ready_reports_503_when_the_store_is_closed(store: PlateStore):
    client = TestClient(build_app(store), raise_server_exceptions=False)
    store.close()
    assert client.get("/ready").status_code == 503


# --- authentication -------------------------------------------------------------------


@pytest.fixture
def secured(store: PlateStore) -> TestClient:
    return TestClient(build_app(store, digests=[hash_token(TOKEN)]))


def test_search_needs_a_token_when_digests_are_configured(secured: TestClient):
    r = secured.get("/plates/search", params={"q": "AB12CDE"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": f"Bearer {TOKEN}"},
        {"X-API-Key": TOKEN},
    ],
)
def test_a_correct_token_is_accepted_either_way(secured: TestClient, headers: dict):
    r = secured.get("/plates/search", params={"q": "AB12CDE"}, headers=headers)
    assert r.status_code == 200 and r.json()[0]["plate_text"] == "AB12CDE"


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": TOKEN},  # missing the scheme
        {"Authorization": f"Basic {TOKEN}"},  # wrong scheme
        {"X-API-Key": "wrong-token"},
    ],
)
def test_a_wrong_token_is_refused(secured: TestClient, headers: dict):
    assert secured.get("/plates/search", params={"q": "A"}, headers=headers).status_code == 401


def test_probes_stay_open_so_containers_can_check_them(secured: TestClient):
    assert secured.get("/health").status_code == 200
    assert secured.get("/ready").status_code == 200


def test_only_the_digest_is_configured_never_the_token():
    digest = hash_token(TOKEN)
    assert TOKEN not in digest and len(digest) == 64
    assert verify_token(TOKEN, [digest])
    assert not verify_token("wrong-token", [digest])
    assert not verify_token(TOKEN, [])


def test_parse_digests_rejects_anything_that_is_not_a_digest():
    assert parse_digests(None) == ()
    assert parse_digests(f"{hash_token('a')}, {hash_token('a')}") == (hash_token("a"),)
    with pytest.raises(ValueError, match="SHA-256 hex digest"):
        parse_digests("not-a-digest")


# --- refusing to serve unauthenticated beyond loopback ---------------------------------


def test_serve_refuses_a_public_bind_without_a_token(store: PlateStore, monkeypatch):
    monkeypatch.delenv("PLATEWATCH_API_TOKEN_SHA256", raising=False)
    with pytest.raises(SystemExit, match="refusing to serve"):
        serve(store, host="0.0.0.0", port=8001)


def test_serve_allows_a_public_bind_when_explicitly_overridden(store: PlateStore, monkeypatch):
    """--allow-unauthenticated is for an authenticating proxy; it must reach uvicorn.run."""
    monkeypatch.delenv("PLATEWATCH_API_TOKEN_SHA256", raising=False)
    started: dict[str, object] = {}
    monkeypatch.setattr(
        "uvicorn.run", lambda app, **kw: started.update(kw) or started.update(app=app)
    )
    serve(store, host="0.0.0.0", port=8001, allow_unauthenticated=True)
    assert started["host"] == "0.0.0.0" and started["port"] == 8001


def test_serve_on_loopback_needs_no_token(store: PlateStore, monkeypatch):
    monkeypatch.delenv("PLATEWATCH_API_TOKEN_SHA256", raising=False)
    started: dict[str, object] = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(kw))
    serve(store, host="127.0.0.1", port=8001)
    assert started["host"] == "127.0.0.1"


# --- CLI wiring -----------------------------------------------------------------------


def test_cli_serve_passes_an_open_store_and_the_bind_options(tmp_path, monkeypatch):
    """`platewatch serve` must hand `serve` a live store: it is used after the CLI returns."""
    from platewatch import api, cli

    seen: dict[str, object] = {}

    def fake_serve(store, host, port, allow_unauthenticated):
        seen.update(host=host, port=port, allow=allow_unauthenticated, plates=store.count())

    monkeypatch.setattr(api, "serve", fake_serve)
    db = tmp_path / "plates.db"
    assert cli.main(["--db", str(db), "serve", "--host", "127.0.0.1", "--port", "9123"]) == 0
    assert seen == {"host": "127.0.0.1", "port": 9123, "allow": False, "plates": 0}
