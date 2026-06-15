from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.portrait_compare import l2_normalize_vector
from app.portrait_tracking_metrics import (
    aggregate_track_template,
    association_decision,
    association_quality_summary,
    box_area,
    box_iou,
    embedding_score,
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


def can_merge_track_fragments(
    left: TrackState,
    right: TrackState,
    *,
    max_gap: int,
    min_appearance: float = 0.86,
    min_motion_iou: float = 0.05,
) -> dict[str, Any] | None:
    if last_frame_index(left) >= first_frame_index(right):
        return None
    gap = first_frame_index(right) - last_frame_index(left) - 1
    if gap < 0 or gap > max_gap:
        return None
    appearance = embedding_score(left.smoothed_embedding, right.smoothed_embedding)
    if appearance is None or appearance < min_appearance:
        return None
    right_first = first_observation(right)
    if right_first is None:
        return None
    predicted_box = predict_track_box(left, int(right_first["frame_index"]))
    motion_iou = box_iou(predicted_box, right_first.get("box", []))
    if motion_iou < min_motion_iou and appearance < 0.92:
        return None
    return {
        "from_track_id": right.track_id,
        "to_track_id": left.track_id,
        "gap": gap,
        "appearance": round(float(appearance), 6),
        "motion_iou": round(float(motion_iou), 6),
        "predicted_box": predicted_box,
    }


def merge_track_state(primary: TrackState, secondary: TrackState) -> None:
    primary.hits += secondary.hits
    primary.age += secondary.age
    primary.confidence_sum += secondary.confidence_sum
    primary.quality_sum += secondary.quality_sum
    primary.frame_indexes.extend(secondary.frame_indexes)
    primary.observations.extend(secondary.observations)
    primary.observations.sort(key=lambda item: item["frame_index"])
    primary.interpolated.extend(secondary.interpolated)
    primary.embedding_samples.extend(secondary.embedding_samples)
    primary.association_decisions.extend(secondary.association_decisions)
    latest = last_observation(primary)
    if latest is not None:
        primary.last_frame_index = int(latest["frame_index"])
        primary.last_box = [float(value) for value in latest.get("box", primary.last_box)]
    template = aggregate_track_template(primary.embedding_samples, include_embedding=True)
    embedding = template.get("embedding")
    if isinstance(embedding, list):
        primary.smoothed_embedding = [float(value) for value in embedding]


def rewrite_merged_track_ids(frames: list[dict[str, Any]], merge: dict[str, Any]) -> None:
    from_track_id = merge["from_track_id"]
    to_track_id = merge["to_track_id"]
    for frame in frames:
        for person in frame.get("persons", []):
            if not isinstance(person, dict) or person.get("track_id") != from_track_id:
                continue
            person["track_id"] = to_track_id
            person["track_fragment_merged_from"] = from_track_id
            person["track_state"] = "merged_fragment"


def merge_track_fragments(
    frames: list[dict[str, Any]],
    tracks: list[TrackState],
    *,
    max_gap: int,
) -> dict[str, Any]:
    merges: list[dict[str, Any]] = []
    changed = True
    while changed:
        changed = False
        tracks.sort(key=lambda item: (first_frame_index(item), item.track_id))
        for left_index, left in enumerate(list(tracks)):
            for right in list(tracks[left_index + 1 :]):
                merge = can_merge_track_fragments(left, right, max_gap=max_gap)
                if merge is None:
                    continue
                merge_track_state(left, right)
                rewrite_merged_track_ids(frames, merge)
                tracks.remove(right)
                merges.append(merge)
                changed = True
                break
            if changed:
                break
    return {
        "enabled": True,
        "max_gap": max_gap,
        "merge_count": len(merges),
        "merges": merges[:50],
    }


def person_embedding(person: dict[str, Any]) -> list[float] | None:
    embedding = person.get("_tracking_embedding") or person.get("embedding")
    if not isinstance(embedding, list):
        return None
    try:
        return [float(value) for value in embedding]
    except (TypeError, ValueError):
        return None


def association_score(track: TrackState, person: dict[str, Any], frame_index: int) -> dict[str, Any]:
    last_iou = box_iou(track.last_box, person.get("box", []))
    predicted_box = predict_track_box(track, frame_index)
    predicted_iou = box_iou(predicted_box, person.get("box", []))
    geometric_score = max(last_iou, predicted_iou)
    gap = max(1, frame_index - track.last_frame_index)
    motion_score = max(0.0, 1.0 - min(gap - 1, 6) / 6.0)
    appearance = embedding_score(track.smoothed_embedding, person_embedding(person))
    if appearance is None:
        score = 0.74 * geometric_score + 0.26 * motion_score
        appearance_value = None
    else:
        score = 0.42 * geometric_score + 0.16 * motion_score + 0.42 * appearance
        appearance_value = round(float(appearance), 6)
    return {
        "score": round(float(score), 6),
        "iou": round(float(geometric_score), 6),
        "last_iou": round(float(last_iou), 6),
        "predicted_iou": round(float(predicted_iou), 6),
        "predicted_box": predicted_box,
        "motion": round(float(motion_score), 6),
        "appearance": appearance_value,
    }


def greedy_match(
    tracks: list[TrackState],
    detections: list[tuple[int, dict[str, Any]]],
    frame_index: int,
    min_score: float,
    *,
    solver_name: str = "greedy_score_sort",
) -> list[tuple[TrackState, int, dict[str, Any]]]:
    candidates: list[tuple[float, TrackState, int, dict[str, Any]]] = []
    for track in tracks:
        for person_index, person in detections:
            details = association_score(track, person, frame_index)
            if details["score"] >= min_score:
                candidates.append((details["score"], track, person_index, details))
    candidates.sort(key=lambda item: item[0], reverse=True)

    used_tracks: set[str] = set()
    used_persons: set[int] = set()
    matches: list[tuple[TrackState, int, dict[str, Any]]] = []
    for _, track, person_index, details in candidates:
        if track.track_id in used_tracks or person_index in used_persons:
            continue
        used_tracks.add(track.track_id)
        used_persons.add(person_index)
        payload = dict(details)
        payload["association_solver"] = solver_name
        payload["decision"] = association_decision(payload, min_score)
        matches.append((track, person_index, payload))
    return matches


def global_match(
    tracks: list[TrackState],
    detections: list[tuple[int, dict[str, Any]]],
    frame_index: int,
    min_score: float,
    *,
    max_exact_size: int = 10,
) -> list[tuple[TrackState, int, dict[str, Any]]]:
    if not tracks or not detections:
        return []
    if len(tracks) > max_exact_size or len(detections) > max_exact_size:
        return greedy_match(
            tracks,
            detections,
            frame_index,
            min_score,
            solver_name="greedy_score_sort_large_batch",
        )

    details_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    for track_position, track in enumerate(tracks):
        for detection_position, (_, person) in enumerate(detections):
            details = association_score(track, person, frame_index)
            if details["score"] >= min_score:
                details_by_pair[(track_position, detection_position)] = details

    def better(
        candidate: tuple[float, tuple[tuple[int, int], ...]],
        current: tuple[float, tuple[tuple[int, int], ...]],
    ) -> bool:
        candidate_score, candidate_pairs = candidate
        current_score, current_pairs = current
        if candidate_score > current_score + 1e-9:
            return True
        if abs(candidate_score - current_score) <= 1e-9 and len(candidate_pairs) > len(current_pairs):
            return True
        if (
            abs(candidate_score - current_score) <= 1e-9
            and len(candidate_pairs) == len(current_pairs)
            and candidate_pairs < current_pairs
        ):
            return True
        return False

    cache: dict[tuple[int, int], tuple[float, tuple[tuple[int, int], ...]]] = {}

    def solve(track_position: int, used_detection_mask: int) -> tuple[float, tuple[tuple[int, int], ...]]:
        cache_key = (track_position, used_detection_mask)
        if cache_key in cache:
            return cache[cache_key]
        if track_position >= len(tracks):
            return 0.0, ()

        best = solve(track_position + 1, used_detection_mask)
        for detection_position in range(len(detections)):
            if used_detection_mask & (1 << detection_position):
                continue
            details = details_by_pair.get((track_position, detection_position))
            if details is None:
                continue
            next_score, next_pairs = solve(track_position + 1, used_detection_mask | (1 << detection_position))
            candidate = (
                float(details["score"]) + next_score,
                ((track_position, detection_position), *next_pairs),
            )
            if better(candidate, best):
                best = candidate
        cache[cache_key] = best
        return best

    _, pairs = solve(0, 0)
    matches: list[tuple[TrackState, int, dict[str, Any]]] = []
    for track_position, detection_position in pairs:
        person_index, _ = detections[detection_position]
        payload = dict(details_by_pair[(track_position, detection_position)])
        payload["association_solver"] = "global_optimal_assignment"
        payload["decision"] = association_decision(payload, min_score)
        matches.append((tracks[track_position], person_index, payload))
    return matches


def associate_person_tracks(
    frames: list[dict[str, Any]],
    *,
    high_confidence: float = 0.45,
    low_confidence: float = 0.10,
    min_match_score: float = 0.28,
    max_age: int = 2,
    max_fragment_merge_gap: int = 4,
    max_global_association_size: int = 10,
    include_template_embeddings: bool = False,
) -> dict[str, Any]:
    active_tracks: list[TrackState] = []
    finished_tracks: list[TrackState] = []
    next_track_number = 1

    for frame in frames:
        frame_index = int(frame.get("frame_index", 0))
        persons = frame.get("persons", [])
        if not isinstance(persons, list):
            continue

        detections = [(index, person) for index, person in enumerate(persons) if isinstance(person, dict)]
        high = [(index, person) for index, person in detections if float(person.get("score", 0.0)) >= high_confidence]
        low = [
            (index, person)
            for index, person in detections
            if low_confidence <= float(person.get("score", 0.0)) < high_confidence
        ]

        unmatched_tracks = [track for track in active_tracks if frame_index - track.last_frame_index <= max_age]
        matched_persons: set[int] = set()

        for track, person_index, details in global_match(
            unmatched_tracks,
            high,
            frame_index,
            min_match_score,
            max_exact_size=max_global_association_size,
        ):
            person = persons[person_index]
            track.update(frame_index, person, person_embedding(person), details)
            person["track_id"] = track.track_id
            person["track_state"] = "tracked"
            person["track_match"] = details
            matched_persons.add(person_index)

        remaining_tracks = [track for track in unmatched_tracks if track.track_id not in {person.get("track_id") for person in persons if isinstance(person, dict)}]
        low_matches = global_match(
            remaining_tracks,
            [(index, person) for index, person in low if index not in matched_persons],
            frame_index,
            max(0.18, min_match_score - 0.08),
            max_exact_size=max_global_association_size,
        )
        for track, person_index, details in low_matches:
            person = persons[person_index]
            track.update(frame_index, person, person_embedding(person), details)
            person["track_id"] = track.track_id
            person["track_state"] = "recovered_low_confidence"
            person["track_match"] = details
            matched_persons.add(person_index)

        for person_index, person in high:
            if person_index in matched_persons:
                continue
            initial_embedding = person_embedding(person)
            initial_sample = make_embedding_sample(frame_index, person, initial_embedding)
            track = TrackState(
                track_id=f"trk_{next_track_number:04d}",
                last_frame_index=frame_index,
                last_box=[float(value) for value in person.get("box", [])],
                smoothed_embedding=initial_embedding,
                confidence_sum=person_confidence(person),
                quality_sum=person_quality_score(person),
                frame_indexes=[frame_index],
                observations=[make_observation(frame_index, person)],
                embedding_samples=[initial_sample] if initial_sample is not None else [],
            )
            next_track_number += 1
            active_tracks.append(track)
            person["track_id"] = track.track_id
            person["track_state"] = "new"
            person["track_match"] = {"score": 1.0, "iou": None, "motion": None, "appearance": None}
            matched_persons.add(person_index)

        for person_index, person in detections:
            if person_index not in matched_persons:
                person["track_id"] = None
                person["track_state"] = "unconfirmed_low_confidence"

        still_active: list[TrackState] = []
        for track in active_tracks:
            if frame_index - track.last_frame_index > max_age:
                finished_tracks.append(track)
            else:
                still_active.append(track)
        active_tracks = still_active

    all_tracks = [*finished_tracks, *active_tracks]
    all_tracks.sort(key=lambda item: item.track_id)
    fragment_merge = merge_track_fragments(frames, all_tracks, max_gap=max_fragment_merge_gap)
    postprocess_tracklets(frames, all_tracks, max_age)
    for frame in frames:
        for person in frame.get("persons", []):
            if isinstance(person, dict):
                person.pop("_tracking_embedding", None)
    return {
        "algorithm": "quality_aware_byte_iou_appearance",
        "association_solver": "global_optimal_assignment",
        "max_global_association_size": max_global_association_size,
        "high_confidence": high_confidence,
        "low_confidence": low_confidence,
        "min_match_score": min_match_score,
        "max_age": max_age,
        "fragment_merge": fragment_merge,
        "tracks": [track.public_dict(include_template_embedding=include_template_embeddings) for track in all_tracks],
        "track_count": len(all_tracks),
    }
