"""按任务划分的推理入口点（重新导出外观门面）。"""

from app.inference_classification import (
    infer_classification_images,
)
from app.inference_detection import (
    infer_detection_images,
    infer_person_frames,
)
from app.inference_reid import (
    infer_reid_images,
)
from app.inference_tracks import (
    infer_tracks_for_images,
    merge_person_quality,
)

__all__ = [
    "infer_classification_images",
    "infer_detection_images",
    "infer_person_frames",
    "infer_reid_images",
    "infer_tracks_for_images",
    "merge_person_quality",
]
