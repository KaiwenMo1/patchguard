import type {
  AnalysisRecord,
  AnalysisSubmitted,
  AnalyzePRRequest,
  RiskReport,
} from "./types";

const API_BASE_URL = (
  import.meta.env.VITE_PATCHGUARD_API_URL ?? "http://127.0.0.1:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface SubmitAnalysisOptions {
  skipLlm?: boolean;
  skipDocker?: boolean;
}

export async function submitAnalysis(
  prUrl: string,
  options: SubmitAnalysisOptions = {},
): Promise<AnalysisSubmitted> {
  return requestJson<AnalysisSubmitted>("/api/analyze-pr", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pr_url: prUrl,
      cleanup_workspace: false,
      skip_llm: options.skipLlm ?? true,
      skip_docker: options.skipDocker ?? false,
    } satisfies AnalyzePRRequest),
  });
}

export async function getAnalysis(analysisId: string): Promise<AnalysisRecord> {
  return requestJson<AnalysisRecord>(`/api/analysis/${analysisId}`);
}

export async function getReport(analysisId: string): Promise<RiskReport> {
  return requestJson<RiskReport>(`/api/report/${analysisId}`);
}

async function requestJson<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, options);
  if (!response.ok) {
    throw new ApiError(await errorMessage(response), response.status);
  }
  return response.json() as Promise<T>;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `Request failed with status ${response.status}`;
  } catch {
    return `Request failed with status ${response.status}`;
  }
}
