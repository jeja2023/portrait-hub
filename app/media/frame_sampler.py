from typing import Iterable


def normalize_frame_interval(frame_interval: int | None, default: int = 1) -> int:
    if frame_interval is None:
        return max(1, default)
    return max(1, int(frame_interval))


def bounded_max_frames(max_frames: int | None, default: int, upper_bound: int) -> int:
    if max_frames is None:
        return max(1, min(default, upper_bound))
    return max(1, min(int(max_frames), upper_bound))


def sample_indexes(total_frames: int, frame_interval: int, max_frames: int) -> list[int]:
    if total_frames <= 0:
        return []
    indexes: list[int] = []
    for index in range(0, total_frames, normalize_frame_interval(frame_interval)):
        indexes.append(index)
        if len(indexes) >= max_frames:
            break
    return indexes


def uniform_sample_indexes(total_frames: int, max_frames: int) -> list[int]:
    if total_frames <= 0:
        return []
    max_frames = max(1, max_frames)
    if total_frames <= max_frames:
        return list(range(total_frames))
    if max_frames == 1:
        return [total_frames // 2]
    step = (total_frames - 1) / float(max_frames - 1)
    return sorted({min(total_frames - 1, int(round(index * step))) for index in range(max_frames)})


def hybrid_sample_indexes(total_frames: int, frame_interval: int, max_frames: int) -> list[int]:
    interval_candidates = sample_indexes(total_frames, frame_interval, max(1, total_frames))
    if len(interval_candidates) <= max_frames:
        return interval_candidates
    selected_positions = uniform_sample_indexes(len(interval_candidates), max_frames)
    return [interval_candidates[index] for index in selected_positions]


def take_every(items: Iterable[object], frame_interval: int, max_items: int) -> list[object]:
    selected: list[object] = []
    interval = normalize_frame_interval(frame_interval)
    for index, item in enumerate(items):
        if index % interval == 0:
            selected.append(item)
            if len(selected) >= max_items:
                break
    return selected
