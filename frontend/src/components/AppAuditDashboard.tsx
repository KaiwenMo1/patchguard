import { ArrowUpRight, Github, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

import {
  getAppInstallations,
  getAppRepositories,
  getAppRepositoryJobs,
} from "../api/client";
import type {
  AnalysisRecord,
  AnalysisStatus,
  AppJobDetail,
  GitHubAppAnalysisJob,
  GitHubAppInstallation,
  GitHubAppJobStatus,
  GitHubAppRepository,
  PolicyGateDecision,
  RiskLevel,
  RiskReport,
} from "../api/types";

interface AppAuditDashboardProps {
  onOpenReport: (detail: AppJobDetail) => Promise<void>;
}

export function AppAuditDashboard({ onOpenReport }: AppAuditDashboardProps) {
  const [installations, setInstallations] = useState<GitHubAppInstallation[]>([]);
  const [repositories, setRepositories] = useState<GitHubAppRepository[]>([]);
  const [selectedRepoFullName, setSelectedRepoFullName] = useState<string | null>(null);
  const [jobs, setJobs] = useState<AppJobDetail[]>([]);
  const [repoFilter, setRepoFilter] = useState("");
  const [isLoadingOverview, setIsLoadingOverview] = useState(true);
  const [isLoadingJobs, setIsLoadingJobs] = useState(false);
  const [openingJobId, setOpeningJobId] = useState<number | null>(null);
  const [appError, setAppError] = useState<string | null>(null);

  const selectedRepo =
    repositories.find((repository) => repository.full_name === selectedRepoFullName) ?? null;
  const filteredRepositories = repositories.filter((repository) =>
    repository.full_name.toLowerCase().includes(repoFilter.trim().toLowerCase()),
  );

  const loadOverview = async () => {
    setIsLoadingOverview(true);
    setAppError(null);
    try {
      const [installationPayload, repositoryPayload] = await Promise.all([
        getAppInstallations(),
        getAppRepositories(),
      ]);
      setInstallations(installationPayload.installations);
      setRepositories(repositoryPayload.repositories);
      setSelectedRepoFullName((current) => {
        if (current && repositoryPayload.repositories.some((repo) => repo.full_name === current)) {
          return current;
        }
        return repositoryPayload.repositories[0]?.full_name ?? null;
      });
    } catch (caught) {
      setAppError(errorText(caught));
    } finally {
      setIsLoadingOverview(false);
    }
  };

  useEffect(() => {
    void loadOverview();
  }, []);

  useEffect(() => {
    if (!selectedRepoFullName) {
      setJobs([]);
      return;
    }

    let cancelled = false;
    const loadJobs = async () => {
      setIsLoadingJobs(true);
      setAppError(null);
      try {
        const { owner, repo } = splitRepositoryFullName(selectedRepoFullName);
        const payload = await getAppRepositoryJobs(owner, repo);
        if (!cancelled) {
          setJobs(payload.jobs);
        }
      } catch (caught) {
        if (!cancelled) {
          setJobs([]);
          setAppError(errorText(caught));
        }
      } finally {
        if (!cancelled) {
          setIsLoadingJobs(false);
        }
      }
    };

    void loadJobs();
    return () => {
      cancelled = true;
    };
  }, [selectedRepoFullName]);

  const openReport = async (detail: AppJobDetail) => {
    if (detail.job.id == null) {
      setAppError("This job cannot be opened because it is missing an id.");
      return;
    }
    setOpeningJobId(detail.job.id);
    setAppError(null);
    try {
      await onOpenReport(detail);
    } catch (caught) {
      setAppError(errorText(caught));
    } finally {
      setOpeningJobId(null);
    }
  };

  return (
    <section className="app-dashboard space-y-5">
      <div className="app-dashboard-hero panel overflow-hidden">
        <div className="border-b border-[#252b28] px-6 py-6 sm:px-8">
          <div className="flex flex-wrap items-start justify-between gap-5">
            <div>
              <p className="section-title">GitHub App mode</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-[-0.03em] text-[#f3f5f4] sm:text-4xl">
                Repository audit history
              </h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-[#9ca6a0]">
                Inspect installed repositories, queued PR analyses, stored reports, and risk
                movement across recent jobs.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadOverview()}
              className="inline-flex min-h-10 items-center justify-center rounded-full border border-[#303632] px-4 text-sm font-semibold text-[#f3f5f4] transition hover:border-[#f3f5f4] hover:bg-[#f3f5f4] hover:text-[#080a09]"
            >
              Refresh
            </button>
          </div>
        </div>
        <div className="grid gap-0 md:grid-cols-3">
          <AppMetric
            label="Installations"
            value={String(installations.length)}
            detail={`${installations.filter((item) => item.active).length} active`}
          />
          <AppMetric
            label="Repositories"
            value={String(repositories.length)}
            detail={`${repositories.filter((item) => item.active && item.selected).length} monitored`}
          />
          <AppMetric
            label="Visible jobs"
            value={String(jobs.length)}
            detail={selectedRepo ? selectedRepo.full_name : "Select a repository"}
          />
        </div>
      </div>

      {appError ? (
        <DashboardNotice title="GitHub App dashboard failed">{appError}</DashboardNotice>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[360px_minmax(0,1fr)]">
        <RepositoryListPanel
          repositories={filteredRepositories}
          allRepositoriesCount={repositories.length}
          selectedRepoFullName={selectedRepoFullName}
          repoFilter={repoFilter}
          isLoading={isLoadingOverview}
          onFilterChange={setRepoFilter}
          onSelect={setSelectedRepoFullName}
        />
        <RepoDetailPanel
          repository={selectedRepo}
          jobs={jobs}
          isLoadingJobs={isLoadingJobs}
          openingJobId={openingJobId}
          onOpenReport={openReport}
        />
      </div>
    </section>
  );
}

export function analysisRecordForAppJob(
  job: GitHubAppAnalysisJob,
  report: RiskReport,
): AnalysisRecord {
  const now = new Date().toISOString();
  return {
    analysis_id: `app-job-${job.id ?? "unknown"}`,
    pr_url: job.pr_url ?? report.pr.url,
    status: analysisStatusFromAppJob(job),
    created_at: job.created_at ?? report.generated_at ?? now,
    updated_at: job.updated_at ?? now,
    report_path: job.report_path ?? report.report_path ?? null,
    error: job.error ?? (report.errors.length > 0 ? report.errors.join("; ") : null),
  };
}

function AppMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="border-t border-[#252b28] px-6 py-5 md:border-l md:border-t-0 md:first:border-l-0">
      <p className="metric-label">{label}</p>
      <p className="mt-2 text-3xl font-semibold text-[#f3f5f4]">{value}</p>
      <p className="mt-1 truncate text-sm text-[#9ca6a0]">{detail}</p>
    </div>
  );
}

function RepositoryListPanel({
  repositories,
  allRepositoriesCount,
  selectedRepoFullName,
  repoFilter,
  isLoading,
  onFilterChange,
  onSelect,
}: {
  repositories: GitHubAppRepository[];
  allRepositoriesCount: number;
  selectedRepoFullName: string | null;
  repoFilter: string;
  isLoading: boolean;
  onFilterChange: (value: string) => void;
  onSelect: (value: string) => void;
}) {
  return (
    <aside className="panel overflow-hidden xl:sticky xl:top-6 xl:self-start">
      <div className="border-b border-[#252b28] px-5 py-5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="section-title">Repositories</p>
            <p className="mt-1 text-sm text-[#9ca6a0]">
              {allRepositoriesCount} {allRepositoriesCount === 1 ? "repository" : "repositories"}{" "}
              stored
            </p>
          </div>
          <Github className="h-5 w-5 text-[#737d77]" aria-hidden="true" />
        </div>
        <label htmlFor="repo-filter" className="mt-4 block text-sm font-medium text-[#f3f5f4]">
          Filter repositories
        </label>
        <input
          id="repo-filter"
          value={repoFilter}
          onChange={(event) => onFilterChange(event.target.value)}
          placeholder="owner/repo"
          className="control-input mt-2 min-h-10 w-full rounded-md border border-[#252b28] bg-[#111411] px-3 text-sm outline-none"
        />
      </div>

      {isLoading ? (
        <div className="space-y-3 p-5">
          {[0, 1, 2].map((item) => (
            <div
              key={item}
              className="h-20 animate-pulse rounded-md border border-[#252b28] bg-[#0b0d0c]"
            />
          ))}
        </div>
      ) : repositories.length === 0 ? (
        <div className="p-5">
          <DashboardEmptyState>
            No repositories match this view. Install the GitHub App on a repository or clear the
            filter.
          </DashboardEmptyState>
        </div>
      ) : (
        <div className="max-h-[680px] overflow-y-auto p-3">
          {repositories.map((repository) => {
            const active = repository.full_name === selectedRepoFullName;
            return (
              <button
                key={repository.github_repo_id}
                type="button"
                onClick={() => onSelect(repository.full_name)}
                className={[
                  "mb-2 w-full rounded-md border p-4 text-left transition",
                  active
                    ? "border-[#74c69a] bg-[#132019]"
                    : "border-[#252b28] bg-[#0b0d0c] hover:border-[#657069]",
                ].join(" ")}
              >
                <span className="block break-words font-mono text-sm font-semibold text-[#f3f5f4]">
                  {repository.full_name}
                </span>
                <span className="mt-3 flex flex-wrap items-center gap-2">
                  <RepositoryStateBadge repository={repository} />
                  <span className="rounded-full bg-[#151a17] px-2.5 py-1 text-xs font-semibold text-[#9ca6a0]">
                    {repository.private ? "Private" : "Public"}
                  </span>
                  <span className="rounded-full bg-[#151a17] px-2.5 py-1 text-xs font-semibold text-[#9ca6a0]">
                    {repository.default_branch}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </aside>
  );
}

function RepoDetailPanel({
  repository,
  jobs,
  isLoadingJobs,
  openingJobId,
  onOpenReport,
}: {
  repository: GitHubAppRepository | null;
  jobs: AppJobDetail[];
  isLoadingJobs: boolean;
  openingJobId: number | null;
  onOpenReport: (detail: AppJobDetail) => Promise<void>;
}) {
  if (!repository) {
    return (
      <article className="panel min-h-[420px] p-6">
        <p className="section-title">Repository detail</p>
        <DashboardEmptyState>
          No repository is selected yet. Once the GitHub App receives installation events, monitored
          repositories will appear here.
        </DashboardEmptyState>
      </article>
    );
  }

  return (
    <article className="panel overflow-hidden">
      <div className="border-b border-[#252b28] px-6 py-6">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <p className="section-title">Repository detail</p>
            <h2 className="mt-2 break-words font-mono text-2xl font-semibold text-[#f3f5f4]">
              {repository.full_name}
            </h2>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <RepositoryStateBadge repository={repository} />
              <span className="rounded-full bg-[#0b0d0c] px-2.5 py-1 text-xs font-semibold text-[#9ca6a0]">
                default: {repository.default_branch}
              </span>
              <span className="rounded-full bg-[#0b0d0c] px-2.5 py-1 text-xs font-semibold text-[#9ca6a0]">
                installation {repository.installation_id}
              </span>
            </div>
          </div>
          <a
            href={`https://github.com/${repository.full_name}`}
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-full border border-[#303632] px-4 text-sm font-semibold text-[#f3f5f4] transition hover:border-[#f3f5f4] hover:bg-[#f3f5f4] hover:text-[#080a09]"
          >
            Open on GitHub
            <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
        </div>
      </div>

      <RiskTrendSummary jobs={jobs} />
      <RecentJobsTable
        jobs={jobs}
        isLoading={isLoadingJobs}
        openingJobId={openingJobId}
        onOpenReport={onOpenReport}
      />
    </article>
  );
}

function RiskTrendSummary({ jobs }: { jobs: AppJobDetail[] }) {
  const reports = jobs.filter(
    (
      detail,
    ): detail is AppJobDetail & {
      report_summary: NonNullable<AppJobDetail["report_summary"]>;
    } => Boolean(detail.report_summary),
  );

  if (reports.length === 0) {
    return (
      <div className="border-b border-[#252b28] px-6 py-6">
        <p className="section-title">Risk trend</p>
        <DashboardEmptyState>
          No completed reports exist for this repository yet. The trend will appear after PatchGuard
          processes queued jobs.
        </DashboardEmptyState>
      </div>
    );
  }

  const latest = reports[0].report_summary;
  const average = Math.round(
    reports.reduce((total, detail) => total + detail.report_summary.risk_score, 0) / reports.length,
  );
  const highRiskCount = reports.filter((detail) =>
    ["high", "critical"].includes(detail.report_summary.risk_level),
  ).length;
  const blockedCount = reports.filter(
    (detail) => detail.report_summary.policy_decision === "block",
  ).length;
  const trend = [...reports].reverse().slice(-12);

  return (
    <div className="border-b border-[#252b28] px-6 py-6">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div>
          <p className="section-title">Risk trend</p>
          <p className="mt-1 text-sm text-[#9ca6a0]">
            Based on {reports.length} stored report{reports.length === 1 ? "" : "s"}
          </p>
        </div>
        <span
          className={`rounded-full px-3 py-1 text-xs font-semibold ${riskTone(latest.risk_level).badge}`}
        >
          latest {latest.risk_score}/100
        </span>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <TrendMetric
          label="Latest score"
          value={`${latest.risk_score}/100`}
          detail={latest.risk_level}
        />
        <TrendMetric label="Average score" value={`${average}/100`} detail="stored reports" />
        <TrendMetric label="High risk jobs" value={String(highRiskCount)} detail="high or critical" />
        <TrendMetric label="Policy blocks" value={String(blockedCount)} detail="block decisions" />
      </div>

      <div className="mt-6 flex h-28 items-end gap-2 rounded-md border border-[#252b28] bg-[#0b0d0c] px-4 py-3">
        {trend.map((detail) => {
          const summary = detail.report_summary;
          return (
            <div
              key={summary.id ?? `${summary.job_id}-${summary.risk_score}`}
              className="flex flex-1 flex-col items-center justify-end gap-2"
              title={`Job ${summary.job_id}: ${summary.risk_score}/100 ${summary.risk_level}`}
            >
              <div
                className={`w-full rounded-t-sm ${riskTone(summary.risk_level).bar}`}
                style={{ height: `${Math.max(10, Math.min(100, summary.risk_score))}%` }}
              />
              <span className="font-mono text-[10px] text-[#737d77]">#{summary.job_id}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TrendMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-md border border-[#252b28] bg-[#0b0d0c] p-4">
      <p className="metric-label">{label}</p>
      <p className="mt-2 text-2xl font-semibold text-[#f3f5f4]">{value}</p>
      <p className="mt-1 text-xs text-[#9ca6a0]">{detail}</p>
    </div>
  );
}

function RecentJobsTable({
  jobs,
  isLoading,
  openingJobId,
  onOpenReport,
}: {
  jobs: AppJobDetail[];
  isLoading: boolean;
  openingJobId: number | null;
  onOpenReport: (detail: AppJobDetail) => Promise<void>;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#252b28] px-6 py-5">
        <div>
          <p className="section-title">Recent jobs</p>
          <p className="mt-1 text-sm text-[#9ca6a0]">
            {jobs.length} job{jobs.length === 1 ? "" : "s"} recorded for this repository
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3 px-6 py-6">
          {[0, 1, 2].map((item) => (
            <div
              key={item}
              className="h-16 animate-pulse rounded-md border border-[#252b28] bg-[#0b0d0c]"
            />
          ))}
        </div>
      ) : jobs.length === 0 ? (
        <div className="px-6 py-8">
          <DashboardEmptyState>
            No jobs have been created for this repository yet. Open, reopen, or synchronize a pull
            request after installing the app.
          </DashboardEmptyState>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-[#252b28] text-sm">
            <thead className="bg-[#0b0d0c]">
              <tr className="text-left text-xs font-semibold uppercase text-[#9ca6a0]">
                <th className="px-6 py-3">Pull request</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Risk</th>
                <th className="px-4 py-3">Policy</th>
                <th className="px-4 py-3">Head</th>
                <th className="px-4 py-3">Updated</th>
                <th className="px-4 py-3">Links</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#202522] bg-[#111411]">
              {jobs.map((detail) => {
                const job = detail.job;
                const summary = detail.report_summary;
                const canOpenReport = job.id != null && Boolean(summary || job.report_path);
                const opening = openingJobId === job.id;
                return (
                  <tr key={job.id ?? `${job.repository_full_name}-${job.pr_number}-${job.head_sha}`}>
                    <td className="max-w-[280px] px-6 py-4">
                      <div className="flex flex-col gap-1">
                        {job.pr_url ? (
                          <a
                            href={job.pr_url}
                            className="font-semibold text-[#74c69a] hover:text-[#91d7b0]"
                          >
                            PR #{job.pr_number ?? "unknown"}
                          </a>
                        ) : (
                          <span className="font-semibold text-[#f3f5f4]">
                            PR #{job.pr_number ?? "unknown"}
                          </span>
                        )}
                        <span className="text-xs text-[#9ca6a0]">{job.event_type}</span>
                      </div>
                    </td>
                    <td className="px-4 py-4">
                      <JobStatusBadge status={job.status} />
                    </td>
                    <td className="px-4 py-4">
                      {summary ? (
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${riskTone(summary.risk_level).badge}`}
                        >
                          {summary.risk_score}/100 {summary.risk_level}
                        </span>
                      ) : (
                        <span className="text-xs text-[#737d77]">No report</span>
                      )}
                    </td>
                    <td className="px-4 py-4">
                      {summary ? (
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${policyTone(summary.policy_decision).badge}`}
                        >
                          {summary.policy_decision}
                        </span>
                      ) : (
                        <span className="text-xs text-[#737d77]">Pending</span>
                      )}
                    </td>
                    <td className="px-4 py-4 font-mono text-xs text-[#f3f5f4]">
                      {shortSha(job.head_sha)}
                    </td>
                    <td className="px-4 py-4 text-[#9ca6a0]">
                      {job.updated_at ? formatDate(job.updated_at) : "Unknown"}
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          disabled={!canOpenReport || opening}
                          onClick={() => void onOpenReport(detail)}
                          className="inline-flex min-h-8 items-center justify-center gap-1.5 rounded-full border border-[#303632] px-3 text-xs font-semibold text-[#f3f5f4] transition hover:border-[#74c69a] disabled:cursor-not-allowed disabled:text-[#737d77]"
                        >
                          {opening ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                          ) : null}
                          Open report
                        </button>
                        {job.check_run_url ? (
                          <a
                            href={job.check_run_url}
                            className="inline-flex min-h-8 items-center justify-center rounded-full border border-[#303632] px-3 text-xs font-semibold text-[#9ca6a0] transition hover:border-[#f3f5f4] hover:text-[#f3f5f4]"
                          >
                            Check
                          </a>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function DashboardNotice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-6 rounded-md border border-[#ff8182] bg-[#ffebe9] p-4 text-[#82071e]">
      <p className="font-semibold">{title}</p>
      <p className="mt-1 text-sm leading-6">{children}</p>
    </div>
  );
}

function DashboardEmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-5 rounded-md border border-dashed border-[#252b28] bg-[#0b0d0c] p-5 text-sm text-[#9ca6a0]">
      {children}
    </div>
  );
}

function RepositoryStateBadge({ repository }: { repository: GitHubAppRepository }) {
  const label = repository.active && repository.selected ? "Monitored" : "Inactive";
  const style =
    repository.active && repository.selected
      ? "bg-[#dafbe1] text-[#116329]"
      : "bg-[#0b0d0c] text-[#9ca6a0]";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${style}`}>{label}</span>;
}

function JobStatusBadge({ status }: { status: GitHubAppJobStatus }) {
  const styles = {
    queued: "bg-[#0b0d0c] text-[#9ca6a0]",
    running: "bg-[#ddf4ff] text-[#0969da]",
    completed: "bg-[#dafbe1] text-[#116329]",
    failed: "bg-[#ffebe9] text-[#cf222e]",
    partial: "bg-[#fff8c5] text-[#9a6700]",
  } satisfies Record<GitHubAppJobStatus, string>;
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${styles[status]}`}>{status}</span>;
}

function riskTone(level: RiskLevel) {
  const tones = {
    low: {
      badge: "bg-[#dafbe1] text-[#116329]",
      bar: "bg-[#1a7f37]",
    },
    medium: {
      badge: "bg-[#fff8c5] text-[#9a6700]",
      bar: "bg-[#bf8700]",
    },
    high: {
      badge: "bg-[#fff1e5] text-[#bc4c00]",
      bar: "bg-[#bc4c00]",
    },
    critical: {
      badge: "bg-[#ffebe9] text-[#cf222e]",
      bar: "bg-[#cf222e]",
    },
  } satisfies Record<RiskLevel, { badge: string; bar: string }>;
  return tones[level];
}

function policyTone(decision: PolicyGateDecision) {
  const tones = {
    pass: {
      badge: "bg-[#dafbe1] text-[#116329]",
    },
    warn: {
      badge: "bg-[#fff8c5] text-[#9a6700]",
    },
    block: {
      badge: "bg-[#ffebe9] text-[#cf222e]",
    },
  } satisfies Record<PolicyGateDecision, { badge: string }>;
  return tones[decision];
}

function splitRepositoryFullName(fullName: string): { owner: string; repo: string } {
  const [owner, repo] = fullName.split("/", 2);
  return {
    owner: owner || "unknown",
    repo: repo || "unknown",
  };
}

function shortSha(value?: string | null): string {
  if (!value) {
    return "unknown";
  }
  return value.slice(0, 7);
}

function analysisStatusFromAppJob(job: GitHubAppAnalysisJob): AnalysisStatus {
  if (job.status === "completed") {
    return "completed";
  }
  if (job.status === "partial" || job.status === "failed") {
    return job.status;
  }
  return "pending";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorText(caught: unknown): string {
  if (caught instanceof Error) {
    return caught.message;
  }
  return "Something went wrong while contacting the PatchGuard API.";
}
