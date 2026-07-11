"""Normalized document and chunk schema shared by all data sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Document:
    doc_id: str
    source: str  # "slack" | "drive" | "tickets"
    title: str
    body: str
    allowed_principals: list[str]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Document":
        return cls(**d)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    source: str
    title: str
    text: str
    allowed_principals: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Chunk":
        return cls(**d)
