from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status

from app.media.image_decode import decode_upload_image, decode_upload_images, duplicate_distance, read_limited_upload
from app.media.media_schema import DecodedImage
from app.observability import request_id_from_headers
from app.portrait_async import run_blocking_io
from app.portrait_auth import permission_dependency
from app.portrait_compare import apply_input_independence_to_decision, fuse_modalities, quality_aware_compare
from app.portrait_jobs import VideoJob, create_batch_job, persist_video_job, run_batch_job
from app.portrait_model_runtime import (
    infer_appearance_record_for_image,
    infer_best_face_embedding_for_image,
    infer_body_record_for_image,
    infer_gait_embedding_for_images,
)
from app.portrait_response import portrait_success
from app.portrait_request_context import PortraitRequestContext, portrait_request_context
from app.portrait_thresholds import validate_threshold_profile
from app.security import require_api_token
from app.settings import MAX_COMPARE_BATCH_PAIRS, MAX_VIDEO_FRAMES


router = APIRouter(dependencies=[Depends(require_api_token)])


def average_quality(score_a: float | None, score_b: float | None) -> float:
    values = [float(value) for value in [score_a, score_b] if value is not None]
    return sum(values) / len(values) if values else 0.0


def quality_score(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict) or payload.get("score") is None:
        return None
    try:
        return float(payload["score"])
    except (TypeError, ValueError):
        return None


def combined_quality(subject_quality: dict[str, Any] | None, frame_quality: dict[str, Any] | None) -> float | None:
    subject_score = quality_score(subject_quality)
    frame_score = quality_score(frame_quality)
    if subject_score is None:
        return frame_score
    if frame_score is None:
        return subject_score
    return subject_score * 0.76 + frame_score * 0.24


def pair_input_evidence(decoded_a: DecodedImage, decoded_b: DecodedImage) -> dict[str, Any]:
    distance = duplicate_distance(decoded_a.frame.fingerprint or {}, decoded_b.frame.fingerprint or {})
    return {
        "a": decoded_a.frame.to_dict(),
        "b": decoded_b.frame.to_dict(),
        "fingerprint_distance": distance,
        "exact_duplicate": distance == 0,
        "near_duplicate": distance is not None and 0 < distance <= 4,
    }


def distinct_sequence(decoded: list[DecodedImage]) -> tuple[list[DecodedImage], dict[str, Any]]:
    distinct = [item for item in decoded if item.frame.duplicate_of is None]
    duplicate_count = len(decoded) - len(distinct)
    return distinct, {
        "input_frame_count": len(decoded),
        "used_frame_count": len(distinct),
        "duplicate_frame_count": duplicate_count,
        "duplicate_policy": "exclude_near_duplicates",
        "frames": [item.frame.to_dict() for item in decoded],
    }


def validate_sequence(files: list[UploadFile], name: str) -> None:
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{name} requires at least one frame")
    if len(files) > MAX_VIDEO_FRAMES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"too many frames in {name}: {len(files)}, max {MAX_VIDEO_FRAMES}",
        )


async def compare_batch_results(
    image_a: list[UploadFile],
    image_b: list[UploadFile],
    *,
    modality_key: str,
    threshold_profile: str,
    include_vectors: bool,
    progress_callback: Any | None = None,
) -> list[dict[str, Any]]:
    results = []
    total = max(1, len(image_a))
    for index, (file_a, file_b) in enumerate(zip(image_a, image_b)):
        decoded_a = await decode_upload_image(file_a)
        decoded_b = await decode_upload_image(file_b)
        if modality_key == "face":
            subject_a_embedding, subject_a = await infer_best_face_embedding_for_image(decoded_a.image)
            subject_b_embedding, subject_b = await infer_best_face_embedding_for_image(decoded_b.image)
        elif modality_key == "appearance":
            subject_a = await infer_appearance_record_for_image(decoded_a.image, include_embedding=True)
            subject_b = await infer_appearance_record_for_image(decoded_b.image, include_embedding=True)
            subject_a_embedding = subject_a["embedding"]
            subject_b_embedding = subject_b["embedding"]
        else:
            subject_a = await infer_body_record_for_image(decoded_a.image, include_embedding=True)
            subject_b = await infer_body_record_for_image(decoded_b.image, include_embedding=True)
            subject_a_embedding = subject_a["embedding"]
            subject_b_embedding = subject_b["embedding"]
        comparison = quality_aware_compare(
            subject_a_embedding,
            subject_b_embedding,
            modality=modality_key,
            threshold_profile=threshold_profile,
            quality_a=combined_quality(subject_a["quality"], decoded_a.frame.quality),
            quality_b=combined_quality(subject_b["quality"], decoded_b.frame.quality),
        )
        comparison["subjects"] = {"a": subject_a, "b": subject_b}
        comparison["input"] = pair_input_evidence(decoded_a, decoded_b)
        apply_input_independence_to_decision(comparison, comparison["input"])
        if not include_vectors:
            comparison["subjects"]["a"].pop("embedding", None)
            comparison["subjects"]["b"].pop("embedding", None)
        results.append({"index": index, "modality": modality_key, "comparison": comparison})
        if progress_callback is not None:
            await progress_callback(0.05 + 0.9 * ((index + 1) / total))
    return results


@router.post("/v1/compare/faces", dependencies=[Depends(permission_dependency("compare"))])
async def v1_compare_faces(
    request: Request,
    image_a: UploadFile = File(...),
    image_b: UploadFile = File(...),
    threshold_profile: str = Form("normal"),
    include_vectors: bool = Form(False),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    threshold_profile = validate_threshold_profile(threshold_profile)
    decoded_a = await decode_upload_image(image_a)
    decoded_b = await decode_upload_image(image_b)
    embedding_a, face_a = await infer_best_face_embedding_for_image(decoded_a.image)
    embedding_b, face_b = await infer_best_face_embedding_for_image(decoded_b.image)
    comparison = quality_aware_compare(
        embedding_a,
        embedding_b,
        modality="face",
        threshold_profile=threshold_profile,
        quality_a=combined_quality(face_a["quality"], decoded_a.frame.quality),
        quality_b=combined_quality(face_b["quality"], decoded_b.frame.quality),
    )
    comparison["quality"] = {
        "a": face_a["quality"],
        "b": face_b["quality"],
        "score": round(average_quality(face_a["quality"].get("score"), face_b["quality"].get("score")), 6),
    }
    comparison["subjects"] = {"a": face_a, "b": face_b}
    evidence = pair_input_evidence(decoded_a, decoded_b)
    apply_input_independence_to_decision(comparison, evidence)
    comparison["input"] = evidence
    if not include_vectors:
        comparison["subjects"]["a"].pop("embedding", None)
        comparison["subjects"]["b"].pop("embedding", None)
    return portrait_success(request_id, comparison)


@router.post("/v1/compare/persons", dependencies=[Depends(permission_dependency("compare"))])
async def v1_compare_persons(
    request: Request,
    image_a: UploadFile = File(...),
    image_b: UploadFile = File(...),
    threshold_profile: str = Form("normal"),
    include_vectors: bool = Form(False),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    threshold_profile = validate_threshold_profile(threshold_profile)
    decoded_a = await decode_upload_image(image_a)
    decoded_b = await decode_upload_image(image_b)
    person_a = await infer_body_record_for_image(decoded_a.image, include_embedding=True)
    person_b = await infer_body_record_for_image(decoded_b.image, include_embedding=True)
    comparison = quality_aware_compare(
        person_a["embedding"],
        person_b["embedding"],
        modality="body",
        threshold_profile=threshold_profile,
        quality_a=combined_quality(person_a["quality"], decoded_a.frame.quality),
        quality_b=combined_quality(person_b["quality"], decoded_b.frame.quality),
    )
    comparison["quality"] = {
        "a": person_a["quality"],
        "b": person_b["quality"],
        "score": round(average_quality(person_a["quality"].get("score"), person_b["quality"].get("score")), 6),
    }
    comparison["subjects"] = {"a": person_a, "b": person_b}
    evidence = pair_input_evidence(decoded_a, decoded_b)
    apply_input_independence_to_decision(comparison, evidence)
    comparison["input"] = evidence
    if not include_vectors:
        comparison["subjects"]["a"].pop("embedding", None)
        comparison["subjects"]["b"].pop("embedding", None)
    return portrait_success(request_id, comparison)


@router.post("/v1/compare/gait", dependencies=[Depends(permission_dependency("compare"))])
async def v1_compare_gait(
    request: Request,
    sequence_a: list[UploadFile] = File(...),
    sequence_b: list[UploadFile] = File(...),
    threshold_profile: str = Form("normal"),
    include_vectors: bool = Form(False),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    threshold_profile = validate_threshold_profile(threshold_profile)
    validate_sequence(sequence_a, "sequence_a")
    validate_sequence(sequence_b, "sequence_b")
    decoded_a = await decode_upload_images(sequence_a)
    decoded_b = await decode_upload_images(sequence_b)
    distinct_a, evidence_a = distinct_sequence(decoded_a)
    distinct_b, evidence_b = distinct_sequence(decoded_b)
    embedding_a, meta_a = await infer_gait_embedding_for_images([item.image for item in distinct_a])
    embedding_b, meta_b = await infer_gait_embedding_for_images([item.image for item in distinct_b])
    meta_a.update(evidence_a)
    meta_b.update(evidence_b)
    if embedding_a is None or embedding_b is None:
        return portrait_success(
            request_id,
            {
                "passed": False,
                "similarity": None,
                "threshold_profile": threshold_profile,
                "subjects": {"a": meta_a, "b": meta_b},
                "reason": "not_enough_unique_frames",
            },
        )
    comparison = quality_aware_compare(
        embedding_a,
        embedding_b,
        modality="gait",
        threshold_profile=threshold_profile,
        quality_a=meta_a.get("quality"),
        quality_b=meta_b.get("quality"),
    )
    comparison["quality"] = {
        "a": meta_a.get("quality"),
        "b": meta_b.get("quality"),
        "score": round(average_quality(meta_a.get("quality"), meta_b.get("quality")), 6),
    }
    comparison["subjects"] = {"a": meta_a, "b": meta_b}
    if include_vectors:
        comparison["subjects"]["a"]["embedding"] = embedding_a
        comparison["subjects"]["b"]["embedding"] = embedding_b
    return portrait_success(request_id, comparison)


@router.post("/v1/fusion/compare", dependencies=[Depends(permission_dependency("compare"))])
async def v1_fusion_compare(
    request: Request,
    image_a: UploadFile = File(...),
    image_b: UploadFile = File(...),
    modalities: str = Form("face,body,appearance"),
    threshold_profile: str = Form("normal"),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    threshold_profile = validate_threshold_profile(threshold_profile)
    decoded_a = await decode_upload_image(image_a)
    decoded_b = await decode_upload_image(image_b)
    requested = {item.strip().lower() for item in modalities.split(",") if item.strip()}
    scores: dict[str, dict[str, Any]] = {}

    if "face" in requested:
        embedding_a, face_a = await infer_best_face_embedding_for_image(decoded_a.image)
        embedding_b, face_b = await infer_best_face_embedding_for_image(decoded_b.image)
        comparison = quality_aware_compare(
            embedding_a,
            embedding_b,
            modality="face",
            threshold_profile=threshold_profile,
            quality_a=combined_quality(face_a["quality"], decoded_a.frame.quality),
            quality_b=combined_quality(face_b["quality"], decoded_b.frame.quality),
        )
        scores["face"] = {
            "score": comparison["quality_adjusted_similarity"],
            "quality": average_quality(
                combined_quality(face_a["quality"], decoded_a.frame.quality),
                combined_quality(face_b["quality"], decoded_b.frame.quality),
            ),
        }

    if "body" in requested or "person" in requested:
        body_a = await infer_body_record_for_image(decoded_a.image, include_embedding=True)
        body_b = await infer_body_record_for_image(decoded_b.image, include_embedding=True)
        comparison = quality_aware_compare(
            body_a["embedding"],
            body_b["embedding"],
            modality="body",
            threshold_profile=threshold_profile,
            quality_a=combined_quality(body_a["quality"], decoded_a.frame.quality),
            quality_b=combined_quality(body_b["quality"], decoded_b.frame.quality),
        )
        scores["body"] = {
            "score": comparison["quality_adjusted_similarity"],
            "quality": average_quality(
                combined_quality(body_a["quality"], decoded_a.frame.quality),
                combined_quality(body_b["quality"], decoded_b.frame.quality),
            ),
        }

    if "appearance" in requested or "clothing" in requested:
        appearance_a = await infer_appearance_record_for_image(decoded_a.image, include_embedding=True)
        appearance_b = await infer_appearance_record_for_image(decoded_b.image, include_embedding=True)
        comparison = quality_aware_compare(
            appearance_a["embedding"],
            appearance_b["embedding"],
            modality="appearance",
            threshold_profile=threshold_profile,
            quality_a=combined_quality(appearance_a["quality"], decoded_a.frame.quality),
            quality_b=combined_quality(appearance_b["quality"], decoded_b.frame.quality),
        )
        scores["appearance"] = {
            "score": comparison["quality_adjusted_similarity"],
            "quality": average_quality(
                combined_quality(appearance_a["quality"], decoded_a.frame.quality),
                combined_quality(appearance_b["quality"], decoded_b.frame.quality),
            ),
        }

    if "gait" in requested:
        scores["gait"] = {"score": None, "quality": None, "reason": "not_enough_frames"}

    fusion = fuse_modalities(scores, threshold_profile=threshold_profile)
    evidence = pair_input_evidence(decoded_a, decoded_b)
    apply_input_independence_to_decision(fusion, evidence)
    fusion["input"] = evidence
    return portrait_success(request_id, fusion)


@router.post("/v1/compare/batch", dependencies=[Depends(permission_dependency("compare"))])
async def v1_compare_batch(
    background_tasks: BackgroundTasks,
    request: Request,
    image_a: list[UploadFile] = File(...),
    image_b: list[UploadFile] = File(...),
    modality: str = Form("body"),
    threshold_profile: str = Form("normal"),
    include_vectors: bool = Form(False),
    async_mode: bool = Form(False),
    ctx: PortraitRequestContext = Depends(portrait_request_context),
) -> dict[str, Any]:
    request_id = request_id_from_headers(request)
    tenant_id = ctx.tenant_id
    threshold_profile = validate_threshold_profile(threshold_profile)
    modality_key = modality.strip().lower()
    if modality_key in {"person", "persons"}:
        modality_key = "body"
    if modality_key not in {"face", "body", "appearance"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported modality")
    if not image_a or len(image_a) != len(image_b):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image_a and image_b must contain the same number of files")
    if len(image_a) > MAX_COMPARE_BATCH_PAIRS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"too many pairs: {len(image_a)}, max {MAX_COMPARE_BATCH_PAIRS}",
        )
    if async_mode:
        left_payloads = [(file.filename, file.content_type, await read_limited_upload(file)) for file in image_a]
        right_payloads = [(file.filename, file.content_type, await read_limited_upload(file)) for file in image_b]
        job = await run_blocking_io(
            create_batch_job,
            "compare_batch",
            tenant_id,
            metadata={"pair_count": len(left_payloads), "modality": modality_key, "threshold_profile": threshold_profile},
        )

        async def handler(batch_job: VideoJob) -> dict[str, Any]:
            from io import BytesIO
            from fastapi import UploadFile
            from starlette.datastructures import Headers

            async def update_progress(progress: float) -> None:
                batch_job.progress = progress
                await run_blocking_io(persist_video_job, batch_job)

            left_files = [
                UploadFile(filename=name, file=BytesIO(data), headers=Headers({"content-type": ctype or "application/octet-stream"}))
                for name, ctype, data in left_payloads
            ]
            right_files = [
                UploadFile(filename=name, file=BytesIO(data), headers=Headers({"content-type": ctype or "application/octet-stream"}))
                for name, ctype, data in right_payloads
            ]
            results = await compare_batch_results(
                left_files,
                right_files,
                modality_key=modality_key,
                threshold_profile=threshold_profile,
                include_vectors=include_vectors,
                progress_callback=update_progress,
            )
            return {
                "results": results,
                "pair_count": len(results),
                "threshold_profile": threshold_profile,
                "modality": modality_key,
            }

        background_tasks.add_task(run_batch_job, job.job_id, tenant_id, handler)
        return portrait_success(request_id, {"batch_id": job.job_id, "job": job.public_dict(include_result=False)})
    results = await compare_batch_results(
        image_a,
        image_b,
        modality_key=modality_key,
        threshold_profile=threshold_profile,
        include_vectors=include_vectors,
    )
    return portrait_success(
        request_id,
        {
            "results": results,
            "pair_count": len(results),
            "threshold_profile": threshold_profile,
            "modality": modality_key,
        },
    )
