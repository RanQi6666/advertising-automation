const REQUIRED_SECTION_KEYS = ["campaign", "adset", "creative", "insight"] as const;

export type ManualAdPerformancePayload = Record<string, unknown>;

export function parseManualAdPerformanceJson(input: string): ManualAdPerformancePayload {
  let parsed: unknown;
  try {
    parsed = JSON.parse(input);
  } catch {
    throw new Error("JSON 格式不正确，请检查逗号、引号和括号。");
  }

  if (!isPlainObject(parsed)) {
    throw new Error("投放分析数据必须是一个 JSON 对象。");
  }

  const hasRequiredSection = REQUIRED_SECTION_KEYS.some((key) => key in parsed);
  if (!hasRequiredSection) {
    throw new Error("JSON 至少包含 campaign、adset、creative 或 insight 中的一个字段。");
  }

  return typeof parsed.source_type === "string" && parsed.source_type.trim()
    ? parsed
    : { ...parsed, source_type: "manual" };
}

export function formatManualAdPerformanceJson(payload: ManualAdPerformancePayload): string {
  return JSON.stringify(payload, null, 2);
}

function isPlainObject(value: unknown): value is ManualAdPerformancePayload {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
