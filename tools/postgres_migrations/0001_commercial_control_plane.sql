-- Expand-only migration for the commercial control plane.
-- No existing columns or tables are removed; contract work requires a later migration.

CREATE TABLE IF NOT EXISTS portrait_control_state (
  state_key TEXT PRIMARY KEY,
  revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  definition_version TEXT NOT NULL DEFAULT '1.0',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL DEFAULT 'migration'
);

CREATE TABLE IF NOT EXISTS portrait_model_registry (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  name TEXT NOT NULL,
  capability TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  owner TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'archived')),
  quality_gates JSONB NOT NULL DEFAULT '{}'::jsonb,
  classification TEXT NOT NULL DEFAULT 'internal',
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  deleted_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, project_id, model_id),
  UNIQUE (tenant_id, project_id, name, capability),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE TABLE IF NOT EXISTS portrait_model_artifacts (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  model_version_id TEXT NOT NULL,
  model_id TEXT NOT NULL,
  version_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'candidate', 'shadow', 'canary', 'active', 'deprecated', 'blocked')),
  framework TEXT NOT NULL,
  runtime TEXT NOT NULL,
  model_target TEXT NOT NULL,
  object_key TEXT,
  artifact_uri TEXT NOT NULL DEFAULT '',
  content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  artifact_size BIGINT NOT NULL CHECK (artifact_size >= 0),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  storage_region TEXT,
  license_name TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  redistribution_allowed BOOLEAN NOT NULL DEFAULT false,
  model_card_ref TEXT NOT NULL,
  governance_ref TEXT NOT NULL,
  input_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_contract JSONB NOT NULL DEFAULT '{}'::jsonb,
  thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
  dataset_lineage JSONB NOT NULL DEFAULT '[]'::jsonb,
  supports_cpu BOOLEAN NOT NULL DEFAULT false,
  supports_batching BOOLEAN NOT NULL DEFAULT false,
  max_batch_size INTEGER NOT NULL DEFAULT 1 CHECK (max_batch_size BETWEEN 1 AND 4096),
  rollback_target BOOLEAN NOT NULL DEFAULT false,
  lifecycle_state TEXT NOT NULL DEFAULT 'available',
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  deleted_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, project_id, model_version_id),
  UNIQUE (tenant_id, project_id, model_id, version_name),
  FOREIGN KEY (tenant_id, project_id, model_id)
    REFERENCES portrait_model_registry (tenant_id, project_id, model_id) ON DELETE RESTRICT,
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE INDEX IF NOT EXISTS portrait_model_artifacts_scope_status_idx
  ON portrait_model_artifacts (tenant_id, project_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS portrait_model_artifacts_digest_idx
  ON portrait_model_artifacts (sha256);

CREATE TABLE IF NOT EXISTS portrait_model_evaluations (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  evaluation_id TEXT NOT NULL,
  model_version_id TEXT NOT NULL,
  dataset_id TEXT NOT NULL,
  dataset_manifest_sha256 TEXT NOT NULL CHECK (length(dataset_manifest_sha256) = 64),
  definition_version TEXT NOT NULL,
  environment JSONB NOT NULL DEFAULT '{}'::jsonb,
  thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
  metrics JSONB NOT NULL,
  quality_gates JSONB NOT NULL DEFAULT '{}'::jsonb,
  gate_results JSONB NOT NULL DEFAULT '[]'::jsonb,
  passed BOOLEAN NOT NULL,
  report_object_key TEXT,
  report_sha256 TEXT CHECK (report_sha256 IS NULL OR length(report_sha256) = 64),
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, evaluation_id),
  FOREIGN KEY (tenant_id, project_id, model_version_id)
    REFERENCES portrait_model_artifacts (tenant_id, project_id, model_version_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS portrait_model_evaluations_version_created_idx
  ON portrait_model_evaluations (tenant_id, project_id, model_version_id, created_at DESC);

CREATE TABLE IF NOT EXISTS portrait_model_approvals (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  approval_id TEXT NOT NULL,
  model_version_id TEXT NOT NULL,
  approver TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
  policy TEXT NOT NULL,
  comment TEXT NOT NULL DEFAULT '',
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, project_id, approval_id),
  UNIQUE (tenant_id, project_id, model_version_id, approver, policy, decision),
  FOREIGN KEY (tenant_id, project_id, model_version_id)
    REFERENCES portrait_model_artifacts (tenant_id, project_id, model_version_id) ON DELETE RESTRICT,
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE TABLE IF NOT EXISTS portrait_model_release_events (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  release_event_id TEXT NOT NULL,
  model_version_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('shadow', 'canary', 'activate', 'pause', 'rollback', 'deprecate')),
  alias TEXT NOT NULL,
  previous_target TEXT,
  target TEXT NOT NULL,
  traffic_percentage INTEGER CHECK (traffic_percentage IS NULL OR traffic_percentage BETWEEN 1 AND 99),
  risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
  reason TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failed', 'rolled_back')),
  definition_version TEXT NOT NULL DEFAULT '1.0',
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, release_event_id),
  FOREIGN KEY (tenant_id, project_id, model_version_id)
    REFERENCES portrait_model_artifacts (tenant_id, project_id, model_version_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS portrait_model_release_events_alias_created_idx
  ON portrait_model_release_events (tenant_id, project_id, alias, created_at DESC);

CREATE TABLE IF NOT EXISTS portrait_review_samples (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  sample_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
  status TEXT NOT NULL CHECK (status IN ('queued', 'assigned', 'exported', 'reviewed', 'accepted', 'rejected', 'deleted')),
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  object_key TEXT,
  object_sha256 TEXT CHECK (object_sha256 IS NULL OR length(object_sha256) = 64),
  model_version_id TEXT,
  score DOUBLE PRECISION,
  threshold DOUBLE PRECISION,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  assigned_to TEXT,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  deleted_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, project_id, sample_id),
  UNIQUE (tenant_id, project_id, source_type, source_id, reason),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);
CREATE INDEX IF NOT EXISTS portrait_review_samples_queue_idx
  ON portrait_review_samples (tenant_id, project_id, status, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS portrait_annotation_exports (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  export_id TEXT NOT NULL,
  format TEXT NOT NULL CHECK (format IN ('label_studio', 'cvat')),
  status TEXT NOT NULL,
  sample_ids JSONB NOT NULL,
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  external_task_ref TEXT,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, export_id)
);

CREATE TABLE IF NOT EXISTS portrait_annotation_imports (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  import_id TEXT NOT NULL,
  export_id TEXT,
  format TEXT NOT NULL CHECK (format IN ('label_studio', 'cvat')),
  status TEXT NOT NULL,
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  accepted_count INTEGER NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
  rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
  conflicts JSONB NOT NULL DEFAULT '[]'::jsonb,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, import_id),
  FOREIGN KEY (tenant_id, project_id, export_id)
    REFERENCES portrait_annotation_exports (tenant_id, project_id, export_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS portrait_dataset_manifests (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  dataset_id TEXT NOT NULL,
  name TEXT NOT NULL,
  purpose TEXT NOT NULL,
  definition_version TEXT NOT NULL,
  split_definition JSONB NOT NULL,
  sample_count BIGINT NOT NULL CHECK (sample_count >= 0),
  object_key TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  lineage JSONB NOT NULL DEFAULT '[]'::jsonb,
  status TEXT NOT NULL DEFAULT 'immutable' CHECK (status IN ('building', 'immutable', 'retired')),
  classification TEXT NOT NULL DEFAULT 'sensitive',
  encryption_key_version TEXT,
  storage_region TEXT,
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, dataset_id),
  UNIQUE (tenant_id, project_id, sha256),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE TABLE IF NOT EXISTS portrait_customer_profiles (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  profile_id TEXT NOT NULL,
  commercial_status TEXT NOT NULL
    CHECK (commercial_status IN ('trial', 'active', 'grace', 'suspended', 'offboarding', 'closed')),
  delivery_tier TEXT NOT NULL,
  environment TEXT NOT NULL,
  timezone TEXT NOT NULL,
  budget_limit NUMERIC(18, 4) CHECK (budget_limit IS NULL OR budget_limit >= 0),
  budget_currency CHAR(3) NOT NULL DEFAULT 'CNY',
  current_entitlement_id TEXT,
  template_id TEXT,
  template_version TEXT,
  template_configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
  notification_channels JSONB NOT NULL DEFAULT '[]'::jsonb,
  status_reason TEXT NOT NULL,
  approved_by TEXT,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  deleted_at TIMESTAMPTZ,
  PRIMARY KEY (tenant_id, project_id, profile_id),
  UNIQUE (tenant_id, project_id),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE TABLE IF NOT EXISTS portrait_entitlements (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  entitlement_id TEXT NOT NULL,
  definition_version TEXT NOT NULL,
  product_version TEXT NOT NULL,
  delivery_tier TEXT NOT NULL,
  allowed_capabilities JSONB NOT NULL,
  allowed_models JSONB NOT NULL DEFAULT '[]'::jsonb,
  project_limit INTEGER NOT NULL CHECK (project_limit > 0),
  concurrency_limit INTEGER NOT NULL CHECK (concurrency_limit > 0),
  stream_limit INTEGER NOT NULL CHECK (stream_limit >= 0),
  support_level TEXT NOT NULL,
  grace_period_seconds INTEGER NOT NULL DEFAULT 0 CHECK (grace_period_seconds >= 0),
  supersedes TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'superseded', 'expired', 'revoked')),
  version BIGINT NOT NULL CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  approved_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, entitlement_id),
  UNIQUE (tenant_id, project_id, version),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS portrait_entitlements_one_active_idx
  ON portrait_entitlements (tenant_id, project_id) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS portrait_usage_daily_summary (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  usage_date DATE NOT NULL,
  capability TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  model_version TEXT NOT NULL,
  definition_version TEXT NOT NULL,
  request_count BIGINT NOT NULL DEFAULT 0 CHECK (request_count >= 0),
  success_count BIGINT NOT NULL DEFAULT 0 CHECK (success_count >= 0),
  business_rejection_count BIGINT NOT NULL DEFAULT 0 CHECK (business_rejection_count >= 0),
  system_failure_count BIGINT NOT NULL DEFAULT 0 CHECK (system_failure_count >= 0),
  retry_count BIGINT NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
  duplicate_count BIGINT NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
  image_count BIGINT NOT NULL DEFAULT 0 CHECK (image_count >= 0),
  video_seconds NUMERIC(20, 3) NOT NULL DEFAULT 0 CHECK (video_seconds >= 0),
  gpu_seconds NUMERIC(20, 3) NOT NULL DEFAULT 0 CHECK (gpu_seconds >= 0),
  source_watermark TEXT NOT NULL,
  aggregate_sha256 TEXT NOT NULL CHECK (length(aggregate_sha256) = 64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, project_id, usage_date, capability, endpoint, model_version, definition_version)
);
CREATE INDEX IF NOT EXISTS portrait_usage_daily_summary_scope_date_idx
  ON portrait_usage_daily_summary (tenant_id, project_id, usage_date DESC);

CREATE TABLE IF NOT EXISTS portrait_cost_attribution (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  attribution_id TEXT NOT NULL,
  usage_date DATE NOT NULL,
  model_version TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  quantity NUMERIC(20, 6) NOT NULL CHECK (quantity >= 0),
  unit_cost NUMERIC(20, 8) NOT NULL CHECK (unit_cost >= 0),
  currency CHAR(3) NOT NULL,
  amount NUMERIC(20, 6) GENERATED ALWAYS AS (quantity * unit_cost) STORED,
  definition_version TEXT NOT NULL,
  parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
  request_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, attribution_id)
);

CREATE TABLE IF NOT EXISTS portrait_sla_definitions (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  sla_definition_id TEXT NOT NULL,
  definition_version TEXT NOT NULL,
  availability_target DOUBLE PRECISION NOT NULL CHECK (availability_target > 0 AND availability_target <= 1),
  p95_latency_ms INTEGER NOT NULL CHECK (p95_latency_ms > 0),
  p99_latency_ms INTEGER NOT NULL CHECK (p99_latency_ms >= p95_latency_ms),
  window_seconds INTEGER NOT NULL CHECK (window_seconds >= 60),
  timezone TEXT NOT NULL,
  exclusion_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, sla_definition_id),
  UNIQUE (tenant_id, project_id, definition_version),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE TABLE IF NOT EXISTS portrait_sla_reports (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  sla_report_id TEXT NOT NULL,
  definition_version TEXT NOT NULL,
  window_started_at TIMESTAMPTZ NOT NULL,
  window_ended_at TIMESTAMPTZ NOT NULL,
  availability DOUBLE PRECISION NOT NULL CHECK (availability >= 0 AND availability <= 1),
  availability_target DOUBLE PRECISION NOT NULL,
  p95_latency_ms DOUBLE PRECISION,
  p99_latency_ms DOUBLE PRECISION,
  request_count BIGINT NOT NULL CHECK (request_count >= 0),
  error_count BIGINT NOT NULL CHECK (error_count >= 0),
  error_budget_allowed DOUBLE PRECISION NOT NULL,
  error_budget_remaining DOUBLE PRECISION NOT NULL,
  met BOOLEAN NOT NULL,
  source_complete BOOLEAN NOT NULL,
  report_object_key TEXT,
  report_sha256 TEXT CHECK (report_sha256 IS NULL OR length(report_sha256) = 64),
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, sla_report_id),
  CHECK (window_ended_at > window_started_at)
);
CREATE INDEX IF NOT EXISTS portrait_sla_reports_scope_window_idx
  ON portrait_sla_reports (tenant_id, project_id, window_ended_at DESC);

CREATE TABLE IF NOT EXISTS portrait_incidents (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  incident_id TEXT NOT NULL,
  incident_number TEXT NOT NULL,
  title TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('sev1', 'sev2', 'sev3', 'sev4')),
  status TEXT NOT NULL CHECK (status IN ('investigating', 'identified', 'monitoring', 'resolved', 'closed')),
  impact_scope TEXT NOT NULL,
  customer_visible_summary TEXT NOT NULL DEFAULT '',
  internal_summary TEXT NOT NULL DEFAULT '',
  owner TEXT NOT NULL,
  root_cause TEXT,
  action_items JSONB NOT NULL DEFAULT '[]'::jsonb,
  timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
  related_request_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  related_model_versions JSONB NOT NULL DEFAULT '[]'::jsonb,
  started_at TIMESTAMPTZ NOT NULL,
  recovered_at TIMESTAMPTZ,
  closed_at TIMESTAMPTZ,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, incident_id),
  UNIQUE (tenant_id, project_id, incident_number)
);
CREATE INDEX IF NOT EXISTS portrait_incidents_scope_status_idx
  ON portrait_incidents (tenant_id, project_id, status, severity, started_at DESC);

CREATE TABLE IF NOT EXISTS portrait_compliance_records (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  compliance_record_id TEXT NOT NULL,
  control_id TEXT NOT NULL CHECK (control_id ~ '^COM-0(0[1-9]|1[0-2])$'),
  status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'rejected')),
  definition_version TEXT NOT NULL,
  applicability TEXT NOT NULL,
  legal_basis TEXT NOT NULL DEFAULT '',
  processing_purpose TEXT NOT NULL DEFAULT '',
  data_categories JSONB NOT NULL DEFAULT '[]'::jsonb,
  data_subjects JSONB NOT NULL DEFAULT '[]'::jsonb,
  storage_regions JSONB NOT NULL DEFAULT '[]'::jsonb,
  retention JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
  risk_summary TEXT NOT NULL DEFAULT '',
  mitigations JSONB NOT NULL DEFAULT '[]'::jsonb,
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, compliance_record_id),
  UNIQUE (tenant_id, project_id, control_id),
  CHECK ((status <> 'approved') OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE TABLE IF NOT EXISTS portrait_rights_requests (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  rights_request_id TEXT NOT NULL,
  request_type TEXT NOT NULL CHECK (request_type IN ('access', 'correction', 'deletion', 'withdrawal', 'restriction', 'export')),
  status TEXT NOT NULL CHECK (status IN ('received', 'identity_pending', 'verified', 'in_progress', 'completed', 'rejected')),
  subject_reference_hash TEXT NOT NULL CHECK (length(subject_reference_hash) = 64),
  identity_verification TEXT NOT NULL DEFAULT 'pending',
  due_at TIMESTAMPTZ NOT NULL,
  exception_basis TEXT,
  execution_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  timeline JSONB NOT NULL DEFAULT '[]'::jsonb,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, rights_request_id)
);
CREATE INDEX IF NOT EXISTS portrait_rights_requests_due_idx
  ON portrait_rights_requests (tenant_id, project_id, status, due_at);

CREATE TABLE IF NOT EXISTS portrait_evidence_packages (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  evidence_package_id TEXT NOT NULL,
  package_type TEXT NOT NULL,
  audience TEXT NOT NULL CHECK (audience IN ('internal', 'customer')),
  environment TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('building', 'complete', 'failed', 'expired')),
  definition_version TEXT NOT NULL,
  manifest_object_key TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
  signature_algorithm TEXT NOT NULL,
  signature TEXT NOT NULL,
  artifact_count INTEGER NOT NULL CHECK (artifact_count >= 0),
  missing_required_artifacts JSONB NOT NULL DEFAULT '[]'::jsonb,
  storage_region TEXT,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  effective_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  retention_policy_id TEXT,
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, evidence_package_id),
  UNIQUE (tenant_id, project_id, manifest_sha256),
  CHECK ((status <> 'complete') OR (artifact_count > 0 AND jsonb_array_length(missing_required_artifacts) = 0)),
  CHECK (expires_at IS NULL OR expires_at > effective_at)
);

CREATE TABLE IF NOT EXISTS portrait_industry_template_applications (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  template_application_id TEXT NOT NULL,
  template_id TEXT NOT NULL,
  template_version TEXT NOT NULL,
  fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
  previous_configuration JSONB NOT NULL DEFAULT '{}'::jsonb,
  applied_configuration JSONB NOT NULL,
  rollback_configuration JSONB NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('applied', 'rolled_back', 'failed')),
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, template_application_id)
);

CREATE TABLE IF NOT EXISTS portrait_support_cases (
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  support_case_id TEXT NOT NULL,
  severity TEXT NOT NULL CHECK (severity IN ('sev1', 'sev2', 'sev3', 'sev4')),
  status TEXT NOT NULL,
  title TEXT NOT NULL,
  environment TEXT NOT NULL,
  product_version TEXT NOT NULL,
  request_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  task_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  owner TEXT,
  response_due_at TIMESTAMPTZ,
  redacted_attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
  version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
  request_id TEXT NOT NULL,
  audit_event_id TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT NOT NULL,
  PRIMARY KEY (tenant_id, project_id, support_case_id)
);

CREATE TABLE IF NOT EXISTS portrait_control_outbox (
  outbox_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  project_id TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  payload JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'delivering', 'delivered', 'failed', 'dead_letter')),
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  delivered_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, project_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS portrait_control_outbox_delivery_idx
  ON portrait_control_outbox (status, available_at, created_at) WHERE status IN ('pending', 'failed');
