<script setup lang="ts">
import { computed } from "vue";
import { ImageOff } from "@lucide/vue";

interface FrameGridItem {
  id: string;
  label: string;
  src: string;
  meta: string[];
}

const props = withDefaults(
  defineProps<{
    data: unknown;
    title?: string;
    limit?: number;
  }>(),
  { title: "结果证据", limit: 24 },
);

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

function firstString(record: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function countMeta(label: string, value: unknown): string | null {
  return typeof value === "number" ? `${label} ${value}` : null;
}

function qualityMeta(value: unknown): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return `质量 ${Math.round(value * 100)}%`;
}

function buildFrameItems(frames: Record<string, unknown>[]): FrameGridItem[] {
  return frames.flatMap((frame, index) => {
    const src = firstString(frame, ["thumbnail", "preview", "image", "src", "content_url"]);
    const frameIndex = frame.source_frame_index ?? frame.frame_index ?? index + 1;
    const sourceSeconds = typeof frame.source_seconds === "number" ? `${frame.source_seconds.toFixed(2)}s` : null;
    const meta = [
      sourceSeconds,
      countMeta("人员", frame.person_count),
      countMeta("人脸", frame.face_count),
      countMeta("轨迹", frame.track_count),
      qualityMeta(asRecord(frame.quality).quality_score ?? frame.quality_score),
    ].filter((item): item is string => Boolean(item));
    return [
      {
        id: String(frame.frame_id ?? frameIndex ?? index),
        label: `帧 ${frameIndex}`,
        src,
        meta,
      },
    ];
  });
}

function buildPreviewItems(previews: Record<string, unknown>[]): FrameGridItem[] {
  return previews.map((preview, index) => ({
    id: String(preview.artifact_id ?? preview.id ?? index),
    label: String(preview.label ?? `预览 ${index + 1}`),
    src: firstString(preview, ["src", "content_url", "preview", "thumbnail", "image"]),
    meta: [
      typeof preview.width === "number" && typeof preview.height === "number"
        ? `${preview.width}×${preview.height}`
        : null,
    ].filter((item): item is string => Boolean(item)),
  }));
}

const items = computed(() => {
  const root = asRecord(props.data);
  const nestedResult = asRecord(root.result);
  const payload = Object.keys(nestedResult).length ? nestedResult : root;
  const frames = buildFrameItems(asArray(payload.frames));
  const previews = buildPreviewItems(asArray(payload.previews));
  return [...frames, ...previews].slice(0, props.limit);
});
</script>

<template>
  <section class="frame-grid" aria-labelledby="frame-grid-title">
    <h3 id="frame-grid-title">{{ title }}</h3>
    <div v-if="items.length" class="frame-grid__items">
      <figure v-for="item in items" :key="item.id" class="frame-card">
        <div class="frame-card__image">
          <img v-if="item.src" :src="item.src" :alt="item.label" loading="lazy" />
          <span v-else><ImageOff :size="24" />无可见预览</span>
        </div>
        <figcaption>
          <strong>{{ item.label }}</strong>
          <small v-if="item.meta.length">{{ item.meta.join(" · ") }}</small>
        </figcaption>
      </figure>
    </div>
    <p v-else class="frame-grid__empty">当前结果没有可展示的帧预览或标注图。</p>
  </section>
</template>

<style scoped>
.frame-grid {
  display: grid;
  gap: 12px;
  margin-top: 18px;
}
.frame-grid h3 {
  margin: 0;
  font-size: 15px;
}
.frame-grid__items {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}
.frame-card {
  min-width: 0;
  margin: 0;
  overflow: hidden;
  background: #fff;
  border: 1px solid #d8e0de;
  border-radius: 5px;
}
.frame-card__image {
  aspect-ratio: 4 / 3;
  display: grid;
  place-items: center;
  overflow: hidden;
  color: #62706d;
  background: #eef3f2;
}
.frame-card__image img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.frame-card__image span {
  display: grid;
  place-items: center;
  gap: 6px;
  font-size: 12px;
}
.frame-card figcaption {
  display: grid;
  gap: 4px;
  padding: 9px;
}
.frame-card small,
.frame-grid__empty {
  color: #62706d;
  font-size: 12px;
}
.frame-grid__empty {
  margin: 0;
  padding: 18px;
  border: 1px dashed #c6d0ce;
  border-radius: 5px;
}
</style>
