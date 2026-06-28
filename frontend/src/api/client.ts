import type {
  AnalysisRecord,
  AnalysisSubmitted,
  AppInstallationListResponse,
  AppJobDetail,
  AppRepositoryJobsResponse,
  AppRepositoryListResponse,
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
  compareBase?: boolean;
  useMemory?: boolean;
  memoryDbPath?: string;
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
      compare_base: options.compareBase ?? false,
      use_memory: options.useMemory ?? false,
      memory_db_path: options.memoryDbPath ?? null,
    } satisfies AnalyzePRRequest),
  });
}

export async function getAnalysis(analysisId: string): Promise<AnalysisRecord> {
  return requestJson<AnalysisRecord>(`/api/analysis/${analysisId}`);
}

export async function getReport(analysisId: string): Promise<RiskReport> {
  return requestJson<RiskReport>(`/api/report/${analysisId}`);
}

export async function getAppInstallations(): Promise<AppInstallationListResponse> {
  return requestJson<AppInstallationListResponse>("/api/app/installations");
}

export async function getAppRepositories(): Promise<AppRepositoryListResponse> {
  return requestJson<AppRepositoryListResponse>("/api/app/repositories");
}

export async function getAppRepositoryJobs(
  owner: string,
  repo: string,
): Promise<AppRepositoryJobsResponse> {
  return requestJson<AppRepositoryJobsResponse>(
    `/api/app/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/jobs`,
  );
}

export async function getAppJob(jobId: number): Promise<AppJobDetail> {
  return requestJson<AppJobDetail>(`/api/app/jobs/${jobId}`);
}

export async function getAppJobReport(jobId: number): Promise<RiskReport> {
  return requestJson<RiskReport>(`/api/app/jobs/${jobId}/report`);
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
