-- Transactional query projection for aggregate commercial control snapshots.

CREATE TABLE IF NOT EXISTS portrait_control_entities (
  state_key TEXT NOT NULL,
  collection_name TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  entity_version BIGINT NOT NULL DEFAULT 1 CHECK (entity_version > 0),
  status TEXT NOT NULL DEFAULT 'recorded',
  classification TEXT NOT NULL DEFAULT 'internal',
  effective_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  updated_by TEXT NOT NULL,
  payload JSONB NOT NULL,
  PRIMARY KEY (state_key, collection_name, tenant_id, project_id, entity_id),
  CHECK (tenant_id <> '' AND project_id <> '' AND entity_id <> ''),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE INDEX IF NOT EXISTS portrait_control_entities_scope_collection_idx
  ON portrait_control_entities (tenant_id, project_id, collection_name, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS portrait_control_entities_expiry_idx
  ON portrait_control_entities (collection_name, expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS portrait_control_entities_payload_gin_idx
  ON portrait_control_entities USING GIN (payload jsonb_path_ops);
