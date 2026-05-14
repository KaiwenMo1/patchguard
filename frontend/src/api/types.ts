export type AnalysisStatus =
  | "pending"
  | "fetching_pr"
  | "cloning"
  | "analyzing_diff"
  | "running_existing_tests"
  | "scanning_security"
  | "generating_tests"
  | "running_generated_tests"
  | "completed"
  | "failed"
  | "partial";

export type ReportStatus = "complete" | "partial" | "failed";
export type RunStatus = "passed" | "failed" | "skipped" | "error";
export type RiskLevel = "low" | "medium" | "high" | "critical";
export type PolicyGateDecision = "pass" | "warn" | "block";

export interface AnalyzePRRequest {
  pr_url: string;
  cleanup_workspace?: boolean;
  skip_llm?: boolean;
  skip_docker?: boolean;
}

export interface AnalysisSubmitted {
  analysis_id: string;
  status: AnalysisStatus;
  status_url: string;
  report_url: string;
}

export interface AnalysisRecord {
  analysis_id: string;
  pr_url: string;
  status: AnalysisStatus;
  created_at: string;
  updated_at: string;
  report_path?: string | null;
  error?: string | null;
}

export interface CommandResult {
  command: string[];
  exit_code?: number | null;
  stdout_tail?: string;
  stderr_tail?: string;
  duration_seconds?: number;
  timed_out?: boolean;
  skipped?: boolean;
  skip_reason?: string | null;
}

export interface ToolRun {
  name: string;
  kind: string;
  status: RunStatus;
  summary: string;
  command?: CommandResult | null;
  findings_count?: number;
}

export interface PullRequestInfo {
  owner: string;
  repo: string;
  number: number;
  url: string;
  title?: string | null;
  author?: string | null;
  state?: string | null;
  is_draft?: boolean;
  base_ref?: string | null;
  base_sha?: string | null;
  base_repo_full_name?: string | null;
  head_ref?: string | null;
  head_sha?: string | null;
  head_repo_full_name?: string | null;
  additions?: number;
  deletions?: number;
  changed_files_count?: number;
}

export interface ChangedFile {
  filename: string;
  status: string;
  additions?: number;
  deletions?: number;
  changes?: number;
  classification?: string | null;
  previous_filename?: string | null;
  raw_url?: string | null;
  blob_url?: string | null;
}

export interface RiskReason {
  category: string;
  score_impact: number;
  reason: string;
  severity?: string;
  evidence?: string[];
}

export interface RiskBreakdown {
  overall_score: number;
  risk_level: RiskLevel;
  change_size_risk: number;
  test_coverage_risk: number;
  behavioral_risk: number;
  security_risk: number;
  uncertainty_risk: number;
  reasons: RiskReason[];
}

export interface PolicyDecision {
  decision: PolicyGateDecision;
  reasons: string[];
  triggered_rules: string[];
  config_path?: string | null;
}

export interface SecurityFinding {
  tool: string;
  severity: string;
  confidence?: string | null;
  filename?: string | null;
  line_number?: number | null;
  message?: string;
  file?: string | null;
  line?: number | null;
  issue_text?: string;
  issue_code?: string | null;
  more_info?: string | null;
}

export interface StaticFinding {
  tool: string;
  code?: string | null;
  message: string;
  file?: string | null;
  line?: number | null;
  severity?: string | null;
}

export interface ChangedFunction {
  file_path: string;
  qualified_name: string;
  symbol_type: string;
  start_line: number;
  end_line: number;
  changed_lines?: number[];
  fallback?: boolean;
  parse_error?: string | null;
}

export interface BehavioralContract {
  intended_new_behaviors: string[];
  existing_behaviors_to_preserve: string[];
  edge_cases_to_test: string[];
  invalid_inputs_to_test: string[];
  contract_uncertainties: string[];
  confidence: number;
}

export interface GeneratedTest {
  path: string;
  target_files: string[];
  rationale: string;
  target_functions?: string[];
  code: string;
  provider?: string | null;
  model?: string | null;
}

export interface FailureMapping {
  failed_test: string;
  target_file?: string | null;
  target_function?: string | null;
  behavior_checked?: string | null;
  failure_summary: string;
  risk_message: string;
  suggested_next_step?: string;
}

export interface EvidenceRisk {
  title: string;
  severity: "low" | "medium" | "high" | "critical";
  evidence: string[];
  files: string[];
  suggested_fix: string;
}

export interface EvidenceBasedReview {
  merge_recommendation: "merge" | "merge_with_caution" | "do_not_merge" | "needs_human_review";
  executive_summary: string;
  pr_change_summary: string[];
  correctness_notes: string[];
  efficiency_notes: string[];
  top_risks: EvidenceRisk[];
  files_to_review_first: string[];
  suggested_followup_tests: string[];
  suggested_fixes: string[];
  limitations: string[];
}

export interface RiskReport {
  version: string;
  generated_at: string;
  status: ReportStatus;
  errors: string[];
  pr: PullRequestInfo;
  changed_files: ChangedFile[];
  changed_functions?: ChangedFunction[];
  behavioral_contract?: BehavioralContract | null;
  contract_extraction?: ToolRun | null;
  generated_tests?: GeneratedTest[];
  failure_mappings?: FailureMapping[];
  test_generation?: ToolRun | null;
  generated_test_results?: ToolRun[];
  security_findings?: SecurityFinding[];
  static_analysis_results?: ToolRun[];
  static_findings?: StaticFinding[];
  clone_results?: ToolRun[];
  sandbox_results?: ToolRun[];
  dependency_install?: ToolRun | null;
  existing_tests?: ToolRun | null;
  workspace_path?: string | null;
  risk_score: number;
  risk_level: RiskLevel;
  risk_breakdown?: RiskBreakdown | null;
  risk_reasons: RiskReason[];
  policy_decision?: PolicyDecision | null;
  ai_review?: EvidenceBasedReview | null;
  ai_review_run?: ToolRun | null;
  merge_decision: string;
  recommendation: string;
  report_path?: string | null;
}
