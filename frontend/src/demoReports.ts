import type { AnalysisRecord, RiskReport } from "./api/types";

export const STATIC_DEMO_MODE = import.meta.env.VITE_PATCHGUARD_STATIC_DEMO === "true";

export interface DemoReportOption {
  id: string;
  label: string;
  description: string;
  path: string;
}

export const DEMO_REPORTS: DemoReportOption[] = [
  {
    id: "demo-security-bug",
    label: "Security bug",
    description: "Unsafe eval introduced; Bandit evidence raises merge risk.",
    path: `${import.meta.env.BASE_URL}sample_reports/demo_security_bug.json`,
  },
  {
    id: "demo-parser-bug",
    label: "Parser regression",
    description: "A parser edge case changes without a matching regression test.",
    path: `${import.meta.env.BASE_URL}sample_reports/demo_parser_bug.json`,
  },
  {
    id: "demo-no-tests-changed",
    label: "No tests changed",
    description: "Source behavior changes in a package with no test suite.",
    path: `${import.meta.env.BASE_URL}sample_reports/demo_no_tests_changed.json`,
  },
];

export async function loadDemoReport(demoId: string): Promise<RiskReport> {
  const demo = DEMO_REPORTS.find((item) => item.id === demoId) ?? DEMO_REPORTS[0];
  const response = await fetch(demo.path);
  if (!response.ok) {
    throw new Error(`Could not load static sample report: ${demo.path}`);
  }
  return response.json() as Promise<RiskReport>;
}

export function analysisRecordForDemo(demo: DemoReportOption, report: RiskReport): AnalysisRecord {
  const now = new Date().toISOString();
  return {
    analysis_id: demo.id,
    pr_url: report.pr.url,
    status: report.status === "complete" ? "completed" : report.status,
    created_at: report.generated_at ?? now,
    updated_at: now,
    report_path: demo.path,
    error: report.errors.length > 0 ? report.errors.join("; ") : null,
  };
}
