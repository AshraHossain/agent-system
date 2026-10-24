"""What changed in the API between two runs.

Each report stores the API *surface* it was tested against: every operation,
the role discovery gave it, and the request fields with their types. Comparing
the surface with the previous run's tells the reader whether a new breach
coincides with a changed endpoint, and flags changes that would break clients
(a removed operation or field, a newly required field, a changed type) before
anyone reads the attack results.

No LLM is involved: it is a structural diff of two JSON documents.
"""

from __future__ import annotations

from typing import Any

from .discovery import Catalog, assign_roles, parse_openapi

MAX_DEPTH = 6


def _fields(schema: Any, prefix: str = "", depth: int = 0) -> dict[str, dict[str, Any]]:
    """Flatten a request schema into ``{"lines[].qty": {"type": ..., "required": ...}}``."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(schema, dict) or depth > MAX_DEPTH:
        return out
    required = set(schema.get("required") or [])
    for name, prop in (schema.get("properties") or {}).items():
        path = f"{prefix}{name}"
        out[path] = {"type": _type(prop), "required": name in required}
        if isinstance(prop, dict) and prop.get("type") == "array":
            out.update(_fields(prop.get("items") or {}, f"{path}[].", depth + 1))
        elif isinstance(prop, dict) and prop.get("properties"):
            out.update(_fields(prop, f"{path}.", depth + 1))
    return out


def _type(prop: Any) -> str:
    if not isinstance(prop, dict):
        return "any"
    if "type" in prop:
        return str(prop["type"])
    variants = prop.get("anyOf") or prop.get("oneOf") or []
    names = sorted({_type(v) for v in variants if isinstance(v, dict)})
    return "|".join(names) if names else "any"


def surface(catalog: Catalog) -> dict[str, Any]:
    role_of = {id(op): role for role, op in catalog.roles.items()}
    return {
        "source": catalog.source,
        "operations": {
            f"{op.method} {op.path}": {
                "role": role_of.get(id(op)),
                "fields": _fields(op.body_schema or {}),
            }
            for op in sorted(catalog.operations, key=lambda o: (o.path, o.method))
        },
    }


def surface_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    ops = parse_openapi(spec)
    return surface(Catalog("openapi", ops, assign_roles(ops)))


def load_surface(doc: dict[str, Any]) -> dict[str, Any] | None:
    """Accept a Ledger Hound report (``api_surface``) or a raw OpenAPI document."""
    if isinstance(doc.get("api_surface"), dict):
        return doc["api_surface"]
    if isinstance(doc.get("paths"), dict):
        return surface_from_spec(doc)
    return None


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    before, after = old.get("operations", {}), new.get("operations", {})
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())

    def roles(ops: dict[str, Any]) -> dict[str, str]:
        return {v["role"]: k for k, v in ops.items() if v.get("role")}

    old_roles, new_roles = roles(before), roles(after)
    role_changes = [
        {"role": r, "before": old_roles.get(r), "after": new_roles.get(r)}
        for r in sorted(old_roles.keys() | new_roles.keys())
        if old_roles.get(r) != new_roles.get(r)
    ]

    changed: dict[str, dict[str, Any]] = {}
    breaking: list[str] = [f"operation removed: {k}" for k in removed]
    breaking += [
        f"role lost: {c['role']} (was {c['before']})" for c in role_changes if not c["after"]
    ]
    for key in sorted(before.keys() & after.keys()):
        f_old, f_new = before[key]["fields"], after[key]["fields"]
        entry: dict[str, Any] = {}
        if add := sorted(f_new.keys() - f_old.keys()):
            entry["fields_added"] = add
            breaking += [f"{key}: new required field {f}" for f in add if f_new[f]["required"]]
        if gone := sorted(f_old.keys() - f_new.keys()):
            entry["fields_removed"] = gone
            breaking += [f"{key}: field removed {f}" for f in gone]
        retyped = {
            f: [f_old[f]["type"], f_new[f]["type"]]
            for f in sorted(f_old.keys() & f_new.keys())
            if f_old[f]["type"] != f_new[f]["type"]
        }
        if retyped:
            entry["type_changed"] = retyped
            breaking += [f"{key}: {f} type {a} -> {b}" for f, (a, b) in retyped.items()]
        now_required = [
            f
            for f in sorted(f_old.keys() & f_new.keys())
            if f_new[f]["required"] and not f_old[f]["required"]
        ]
        if now_required:
            entry["now_required"] = now_required
            breaking += [f"{key}: {f} is now required" for f in now_required]
        if entry:
            changed[key] = entry

    source_changed = old.get("source") != new.get("source")
    return {
        "unchanged": not (added or removed or role_changes or changed or source_changed),
        "source": [old.get("source"), new.get("source")] if source_changed else None,
        "operations_added": added,
        "operations_removed": removed,
        "role_changes": role_changes,
        "changed": changed,
        "breaking": breaking,
    }


def describe(d: dict[str, Any]) -> list[str]:
    """One line per change, breaking ones first; for the console."""
    if d["unchanged"]:
        return ["no change"]
    lines = [f"BREAKING {b}" for b in d["breaking"]]
    if d["source"]:
        lines.append(f"discovery source {d['source'][0]} -> {d['source'][1]}")
    lines += [f"added {k}" for k in d["operations_added"]]
    for c in d["role_changes"]:
        if c["before"] and c["after"]:
            lines.append(f"role {c['role']} moved {c['before']} -> {c['after']}")
        elif c["after"]:
            lines.append(f"role {c['role']} gained at {c['after']}")
    for key, entry in d["changed"].items():
        if entry.get("fields_added"):
            lines.append(f"{key}: fields added {', '.join(entry['fields_added'])}")
    return lines
