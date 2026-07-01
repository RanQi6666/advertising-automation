export type GenerationAttemptStatus =
  | "running"
  | "succeeded"
  | "partial_succeeded"
  | "failed";

export type GenerationAttempt = {
  id: string;
  business_type: string;
  business_id: string;
  job_id: string | null;
  campaign_id: string | null;
  stage: string;
  status: GenerationAttemptStatus;
  total_count: number;
  success_count: number;
  failed_count: number;
  provider: string | null;
  model: string | null;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  metadata: Record<string, unknown>;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  created_at: string;
  updated_at: string;
};

export function generationAttemptIsFinal(attempt: GenerationAttempt): boolean {
  return ["succeeded", "partial_succeeded", "failed"].includes(attempt.status);
}

export function generationAttemptIsRecoverable(attempt: GenerationAttempt): boolean {
  return attempt.status === "succeeded" || attempt.status === "partial_succeeded";
}

export function generationAttemptSummary(
  attempt: GenerationAttempt,
  label: string,
): string {
  const total = attempt.total_count || attempt.success_count + attempt.failed_count;
  if (attempt.status === "succeeded") {
    return `后台已生成 ${attempt.success_count}/${total} 个${label}。`;
  }
  if (attempt.status === "partial_succeeded") {
    return `后台已生成 ${attempt.success_count}/${total} 个${label}，未完成部分可重试。`;
  }
  if (attempt.status === "failed" && attempt.error_message) {
    return `${label}生成失败：${attempt.error_message}`;
  }
  return `${label}仍在后台处理中，请稍后刷新查看。`;
}

export function generationAttemptLastSuccessEvent<T extends object>(
  attempt: GenerationAttempt,
): T | null {
  const value = attempt.metadata?.last_success_event;
  return isRecord(value) ? (value as T) : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
