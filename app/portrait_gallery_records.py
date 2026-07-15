from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.observability import wall_time
from app.portrait_object_storage import public_object_info
from app.portrait_security import redact_sensitive_fields, validate_person_id


@dataclass
class FeatureRecord:
    feature_id: str
    modality: str
    embedding: list[float]
    embedding_dim: int
    model_id: str
    model_version: str
    quality_score: float
    source_id: str
    created_at: float
    object_info: dict[str, Any] | None = None

    def public_dict(self, include_embedding: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "feature_id": self.feature_id,
            "modality": self.modality,
            "embedding_dim": self.embedding_dim,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "quality_score": self.quality_score,
            "source_id": self.source_id,
            "created_at": self.created_at,
        }
        if include_embedding:
            payload["embedding"] = self.embedding
        if self.object_info:
            payload["object"] = public_object_info(self.object_info)
            if "thumbnail" in self.object_info:
                payload["thumbnail"] = self.object_info["thumbnail"]
        return payload

    def state_dict(self) -> dict[str, Any]:
        payload = self.public_dict(include_embedding=True)
        payload.pop("object", None)
        if self.object_info:
            payload["object_info"] = deepcopy(self.object_info)
        return payload

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> FeatureRecord:
        object_info = payload.get("object_info") if isinstance(payload.get("object_info"), dict) else None
        return cls(
            feature_id=str(payload["feature_id"]),
            modality=str(payload["modality"]),
            embedding=[float(value) for value in payload.get("embedding", [])],
            embedding_dim=int(payload.get("embedding_dim", len(payload.get("embedding", [])))),
            model_id=str(payload.get("model_id", "")),
            model_version=str(payload.get("model_version", "")),
            quality_score=float(payload.get("quality_score", 0.0)),
            source_id=str(payload.get("source_id", "")),
            created_at=float(payload.get("created_at", wall_time())),
            object_info=deepcopy(object_info) if object_info else None,
        )


@dataclass
class PersonRecord:
    tenant_id: str
    person_id: str
    display_name: str | None
    metadata: dict[str, Any]
    features: list[FeatureRecord] = field(default_factory=list)
    created_at: float = field(default_factory=wall_time)
    updated_at: float = field(default_factory=wall_time)

    def public_dict(self, include_embeddings: bool = False) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "person_id": self.person_id,
            "display_name": self.display_name,
            "metadata": redact_sensitive_fields(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "features": [feature.public_dict(include_embeddings) for feature in self.features],
            "feature_count": len(self.features),
        }

    def state_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "person_id": self.person_id,
            "display_name": self.display_name,
            "metadata": deepcopy(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "features": [feature.state_dict() for feature in self.features],
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> PersonRecord:
        metadata = payload.get("metadata")
        return cls(
            tenant_id=str(payload.get("tenant_id", "default")),
            person_id=str(payload["person_id"]),
            display_name=payload.get("display_name"),
            metadata=deepcopy(metadata) if isinstance(metadata, dict) else {},
            features=[FeatureRecord.from_state(item) for item in payload.get("features", [])],
            created_at=float(payload.get("created_at", wall_time())),
            updated_at=float(payload.get("updated_at", wall_time())),
        )


GalleryKey = tuple[str, str]


def gallery_key(tenant_id: str, person_id: str) -> GalleryKey:
    return (str(tenant_id), validate_person_id(person_id))


def feature_object_infos(person: PersonRecord) -> list[dict[str, Any]]:
    return [
        deepcopy(feature.object_info)
        for feature in person.features
        if isinstance(feature.object_info, dict) and feature.object_info.get("object_key")
    ]
