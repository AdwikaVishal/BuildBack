// ─────────────────────────────────────────────────────────────────────────────
// RippleBack frontend types — aligned to docs/data-contract.md
// All shapes match the actual MCP tool outputs. Do NOT invent extra fields.
// ─────────────────────────────────────────────────────────────────────────────

// ── §1–3  Core plan schema ────────────────────────────────────────────────────

export type EntityStatus = 'pending' | 'in_progress' | 'done' | 'blocked';
export type ScenarioStatus = 'healthy' | 'degraded' | 'disrupted';
export type DependencyType =
  | 'requires_completion'
  | 'requires_arrival_by'
  | 'benefits_from';

export interface EntityMetadata {
  external?: boolean;
  slack_minutes?: number;
  substitutes?: string[];
  [key: string]: unknown;
}

export interface Entity {
  id: string;
  name: string;
  status: EntityStatus;
  // Phase 5 staffing (optional)
  assignee?: string;
  effort?: number;
  // Phase 6 graph (optional)
  duration?: number;
  metadata?: EntityMetadata;
}

export interface Dependency {
  dependent: string;
  prerequisite: string;
  dependency_type?: DependencyType;
  constraint_parameters?: { required_buffer_minutes?: number; [key: string]: unknown };
}

export interface Constraint {
  type: string;
  entity_ids: string[];
  description?: string;
}

export interface Resource {
  id: string;
  name: string;
  capacity: number;
}

export interface Scenario {
  scenario_id: string;
  plan_id: string;
  version: number;
  status: ScenarioStatus;
  entities: Entity[];
  dependencies: Dependency[];
  constraints: Constraint[];
  disruptions: unknown[];
  resources?: Resource[];
}

// ── §4  Impact / simulate_disruption ─────────────────────────────────────────

export type DisruptionSeverity = 'low' | 'medium' | 'high' | 'critical';

export interface DisruptionInfo {
  entity_id: string;
  name: string;
  state: 'blocked';
  reason: string;
  severity: DisruptionSeverity;
}

export interface AffectedEntry {
  entity_id: string;
  name: string;
  state: 'blocked';
  evidence_path: string[];
}

export interface UnaffectedEntry {
  entity_id: string;
  name: string;
  state: EntityStatus;
}

export interface ImpactResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  simulated: true;
  disruption: DisruptionInfo;
  directly_affected: AffectedEntry[];
  transitively_affected: AffectedEntry[];
  unaffected: UnaffectedEntry[];
}

// ── §5  Recovery alternatives / find_alternative_dependencies ────────────────

export interface RecoveryAlternative {
  action: 'remove_dependency';
  removed_dependency: Dependency;
  dependencies: Dependency[];
}

export interface RecoveryOption {
  option_id: string;
  description: string;
  affected_entity_id: string;
  affected_dependency: Dependency;
  alternative: RecoveryAlternative;
  unblocks: string[];
  assumptions: string[];
  limitations: string[];
}

export interface RecoveryResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  simulated: true;
  disruption: { entity_id: string; name: string };
  options: RecoveryOption[];
}

// ── §6  Evaluation / evaluate_recovery_plan ───────────────────────────────────

export type EvaluationVerdict = 'structurally_valid' | 'rejected';
export type FindingLevel = 'info' | 'warning' | 'error';
export type FindingCode =
  | 'plan_version_match'
  | 'stale_plan_version'
  | 'dependency_edge_exists'
  | 'missing_dependency'
  | 'unknown_entity'
  | 'business_readiness_not_assessed';

export interface EvaluationFinding {
  code: FindingCode;
  level: FindingLevel;
  message: string;
}

export interface EvaluationImpact {
  disrupted_entity_id: string;
  removed_dependency: Dependency;
  unblocks: string[];
  still_blocked: string[];
}

export interface EvaluationResult {
  evaluation_id: string;
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  expected_plan_version: number;
  verdict: EvaluationVerdict;
  dependency: Dependency;
  findings: EvaluationFinding[];
  impact: EvaluationImpact | null;
  assumptions: string[];
  limitations: string[];
}

// ── §7  Workload / rebalance_workload ─────────────────────────────────────────

export type WorkloadOutcome = 'ok' | 'insufficient_data';

export interface ResourceSummary {
  resource_id: string;
  name: string;
  capacity: number;
  assigned_effort: number;
  free_capacity: number;
  overloaded: boolean;
  assigned_entity_ids: string[];
}

export interface RebalanceSuggestion {
  suggestion_id: string;
  entity_id: string;
  effort: number;
  from_resource_id: string;
  to_resource_id: string;
  reason: string;
  assumptions: string[];
  limitations: string[];
}

export interface UnresolvedItem {
  entity_id: string;
  resource_id: string;
  effort: number;
  reason: string;
}

export interface WorkloadResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  expected_plan_version: number;
  simulated: true;
  unit: 'work_units';
  outcome: WorkloadOutcome;
  resources: ResourceSummary[];
  suggestions: RebalanceSuggestion[];
  unresolved: UnresolvedItem[];
  assumptions: string[];
  limitations: string[];
}

// ── §8  Triage / analyze_blocked_tasks ───────────────────────────────────────

export type TriageState = 'done' | 'blocked' | 'external' | 'at_risk' | 'healthy';
export type EventType =
  | 'service_outage'
  | 'resource_unavailable'
  | 'delivery_delay'
  | 'transport_delay';

export interface EvidenceEdge {
  dependent: string;
  prerequisite: string;
  dependency_type: DependencyType;
}

export interface TriageStateEntry {
  entity_id: string;
  name: string;
  state: TriageState;
  reason: string;
  evidence_path: string[];
  evidence_edges: EvidenceEdge[];
}

export interface TriageSummary {
  done: number;
  blocked: number;
  external: number;
  at_risk: number;
  healthy: number;
  [key: string]: number;
}

export interface ParallelWork {
  entity_id: string;
  name: string;
}

export interface DisruptionDetails {
  entity_id: string;
  name: string;
  event_type: EventType;
  reason: string | null;
  delay_minutes: number | null;
}

export interface CriticalPathInfo {
  path: string[];
  length: number;
  unit: string;
}

export interface TriageResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  simulated: true;
  disruption: DisruptionDetails;
  states: TriageStateEntry[];
  summary: TriageSummary;
  parallel_work: ParallelWork[];
  critical_path: CriticalPathInfo;
  critical_path_changes: string[];
  assumptions: string[];
  limitations: string[];
}

// ── §10  Forecast / recalculate_delivery_forecast ────────────────────────────

export type ForecastUnit = 'steps' | 'duration_units';

export interface ForecastResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  simulated: true;
  disrupted_entity_id: string | null;
  option_id: string | null;
  changes: ChangeItem[];
  feasible: boolean;
  error: { code: string; message: string } | null;
  unit: ForecastUnit;
  baseline_path: string[];
  baseline_length: number;
  baseline_edges: EvidenceEdge[];
  forecast_path: string[] | null;
  forecast_length: number | null;
  forecast_edges: EvidenceEdge[] | null;
  delay_delta: number | null;
  assumptions: string[];
  limitations: string[];
}

// ── §11  Constraints / validate_hard_constraints ──────────────────────────────

export type ViolationCode =
  | 'CONSTRAINT_VIOLATED'
  | 'UNSUPPORTED_CONSTRAINT'
  | 'ENTITY_NOT_FOUND'
  | 'INVALID_DEPENDENCY'
  | 'INVALID_DEPENDENCY_TYPE'
  | 'CYCLIC_DEPENDENCY';

export type ViolationSource = 'graph' | 'constraint';
export type ConstraintStatus = 'satisfied' | 'violated' | 'unsupported';

export interface Violation {
  code: ViolationCode;
  message: string;
  source: ViolationSource;
  constraint_index: number | null;
  constraint_type: string | null;
  entity_ids: string[] | null;
}

export interface ConstraintDetail {
  index: number;
  type: string;
  entity_ids: string[];
  description?: string;
  status: ConstraintStatus;
}

export interface ConstraintValidationResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  simulated: true;
  changes: ChangeItem[];
  feasible: boolean;
  graph_errors: Array<{ code: string; message: string }>;
  constraints: ConstraintDetail[];
  violations: Violation[];
  assumptions: string[];
  limitations: string[];
}

// ── §12–13  Scoring & comparison / compare_recovery_options ──────────────────

export type ScoringOutcome = 'ranked' | 'no_feasible_recovery';

export type ScoringMetricKey =
  | 'delay'
  | 'cost'
  | 'risk'
  | 'resource_strain'
  | 'disruption_count';

export interface ScoringWeights {
  delay: number;
  cost: number;
  risk: number;
  resource_strain: number;
  disruption_count: number;
}

export interface RankedEntry {
  option_id: string;
  rank: number | null;
  feasible: boolean;
  scorable: boolean;
  score: number | null;
  components: Record<ScoringMetricKey, number> | null;
  metrics: Partial<Record<ScoringMetricKey, number | null>>;
  missing_metrics: string[];
  violations: Violation[];
  reasons: string[];
  // Added by compare_recovery_options:
  forecast: ForecastResult;
  no_op: boolean;
  assumptions: string[];
  limitations: string[];
}

export interface ComparisonResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  simulated: true;
  outcome: ScoringOutcome;
  weights: ScoringWeights;
  feasible_count: number;
  scorable_count: number;
  recommendation: string | null;
  baseline: CriticalPathInfo;
  ranked: RankedEntry[];
  excluded: RankedEntry[];
  assumptions: string[];
  limitations: string[];
}

// ── §14  Substitutes / find_substitute_options ───────────────────────────────

export type SubstituteOutcome =
  | 'substitutes_proposed'
  | 'no_feasible_substitute'
  | 'no_substitute_candidates';

export interface SubstituteOption {
  option_id: string;
  explanation: string;
  disrupted_entity_id: string;
  substitute_entity_id: string;
  affected_entity_ids: string[];
  affected_dependencies: Dependency[];
  changes: ChangeItem[];
  feasible: boolean;
  reasons: string[];
  violations: Violation[];
  assumptions: string[];
  limitations: string[];
}

export interface SubstituteResult {
  scenario_id: string;
  plan_id: string;
  plan_version: number;
  simulated: true;
  disruption: { entity_id: string; name: string };
  outcome: SubstituteOutcome;
  options: SubstituteOption[];
  missing_data: string[];
  assumptions: string[];
  limitations: string[];
}

// ── Change set (used by forecast, constraints, comparison) ───────────────────

export interface ChangeItem {
  action: 'remove_dependency' | 'add_dependency';
  dependent: string;
  prerequisite: string;
  dependency_type?: DependencyType;
  constraint_parameters?: { required_buffer_minutes?: number };
}

// ── MCP client layer ──────────────────────────────────────────────────────────

export interface MCPToolCall {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  startedAt: number;
  completedAt?: number;
  durationMs?: number;
  result?: unknown;
  error?: string;
  isError: boolean;
}

export interface MCPConnectionStatus {
  connected: boolean;
  serverUrl: string;
  serverName?: string;
  serverVersion?: string;
  tools?: string[];
  lastCheckedAt?: number;
  error?: string;
}

// ── UI state ──────────────────────────────────────────────────────────────────

export type ScenarioId =
  | 'demo-release-plan'
  | 'demo-release-plan-staffed'
  | 'project-full'
  | 'travel';

export type DashboardView =
  | 'graph'
  | 'impact'
  | 'recovery'
  | 'timeline'
  | 'approval'
  | 'mcp';

export interface DisruptionState {
  active: boolean;
  entityId: string;
  reason: string;
  severity: DisruptionSeverity;
}

export interface SelectedRecoveryOption {
  optionId: string;
  confirmedAt?: number;
  evaluationResult?: EvaluationResult;
}
