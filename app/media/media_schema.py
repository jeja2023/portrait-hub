from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MediaFrame:
    source_type: str
    source_id: str
    frame_index: int
    pts_ms: int
    width: int
    height: int
    filename: str | None = None
    quality: dict[str, Any] | None = None
    fingerprint: dict[str, Any] | None = None
    duplicate_of: str | None = None
    duplicate_distance: int | None = None

    def to_dict(self, include_filename: bool = False, include_fingerprint: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "frame_index": self.frame_index,
            "pts_ms": self.pts_ms,
            "width": self.width,
            "height": self.height,
        }
        if include_filename and self.filename is not None:
            payload["filename"] = self.filename
        if self.quality is not None:
            payload["quality"] = self.quality
        if include_fingerprint and self.fingerprint is not None:
            payload["fingerprint"] = self.fingerprint
        if self.duplicate_of is not None:
            payload["duplicate_of"] = self.duplicate_of
            payload["duplicate_distance"] = self.duplicate_distance
        return payload


@dataclass(slots=True)
class DecodedImage:
    image: Any
    frame: MediaFrame
    format: str
    bytes_count: int
    data: bytes | None = None
