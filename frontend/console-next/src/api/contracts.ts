export interface PortraitEnvelope<T> {
  status: "success";
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
