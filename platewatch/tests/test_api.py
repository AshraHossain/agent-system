"""Read-only `GET /plates/search` and `platewatch serve`."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from platewatch import cli
from platewatch.api import create_app
from platewatch.contracts import BoundingBox, PlateRecord
from platewatch.core.store import PlateStore

TOKEN = "t" * 40


def _rec(pid: str, text: str, image: str = "s") -> PlateRecord:
    return PlateRecord(
        id=pid,
        image_sha256=image,
        source=f"{pid}.png",
        box=BoundingBox(x_min=0, y_min=0, x_max=5, y_max=5),
        raw_text=text,
        plate_text=text,
        detection_confidence=1,
        ocr_confidence=0.9,
        confidence=0.9,
        tags=["plausible-format"],
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "plates.db"
    with PlateStore(path) as s:
        s.add(_rec("p1", "ABC1234"))
        s.add(_rec("p2", "ABC1234", image="t"))  # a second sighting of the same plate
        s.add(_rec("p3", "XYZ9081"))
    return path


@pytest.fixture
def client(db):
    with TestClient(create_app(db)) as c:
        yield c


def test_search_returns_exact_matches_first_with_sightings(client):
    r = client.get("/plates/search", params={"q": "abc-1234"})
    assert r.status_code == 200
    hits = r.json()
    assert {h["plate_id"] for h in hits[:2]} == {"p1", "p2"}
    assert all(h["exact"] and h["sightings"] == 2 for h in hits[:2])
    assert "raw_text" not in hits[0]  # only the SearchHit fields are exposed


def test_fuzzy_search_and_limits(client):
    hits = client.get("/plates/search", params={"q": "XYZ 908I"}).json()
    assert hits[0]["plate_id"] == "p3" and not hits[0]["exact"]
    assert len(client.get("/plates/search", params={"q": "ABC1234", "limit": 1}).json()) == 1
    strict = client.get("/plates/search", params={"q": "XYZ 908I", "min_score": 1}).json()
    assert strict == []  # fuzzy matches need min_score; exact ones always pass


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "x" * 65},
        {"q": "--- ..."},
        {"q": "ABC", "limit": 0},
        {"q": "ABC", "limit": 51},
        {"q": "ABC", "min_score": 1.5},
    ],
)
def test_bad_queries_are_422(client, params):
    assert client.get("/plates/search", params=params).status_code == 422


def test_only_reads_are_served(client):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/plates/search").status_code == 405, method
    assert client.get("/health").json() == {"status": "ok"}


def test_the_store_is_opened_read_only(db):
    with PlateStore(db, read_only=True) as ro:
        assert ro.count() == 3
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro.add(_rec("p9", "NEW1"))
    with pytest.raises(FileNotFoundError):
        PlateStore(db.parent / "missing.db", read_only=True)
    assert not (db.parent / "missing.db").exists()  # nothing was created


def test_token_is_required_when_configured(db):
    with TestClient(create_app(db, TOKEN)) as c:
        missing = c.get("/plates/search", params={"q": "ABC1234"})
        assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
        wrong = c.get(
            "/plates/search", params={"q": "ABC1234"}, headers={"Authorization": "Bearer nope"}
        )
        assert wrong.status_code == 401
        ok = c.get(
            "/plates/search", params={"q": "ABC1234"}, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert ok.status_code == 200 and ok.json()[0]["exact"]
        assert c.get("/health").status_code == 200  # probes need no token


# --- platewatch serve ----------------------------------------------------------------


@pytest.fixture
def served(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    monkeypatch.delenv("PLATEWATCH_API_TOKEN", raising=False)
    return calls


def test_serve_binds_loopback_by_default(db, served, capsys):
    assert cli.main(["--db", str(db), "serve"]) == 0
    assert served == [{"host": "127.0.0.1", "port": 8100}]
    assert capsys.readouterr().err == ""


def test_serve_refuses_other_hosts_without_a_token(db, served, capsys):
    assert cli.main(["--db", str(db), "serve", "--host", "0.0.0.0"]) == 2
    assert "refusing" in capsys.readouterr().err and served == []
    assert cli.main(["--db", str(db), "serve", "--host", "0.0.0.0", "--allow-unauthenticated"]) == 0
    assert "no authentication" in capsys.readouterr().err


def test_serve_with_a_token(db, served, monkeypatch, capsys):
    monkeypatch.setenv("PLATEWATCH_API_TOKEN", TOKEN)
    assert cli.main(["--db", str(db), "serve", "--host", "0.0.0.0"]) == 0
    assert "bearer token required" in capsys.readouterr().err and served
    monkeypatch.setenv("PLATEWATCH_API_TOKEN", "short")
    assert cli.main(["--db", str(db), "serve"]) == 2
    assert "at least 32" in capsys.readouterr().err


def test_serve_needs_an_existing_database(tmp_path, served, capsys):
    assert cli.main(["--db", str(tmp_path / "none.db"), "serve"]) == 2
    assert "no plate database" in capsys.readouterr().err and served == []
