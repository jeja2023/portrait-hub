export interface PortraitEnvelope<T> {
  status: "success";
  schema_version: string;
  request_id?: string;
  data: T;
}

export interface PortraitErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
  detail?: string;
  request_id?: string;
}

export interface Pagination {
  count: number;
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
  cursor: string | null;
  next_cursor: string | null;
  has_more: boolean;
}

export interface ConsoleIdentityMetadata {
  enabled: boolean;
  provider_name: string;
  issuer: string;
  identity_admin_url: string;
}

export interface ConsoleRole {
  role: string;
  permissions: string[];
}

export interface ConsoleCapabilities {
  tenant_id: string;
  project_id: string;
  auth_kind: string;
  subject: string;
  display_name?: string;
  email?: string;
  roles: string[];
  permissions: string[];
  scopes: string[];
  expires_at: number | null;
  identity: ConsoleIdentityMetadata;
}

export interface IdentityAdminPayload {
  identity: ConsoleIdentityMetadata;
  roles: ConsoleRole[];
}

export interface IdentityMember {
  member_id: string;
  tenant_id: string;
  phone: string;
  display_name: string;
  subject: string | null;
  roles: string[];
  status: "active" | "disabled";
  created_at: number;
  updated_at: number;
}

export interface IdentityMemberListPayload {
  members: IdentityMember[];
  count: number;
}

export interface AccessTenant {
  tenant_id: string;
  name: string;
  status: "active" | "disabled";
  member_count: number;
  application_count: number;
  webhook_count: number;
  created_at: number;
  updated_at: number;
}

export interface AccessTenantListPayload {
  tenants: AccessTenant[];
  count: number;
}

export interface AuthPublicConfig {
  local_enabled: boolean;
  oidc_enabled: boolean;
  provider_name: string;
  credential_login_available: boolean;
}

export interface StepUpStatus {
  authenticated: boolean;
  auth_kind: string;
  recent: boolean;
  seconds_remaining: number;
  max_age_seconds: number;
}

export interface WebhookDeliveryAttempt {
  attempt: number;
  started_at: number;
  finished_at: number;
  status_code: number | null;
  success: boolean;
  error_type: string | null;
  response_bytes: number;
  signature_status?: string;
  signed_at?: number | null;
  trigger?: string;
}

export interface WebhookDelivery {
  delivery_id: string;
  webhook_id: string;
  event: string;
  resource_id: string;
  request_id: string;
  endpoint: string;
  status: string;
  attempt_count: number;
  attempts: WebhookDeliveryAttempt[];
  signature_status?: string;
  signature_algorithm?: string;
  next_retry_at?: number | null;
  dead_letter?: boolean;
  dead_lettered_at?: number | null;
  dead_letter_reason?: string | null;
  manual_retry_count?: number;
  last_manual_retry_at?: number | null;
  created_at: number;
  updated_at: number;
  delivered_at?: number | null;
}

export interface JobSummary {
  job_id: string;
  kind: "video" | "batch";
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: number;
  created_at: number;
  updated_at: number;
  error: string | null;
  cancel_requested: boolean;
}

export interface JobListResponse extends Pagination {
  items: JobSummary[];
  jobs: JobSummary[];
}

export interface PersonSummary {
  person_id: string;
  display_name: string | null;
  metadata: Record<string, unknown>;
  feature_count: number;
  modalities: string[];
  created_at: number;
  updated_at: number;
  thumbnail: string | null;
}

export interface GalleryListResponse extends Pagination {
  items: PersonSummary[];
  people: PersonSummary[];
}

export interface WebSocketTicketResponse {
  ticket: string;
  expires_at: number;
  websocket_path: string;
}
