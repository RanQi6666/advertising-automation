export type EntityStatus = "proposed" | "selected" | "approved" | "rejected" | string;

export interface Timestamped {
  id: string;
  created_at: string;
  updated_at: string;
}

export interface WorkOrder extends Timestamped {
  raw_content: string;
  parsed_fields: Record<string, unknown>;
  project_name: string | null;
  country: string | null;
  media: string | null;
  event_name: string | null;
  product_name: string | null;
  audience_description: string | null;
  landing_url: string | null;
  report_timezone: string | null;
  status: string;
  metadata_json: Record<string, unknown>;
}

export interface OperatorUser extends Timestamped {
  email: string;
  full_name: string | null;
  role: "operator" | "admin" | string;
  is_active: boolean;
}

export type DeliveryFieldStatus = "extracted" | "suggested" | "missing" | "conflict";

export interface WorkOrderDeliveryField<T = unknown> {
  value: T | null;
  normalized_value: T | null;
  status: DeliveryFieldStatus;
  confidence: number;
  evidence: string[];
  candidates: unknown[];
  reason: string | null;
}

export interface WorkOrderDeliveryFields {
  landing_url: WorkOrderDeliveryField<string>;
  event_name: WorkOrderDeliveryField<string>;
  country: WorkOrderDeliveryField<string>;
  age_min: WorkOrderDeliveryField<number | string>;
  age_max: WorkOrderDeliveryField<number | string>;
  gender: WorkOrderDeliveryField<string>;
  audience_description_raw: WorkOrderDeliveryField<string>;
}

export interface WorkOrderDeliveryExtraction {
  schema_version: string;
  fields: WorkOrderDeliveryFields;
  review: Record<string, unknown>;
}

export interface ReviewedDeliveryFields {
  landing_url: string;
  event_name: string;
  country: string;
  age_min: string;
  age_max: string;
  gender: string;
  audience_description_raw: string;
}

export interface Campaign extends Timestamped {
  client_id: string | null;
  brand_id: string | null;
  work_order_id: string | null;
  name: string;
  objective: string | null;
  product_name: string | null;
  audience_description: string | null;
  budget_notes: string | null;
  status: string;
  metadata_json: Record<string, unknown>;
}

export type AdGenerationStatus =
  | "queued"
  | "processing"
  | "fields_review"
  | "topic_review"
  | "copy_review"
  | "image_review"
  | "video_review"
  | "final_review"
  | "generated"
  | "reviewing"
  | "reviewed"
  | "returned"
  | "completed"
  | "failed"
  | string;

export interface AdGenerationJob extends Timestamped {
  external_order_id: string | null;
  status: AdGenerationStatus;
  callback_url: string | null;
  request_payload: Record<string, unknown>;
  result_payload: Record<string, unknown>;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  owner_user_id: string | null;
  locked_by: string | null;
  locked_at: string | null;
  can_edit: boolean;
  metadata_json: Record<string, unknown>;
  review_url: string | null;
  return_url: string | null;
}

export interface AdGenerationJobAccepted {
  job_id: string;
  status: AdGenerationStatus;
  review_url: string;
}

export type AdPerformanceConfidence = "low" | "medium" | "high" | string;
export type AdPerformanceSeverity = "info" | "warning" | "critical" | string;
export type AdPerformancePriority = "low" | "medium" | "high" | string;
export type AdPerformanceOptimizationAction =
  | "keep"
  | "regenerate"
  | "rewrite"
  | "check"
  | "watch"
  | "reduce"
  | "increase"
  | "pause"
  | "create_draft"
  | "missing"
  | string;

export interface AdPerformanceProblem {
  code: string;
  severity: AdPerformanceSeverity;
  area: string;
  title: string;
  evidence: string[];
  diagnosis: string;
}

export interface AdPerformanceRecommendation {
  code: string;
  priority: AdPerformancePriority;
  area: string;
  action: string;
  detail: string;
}

export interface AdPerformanceVisualAnalysis {
  summary: string | null;
  observed_elements: string[];
  strengths: string[];
  weaknesses: string[];
  recommendations: string[];
  risk_notes: string[];
  source_image_url: string | null;
  source_video_url: string | null;
  confidence_note: string | null;
}

export interface AdPerformanceAIAnalysis {
  summary: string;
  root_causes: string[];
  recommended_actions: string[];
  next_tests: string[];
  creative_feedback: string[];
  audience_feedback: string[];
  landing_page_feedback: string[];
  budget_delivery_feedback: string[];
  risk_notes: string[];
  visual_analysis: AdPerformanceVisualAnalysis | null;
  optimization_work_order: AdPerformanceOptimizationWorkOrder | null;
  confidence_note: string | null;
}

export interface AdPerformanceDataCompleteness {
  level: "unknown" | "low" | "medium" | "high" | string;
  score: number;
  available: string[];
  missing: string[];
  can_analyze: string[];
  cannot_analyze: string[];
  notes: string[];
}

export interface AdPerformanceOptimizationFieldAdvice {
  field: string;
  label: string;
  current_value: unknown;
  action: AdPerformanceOptimizationAction;
  priority: AdPerformancePriority;
  suggested_value: unknown;
  suggested_direction: string | null;
  generation_prompt: string | null;
  reason: string;
  source: "rules" | "ai" | "rules_and_ai" | string;
  can_apply_to_generation: boolean;
  missing: boolean;
}

export interface AdPerformanceOptimizationWorkOrder {
  schema_version: string;
  operator_summary: string;
  priority: AdPerformancePriority;
  overall_action: string;
  next_step: string | null;
  modules_to_change: string[];
  modules_to_keep: string[];
  modules_to_watch: string[];
  campaign: AdPerformanceOptimizationFieldAdvice[];
  adset: AdPerformanceOptimizationFieldAdvice[];
  creative: AdPerformanceOptimizationFieldAdvice[];
  warnings: string[];
}

export interface AdPerformanceAnalysisResult {
  summary: string;
  confidence: AdPerformanceConfidence;
  analysis_mode: "llm_only" | "llm_failed" | "rules_only" | "rules_and_llm" | "rules_with_llm_fallback" | string;
  data_completeness: AdPerformanceDataCompleteness;
  optimization_work_order: AdPerformanceOptimizationWorkOrder;
  problems: AdPerformanceProblem[];
  recommendations: AdPerformanceRecommendation[];
  next_checks: string[];
  ai_analysis: AdPerformanceAIAnalysis | null;
  rule_summary: string | null;
  llm_error: string | null;
}

export interface AdPerformanceAnalysis extends Timestamped {
  analysis_id: string;
  external_user_id: string | null;
  source_type: string;
  status: string;
  campaign_external_id: string | null;
  campaign_name: string | null;
  adset_external_id: string | null;
  adset_name: string | null;
  creative_external_id: string | null;
  creative_name: string | null;
  date_start: string | null;
  date_stop: string | null;
  request_payload: Record<string, unknown>;
  metrics: Record<string, unknown>;
  analysis_result: AdPerformanceAnalysisResult;
  error_message: string | null;
  owner_user_id: string | null;
  locked_by: string | null;
  locked_at: string | null;
  can_edit: boolean;
}

export interface LandingPageSnapshot extends Timestamped {
  campaign_id: string;
  work_order_id: string | null;
  url: string;
  status: string;
  http_status: number | null;
  title: string | null;
  description: string | null;
  text_content: string | null;
  extracted_data: Record<string, unknown>;
  error_message: string | null;
  fetched_at: string;
  metadata_json: Record<string, unknown>;
}

export interface Topic extends Timestamped {
  campaign_id: string;
  title: string;
  angle: string;
  audience: string | null;
  selling_points: string[];
  risk_notes: string | null;
  rationale: string | null;
  status: string;
  score: number | null;
  source_data: Record<string, unknown>;
}

export interface CopyDraft extends Timestamped {
  campaign_id: string;
  topic_id: string;
  body: string;
  primary_text: string | null;
  headline: string | null;
  description: string | null;
  cta: string | null;
  status: string;
  version: number;
  model_name: string | null;
  prompt_version: string | null;
  metadata_json: Record<string, unknown>;
}

export interface CreativeAsset extends Timestamped {
  campaign_id: string;
  draft_id: string;
  kind: string;
  url: string | null;
  storage_key: string | null;
  prompt: string;
  alt_text: string | null;
  size: string;
  status: string;
  version: number;
  metadata_json: Record<string, unknown>;
}

export interface VideoAsset extends Timestamped {
  campaign_id: string;
  draft_id: string | null;
  source_asset_ids: string[];
  url: string | null;
  storage_key: string | null;
  prompt: string | null;
  storyboard: unknown[];
  duration_seconds: number | null;
  aspect_ratio: string;
  status: string;
  provider_job_id: string | null;
  error_message: string | null;
  version: number;
  metadata_json: Record<string, unknown>;
}

export interface VideoStoryboardResponse {
  campaign_id: string;
  draft_id: string | null;
  creative_asset_ids: string[];
  duration_seconds: number;
  aspect_ratio: string;
  storyboard: Record<string, unknown>[];
  prompt: string;
  metadata_json: Record<string, unknown>;
}

export interface ModelOption {
  id: string;
  label: string;
  provider: string;
  is_default: boolean;
}

export interface ModelOptions {
  text: ModelOption[];
  image: ModelOption[];
  defaults: Record<string, string | null>;
}

export interface ReviewTask extends Timestamped {
  campaign_id: string | null;
  entity_type: string;
  entity_id: string;
  reviewer_id: string | null;
  status: string;
  decision: string | null;
  feedback: string | null;
  metadata_json: Record<string, unknown>;
}
