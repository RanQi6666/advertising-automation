export type GenerationTaskStatus = "queued" | "running" | "succeeded" | "failed";

export type GenerationTask = {
  id: string;
  queue_name: string;
  task_type: string;
  business_type: string;
  business_id: string;
  campaign_id: string | null;
  status: GenerationTaskStatus;
  priority: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  attempt_count: number;
  max_attempts: number;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export function generationTaskIsFinal(task: GenerationTask): boolean {
  return task.status === "succeeded" || task.status === "failed";
}

export function generationTaskIsSuccessful(task: GenerationTask): boolean {
  return task.status === "succeeded";
}

export function generationTaskSummary(task: GenerationTask, label: string): string {
  if (task.status === "queued") return `${label}已进入文本队列，等待处理。`;
  if (task.status === "running") return `${label}正在生成中，请稍候。`;
  if (task.status === "succeeded") return `${label}已生成。`;
  if (task.error_message) return `${label}生成失败：${task.error_message}`;
  return `${label}生成失败，请重试。`;
}
