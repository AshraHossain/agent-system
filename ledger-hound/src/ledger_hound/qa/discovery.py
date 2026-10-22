"""Turn a base URL into a map of what the API can do.

The agent starts from nothing but a base URL. Discovery reads the OpenAPI
document (FastAPI serves it at ``/openapi.json``) and assigns each operation a
*role* such as ``create_po`` or ``approve_invoice`` by matching its method and
path shape. All other code refers to roles, never to hard-coded paths, so a
renamed route or an extra path prefix does not break the agent.

It also keeps each request-body schema, so ``adapt`` can rename fields when the
API calls ``qty`` ``quantity`` or ``unit_price`` ``price``. That is the
"self-healing" part: the agent adjusts its calls to the schema it finds
instead of crashing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .http import ApiClient

# role -> (HTTP method, path regex with "{}" standing for any path parameter)
ROLE_PATTERNS: dict[str, tuple[str, str]] = {
    "list_vendors": ("GET", r".*/vendors"),
    "create_vendor": ("POST", r".*/vendors"),
    "update_vendor": ("PATCH|PUT", r".*/vendors/{}"),
    "vendor_exposure": ("GET", r".*/vendors/{}/exposure"),
    "list_items": ("GET", r".*/(items|skus|products|catalog)"),
    "create_po": ("POST", r".*/purchase[-_]?orders"),
    "get_po": ("GET", r".*/purchase[-_]?orders/{}"),
    "submit_po": ("POST", r".*/purchase[-_]?orders/{}/submit"),
    "receive_po": ("POST", r".*/purchase[-_]?orders/{}/(receive|receipts?)"),
    "create_invoice": ("POST", r".*/invoices"),
    "get_invoice": ("GET", r".*/invoices/{}"),
    "match_invoice": ("POST", r".*/invoices/{}/match"),
    "approve_invoice": ("POST", r".*/invoices/{}/approve"),
    "pay_invoice": ("POST", r".*/invoices/{}/pay"),
    "gl_entries": ("GET", r".*/(gl|ledger)(/entries)?"),
}

# Used when the API publishes no OpenAPI document: the endpoint table from the brief.
FALLBACK_OPERATIONS: list[tuple[str, str]] = [
    ("GET", "/vendors"),
    ("POST", "/vendors"),
    ("POST", "/purchase-orders"),
    ("POST", "/purchase-orders/{id}/submit"),
    ("POST", "/purchase-orders/{id}/receive"),
    ("GET", "/purchase-orders/{id}"),
    ("POST", "/invoices"),
    ("POST", "/invoices/{id}/match"),
    ("POST", "/invoices/{id}/approve"),
    ("POST", "/invoices/{id}/pay"),
    ("GET", "/vendors/{id}/exposure"),
]

# Field names that mean the same thing. The first entry is our canonical name.
FIELD_ALIASES: list[list[str]] = [
    ["qty", "quantity", "qty_ordered", "quantity_ordered", "qty_received", "quantity_received"],
    ["unit_price", "price", "unit_cost", "cost"],
    ["sku", "item_sku", "item_code", "item_id", "product_code"],
    ["lines", "line_items", "items"],
    ["vendor_id", "supplier_id"],
    ["po_id", "purchase_order_id"],
    ["invoice_number", "invoice_no", "vendor_invoice_number", "number"],
    ["name", "vendor_name"],
    ["active", "is_active", "enabled"],
    ["uom", "unit", "unit_of_measure"],
]
_ALIAS_GROUP = {name: group for group in FIELD_ALIASES for name in group}

_PARAM = re.compile(r"{[^}]+}")


@dataclass
class Operation:
    method: str
    path: str
    summary: str = ""
    body_schema: dict[str, Any] | None = None

    @property
    def shape(self) -> str:
        return _PARAM.sub("{}", self.path)


@dataclass
class Catalog:
    source: str  # "openapi" or "fallback"
    operations: list[Operation]
    roles: dict[str, Operation] = field(default_factory=dict)

    def has(self, *roles: str) -> bool:
        return all(r in self.roles for r in roles)

    def missing(self, *roles: str) -> list[str]:
        return [r for r in roles if r not in self.roles]

    def path(self, role: str, *ids: Any) -> str:
        template = self.roles[role].path
        for value in ids:
            template = _PARAM.sub(str(value), template, count=1)
        return template

    def method(self, role: str) -> str:
        return self.roles[role].method

    def adapt(self, role: str, body: Any) -> Any:
        """Rename canonical field names to whatever the API's schema uses."""
        schema = self.roles[role].body_schema
        return _adapt(body, schema) if schema else body

    def line_field(self, role: str, name: str) -> bool:
        """True if the role's request lines declare ``name`` (or one of its aliases)."""
        if role not in self.roles:
            return False
        schema = self.roles[role].body_schema or {}
        for key in _ALIAS_GROUP.get("lines", ["lines"]):
            line = (schema.get("properties") or {}).get(key, {}).get("items") or {}
            props = line.get("properties") or {}
            if any(n in props for n in _ALIAS_GROUP.get(name, [name])):
                return True
        return False

    def allows(self, method: str, path: str) -> bool:
        """True if (method, concrete path) matches a discovered operation."""
        bare = path.split("?")[0]
        for op in self.operations:
            pattern = "[^/]+".join(re.escape(part) for part in _PARAM.split(op.path))
            if op.method == method.upper() and re.fullmatch(pattern, bare):
                return True
        return False

    def describe(self) -> list[dict[str, Any]]:
        role_of = {id(op): role for role, op in self.roles.items()}
        return [
            {
                "method": op.method,
                "path": op.path,
                "role": role_of.get(id(op)),
                "summary": op.summary,
                "body_fields": sorted((op.body_schema or {}).get("properties", {}).keys()),
            }
            for op in self.operations
        ]


def _adapt(value: Any, schema: dict[str, Any]) -> Any:
    if isinstance(value, list):
        items = schema.get("items") or {}
        return [_adapt(v, items) for v in value]
    if not isinstance(value, dict):
        return value
    props: dict[str, Any] = schema.get("properties") or {}
    if not props:
        return value
    adapted: dict[str, Any] = {}
    for key, inner in value.items():
        target = key
        if key not in props:
            for alias in _ALIAS_GROUP.get(key, []):
                if alias in props:
                    target = alias
                    break
        adapted[target] = _adapt(inner, props.get(target, {}))
    return adapted


def _resolve(schema: Any, spec: dict[str, Any], depth: int = 0) -> Any:
    """Inline ``$ref``s so request schemas can be walked without the spec."""
    if depth > 12:
        return schema
    if isinstance(schema, dict):
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            target = spec.get("components", {}).get("schemas", {}).get(name, {})
            return _resolve(target, spec, depth + 1)
        if "allOf" in schema and len(schema["allOf"]) == 1:
            return _resolve(schema["allOf"][0], spec, depth + 1)
        return {k: _resolve(v, spec, depth + 1) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_resolve(v, spec, depth + 1) for v in schema]
    return schema


def parse_openapi(spec: dict[str, Any]) -> list[Operation]:
    ops: list[Operation] = []
    for path, methods in (spec.get("paths") or {}).items():
        for method, detail in methods.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            body = (
                (detail.get("requestBody") or {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
            )
            ops.append(
                Operation(
                    method=method.upper(),
                    path=path,
                    summary=detail.get("summary", ""),
                    body_schema=_resolve(body, spec) if body else None,
                )
            )
    return ops


def assign_roles(ops: list[Operation]) -> dict[str, Operation]:
    roles: dict[str, Operation] = {}
    for role, (methods, shape) in ROLE_PATTERNS.items():
        rx = re.compile("^" + shape.replace("{}", r"\{\}") + "$")
        for op in ops:
            if re.fullmatch(methods, op.method) and rx.match(op.shape):
                roles[role] = op
                break
    return roles


async def discover(client: ApiClient) -> Catalog:
    for candidate in ("/openapi.json", "/docs/openapi.json", "/swagger.json"):
        ex = await client.call("GET", candidate)
        if ex.ok and isinstance(ex.body, dict) and "paths" in ex.body:
            ops = parse_openapi(ex.body)
            return Catalog("openapi", ops, assign_roles(ops))
    ops = [Operation(m, p) for m, p in FALLBACK_OPERATIONS]
    return Catalog("fallback", ops, assign_roles(ops))
