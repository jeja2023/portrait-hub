from __future__ import annotations

from typing import Any

from app.portrait_tracking_metrics import (
    aggregate_track_template,
    association_decision,
    box_iou,
    embedding_score,
    make_embedding_sample,
    person_confidence,
    person_quality_score,
)
from app.tracking_state import (
    TrackState,
    first_frame_index,
    first_observation,
    last_frame_index,
    last_observation,
    make_observation,
    postprocess_tracklets,
    predict_track_box,
)


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


class TrackUnionFind:
    def __init__(self, tracks: list[TrackState]) -> None:
        self.parent = {track.track_id: track.track_id for track in tracks}
        self.rank = {track.track_id: 0 for track in tracks}

    def find(self, track_id: str) -> str:
        parent = self.parent[track_id]
        if parent != track_id:
            self.parent[track_id] = self.find(parent)
        return self.parent[track_id]

    def union(self, left_id: str, right_id: str) -> bool:
        left_root = self.find(left_id)
        right_root = self.find(right_id)
        if left_root == right_root:
            return False
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1
        return True


def track_time_bounds(track: TrackState) -> tuple[int, int]:
    return first_frame_index(track), last_frame_index(track)


def track_groups_are_temporally_compatible(left: list[TrackState], right: list[TrackState]) -> bool:
    left_start = min(track_time_bounds(track)[0] for track in left)
    left_end = max(track_time_bounds(track)[1] for track in left)
    right_start = min(track_time_bounds(track)[0] for track in right)
    right_end = max(track_time_bounds(track)[1] for track in right)
    return left_end < right_start or right_end < left_start


def merge_track_fragments(
    frames: list[dict[str, Any]],
    tracks: list[TrackState],
    *,
    max_gap: int,
) -> dict[str, Any]:
    ordered_tracks = sorted(tracks, key=lambda item: (first_frame_index(item), item.track_id))
    candidates: list[dict[str, Any]] = []
    for left_index, left in enumerate(ordered_tracks):
        for right in ordered_tracks[left_index + 1 :]:
            if first_frame_index(right) - last_frame_index(left) - 1 > max_gap:
                break
            merge = can_merge_track_fragments(left, right, max_gap=max_gap)
            if merge is not None:
                candidates.append(merge)

    candidates.sort(
        key=lambda item: (
            int(item["gap"]),
            -float(item["appearance"]),
            -float(item["motion_iou"]),
            str(item["to_track_id"]),
            str(item["from_track_id"]),
        )
    )

    union_find = TrackUnionFind(ordered_tracks)
    groups: dict[str, list[TrackState]] = {track.track_id: [track] for track in ordered_tracks}
    accepted_edges: list[dict[str, Any]] = []
    for candidate in candidates:
        left_id = str(candidate["to_track_id"])
        right_id = str(candidate["from_track_id"])
        left_root = union_find.find(left_id)
        right_root = union_find.find(right_id)
        if left_root == right_root:
            continue
        if not track_groups_are_temporally_compatible(groups[left_root], groups[right_root]):
            continue
        union_find.union(left_id, right_id)
        new_root = union_find.find(left_id)
        old_root = right_root if new_root == left_root else left_root
        groups[new_root] = [*groups.pop(left_root, []), *groups.pop(right_root, [])]
        groups.pop(old_root, None)
        accepted_edges.append(candidate)

    edge_by_source = {str(edge["from_track_id"]): edge for edge in accepted_edges}
    merges: list[dict[str, Any]] = []
    merged_tracks: list[TrackState] = []
    for group in sorted(groups.values(), key=lambda items: min(first_frame_index(item) for item in items)):
        members = sorted(group, key=lambda item: (first_frame_index(item), item.track_id))
        primary = members[0]
        for secondary in members[1:]:
            edge = dict(edge_by_source.get(secondary.track_id, {}))
            edge.update(
                {
                    "from_track_id": secondary.track_id,
                    "to_track_id": primary.track_id,
                    "strategy": "union_find_batch",
                }
            )
            merge_track_state(primary, secondary)
            rewrite_merged_track_ids(frames, edge)
            merges.append(edge)
        merged_tracks.append(primary)

    merged_tracks.sort(key=lambda item: item.track_id)
    tracks[:] = merged_tracks
    return {
        "enabled": True,
        "max_gap": max_gap,
        "strategy": "union_find_batch",
        "candidate_count": len(candidates),
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

    details_by_pair: dict[tuple[int, int], dict[str, Any]] = {}
    for track_position, track in enumerate(tracks):
        for detection_position, (_, person) in enumerate(detections):
            details = association_score(track, person, frame_index)
            if details["score"] >= min_score:
                details_by_pair[(track_position, detection_position)] = details
    if not details_by_pair:
        return []

    if len(tracks) > max_exact_size or len(detections) > max_exact_size:
        pairs = hungarian_assignment_pairs(details_by_pair, len(tracks), len(detections))
        return assignment_matches(
            tracks,
            detections,
            details_by_pair,
            pairs,
            min_score,
            solver_strategy="hungarian_large_batch",
        )

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

    _, dynamic_pairs = solve(0, 0)
    return assignment_matches(
        tracks,
        detections,
        details_by_pair,
        list(dynamic_pairs),
        min_score,
        solver_strategy="exact_dynamic_programming",
    )


def assignment_matches(
    tracks: list[TrackState],
    detections: list[tuple[int, dict[str, Any]]],
    details_by_pair: dict[tuple[int, int], dict[str, Any]],
    pairs: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    min_score: float,
    *,
    solver_strategy: str,
) -> list[tuple[TrackState, int, dict[str, Any]]]:
    matches: list[tuple[TrackState, int, dict[str, Any]]] = []
    for track_position, detection_position in pairs:
        details = details_by_pair.get((track_position, detection_position))
        if details is None:
            continue
        person_index, _ = detections[detection_position]
        payload = dict(details)
        payload["association_solver"] = "global_optimal_assignment"
        payload["association_solver_strategy"] = solver_strategy
        payload["decision"] = association_decision(payload, min_score)
        matches.append((tracks[track_position], person_index, payload))
    return matches


def hungarian_assignment_pairs(
    details_by_pair: dict[tuple[int, int], dict[str, Any]],
    track_count: int,
    detection_count: int,
) -> list[tuple[int, int]]:
    size = max(track_count, detection_count)
    if size == 0:
        return []
    max_score = max(float(details["score"]) for details in details_by_pair.values())
    missing_cost = max_score + 1.0
    cost = [[missing_cost for _ in range(size)] for _ in range(size)]
    for (track_position, detection_position), details in details_by_pair.items():
        cost[track_position][detection_position] = max_score - float(details["score"])

    potentials_u = [0.0] * (size + 1)
    potentials_v = [0.0] * (size + 1)
    matching = [0] * (size + 1)
    predecessor = [0] * (size + 1)

    for row in range(1, size + 1):
        matching[0] = row
        column = 0
        min_values = [float("inf")] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column] = True
            current_row = matching[column]
            delta = float("inf")
            next_column = 0
            for candidate_column in range(1, size + 1):
                if used[candidate_column]:
                    continue
                current = cost[current_row - 1][candidate_column - 1] - potentials_u[current_row] - potentials_v[candidate_column]
                if current < min_values[candidate_column]:
                    min_values[candidate_column] = current
                    predecessor[candidate_column] = column
                if min_values[candidate_column] < delta:
                    delta = min_values[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(size + 1):
                if used[candidate_column]:
                    potentials_u[matching[candidate_column]] += delta
                    potentials_v[candidate_column] -= delta
                else:
                    min_values[candidate_column] -= delta
            column = next_column
            if matching[column] == 0:
                break
        while True:
            previous_column = predecessor[column]
            matching[column] = matching[previous_column]
            column = previous_column
            if column == 0:
                break

    result: list[tuple[int, int]] = []
    for column in range(1, size + 1):
        row = matching[column] - 1
        detection_position = column - 1
        if row < track_count and detection_position < detection_count and (row, detection_position) in details_by_pair:
            result.append((row, detection_position))
    return sorted(result)


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


__all__ = [
    "TrackUnionFind",
    "assignment_matches",
    "associate_person_tracks",
    "association_score",
    "can_merge_track_fragments",
    "global_match",
    "greedy_match",
    "hungarian_assignment_pairs",
    "merge_track_fragments",
    "merge_track_state",
    "person_embedding",
    "rewrite_merged_track_ids",
    "track_groups_are_temporally_compatible",
    "track_time_bounds",
]
