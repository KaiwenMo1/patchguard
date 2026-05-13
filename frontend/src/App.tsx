import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  ClipboardList,
  Code2,
  FileCode2,
  GitPullRequest,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
  TestTube2,
  XCircle,
} from "lucide-react";
import { FormEvent, MutableRefObject, ReactNode, useEffect, useRef, useState } from "react";

import { ApiError, getAnalysis, getReport, submitAnalysis } from "./api/client";
import type {
  AnalysisRecord,
  AnalysisStatus,
  ChangedFile,
  GeneratedTest,
  RiskLevel,
  RiskReport,
  RunStatus,
  SecurityFinding,
  ToolRun,
} from "./api/types";

const TERMINAL_STATUSES: AnalysisStatus[] = ["completed", "failed", "partial"];

const STATUS_STEPS: Array<{ status: AnalysisStatus; label: string }> = [
  { status: "pending", label: "Queued" },
  { status: "fetching_pr", label: "Fetching PR" },
  { status: "cloning", label: "Cloning" },
  { status: "analyzing_diff", label: "Analyzing diff" },
  { status: "running_existing_tests", label: "Existing tests" },
  { status: "scanning_security", label: "Security scans" },
  { status: "generating_tests", label: "Generating tests" },
  { status: "running_generated_tests", label: "Generated tests" },
  { status: "completed", label: "Completed" },
];

export default function App() {
  const [prUrl, setPrUrl] = useState("");
  const [analysisId, setAnalysisId] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisRecord | null>(null);
  const [report, setReport] = useState<RiskReport | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [skipLlm, setSkipLlm] = useState(true);
  const [skipDocker, setSkipDocker] = useState(false);
  const reportRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!analysisId) {
      return;
    }

    let cancelled = false;
    let intervalId: number | undefined;

    const poll = async (): Promise<boolean> => {
      try {
        const nextAnalysis = await getAnalysis(analysisId);
        if (cancelled) {
          return true;
        }
        setAnalysis(nextAnalysis);
        if (shouldFetchReport(nextAnalysis)) {
          try {
            const nextReport = await getReport(analysisId);
            if (!cancelled) {
              setReport(nextReport);
              setError(null);
            }
            return true;
          } catch (caught) {
            if (!cancelled && TERMINAL_STATUSES.includes(nextAnalysis.status)) {
              setError(`Analysis finished, but the report is still loading: ${errorText(caught)}`);
            }
            return false;
          }
        }
        return TERMINAL_STATUSES.includes(nextAnalysis.status);
      } catch (caught) {
        if (!cancelled) {
          setError(errorText(caught));
        }
        return false;
      }
    };

    const tick = async () => {
      const done = await poll();
      if (done && intervalId !== undefined) {
        window.clearInterval(intervalId);
      }
    };

    void tick();
    intervalId = window.setInterval(tick, 1800);
    return () => {
      cancelled = true;
      if (intervalId !== undefined) {
        window.clearInterval(intervalId);
      }
    };
  }, [analysisId]);

  useEffect(() => {
    if (report) {
      reportRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [report]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedUrl = prUrl.trim();
    if (!trimmedUrl) {
      setError("Enter a public GitHub pull request URL.");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    setReport(null);
    setAnalysis(null);

    try {
      const submitted = await submitAnalysis(trimmedUrl, { skipLlm, skipDocker });
      setAnalysisId(submitted.analysis_id);
      setAnalysis({
        analysis_id: submitted.analysis_id,
        pr_url: trimmedUrl,
        status: submitted.status,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setIsSubmitting(false);
    }
  };

  const failedWithoutReport = analysis?.status === "failed" && !report;

  return (
    <div className="min-h-screen bg-[linear-gradient(180deg,#f7f9fc_0%,#eef3f8_100%)]">
      <header className="border-b border-slate-200 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-600 text-white">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-base font-semibold text-slate-950">PatchGuard</p>
              <p className="text-sm text-slate-500">CI for AI-generated code</p>
            </div>
          </div>
          <a
            href="https://github.com"
            className="hidden text-sm font-medium text-slate-500 hover:text-slate-900 sm:inline"
          >
            Public GitHub PRs
          </a>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <section className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
          <div className="panel overflow-hidden">
            <div className="border-b border-slate-200 bg-slate-950 px-6 py-5 text-white">
              <div className="flex items-center gap-2 text-sm font-medium text-emerald-300">
                <GitPullRequest className="h-4 w-4" aria-hidden="true" />
                Evidence-backed PR verification
              </div>
              <h1 className="mt-4 text-3xl font-semibold text-white sm:text-4xl">PatchGuard</h1>
              <p className="mt-2 max-w-2xl text-base leading-7 text-slate-300">
                CI for AI-generated code. Submit a public Python PR and get changed-file evidence,
                Docker test results, static scans, generated-test evidence, and a deterministic
                merge-risk recommendation.
              </p>
            </div>

            <form className="space-y-4 p-6" onSubmit={onSubmit}>
              <label htmlFor="pr-url" className="block text-sm font-medium text-slate-700">
                GitHub pull request URL
              </label>
              <div className="flex flex-col gap-3 sm:flex-row">
                <input
                  id="pr-url"
                  value={prUrl}
                  onChange={(event) => setPrUrl(event.target.value)}
                  placeholder="https://github.com/owner/repo/pull/123"
                  className="min-h-12 flex-1 rounded-lg border border-slate-300 bg-white px-4 text-sm text-slate-950 outline-none transition focus:border-emerald-500 focus:ring-4 focus:ring-emerald-100"
                />
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="inline-flex min-h-12 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-5 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-400"
                >
                  {isSubmitting ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Play className="h-4 w-4" aria-hidden="true" />
                  )}
                  Analyze
                </button>
              </div>
              <p className="text-sm text-slate-500">
                The dashboard calls your local FastAPI backend. OpenAI generation is off by default.
              </p>
              <div className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50 p-4 sm:grid-cols-2">
                <label className="flex items-center gap-3 text-sm font-medium text-slate-700">
                  <input
                    type="checkbox"
                    checked={skipLlm}
                    onChange={(event) => setSkipLlm(event.target.checked)}
                    className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                  />
                  Skip OpenAI tests
                </label>
                <label className="flex items-center gap-3 text-sm font-medium text-slate-700">
                  <input
                    type="checkbox"
                    checked={skipDocker}
                    onChange={(event) => setSkipDocker(event.target.checked)}
                    className="h-4 w-4 rounded border-slate-300 text-emerald-600 focus:ring-emerald-500"
                  />
                  Skip Docker
                </label>
              </div>
            </form>
          </div>

          <StatusPanel analysis={analysis} report={report} />
        </section>

        {error ? (
          <Notice tone="danger" title="Dashboard request failed">
            {error}
          </Notice>
        ) : null}

        {failedWithoutReport ? (
          <Notice tone="danger" title="Analysis failed">
            {analysis?.error ?? "The backend did not produce a report for this run."}
          </Notice>
        ) : null}

        {analysis && shouldFetchReport(analysis) && !report && !failedWithoutReport ? (
          <Notice tone="warning" title="Final report is loading">
            PatchGuard finished the analysis step and is loading the report JSON. This should only
            take a moment.
          </Notice>
        ) : null}

        {report ? <ReportView report={report} analysis={analysis} reportRef={reportRef} /> : null}
      </main>
    </div>
  );
}

function StatusPanel({ analysis, report }: { analysis: AnalysisRecord | null; report: RiskReport | null }) {
  const currentStatus = analysis?.status ?? "pending";
  const currentIndex = Math.max(
    0,
    STATUS_STEPS.findIndex((step) => step.status === currentStatus),
  );
  const isTerminal = TERMINAL_STATUSES.includes(currentStatus);
  const waitingForReport = analysis && shouldFetchReport(analysis) && !report;
  const title = report
    ? "Report ready"
    : waitingForReport
      ? "Loading report"
      : analysis
        ? formatStatus(currentStatus)
        : "Ready to analyze";

  return (
    <aside className="panel p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="section-title">Current step</p>
          <h2 className="mt-2 text-2xl font-semibold text-slate-950">{title}</h2>
        </div>
        {analysis && !isTerminal ? (
          <Loader2 className="mt-1 h-6 w-6 animate-spin text-emerald-600" aria-hidden="true" />
        ) : (
          <CircleDashed className="mt-1 h-6 w-6 text-slate-400" aria-hidden="true" />
        )}
      </div>

      <div className="mt-6 space-y-3">
        {STATUS_STEPS.slice(0, -1).map((step, index) => {
          const active = index === currentIndex && !isTerminal;
          const complete = report || index < currentIndex || currentStatus === "completed";
          return (
            <div key={step.status} className="flex items-center gap-3">
              <span
                className={[
                  "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                  active
                    ? "border-emerald-500 bg-emerald-50 text-emerald-700"
                    : complete
                      ? "border-emerald-500 bg-emerald-600 text-white"
                      : "border-slate-300 bg-white text-slate-400",
                ].join(" ")}
              >
                {complete ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> : index + 1}
              </span>
              <span className={active ? "font-medium text-slate-950" : "text-sm text-slate-600"}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      {analysis ? (
        <div className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
          <p className="font-medium text-slate-900">Analysis ID</p>
          <p className="mt-1 break-all font-mono text-xs">{analysis.analysis_id}</p>
          {report ? (
            <a className="mt-3 inline-block font-semibold text-emerald-700 hover:text-emerald-900" href="#patchguard-report">
              View report
            </a>
          ) : null}
          {waitingForReport ? (
            <p className="mt-3 text-xs font-medium text-amber-700">
              Analysis is done; loading the final report.
            </p>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}

function ReportView({
  report,
  analysis,
  reportRef,
}: {
  report: RiskReport;
  analysis: AnalysisRecord | null;
  reportRef: MutableRefObject<HTMLElement | null>;
}) {
  const generatedTests = report.generated_tests ?? [];
  const securityFindings = report.security_findings ?? [];
  const logRuns = collectLogRuns(report);

  return (
    <section id="patchguard-report" ref={reportRef} className="mt-8 scroll-mt-6 space-y-6">
      {report.status !== "complete" || report.errors.length > 0 ? (
        <Notice tone="warning" title="Partial evidence">
          {report.errors.length > 0
            ? report.errors.join(" ")
            : "PatchGuard produced a report, but one or more analysis steps did not complete. This is expected when Docker or OpenAI generation is skipped."}
        </Notice>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        <RiskCard report={report} />
        <PRMetadataCard report={report} analysis={analysis} />
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <RunCard
          title="Existing Tests"
          icon={<TestTube2 className="h-5 w-5" aria-hidden="true" />}
          run={report.existing_tests}
          emptyText="Existing tests did not run or were skipped."
        />
        <RunCard
          title="Generated Tests"
          icon={<Code2 className="h-5 w-5" aria-hidden="true" />}
          run={(report.generated_test_results ?? [])[0] ?? report.test_generation}
          emptyText="No generated tests were run."
          extra={`${generatedTests.length} generated test file${generatedTests.length === 1 ? "" : "s"}`}
        />
      </div>

      <ChangedFilesTable files={report.changed_files} />
      <RiskReasons reasons={report.risk_reasons} />
      <SecurityFindings findings={securityFindings} />

      <CollapsibleSection
        title="Generated Test Code"
        icon={<FileCode2 className="h-5 w-5" aria-hidden="true" />}
        count={generatedTests.length}
      >
        {generatedTests.length === 0 ? (
          <EmptyState>No generated test code is available for this report.</EmptyState>
        ) : (
          <div className="space-y-5">
            {generatedTests.map((test) => (
              <GeneratedTestBlock key={test.path} test={test} />
            ))}
          </div>
        )}
      </CollapsibleSection>

      <CollapsibleSection
        title="Raw Logs"
        icon={<ClipboardList className="h-5 w-5" aria-hidden="true" />}
        count={logRuns.length}
      >
        {logRuns.length === 0 ? (
          <EmptyState>No raw command logs were attached to this report.</EmptyState>
        ) : (
          <div className="space-y-5">
            {logRuns.map((run, index) => (
              <LogRunBlock key={`${run.kind}-${run.name}-${index}`} run={run} />
            ))}
          </div>
        )}
      </CollapsibleSection>
    </section>
  );
}

function RiskCard({ report }: { report: RiskReport }) {
  const tone = riskTone(report.risk_level);
  return (
    <article className="panel p-6">
      <div className="flex items-center justify-between">
        <p className="section-title">Merge risk</p>
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${tone.badge}`}>
          {report.risk_level}
        </span>
      </div>
      <div className="mt-5 flex items-end gap-3">
        <span className={`text-6xl font-semibold ${tone.text}`}>{report.risk_score}</span>
        <span className="pb-2 text-lg font-medium text-slate-500">/100</span>
      </div>
      <div className="mt-5 h-3 overflow-hidden rounded-full bg-slate-100">
        <div
          className={`h-full rounded-full ${tone.bar}`}
          style={{ width: `${Math.min(100, Math.max(0, report.risk_score))}%` }}
        />
      </div>
      <div className="mt-6 border-t border-slate-200 pt-5">
        <p className="metric-label">Recommendation</p>
        <p className="mt-2 text-base font-semibold leading-6 text-slate-950">
          {report.recommendation}
        </p>
        <p className="mt-2 text-sm text-slate-500">
          Decision: <span className="font-medium text-slate-700">{report.merge_decision}</span>
        </p>
      </div>
    </article>
  );
}

function PRMetadataCard({ report, analysis }: { report: RiskReport; analysis: AnalysisRecord | null }) {
  const pr = report.pr;
  const metadata = [
    ["Repository", `${pr.owner}/${pr.repo}`],
    ["PR", `#${pr.number}`],
    ["Author", pr.author ?? "Unknown"],
    ["State", pr.state ?? "Unknown"],
    ["Base", pr.base_ref ?? "Unknown"],
    ["Head", pr.head_ref ?? "Unknown"],
    ["Changed files", String(pr.changed_files_count ?? report.changed_files.length)],
    ["Lines", `+${pr.additions ?? 0} / -${pr.deletions ?? 0}`],
  ];

  return (
    <article className="panel p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="section-title">Pull request</p>
          <h2 className="mt-2 text-2xl font-semibold text-slate-950">{pr.title ?? "Untitled PR"}</h2>
        </div>
        <StatusBadge status={report.status === "complete" ? "passed" : "error"} label={report.status} />
      </div>
      <dl className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {metadata.map(([label, value]) => (
          <div key={label}>
            <dt className="metric-label">{label}</dt>
            <dd className="mt-1 break-words text-sm font-medium text-slate-900">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-6 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        <p className="font-medium text-slate-900">Source</p>
        <a className="mt-1 block break-all text-emerald-700 hover:text-emerald-900" href={pr.url}>
          {pr.url}
        </a>
        {analysis ? <p className="mt-2 text-xs text-slate-500">Updated {formatDate(analysis.updated_at)}</p> : null}
      </div>
    </article>
  );
}

function RunCard({
  title,
  icon,
  run,
  emptyText,
  extra,
}: {
  title: string;
  icon: ReactNode;
  run?: ToolRun | null;
  emptyText: string;
  extra?: string;
}) {
  return (
    <article className="panel p-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-100 text-slate-700">
            {icon}
          </span>
          <div>
            <p className="section-title">{title}</p>
            {extra ? <p className="mt-1 text-sm text-slate-500">{extra}</p> : null}
          </div>
        </div>
        {run ? <StatusBadge status={run.status} /> : <StatusBadge status="skipped" />}
      </div>
      {run ? (
        <div className="mt-5">
          <p className="text-base font-semibold text-slate-950">{run.summary}</p>
          {run.command ? (
            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="metric-label">Exit code</dt>
                <dd className="mt-1 text-slate-900">{run.command.exit_code ?? "n/a"}</dd>
              </div>
              <div>
                <dt className="metric-label">Duration</dt>
                <dd className="mt-1 text-slate-900">
                  {(run.command.duration_seconds ?? 0).toFixed(1)}s
                </dd>
              </div>
              <div>
                <dt className="metric-label">Timed out</dt>
                <dd className="mt-1 text-slate-900">{run.command.timed_out ? "Yes" : "No"}</dd>
              </div>
            </dl>
          ) : null}
        </div>
      ) : (
        <EmptyState>{emptyText}</EmptyState>
      )}
    </article>
  );
}

function ChangedFilesTable({ files }: { files: ChangedFile[] }) {
  return (
    <article className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-4 border-b border-slate-200 px-6 py-5">
        <div>
          <p className="section-title">Changed files</p>
          <h2 className="mt-1 text-xl font-semibold text-slate-950">{files.length} files</h2>
        </div>
      </div>
      {files.length === 0 ? (
        <div className="px-6 py-8">
          <EmptyState>No changed files were returned by the backend.</EmptyState>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr className="text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <th className="px-6 py-3">File</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Additions</th>
                <th className="px-4 py-3 text-right">Deletions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {files.map((file) => (
                <tr key={file.filename}>
                  <td className="max-w-[520px] break-words px-6 py-4 font-mono text-xs text-slate-900">
                    {file.filename}
                  </td>
                  <td className="px-4 py-4">
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700">
                      {file.classification ?? "unknown"}
                    </span>
                  </td>
                  <td className="px-4 py-4 text-slate-600">{file.status}</td>
                  <td className="px-4 py-4 text-right font-medium text-emerald-700">
                    +{file.additions ?? 0}
                  </td>
                  <td className="px-4 py-4 text-right font-medium text-rose-700">
                    -{file.deletions ?? 0}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

function RiskReasons({ reasons }: { reasons: RiskReport["risk_reasons"] }) {
  return (
    <article className="panel p-6">
      <p className="section-title">Risk reasons</p>
      {reasons.length === 0 ? (
        <EmptyState>No risk reasons were recorded.</EmptyState>
      ) : (
        <div className="mt-5 grid gap-3 md:grid-cols-2">
          {reasons.map((reason) => (
            <div key={`${reason.category}-${reason.reason}`} className="rounded-lg border border-slate-200 p-4">
              <div className="flex items-start justify-between gap-4">
                <p className="text-sm font-semibold text-slate-950">{reason.category}</p>
                <span className="shrink-0 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">
                  +{reason.score_impact}
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-slate-600">{reason.reason}</p>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

function SecurityFindings({ findings }: { findings: SecurityFinding[] }) {
  return (
    <article className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-4 border-b border-slate-200 px-6 py-5">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-rose-50 text-rose-700">
            <ShieldAlert className="h-5 w-5" aria-hidden="true" />
          </span>
          <div>
            <p className="section-title">Security findings</p>
            <p className="mt-1 text-sm text-slate-500">{findings.length} Bandit finding{findings.length === 1 ? "" : "s"}</p>
          </div>
        </div>
      </div>
      {findings.length === 0 ? (
        <div className="px-6 py-8">
          <EmptyState>No Bandit findings were recorded.</EmptyState>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50">
              <tr className="text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <th className="px-6 py-3">Severity</th>
                <th className="px-4 py-3">Confidence</th>
                <th className="px-4 py-3">Location</th>
                <th className="px-4 py-3">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {findings.map((finding, index) => (
                <tr key={`${finding.filename ?? finding.file}-${finding.line_number ?? finding.line}-${index}`}>
                  <td className="px-6 py-4">
                    <SeverityBadge severity={finding.severity} />
                  </td>
                  <td className="px-4 py-4 text-slate-600">{finding.confidence ?? "n/a"}</td>
                  <td className="max-w-[340px] break-words px-4 py-4 font-mono text-xs text-slate-900">
                    {finding.filename ?? finding.file ?? "unknown"}:
                    {finding.line_number ?? finding.line ?? "?"}
                  </td>
                  <td className="px-4 py-4 text-slate-700">
                    {finding.message || finding.issue_text || "No message"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

function CollapsibleSection({
  title,
  icon,
  count,
  children,
}: {
  title: string;
  icon: ReactNode;
  count: number;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);

  return (
    <section className="panel overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-slate-50"
      >
        <span className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-700">
            {icon}
          </span>
          <span>
            <span className="section-title">{title}</span>
            <span className="mt-1 block text-sm text-slate-500">{count} item{count === 1 ? "" : "s"}</span>
          </span>
        </span>
        <ChevronDown
          className={`h-5 w-5 text-slate-500 transition ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>
      {open ? <div className="border-t border-slate-200 px-6 py-5">{children}</div> : null}
    </section>
  );
}

function GeneratedTestBlock({ test }: { test: GeneratedTest }) {
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono text-xs font-semibold text-slate-950">{test.path}</span>
        {test.target_functions?.map((target) => (
          <span key={target} className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-700">
            {target}
          </span>
        ))}
      </div>
      <pre className="max-h-[520px] overflow-auto rounded-lg bg-slate-950 p-4 text-xs leading-6 text-slate-100">
        <code>{test.code}</code>
      </pre>
    </div>
  );
}

function LogRunBlock({ run }: { run: ToolRun }) {
  return (
    <div className="rounded-lg border border-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-slate-950">{run.name}</p>
          <p className="text-xs text-slate-500">{run.kind}</p>
        </div>
        <StatusBadge status={run.status} />
      </div>
      <div className="space-y-4 p-4">
        <p className="text-sm text-slate-700">{run.summary}</p>
        {run.command ? (
          <>
            <pre className="rounded-lg bg-slate-100 p-3 text-xs text-slate-700">
              <code>{run.command.command.join(" ")}</code>
            </pre>
            {run.command.stdout_tail ? (
              <LogOutput label="stdout" value={run.command.stdout_tail} />
            ) : null}
            {run.command.stderr_tail ? (
              <LogOutput label="stderr" value={run.command.stderr_tail} />
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

function LogOutput({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="metric-label">{label}</p>
      <pre className="mt-2 max-h-[360px] overflow-auto rounded-lg bg-slate-950 p-4 text-xs leading-6 text-slate-100">
        <code>{value}</code>
      </pre>
    </div>
  );
}

function Notice({
  tone,
  title,
  children,
}: {
  tone: "warning" | "danger";
  title: string;
  children: ReactNode;
}) {
  const styles =
    tone === "warning"
      ? "border-amber-200 bg-amber-50 text-amber-900"
      : "border-rose-200 bg-rose-50 text-rose-900";
  const Icon = tone === "warning" ? AlertTriangle : XCircle;

  return (
    <div className={`mt-6 flex gap-3 rounded-lg border p-4 ${styles}`}>
      <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-semibold">{title}</p>
        <p className="mt-1 text-sm leading-6">{children}</p>
      </div>
    </div>
  );
}

function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="mt-5 rounded-lg border border-dashed border-slate-300 bg-slate-50 p-5 text-sm text-slate-500">
      {children}
    </div>
  );
}

function StatusBadge({ status, label }: { status: RunStatus; label?: string }) {
  const styles: Record<RunStatus, string> = {
    passed: "bg-emerald-100 text-emerald-800",
    failed: "bg-rose-100 text-rose-800",
    skipped: "bg-slate-100 text-slate-700",
    error: "bg-amber-100 text-amber-800",
  };
  const Icon = status === "passed" ? CheckCircle2 : status === "failed" ? XCircle : AlertTriangle;

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${styles[status]}`}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden="true" />
      {label ?? status}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const normalized = severity.toLowerCase();
  const style =
    normalized === "high"
      ? "bg-rose-100 text-rose-800"
      : normalized === "medium"
        ? "bg-amber-100 text-amber-800"
        : "bg-slate-100 text-slate-700";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${style}`}>{severity}</span>;
}

function riskTone(level: RiskLevel) {
  const tones = {
    low: {
      text: "text-emerald-700",
      badge: "bg-emerald-100 text-emerald-800",
      bar: "bg-emerald-600",
    },
    medium: {
      text: "text-amber-700",
      badge: "bg-amber-100 text-amber-800",
      bar: "bg-amber-500",
    },
    high: {
      text: "text-orange-700",
      badge: "bg-orange-100 text-orange-800",
      bar: "bg-orange-600",
    },
    critical: {
      text: "text-rose-700",
      badge: "bg-rose-100 text-rose-800",
      bar: "bg-rose-600",
    },
  } satisfies Record<RiskLevel, { text: string; badge: string; bar: string }>;
  return tones[level];
}

function collectLogRuns(report: RiskReport): ToolRun[] {
  return [
    ...(report.clone_results ?? []),
    report.dependency_install,
    report.existing_tests,
    ...(report.static_analysis_results ?? []),
    report.test_generation,
    ...(report.generated_test_results ?? []),
    ...(report.sandbox_results ?? []),
  ].filter((run): run is ToolRun => Boolean(run));
}

function formatStatus(status: AnalysisStatus): string {
  return status.replace(/_/g, " ");
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function errorText(caught: unknown): string {
  if (caught instanceof ApiError || caught instanceof Error) {
    return caught.message;
  }
  return "Something went wrong while contacting the PatchGuard API.";
}

function shouldFetchReport(analysis: AnalysisRecord): boolean {
  return (
    analysis.status === "completed"
    || analysis.status === "partial"
    || (analysis.status === "failed" && Boolean(analysis.report_path))
  );
}
