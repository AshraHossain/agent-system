"""Discovery, schema adaptation ("self-healing") and the money oracle."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from ledger_hound.qa.discovery import discover
from ledger_hound.qa.http import ApiClient
from ledger_hound.qa.oracle import gl_problems, has_sub_cent, match_is_clean, surfaces_partial


async def test_roles_from_openapi(clean):
    _, catalog = clean
    assert catalog.source == "openapi"
    assert catalog.path("submit_po", 7) == "/purchase-orders/7/submit"
    assert catalog.allows("POST", "/invoices/9/approve")
    assert not catalog.allows("DELETE", "/vendors/1")
    assert not catalog.allows("GET", "/admin/secrets")


async def test_fallback_without_openapi():
    app = FastAPI(openapi_url=None)
    client = ApiClient(
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x")
    )
    catalog = await discover(client)
    assert catalog.source == "fallback"
    assert catalog.has("create_po", "approve_invoice", "vendor_exposure")
    await client.aclose()


class V2Line(BaseModel):
    item_code: str
    quantity: str
    price: str


class V2PO(BaseModel):
    supplier_id: int
    line_items: list[V2Line]


async def test_adapts_to_renamed_fields_and_prefixed_paths():
    """A v2 API with an /api/v2 prefix and different field names still maps cleanly."""
    app = FastAPI()

    @app.post("/api/v2/purchase_orders")
    async def create(po: V2PO) -> dict:
        return {"id": 1, "echo": po.model_dump()}

    client = ApiClient(
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x")
    )
    catalog = await discover(client)
    body = catalog.adapt(
        "create_po",
        {"vendor_id": 3, "lines": [{"sku": "A", "qty": "2", "unit_price": "1.50"}]},
    )
    assert body == {
        "supplier_id": 3,
        "line_items": [{"item_code": "A", "quantity": "2", "price": "1.50"}],
    }
    ex = await client.call("POST", catalog.path("create_po"), body)
    assert ex.status == 200
    await client.aclose()


def test_gl_problems():
    balanced = {
        "lines": [
            {"account": "1400", "debit": "57.00", "credit": "0"},
            {"account": "2000", "debit": "0", "credit": "57.00"},
        ]
    }
    assert gl_problems(balanced) == []
    drifted = {
        "lines": [
            {"account": "1400", "debit": 56.99999999999999, "credit": 0},
            {"account": "2000", "debit": 0, "credit": 57.0},
        ]
    }
    problems = gl_problems(drifted)
    assert any("sub-cent" in p for p in problems)
    assert any("unbalanced" in p for p in problems)
    assert gl_problems({"lines": []}) == ["GL entry has no lines"]


def test_money_helpers():
    assert has_sub_cent(0.30000000000000004)
    assert not has_sub_cent("0.30")
    assert not has_sub_cent(0.3)
    assert surfaces_partial(
        {"status": "matched_with_exceptions", "exceptions": ["partial_receipt"]}
    )
    assert not surfaces_partial({"status": "matched", "exceptions": []})
    assert match_is_clean({"status": "matched", "exceptions": []})
    assert not match_is_clean({"status": "matched", "warnings": ["short"]})
