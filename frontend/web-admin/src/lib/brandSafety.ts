export type BrandSafetyStatus = "passed" | "blocked" | string;

export interface BrandSafetyFinding {
  category: string;
  severity: string;
  field_path: string;
  matched_text: string;
  suggestion: string;
}

export interface BrandSafetyReport {
  status: BrandSafetyStatus;
  highest_severity: string | null;
  findings: BrandSafetyFinding[];
}

export function brandSafetyReportFromPayload(payload: unknown): BrandSafetyReport | null {
  if (!isRecord(payload)) return null;
  const review = payload.review;
  if (!isRecord(review)) return null;
  const report = review.brand_safety;
  if (!isRecord(report)) return null;

  return {
    status: readText(report.status) || "unknown",
    highest_severity: readText(report.highest_severity) || null,
    findings: Array.isArray(report.findings)
      ? report.findings.filter(isRecord).map((finding) => ({
          category: readText(finding.category) || "unknown",
          severity: readText(finding.severity) || "unknown",
          field_path: readText(finding.field_path) || "$",
          matched_text: readText(finding.matched_text),
          suggestion: readText(finding.suggestion),
        }))
      : [],
  };
}

export function brandSafetyBlocksReturn(report: BrandSafetyReport | null): boolean {
  if (!report) return false;
  return (
    report.status === "blocked" ||
    report.highest_severity === "high" ||
    report.findings.some((finding) => finding.severity === "high")
  );
}

export function brandSafetyAllowsReturn(report: BrandSafetyReport | null): boolean {
  return !brandSafetyBlocksReturn(report);
}

export function brandSafetySummaryLabel(report: BrandSafetyReport | null): string {
  if (!report) return "待检查";
  if (brandSafetyBlocksReturn(report)) return "品牌安全未通过";
  return "品牌安全通过";
}

function readText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
