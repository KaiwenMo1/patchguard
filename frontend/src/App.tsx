import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  ClipboardList,
  Code2,
  FileCode2,
  GitPullRequest,
  ListChecks,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
  TestTube2,
  XCircle,
} from "lucide-react";
import { FormEvent, MutableRefObject, ReactNode, useEffect, useRef, useState } from "react";

import { ApiError, getAnalysis, getReport, submitAnalysis } from "./api/client";
import {
  DEMO_REPORTS,
  STATIC_DEMO_MODE,
  analysisRecordForDemo,
  loadDemoReport,
} from "./demoReports";
import type {
  AnalysisRecord,
  AnalysisStatus,
  BehavioralContract,
  ChangedFile,
  FailureMapping,
  GeneratedTest,
  PolicyGateDecision,
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
  const [selectedDemoId, setSelectedDemoId] = useState(DEMO_REPORTS[0].id);
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
    if (!STATIC_DEMO_MODE) {
      return;
    }

    let cancelled = false;
    const loadInitialDemo = async () => {
      try {
        const demo = DEMO_REPORTS[0];
        const demoReport = await loadDemoReport(demo.id);
        if (!cancelled) {
          setReport(demoReport);
          setAnalysis(analysisRecordForDemo(demo, demoReport));
        }
      } catch (caught) {
        if (!cancelled) {
          setError(errorText(caught));
        }
      }
    };

    void loadInitialDemo();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (report) {
      reportRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [report]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (STATIC_DEMO_MODE) {
      await loadSelectedDemo(selectedDemoId);
      return;
    }

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

  const loadSelectedDemo = async (demoId: string) => {
    const demo = DEMO_REPORTS.find((item) => item.id === demoId) ?? DEMO_REPORTS[0];
    setIsSubmitting(true);
    setError(null);
    setAnalysisId(null);
    try {
      const demoReport = await loadDemoReport(demo.id);
      setReport(demoReport);
      setAnalysis(analysisRecordForDemo(demo, demoReport));
    } catch (caught) {
      setError(errorText(caught));
    } finally {
      setIsSubmitting(false);
    }
  };

  const failedWithoutReport = analysis?.status === "failed" && !report;

  return (
    <div className="min-h-screen bg-[#f6f8fa]">
      <header className="border-b border-[#d0d7de] bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-md bg-[#0969da] text-white">
              <ShieldCheck className="h-5 w-5" aria-hidden="true" />
            </div>
            <div>
              <p className="text-sm font-semibold text-[#24292f]">PatchGuard</p>
              <p className="text-xs text-[#57606a]">CI for AI-generated code</p>
            </div>
          </div>
          <a
            href="https://github.com"
            className="hidden text-sm font-medium text-[#57606a] hover:text-[#0969da] sm:inline"
          >
            Public GitHub PRs
          </a>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <section className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(340px,0.9fr)]">
          <div className="panel overflow-hidden">
            <div className="border-b border-[#d0d7de] bg-white px-6 py-5">
              <div className="flex items-center gap-2 text-sm font-medium text-[#0969da]">
                <GitPullRequest className="h-4 w-4" aria-hidden="true" />
                Evidence-backed PR verification
              </div>
              <h1 className="mt-4 text-3xl font-semibold text-[#24292f] sm:text-4xl">PatchGuard</h1>
              <p className="mt-2 max-w-2xl text-base leading-7 text-[#57606a]">
                CI for AI-generated code. Submit a public Python PR and get changed-file evidence,
                Docker test results, static scans, generated-test evidence, and a deterministic
                merge-risk recommendation.
              </p>
            </div>

            <form className="space-y-4 p-6" onSubmit={onSubmit}>
              {STATIC_DEMO_MODE ? (
                <>
                  <label htmlFor="demo-report" className="block text-sm font-medium text-[#24292f]">
                    Sample report
                  </label>
                  <div className="flex flex-col gap-3 sm:flex-row">
                    <select
                      id="demo-report"
                      value={selectedDemoId}
                      onChange={(event) => setSelectedDemoId(event.target.value)}
                      className="min-h-11 flex-1 rounded-md border border-[#d0d7de] bg-white px-3 text-sm text-[#24292f] outline-none transition focus:border-[#0969da] focus:ring-2 focus:ring-[#0969da]/20"
                    >
                      {DEMO_REPORTS.map((demo) => (
                        <option key={demo.id} value={demo.id}>
                          {demo.label}
                        </option>
                      ))}
                    </select>
                    <button
                      type="submit"
                      disabled={isSubmitting}
                      className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-[#0969da] px-5 text-sm font-semibold text-white transition hover:bg-[#0757b3] disabled:cursor-not-allowed disabled:bg-[#8c959f]"
                    >
                      {isSubmitting ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <Play className="h-4 w-4" aria-hidden="true" />
                      )}
                      Load demo
                    </button>
                  </div>
                  <div className="rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4">
                    <p className="text-sm font-medium text-[#24292f]">Static GitHub Pages mode</p>
                    <p className="mt-1 text-sm leading-6 text-[#57606a]">
                      This page loads real sample JSON reports generated by the PatchGuard CLI. It
                      does not call FastAPI, Docker, GitHub, or OpenAI.
                    </p>
                    <div className="mt-3 grid gap-2">
                      {DEMO_REPORTS.map((demo) => (
                        <button
                          key={demo.id}
                          type="button"
                          onClick={() => {
                            setSelectedDemoId(demo.id);
                            void loadSelectedDemo(demo.id);
                          }}
                          className={[
                            "rounded-md border px-3 py-2 text-left text-sm transition",
                            selectedDemoId === demo.id
                              ? "border-[#0969da] bg-[#ddf4ff] text-[#24292f]"
                              : "border-[#d0d7de] bg-white text-[#57606a] hover:border-[#0969da]",
                          ].join(" ")}
                        >
                          <span className="block font-medium text-[#24292f]">{demo.label}</span>
                          <span className="mt-1 block text-xs leading-5">{demo.description}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <>
                  <label htmlFor="pr-url" className="block text-sm font-medium text-[#24292f]">
                    GitHub pull request URL
                  </label>
                  <div className="flex flex-col gap-3 sm:flex-row">
                    <input
                      id="pr-url"
                      value={prUrl}
                      onChange={(event) => setPrUrl(event.target.value)}
                      placeholder="https://github.com/owner/repo/pull/123"
                      className="min-h-11 flex-1 rounded-md border border-[#d0d7de] bg-white px-3 text-sm text-[#24292f] outline-none transition focus:border-[#0969da] focus:ring-2 focus:ring-[#0969da]/20"
                    />
                    <button
                      type="submit"
                      disabled={isSubmitting}
                      className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-[#0969da] px-5 text-sm font-semibold text-white transition hover:bg-[#0757b3] disabled:cursor-not-allowed disabled:bg-[#8c959f]"
                    >
                      {isSubmitting ? (
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                      ) : (
                        <Play className="h-4 w-4" aria-hidden="true" />
                      )}
                      Analyze
                    </button>
                  </div>
                  <p className="text-sm text-[#57606a]">
                    The dashboard calls your local FastAPI backend. OpenAI generation is off by default.
                  </p>
                  <div className="grid gap-3 rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4 sm:grid-cols-2">
                    <label className="flex items-center gap-3 text-sm font-medium text-[#24292f]">
                      <input
                        type="checkbox"
                        checked={skipLlm}
                        onChange={(event) => setSkipLlm(event.target.checked)}
                        className="h-4 w-4 rounded border-[#d0d7de] text-[#0969da] focus:ring-[#0969da]"
                      />
                      Skip OpenAI tests
                    </label>
                    <label className="flex items-center gap-3 text-sm font-medium text-[#24292f]">
                      <input
                        type="checkbox"
                        checked={skipDocker}
                        onChange={(event) => setSkipDocker(event.target.checked)}
                        className="h-4 w-4 rounded border-[#d0d7de] text-[#0969da] focus:ring-[#0969da]"
                      />
                      Skip Docker
                    </label>
                  </div>
                </>
              )}
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
          <h2 className="mt-2 text-2xl font-semibold text-[#24292f]">{title}</h2>
        </div>
        {analysis && !isTerminal ? (
          <Loader2 className="mt-1 h-6 w-6 animate-spin text-[#0969da]" aria-hidden="true" />
        ) : (
          <CircleDashed className="mt-1 h-6 w-6 text-[#8c959f]" aria-hidden="true" />
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
                    ? "border-[#0969da] bg-[#ddf4ff] text-[#0969da]"
                    : complete
                      ? "border-[#1a7f37] bg-[#1a7f37] text-white"
                      : "border-[#d0d7de] bg-white text-[#8c959f]",
                ].join(" ")}
              >
                {complete ? <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> : index + 1}
              </span>
              <span className={active ? "font-medium text-[#24292f]" : "text-sm text-[#57606a]"}>
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      {analysis ? (
        <div className="mt-6 rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4 text-sm text-[#57606a]">
          <p className="font-medium text-[#24292f]">Analysis ID</p>
          <p className="mt-1 break-all font-mono text-xs">{analysis.analysis_id}</p>
          {report ? (
            <a className="mt-3 inline-block font-semibold text-[#0969da] hover:text-[#0757b3]" href="#patchguard-report">
              View report
            </a>
          ) : null}
          {waitingForReport ? (
            <p className="mt-3 text-xs font-medium text-[#9a6700]">
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
  const failureMappings = report.failure_mappings ?? [];
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

      <div className="grid gap-6 xl:grid-cols-[320px_320px_minmax(0,1fr)]">
        <RiskCard report={report} />
        <PolicyCard report={report} />
        <PRMetadataCard report={report} analysis={analysis} />
      </div>

      <AIReviewCard report={report} />
      <BehavioralContractCard report={report} />

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

      {failureMappings.length > 0 ? <FailureMappings mappings={failureMappings} /> : null}
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
  const breakdown = report.risk_breakdown;
  const dimensions = breakdown
    ? [
        ["Change", breakdown.change_size_risk],
        ["Tests", breakdown.test_coverage_risk],
        ["Behavior", breakdown.behavioral_risk],
        ["Security", breakdown.security_risk],
        ["Uncertainty", breakdown.uncertainty_risk],
      ]
    : [];
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
        <span className="pb-2 text-lg font-medium text-[#57606a]">/100</span>
      </div>
      <div className="mt-5 h-2.5 overflow-hidden rounded-full bg-[#eaeef2]">
        <div
          className={`h-full rounded-full ${tone.bar}`}
          style={{ width: `${Math.min(100, Math.max(0, report.risk_score))}%` }}
        />
      </div>
      {dimensions.length > 0 && (
        <div className="mt-5 space-y-3">
          {dimensions.map(([label, value]) => (
            <div key={label}>
              <div className="flex items-center justify-between text-xs font-medium text-[#57606a]">
                <span>{label}</span>
                <span>{value}</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[#eaeef2]">
                <div
                  className="h-full rounded-full bg-[#6e7781]"
                  style={{ width: `${Math.min(100, Math.max(0, Number(value)))}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="mt-6 border-t border-[#d0d7de] pt-5">
        <p className="metric-label">Recommendation</p>
        <p className="mt-2 text-base font-semibold leading-6 text-[#24292f]">
          {report.recommendation}
        </p>
        <p className="mt-2 text-sm text-[#57606a]">
          Decision: <span className="font-medium text-[#24292f]">{report.merge_decision}</span>
        </p>
      </div>
    </article>
  );
}

function PolicyCard({ report }: { report: RiskReport }) {
  const policy = report.policy_decision;
  const tone = policyTone(policy?.decision ?? "warn");
  const reasons = policy?.reasons ?? ["Policy gate did not run for this report."];
  const rules = policy?.triggered_rules ?? [];

  return (
    <article className="panel p-6">
      <div className="flex items-center justify-between">
        <p className="section-title">Policy gate</p>
        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${tone.badge}`}>
          {policy?.decision ?? "unknown"}
        </span>
      </div>
      <div className="mt-5 flex items-center gap-3">
        <span className={`flex h-10 w-10 items-center justify-center rounded-md ${tone.icon}`}>
          {tone.symbol}
        </span>
        <div>
          <p className="text-base font-semibold text-[#24292f]">{tone.title}</p>
          <p className="mt-1 text-sm text-[#57606a]">
            {rules.length} triggered rule{rules.length === 1 ? "" : "s"}
          </p>
        </div>
      </div>
      <ul className="mt-5 space-y-2 text-sm text-[#57606a]">
        {reasons.slice(0, 3).map((reason) => (
          <li key={reason} className="rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-3">
            {reason}
          </li>
        ))}
      </ul>
      {policy?.config_path ? (
        <p className="mt-4 break-all text-xs text-[#57606a]">Config: {policy.config_path}</p>
      ) : (
        <p className="mt-4 text-xs text-[#57606a]">Using default policy.</p>
      )}
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
          <h2 className="mt-2 text-2xl font-semibold text-[#24292f]">{pr.title ?? "Untitled PR"}</h2>
        </div>
        <StatusBadge status={report.status === "complete" ? "passed" : "error"} label={report.status} />
      </div>
      <dl className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {metadata.map(([label, value]) => (
          <div key={label}>
            <dt className="metric-label">{label}</dt>
            <dd className="mt-1 break-words text-sm font-medium text-[#24292f]">{value}</dd>
          </div>
        ))}
      </dl>
      <div className="mt-6 rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4 text-sm text-[#57606a]">
        <p className="font-medium text-[#24292f]">Source</p>
        <a className="mt-1 block break-all text-[#0969da] hover:text-[#0757b3]" href={pr.url}>
          {pr.url}
        </a>
        {analysis ? <p className="mt-2 text-xs text-[#57606a]">Updated {formatDate(analysis.updated_at)}</p> : null}
      </div>
    </article>
  );
}

function BehavioralContractCard({ report }: { report: RiskReport }) {
  const contract = report.behavioral_contract ?? emptyBehavioralContract();
  const run = report.contract_extraction;
  const itemCount =
    contract.intended_new_behaviors.length
    + contract.existing_behaviors_to_preserve.length
    + contract.edge_cases_to_test.length
    + contract.invalid_inputs_to_test.length
    + contract.contract_uncertainties.length;
  const confidence = Math.round((contract.confidence ?? 0) * 100);

  return (
    <article className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#d0d7de] px-6 py-5">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-md bg-[#ddf4ff] text-[#0969da]">
            <ListChecks className="h-5 w-5" aria-hidden="true" />
          </span>
          <div>
            <p className="section-title">Behavioral contract</p>
            <p className="mt-1 text-sm text-[#57606a]">
              {itemCount} contract item{itemCount === 1 ? "" : "s"} extracted for test targeting
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm font-medium text-[#57606a]">Confidence {confidence}%</span>
          {run ? <StatusBadge status={run.status} /> : <StatusBadge status="skipped" />}
        </div>
      </div>
      <div className="px-6 py-5">
        {run ? <p className="mb-5 text-sm text-[#57606a]">{run.summary}</p> : null}
        <div className="mb-5 h-2 overflow-hidden rounded-full bg-[#eaeef2]">
          <div
            className="h-full rounded-full bg-[#0969da]"
            style={{ width: `${Math.min(100, Math.max(0, confidence))}%` }}
          />
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <ContractList
            title="Intended new behavior"
            values={contract.intended_new_behaviors}
            emptyText="No new behavior was extracted."
          />
          <ContractList
            title="Behavior to preserve"
            values={contract.existing_behaviors_to_preserve}
            emptyText="No preservation behavior was extracted."
          />
          <ContractList
            title="Edge cases"
            values={contract.edge_cases_to_test}
            emptyText="No edge cases were extracted."
          />
          <ContractList
            title="Invalid inputs"
            values={contract.invalid_inputs_to_test}
            emptyText="No invalid-input cases were extracted."
          />
        </div>
        {contract.contract_uncertainties.length > 0 ? (
          <div className="mt-4 rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4">
            <p className="text-sm font-semibold text-[#24292f]">Uncertainties</p>
            <ul className="mt-3 space-y-2 text-sm text-[#57606a]">
              {contract.contract_uncertainties.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function AIReviewCard({ report }: { report: RiskReport }) {
  const review = report.ai_review;
  const run = report.ai_review_run;
  const topRisks = review?.top_risks ?? [];

  return (
    <article className="panel overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#d0d7de] px-6 py-5">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-md bg-[#f6f8fa] text-[#57606a]">
            <ClipboardList className="h-5 w-5" aria-hidden="true" />
          </span>
          <div>
            <p className="section-title">Evidence-based AI review</p>
            <p className="mt-1 text-sm text-[#57606a]">
              Summary and next actions grounded in collected PatchGuard evidence
            </p>
          </div>
        </div>
        {run ? <StatusBadge status={run.status} /> : <StatusBadge status="skipped" />}
      </div>

      <div className="space-y-5 px-6 py-5">
        {run ? <p className="text-sm text-[#57606a]">{run.summary}</p> : null}
        {review ? (
          <>
            <div className="rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4">
              <p className="text-sm font-semibold text-[#24292f]">Summary</p>
              <p className="mt-2 text-sm leading-6 text-[#57606a]">
                {review.executive_summary || "No AI review summary was generated."}
              </p>
              <p className="mt-3 text-xs font-medium text-[#57606a]">
                AI recommendation: {review.merge_recommendation.replace(/_/g, " ")}
              </p>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              <ReviewList
                title="What changed"
                values={review.pr_change_summary}
                emptyText="No AI change summary was generated."
              />
              <ReviewList
                title="Correctness notes"
                values={review.correctness_notes}
                emptyText="No correctness notes were generated."
              />
              <ReviewList
                title="Efficiency notes"
                values={review.efficiency_notes}
                emptyText="No performance evidence was collected."
              />
            </div>

            {topRisks.length > 0 ? (
              <div className="overflow-hidden rounded-md border border-[#d0d7de]">
                <div className="border-b border-[#d0d7de] bg-[#f6f8fa] px-4 py-3">
                  <p className="text-sm font-semibold text-[#24292f]">Top AI-highlighted risks</p>
                </div>
                <div className="divide-y divide-[#d8dee4]">
                  {topRisks.map((risk) => (
                    <div key={`${risk.title}-${risk.severity}`} className="grid gap-3 p-4 lg:grid-cols-[160px_minmax(0,1fr)_minmax(220px,0.6fr)]">
                      <div>
                        <SeverityBadge severity={risk.severity} />
                        <p className="mt-2 text-sm font-semibold text-[#24292f]">{risk.title}</p>
                      </div>
                      <div>
                        <p className="metric-label">Evidence</p>
                        <ul className="mt-2 space-y-1 text-sm leading-6 text-[#57606a]">
                          {risk.evidence.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <p className="metric-label">Suggested fix</p>
                        <p className="mt-2 text-sm leading-6 text-[#57606a]">
                          {risk.suggested_fix || "Review the cited evidence before merge."}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-3">
              <ReviewList
                title="Follow-up tests"
                values={review.suggested_followup_tests}
                emptyText="No follow-up tests were suggested."
              />
              <ReviewList
                title="Suggested fixes"
                values={review.suggested_fixes}
                emptyText="No fixes were suggested."
              />
              <ReviewList
                title="Limitations"
                values={review.limitations}
                emptyText="No limitations were reported."
              />
            </div>
          </>
        ) : (
          <EmptyState>No evidence-based AI review is attached to this report.</EmptyState>
        )}
      </div>
    </article>
  );
}

function ReviewList({
  title,
  values,
  emptyText,
}: {
  title: string;
  values: string[];
  emptyText: string;
}) {
  return (
    <div className="rounded-md border border-[#d0d7de] p-4">
      <p className="text-sm font-semibold text-[#24292f]">{title}</p>
      {values.length === 0 ? (
        <p className="mt-3 text-sm leading-6 text-[#57606a]">{emptyText}</p>
      ) : (
        <ul className="mt-3 space-y-2 text-sm leading-6 text-[#57606a]">
          {values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ContractList({
  title,
  values,
  emptyText,
}: {
  title: string;
  values: string[];
  emptyText: string;
}) {
  return (
    <div className="rounded-md border border-[#d0d7de] p-4">
      <p className="text-sm font-semibold text-[#24292f]">{title}</p>
      {values.length === 0 ? (
        <p className="mt-3 text-sm text-[#57606a]">{emptyText}</p>
      ) : (
        <ul className="mt-3 space-y-2 text-sm leading-6 text-[#57606a]">
          {values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      )}
    </div>
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
          <span className="flex h-10 w-10 items-center justify-center rounded-md bg-[#f6f8fa] text-[#57606a]">
            {icon}
          </span>
          <div>
            <p className="section-title">{title}</p>
            {extra ? <p className="mt-1 text-sm text-[#57606a]">{extra}</p> : null}
          </div>
        </div>
        {run ? <StatusBadge status={run.status} /> : <StatusBadge status="skipped" />}
      </div>
      {run ? (
        <div className="mt-5">
          <p className="text-base font-semibold text-[#24292f]">{run.summary}</p>
          {run.command ? (
            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="metric-label">Exit code</dt>
                <dd className="mt-1 text-[#24292f]">{run.command.exit_code ?? "n/a"}</dd>
              </div>
              <div>
                <dt className="metric-label">Duration</dt>
                <dd className="mt-1 text-[#24292f]">
                  {(run.command.duration_seconds ?? 0).toFixed(1)}s
                </dd>
              </div>
              <div>
                <dt className="metric-label">Timed out</dt>
                <dd className="mt-1 text-[#24292f]">{run.command.timed_out ? "Yes" : "No"}</dd>
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

function FailureMappings({ mappings }: { mappings: FailureMapping[] }) {
  return (
    <article className="panel overflow-hidden">
      <div className="border-b border-[#d0d7de] px-6 py-5">
        <p className="section-title">Failed generated tests</p>
        <h2 className="mt-1 text-xl font-semibold text-[#24292f]">
          {mappings.length} mapped failure{mappings.length === 1 ? "" : "s"}
        </h2>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-[#d0d7de] text-sm">
          <thead className="bg-[#f6f8fa]">
            <tr className="text-left text-xs font-semibold uppercase text-[#57606a]">
              <th className="px-6 py-3">Failed test</th>
              <th className="px-4 py-3">Target</th>
              <th className="px-4 py-3">Behavior checked</th>
              <th className="px-4 py-3">Failure</th>
              <th className="px-4 py-3">Next step</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#d8dee4] bg-white">
            {mappings.map((mapping) => (
              <tr key={`${mapping.failed_test}-${mapping.target_file ?? "unknown"}`}>
                <td className="max-w-[260px] break-words px-6 py-4 font-mono text-xs text-[#cf222e]">
                  {mapping.failed_test}
                </td>
                <td className="max-w-[300px] break-words px-4 py-4 font-mono text-xs text-[#24292f]">
                  {mapping.target_file ?? "unknown"}
                  {mapping.target_function ? `::${mapping.target_function}` : ""}
                </td>
                <td className="max-w-[360px] break-words px-4 py-4 text-[#57606a]">
                  {mapping.behavior_checked ?? "No behavior metadata"}
                </td>
                <td className="max-w-[320px] break-words px-4 py-4 text-[#57606a]">
                  <p>{mapping.failure_summary}</p>
                  <p className="mt-2 text-xs text-[#8c959f]">{mapping.risk_message}</p>
                </td>
                <td className="max-w-[360px] break-words px-4 py-4 text-[#57606a]">
                  {mapping.suggested_next_step ?? "Review the generated test failure before merging."}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}

function ChangedFilesTable({ files }: { files: ChangedFile[] }) {
  return (
    <article className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-4 border-b border-[#d0d7de] px-6 py-5">
        <div>
          <p className="section-title">Changed files</p>
          <h2 className="mt-1 text-xl font-semibold text-[#24292f]">{files.length} files</h2>
        </div>
      </div>
      {files.length === 0 ? (
        <div className="px-6 py-8">
          <EmptyState>No changed files were returned by the backend.</EmptyState>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-[#d0d7de] text-sm">
            <thead className="bg-[#f6f8fa]">
              <tr className="text-left text-xs font-semibold uppercase text-[#57606a]">
                <th className="px-6 py-3">File</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3 text-right">Additions</th>
                <th className="px-4 py-3 text-right">Deletions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#d8dee4] bg-white">
              {files.map((file) => (
                <tr key={file.filename}>
                  <td className="max-w-[520px] break-words px-6 py-4 font-mono text-xs text-[#24292f]">
                    {file.filename}
                  </td>
                  <td className="px-4 py-4">
                    <span className="rounded-full bg-[#f6f8fa] px-2.5 py-1 text-xs font-medium text-[#57606a]">
                      {file.classification ?? "unknown"}
                    </span>
                  </td>
                  <td className="px-4 py-4 text-[#57606a]">{file.status}</td>
                  <td className="px-4 py-4 text-right font-medium text-[#1a7f37]">
                    +{file.additions ?? 0}
                  </td>
                  <td className="px-4 py-4 text-right font-medium text-[#cf222e]">
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
            <div key={`${reason.category}-${reason.reason}`} className="rounded-md border border-[#d0d7de] p-4">
              <div className="flex items-start justify-between gap-4">
                <p className="text-sm font-semibold text-[#24292f]">{reason.category}</p>
                <span className="shrink-0 rounded-full bg-[#fff8c5] px-2.5 py-1 text-xs font-semibold text-[#9a6700]">
                  +{reason.score_impact}
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-[#57606a]">{reason.reason}</p>
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
      <div className="flex items-center justify-between gap-4 border-b border-[#d0d7de] px-6 py-5">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-md bg-[#ffebe9] text-[#cf222e]">
            <ShieldAlert className="h-5 w-5" aria-hidden="true" />
          </span>
          <div>
            <p className="section-title">Security findings</p>
            <p className="mt-1 text-sm text-[#57606a]">{findings.length} Bandit finding{findings.length === 1 ? "" : "s"}</p>
          </div>
        </div>
      </div>
      {findings.length === 0 ? (
        <div className="px-6 py-8">
          <EmptyState>No Bandit findings were recorded.</EmptyState>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-[#d0d7de] text-sm">
            <thead className="bg-[#f6f8fa]">
              <tr className="text-left text-xs font-semibold uppercase text-[#57606a]">
                <th className="px-6 py-3">Severity</th>
                <th className="px-4 py-3">Confidence</th>
                <th className="px-4 py-3">Location</th>
                <th className="px-4 py-3">Message</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#d8dee4] bg-white">
              {findings.map((finding, index) => (
                <tr key={`${finding.filename ?? finding.file}-${finding.line_number ?? finding.line}-${index}`}>
                  <td className="px-6 py-4">
                    <SeverityBadge severity={finding.severity} />
                  </td>
                  <td className="px-4 py-4 text-[#57606a]">{finding.confidence ?? "n/a"}</td>
                  <td className="max-w-[340px] break-words px-4 py-4 font-mono text-xs text-[#24292f]">
                    {finding.filename ?? finding.file ?? "unknown"}:
                    {finding.line_number ?? finding.line ?? "?"}
                  </td>
                  <td className="px-4 py-4 text-[#57606a]">
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
        className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left transition hover:bg-[#f6f8fa]"
      >
        <span className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-md bg-[#ddf4ff] text-[#0969da]">
            {icon}
          </span>
          <span>
            <span className="section-title">{title}</span>
            <span className="mt-1 block text-sm text-[#57606a]">{count} item{count === 1 ? "" : "s"}</span>
          </span>
        </span>
        <ChevronDown
          className={`h-5 w-5 text-[#57606a] transition ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>
      {open ? <div className="border-t border-[#d0d7de] px-6 py-5">{children}</div> : null}
    </section>
  );
}

function GeneratedTestBlock({ test }: { test: GeneratedTest }) {
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="font-mono text-xs font-semibold text-[#24292f]">{test.path}</span>
        {test.target_functions?.map((target) => (
          <span key={target} className="rounded-full bg-[#f6f8fa] px-2.5 py-1 text-xs text-[#57606a]">
            {target}
          </span>
        ))}
      </div>
      <pre className="max-h-[520px] overflow-auto rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4 text-xs leading-6 text-[#24292f]">
        <code>{test.code}</code>
      </pre>
    </div>
  );
}

function LogRunBlock({ run }: { run: ToolRun }) {
  return (
    <div className="rounded-md border border-[#d0d7de]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#d0d7de] bg-[#f6f8fa] px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-[#24292f]">{run.name}</p>
          <p className="text-xs text-[#57606a]">{run.kind}</p>
        </div>
        <StatusBadge status={run.status} />
      </div>
      <div className="space-y-4 p-4">
        <p className="text-sm text-[#57606a]">{run.summary}</p>
        {run.command ? (
          <>
            <pre className="rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-3 text-xs text-[#24292f]">
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
      <pre className="mt-2 max-h-[360px] overflow-auto rounded-md border border-[#d0d7de] bg-[#f6f8fa] p-4 text-xs leading-6 text-[#24292f]">
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
      ? "border-[#d4a72c] bg-[#fff8c5] text-[#633c01]"
      : "border-[#ff8182] bg-[#ffebe9] text-[#82071e]";
  const Icon = tone === "warning" ? AlertTriangle : XCircle;

  return (
    <div className={`mt-6 flex gap-3 rounded-md border p-4 ${styles}`}>
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
    <div className="mt-5 rounded-md border border-dashed border-[#d0d7de] bg-[#f6f8fa] p-5 text-sm text-[#57606a]">
      {children}
    </div>
  );
}

function StatusBadge({ status, label }: { status: RunStatus; label?: string }) {
  const styles: Record<RunStatus, string> = {
    passed: "bg-[#dafbe1] text-[#116329]",
    failed: "bg-[#ffebe9] text-[#cf222e]",
    skipped: "bg-[#f6f8fa] text-[#57606a]",
    error: "bg-[#fff8c5] text-[#9a6700]",
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
      ? "bg-[#ffebe9] text-[#cf222e]"
      : normalized === "medium"
        ? "bg-[#fff8c5] text-[#9a6700]"
        : "bg-[#f6f8fa] text-[#57606a]";
  return <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${style}`}>{severity}</span>;
}

function riskTone(level: RiskLevel) {
  const tones = {
    low: {
      text: "text-[#1a7f37]",
      badge: "bg-[#dafbe1] text-[#116329]",
      bar: "bg-[#1a7f37]",
    },
    medium: {
      text: "text-[#9a6700]",
      badge: "bg-[#fff8c5] text-[#9a6700]",
      bar: "bg-[#bf8700]",
    },
    high: {
      text: "text-[#bc4c00]",
      badge: "bg-[#fff1e5] text-[#bc4c00]",
      bar: "bg-[#bc4c00]",
    },
    critical: {
      text: "text-[#cf222e]",
      badge: "bg-[#ffebe9] text-[#cf222e]",
      bar: "bg-[#cf222e]",
    },
  } satisfies Record<RiskLevel, { text: string; badge: string; bar: string }>;
  return tones[level];
}

function policyTone(decision: PolicyGateDecision) {
  const tones = {
    pass: {
      title: "Policy passed",
      badge: "bg-[#dafbe1] text-[#116329]",
      icon: "bg-[#dafbe1] text-[#116329]",
      symbol: <CheckCircle2 className="h-5 w-5" aria-hidden="true" />,
    },
    warn: {
      title: "Review required",
      badge: "bg-[#fff8c5] text-[#9a6700]",
      icon: "bg-[#fff8c5] text-[#9a6700]",
      symbol: <AlertTriangle className="h-5 w-5" aria-hidden="true" />,
    },
    block: {
      title: "Policy blocked",
      badge: "bg-[#ffebe9] text-[#cf222e]",
      icon: "bg-[#ffebe9] text-[#cf222e]",
      symbol: <XCircle className="h-5 w-5" aria-hidden="true" />,
    },
  } satisfies Record<
    PolicyGateDecision,
    { title: string; badge: string; icon: string; symbol: ReactNode }
  >;
  return tones[decision];
}

function collectLogRuns(report: RiskReport): ToolRun[] {
  return [
    ...(report.clone_results ?? []),
    report.dependency_install,
    report.existing_tests,
    ...(report.static_analysis_results ?? []),
    report.contract_extraction,
    report.test_generation,
    ...(report.generated_test_results ?? []),
    report.ai_review_run,
    ...(report.sandbox_results ?? []),
  ].filter((run): run is ToolRun => Boolean(run));
}

function emptyBehavioralContract(): BehavioralContract {
  return {
    intended_new_behaviors: [],
    existing_behaviors_to_preserve: [],
    edge_cases_to_test: [],
    invalid_inputs_to_test: [],
    contract_uncertainties: ["Behavioral contract extraction did not run for this report."],
    confidence: 0,
  };
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
