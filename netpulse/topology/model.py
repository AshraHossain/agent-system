"""Validated topology schema."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Node(_Model):
    node_id: str
    role: str
    tier: Literal["external", "edge", "core", "aggregation", "access"]
    monitored: bool


class Link(_Model):
    link_id: str
    a: str
    b: str
    capacity_gbps: float = Field(gt=0)


class Service(_Model):
    service_id: str
    criticality: Literal["low", "medium", "high"]
    customers: int = Field(ge=0)
    depends_on: list[str]
    probe_path: list[str]


class Topology(_Model):
    provenance: Literal["synthetic"]
    nodes: list[Node]
    links: list[Link]
    services: list[Service]

    @model_validator(mode="after")
    def _referential_integrity(self) -> Topology:
        node_ids = [n.node_id for n in self.nodes]
        link_ids = [ln.link_id for ln in self.links]
        service_ids = [s.service_id for s in self.services]
        all_ids = node_ids + link_ids + service_ids
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("entity ids must be unique across nodes, links and services")
        nodes = set(node_ids)
        for link in self.links:
            if link.a not in nodes or link.b not in nodes:
                raise ValueError(f"link {link.link_id} references an unknown node")
        for svc in self.services:
            if not set(svc.depends_on) <= nodes:
                raise ValueError(f"service {svc.service_id} depends on unknown nodes")
            if not set(svc.probe_path) <= set(link_ids):
                raise ValueError(f"service {svc.service_id} probes unknown links")
        return self

    def entity_ids(self) -> set[str]:
        return (
            {n.node_id for n in self.nodes} | {ln.link_id for ln in self.links} | {s.service_id for s in self.services}
        )

    def kind(self, entity_id: str) -> Literal["node", "link", "service"] | None:
        if any(n.node_id == entity_id for n in self.nodes):
            return "node"
        if any(ln.link_id == entity_id for ln in self.links):
            return "link"
        if any(s.service_id == entity_id for s in self.services):
            return "service"
        return None
