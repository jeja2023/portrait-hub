CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS portrait_tenants (
  tenant_id TEXT PRIMARY KEY,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portrait_people (
  tenant_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  display_name TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, person_id)
);

CREATE TABLE IF NOT EXISTS portrait_features (
  tenant_id TEXT NOT NULL,
  person_id TEXT NOT NULL,
  feature_id TEXT NOT NULL,
  modality TEXT NOT NULL,
  model_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  embedding_dim INTEGER NOT NULL,
  -- embedding keeps a compact JSON byte payload for portability.
  embedding BYTEA NOT NULL,
  -- embedding_json is readable and convenient for export/debug.
  embedding_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  -- embedding_vector enables pgvector cosine search.
  embedding_vector vector,
  quality_score DOUBLE PRECISION NOT NULL,
  source_id TEXT NOT NULL,
  object_info JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, feature_id),
  FOREIGN KEY (tenant_id, person_id) REFERENCES portrait_people (tenant_id, person_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS portrait_features_lookup_idx
  ON portrait_features (tenant_id, modality, model_id, model_version);

-- Covers the exact equality predicates of the vector search query
-- (tenant_id, modality, embedding_dim). This bounds the candidate set before the
-- ANN/sequential distance work and is the only index that helps dimensions
-- without an HNSW index (e.g. 2048, which exceeds pgvector's HNSW dimension limit).
CREATE INDEX IF NOT EXISTS portrait_features_search_idx
  ON portrait_features (tenant_id, modality, embedding_dim);

-- HNSW indexes per supported embedding dimension. pgvector requires a fixed
-- dimension for an HNSW index, hence one partial index per dimension. Note:
-- pgvector caps HNSW indexes at 2000 dimensions, so 2048-d embeddings rely on the
-- btree predicate index above plus an exact distance sort.
-- 索引只按 embedding_dim 分区（tenant_id/modality 是动态的，无法各自单独建索引）；
-- 搜索查询在 ANN 候选之上施加这些等值过滤，并按查询提升 hnsw.ef_search
--（见 search_pgvector / PGVECTOR_HNSW_EF_SEARCH），从而在过滤下保持较高召回。
CREATE INDEX IF NOT EXISTS portrait_features_vector_64_hnsw_idx
  ON portrait_features USING hnsw ((embedding_vector::vector(64)) vector_cosine_ops)
  WHERE embedding_dim = 64;

CREATE INDEX IF NOT EXISTS portrait_features_vector_128_hnsw_idx
  ON portrait_features USING hnsw ((embedding_vector::vector(128)) vector_cosine_ops)
  WHERE embedding_dim = 128;

CREATE INDEX IF NOT EXISTS portrait_features_vector_256_hnsw_idx
  ON portrait_features USING hnsw ((embedding_vector::vector(256)) vector_cosine_ops)
  WHERE embedding_dim = 256;

CREATE INDEX IF NOT EXISTS portrait_features_vector_512_hnsw_idx
  ON portrait_features USING hnsw ((embedding_vector::vector(512)) vector_cosine_ops)
  WHERE embedding_dim = 512;

CREATE INDEX IF NOT EXISTS portrait_features_vector_1024_hnsw_idx
  ON portrait_features USING hnsw ((embedding_vector::vector(1024)) vector_cosine_ops)
  WHERE embedding_dim = 1024;

CREATE TABLE IF NOT EXISTS portrait_thresholds (
  profile TEXT NOT NULL,
  modality TEXT NOT NULL,
  threshold DOUBLE PRECISION NOT NULL CHECK (threshold >= 0 AND threshold <= 1),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (profile, modality)
);

CREATE TABLE IF NOT EXISTS portrait_objects (
  tenant_id TEXT NOT NULL,
  object_key TEXT NOT NULL,
  backend TEXT NOT NULL,
  bucket TEXT,
  sha256 TEXT NOT NULL,
  bytes BIGINT NOT NULL,
  encrypted BOOLEAN NOT NULL DEFAULT false,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, object_key)
);

CREATE TABLE IF NOT EXISTS portrait_video_jobs (
  tenant_id TEXT NOT NULL,
  job_id TEXT NOT NULL,
  status TEXT NOT NULL,
  progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  result JSONB,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, job_id)
);

CREATE TABLE IF NOT EXISTS portrait_streams (
  tenant_id TEXT NOT NULL,
  stream_id TEXT NOT NULL,
  stream_url_ciphertext BYTEA NOT NULL,
  name TEXT,
  settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, stream_id)
);

CREATE TABLE IF NOT EXISTS portrait_stream_events (
  tenant_id TEXT NOT NULL,
  stream_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  type TEXT NOT NULL,
  message TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, stream_id, event_id),
  FOREIGN KEY (tenant_id, stream_id) REFERENCES portrait_streams (tenant_id, stream_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS portrait_task_messages (
  message_id TEXT PRIMARY KEY,
  queue TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portrait_audit_events (
  audit_id BIGSERIAL PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  request_id TEXT NOT NULL,
  event TEXT NOT NULL,
  outcome TEXT NOT NULL,
  audit_prev_hash TEXT,
  audit_hash TEXT NOT NULL,
  audit_hash_algorithm TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portrait_audit_events_hash
  ON portrait_audit_events (audit_hash);
