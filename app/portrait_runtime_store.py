from __future__ import annotations

from copy import deepcopy

from app.portrait_gallery import GALLERY, PersonRecord, gallery_key, persist_feature, persist_person
from app.portrait_jobs import VIDEO_JOBS, VIDEO_JOBS_LOCK, VideoJob, job_key, persist_video_job
from app.portrait_streams import StreamRecord, persist_stream, restore_stream_snapshot_in_store, stream_records_snapshot
from app.portrait_task_queue import TASK_MESSAGE_STORE, QueueMessage


class RuntimeStateStore:
    """进程内兼容状态的统一服务门面。

    历史模块仍会导出这些字典，供测试、迁移和本地工具继续使用。
    新的路由和服务代码应优先通过这个门面访问状态，先把生命周期边界集中起来，
    再逐步替换为完全外部化的后端存储。
    """

    def gallery_person_snapshot(self, tenant_id: str, person_id: str) -> PersonRecord | None:
        person = GALLERY.get(gallery_key(tenant_id, person_id))
        return deepcopy(person) if person is not None else None

    def gallery_people_snapshots(self, tenant_id: str | None = None) -> list[PersonRecord]:
        people = [person for person in GALLERY.values() if tenant_id is None or person.tenant_id == tenant_id]
        return [deepcopy(person) for person in sorted(people, key=lambda item: (item.tenant_id, item.person_id))]

    def restore_gallery_person(self, person: PersonRecord) -> None:
        restored_person = deepcopy(person)
        GALLERY[gallery_key(restored_person.tenant_id, restored_person.person_id)] = restored_person
        persist_person(restored_person)
        for feature in restored_person.features:
            persist_feature(restored_person, feature)

    def video_job_snapshot(self, tenant_id: str, job_id: str) -> VideoJob | None:
        with VIDEO_JOBS_LOCK:
            job = VIDEO_JOBS.get(job_key(tenant_id, job_id))
            return deepcopy(job) if job is not None else None

    def video_jobs_snapshots(self, tenant_id: str | None = None) -> list[VideoJob]:
        with VIDEO_JOBS_LOCK:
            jobs = [job for job in VIDEO_JOBS.values() if tenant_id is None or job.tenant_id == tenant_id]
            return [deepcopy(job) for job in sorted(jobs, key=lambda item: (item.tenant_id, item.job_id))]

    def restore_video_job(self, job: VideoJob) -> None:
        restored_job = deepcopy(job)
        VIDEO_JOBS[job_key(restored_job.tenant_id, restored_job.job_id)] = restored_job
        persist_video_job(restored_job)

    def stream_snapshots(self, tenant_id: str | None = None) -> list[StreamRecord]:
        streams = [stream for stream in stream_records_snapshot() if tenant_id is None or stream.tenant_id == tenant_id]
        return [deepcopy(stream) for stream in sorted(streams, key=lambda item: (item.tenant_id, item.stream_id))]

    def restore_stream(self, stream: StreamRecord) -> None:
        restored_stream = deepcopy(stream)
        restore_stream_snapshot_in_store(restored_stream)
        persist_stream(restored_stream)

    def task_message_snapshots(self) -> list[QueueMessage]:
        return [deepcopy(message) for message in TASK_MESSAGE_STORE.snapshot()]

    def task_message_count(self) -> int:
        return TASK_MESSAGE_STORE.count()


RUNTIME_STORE = RuntimeStateStore()


def gallery_person_snapshot(tenant_id: str, person_id: str) -> PersonRecord | None:
    return RUNTIME_STORE.gallery_person_snapshot(tenant_id, person_id)


def gallery_people_snapshots(tenant_id: str | None = None) -> list[PersonRecord]:
    return RUNTIME_STORE.gallery_people_snapshots(tenant_id)


def restore_gallery_person(person: PersonRecord) -> None:
    RUNTIME_STORE.restore_gallery_person(person)


def video_job_snapshot(tenant_id: str, job_id: str) -> VideoJob | None:
    return RUNTIME_STORE.video_job_snapshot(tenant_id, job_id)


def video_jobs_snapshots(tenant_id: str | None = None) -> list[VideoJob]:
    return RUNTIME_STORE.video_jobs_snapshots(tenant_id)


def restore_video_job_in_store(job: VideoJob) -> None:
    RUNTIME_STORE.restore_video_job(job)


def stream_snapshots(tenant_id: str | None = None) -> list[StreamRecord]:
    return RUNTIME_STORE.stream_snapshots(tenant_id)


def restore_stream_in_store(stream: StreamRecord) -> None:
    RUNTIME_STORE.restore_stream(stream)


def task_message_snapshots() -> list[QueueMessage]:
    return RUNTIME_STORE.task_message_snapshots()


def task_message_count() -> int:
    return RUNTIME_STORE.task_message_count()


__all__ = [
    "RUNTIME_STORE",
    "RuntimeStateStore",
    "gallery_people_snapshots",
    "gallery_person_snapshot",
    "restore_gallery_person",
    "restore_stream_in_store",
    "restore_video_job_in_store",
    "stream_snapshots",
    "task_message_count",
    "task_message_snapshots",
    "video_job_snapshot",
    "video_jobs_snapshots",
]

