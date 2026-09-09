export type TicketStatus =
  | "OPEN"
  | "PROCESSING"
  | "WAITING_FOR_APPROVAL"
  | "RESOLVED"
  | "ESCALATED"
  | "FAILED";
export type Permission = "none" | "read" | "write" | "admin";
export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED";
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
export interface Employee {
  id: string;
  name: string;
  email: string;
  department: string;
  role: string;
  manager_id: string | null;
  is_active: boolean;
}
export interface Repository {
  id: string;
  name: string;
  owning_department: string;
  sensitivity_level: string;
}
export interface User {
  id: string;
  employee_id: string;
  name: string;
  email: string;
  role: "employee" | "manager" | "admin";
}
export interface Session {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}
export interface Ticket {
  id: string;
  employee_id: string;
  request_text: string;
  status: TicketStatus;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  final_response: string | null;
}
export interface TicketDetail extends Ticket {
  messages: { id: string; role: string; content: string; created_at: string }[];
}
export interface Evidence {
  document_id: string;
  chunk_id: string;
  slug: string;
  title: string;
  version: string;
  section: string;
  excerpt: string;
  score: number;
  content_hash: string;
}
export interface AgentState {
  workflow_version: number;
  request_text: string;
  current_node: string;
  intent: string | null;
  employee_id: string | null;
  employee: Employee | null;
  repository: Repository | null;
  repository_name: string | null;
  requested_permission: Permission | null;
  current_permission: Permission | null;
  retrieved_policies: Evidence[];
  decision: {
    disposition: string;
    summary: string;
    policy_chunk_ids: string[];
  } | null;
  planned_action: {
    tool_name: string;
    employee_id: string;
    repository_id: string;
    permission: Permission;
  } | null;
  approval_id: string | null;
  approval_status: ApprovalStatus | null;
  execution_result: {
    tool_execution_id: string;
    succeeded: boolean;
    changed: boolean | null;
  } | null;
  verification_result: {
    tool_execution_id: string;
    observed_permission: Permission;
    sufficient: boolean;
  } | null;
  outcome: string | null;
  final_response: string | null;
  error_code: string | null;
}
export interface AgentRun {
  id: string;
  ticket_id: string;
  status:
    | "PENDING"
    | "RUNNING"
    | "WAITING_FOR_APPROVAL"
    | "COMPLETED"
    | "FAILED";
  state: AgentState;
  provider_name: string;
  model_name: string;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  attempt_count: number;
  error: string | null;
}
export interface AgentStep {
  id: string;
  sequence: number;
  node: string;
  status: "RUNNING" | "COMPLETED" | "WAITING" | "FAILED";
  summary: string;
  details: Record<string, unknown>;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
}
export interface ToolExecution {
  id: string;
  step_id: string | null;
  tool_name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  error: string | null;
  started_at: string;
  completed_at: string | null;
  latency_ms: number | null;
}
export interface Approval {
  id: string;
  run_id: string;
  ticket_id: string;
  employee_id: string;
  repository_id: string;
  permission: Permission;
  approver_id: string;
  status: ApprovalStatus;
  policy_evidence: Evidence[];
  recommendation: string;
  requested_at: string;
  decided_at: string | null;
  decided_by_id: string | null;
  decision_comment: string | null;
  employee: Employee;
  repository: Repository;
  approver: User;
  can_decide: boolean;
}

export type EvaluationSource = "live" | "scenario";
export interface EvaluationAssertion {
  name: string;
  passed: boolean | null;
  summary: string;
  expected: unknown;
  observed: unknown;
}
export interface EvaluationExpectedOutcome {
  expected_outcome: string;
  expected_final_status: TicketStatus;
  expected_tools: string[];
  forbidden_tools: string[];
  requires_approval: boolean;
  expected_permission: Permission | null;
  expected_policy_sections: string[];
}
export interface EvaluationScenario extends EvaluationExpectedOutcome {
  id: string;
  name: string;
  description: string;
}
export interface EvaluationBatch {
  id: string;
  requested_by_id: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  scenario_ids: string[];
  catalog_version: string;
  evaluator_version: string;
  total_count: number;
  completed_count: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
}
export interface EvaluationResult {
  id: string;
  run_id: string | null;
  batch_id: string | null;
  source: EvaluationSource;
  scenario_id: string;
  evaluator_version: string;
  created_at: string;
  passed: boolean;
  metrics: Record<string, number | null>;
  assertions: EvaluationAssertion[];
  latency_ms: number | null;
  human_wait_ms: number | null;
  observed: {
    ticket_id: string | null;
    request_text: string | null;
    ticket_status: TicketStatus | null;
    outcome: string | null;
    employee_name: string | null;
    repository_name: string | null;
  };
}
export interface EvaluationTrace {
  run: Record<string, unknown>;
  ticket: Record<string, unknown>;
  steps: Record<string, unknown>[];
  tools: Record<string, unknown>[];
  approvals: Record<string, unknown>[];
  initial_permission: Permission | null;
  final_permission: Permission | null;
}
export interface EvaluationDetail extends EvaluationResult {
  trace: EvaluationTrace;
  expected: EvaluationExpectedOutcome | null;
}
export interface EvaluationSummary {
  source: EvaluationSource;
  batch_id: string | null;
  evaluator_version: string;
  total_scenarios: number;
  total_results: number;
  passed_results: number;
  selected_scenarios: number | null;
  metrics: Record<string, number | null>;
  metric_samples: Record<string, number>;
  average_latency_ms: number | null;
  average_human_wait_ms: number | null;
}
