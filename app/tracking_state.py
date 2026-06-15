from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.portrait_compare import l2_normalize_vector
from app.portrait_tracking_metrics import (
    aggregate_track_template,
    association_quality_summary,
    box_iou,
    make_embedding_sample,
    person_confidence,
    person_quality_score,
    tracklet_quality_score,
)


def first_frame_index(track: "TrackState") -> int:
    return min(track.frame_indexes) if track.frame_indexes else track.last_frame_index


def last_frame_index(track: "TrackState") -> int:
    return max(track.frame_indexes) if track.frame_indexes else track.last_frame_index


@dataclass
class TrackState:
    track_id: str
    last_frame_index: int
    last_box: list[float]
    smoothed_embedding: list[float] | None = None
    hits: int = 1
    age: int = 1
    confidence_sum: float = 0.0
    quality_sum: float = 0.0
    frame_indexes: list[int] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    interpolated: list[dict[str, Any]] = field(default_factory=list)
    embedding_samples: list[dict[str, Any]] = field(default_factory=list)
    association_decisions: list[dict[str, Any]] = field(default_factory=list)

    def update(
        self,
        frame_index: int,
        person: dict[str, Any],
        embedding: list[float] | None,
        match_details: dict[str, Any] | None = None,
    ) -> None:
        self.last_frame_index = frame_index
        self.last_box = [float(value) for value in person.get("box", self.last_box)]
        self.hits += 1
        self.age += 1
        self.confidence_sum += person_confidence(person)
        self.quality_sum += person_quality_score(person)
        self.frame_indexes.append(frame_index)
        self.observations.append(
            {
                "frame_index": frame_index,
                "box": self.last_box,
                "score": person_confidence(person),
                "quality": person_quality_score(person),
            }
        )
        if isinstance(match_details, dict) and isinstance(match_details.get("decision"), dict):
            decision = dict(match_details["decision"])
            decision["frame_index"] = frame_index
            decision["score"] = match_details.get("score")
            self.association_decisions.append(decision)
        if embedding:
            sample = make_embedding_sample(frame_index, person, embedding)
            if sample is not None:
                self.embedding_samples.append(sample)
            if self.smoothed_embedding is None or len(self.smoothed_embedding) != len(embedding):
                self.smoothed_embedding = embedding
            else:
                previous = np.asarray(self.smoothed_embedding, dtype=np.float32)
                current = np.asarray(embedding, dtype=np.float32)
                self.smoothed_embedding = [round(float(value), 8) for value in l2_normalize_vector(previous * 0.85 + current * 0.15)]

    def public_dict(self, *, include_template_embedding: bool = False) -> dict[str, Any]:
        ordered = sorted(self.frame_indexes)
        gaps = [right - left - 1 for left, right in zip(ordered, ordered[1:]) if right - left > 1]
        stability = track_stability_score(self.observations)
        average_confidence = self.confidence_sum / max(1, self.hits)
        average_quality = self.quality_sum / max(1, self.hits)
        return {
            "track_id": self.track_id,
            "frame_count": self.hits,
            "first_frame_index": min(self.frame_indexes) if self.frame_indexes else self.last_frame_index,
            "last_frame_index": self.last_frame_index,
            "average_confidence": round(average_confidence, 6),
            "average_quality": round(average_quality, 6),
            "gap_count": len(gaps),
            "max_gap": max(gaps) if gaps else 0,
            "interpolated_count": len(self.interpolated),
            "stability_score": stability,
            "tracklet_quality_score": tracklet_quality_score(average_confidence, average_quality, stability, len(gaps)),
            "association_quality": association_quality_summary(self.association_decisions),
            "template": aggregate_track_template(self.embedding_samples, include_embedding=include_template_embedding),
            "interpolated": self.interpolated[:20],
        }


def make_observation(frame_index: int, person: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame_index": frame_index,
        "box": [float(value) for value in person.get("box", [])],
        "score": person_confidence(person),
        "quality": person_quality_score(person),
    }


def average_boxes(boxes: list[list[float]], weights: list[float]) -> list[float]:
    total = max(1e-9, sum(weights))
    return [
        round(sum(float(box[index]) * weight for box, weight in zip(boxes, weights)) / total, 4)
        for index in range(4)
    ]


def interpolate_box(left: list[float], right: list[float], ratio: float) -> list[float]:
    return [round(float(a) * (1.0 - ratio) + float(b) * ratio, 4) for a, b in zip(left, right)]


def predict_track_box(track: TrackState, frame_index: int) -> list[float]:
    observations = sorted(track.observations, key=lambda item: item["frame_index"])
    if len(observations) < 2:
        return track.last_box
    previous = observations[-2]
    current = observations[-1]
    frame_gap = max(1, int(current["frame_index"]) - int(previous["frame_index"]))
    predict_gap = max(1, frame_index - int(current["frame_index"]))
    velocity = [
        (float(current["box"][index]) - float(previous["box"][index])) / frame_gap
        for index in range(4)
    ]
    return [
        round(float(current["box"][index]) + velocity[index] * predict_gap, 4)
        for index in range(4)
    ]


def track_stability_score(observations: list[dict[str, Any]]) -> float:
    if len(observations) < 2:
        return 1.0
    ious = [
        box_iou(left.get("box", []), right.get("box", []))
        for left, right in zip(observations, observations[1:])
    ]
    return round(float(sum(ious) / len(ious)), 6) if ious else 1.0


def find_person_by_track(frames_by_index: dict[int, dict[str, Any]], frame_index: int, track_id: str) -> dict[str, Any] | None:
    frame = frames_by_index.get(frame_index)
    if not frame:
        return None
    for person in frame.get("persons", []):
        if isinstance(person, dict) and person.get("track_id") == track_id:
            return person
    return None


def postprocess_tracklets(frames: list[dict[str, Any]], tracks: list[TrackState], max_interpolation_gap: int) -> None:
    frames_by_index = {int(frame.get("frame_index", 0)): frame for frame in frames}
    for track in tracks:
        observations = sorted(track.observations, key=lambda item: item["frame_index"])
        for index, observation in enumerate(observations):
            boxes = [observation["box"]]
            weights = [0.50]
            if index > 0 and observation["frame_index"] - observations[index - 1]["frame_index"] <= max_interpolation_gap + 1:
                boxes.append(observations[index - 1]["box"])
                weights.append(0.25)
            if index + 1 < len(observations) and observations[index + 1]["frame_index"] - observation["frame_index"] <= max_interpolation_gap + 1:
                boxes.append(observations[index + 1]["box"])
                weights.append(0.25)
            person = find_person_by_track(frames_by_index, int(observation["frame_index"]), track.track_id)
            if person is not None and boxes:
                person["smoothed_box"] = average_boxes(boxes, weights)
                person["box_smoothing"] = {
                    "method": "temporal_weighted_average",
                    "support": len(boxes),
                }

        for left, right in zip(observations, observations[1:]):
            gap = int(right["frame_index"]) - int(left["frame_index"]) - 1
            if gap <= 0 or gap > max_interpolation_gap:
                continue
            for offset in range(1, gap + 1):
                ratio = offset / float(gap + 1)
                track.interpolated.append(
                    {
                        "frame_index": int(left["frame_index"]) + offset,
                        "box": interpolate_box(left["box"], right["box"], ratio),
                        "source": "linear_short_gap",
                    }
                )


def first_observation(track: TrackState) -> dict[str, Any] | None:
    if not track.observations:
        return None
    return min(track.observations, key=lambda item: item["frame_index"])


def last_observation(track: TrackState) -> dict[str, Any] | None:
    if not track.observations:
        return None
    return max(track.observations, key=lambda item: item["frame_index"])


__all__ = [
    "TrackState",
    "average_boxes",
    "find_person_by_track",
    "first_frame_index",
    "first_observation",
    "interpolate_box",
    "last_frame_index",
    "last_observation",
    "make_observation",
    "postprocess_tracklets",
    "predict_track_box",
    "track_stability_score",
]
