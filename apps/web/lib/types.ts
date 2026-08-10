export type SessionStatus = "active" | "archived";
export type MessageRole = "system" | "user" | "assistant" | "tool";
export type RunStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancel_requested"
  | "cancelled";

export type ProviderName =
  | "openai"
  | "deepseek"
  | "anthropic"
  | "gemini"
  | "openai_compatible";

export type CursorPage<T> = {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
  limit: number;
};

export type Session = {
  session_id: string;
  user_id: string;
  title: string | null;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
};

export type MessageSummary = {
  message_id: string;
  session_id: string;
  run_id: string | null;
  parent_message_id: string | null;
  role: MessageRole;
  content_type: string;
  content_preview: string | null;
  content_uri: string | null;
  sequence: number;
  token_count: number | null;
  created_at: string;
};

export type Message = Omit<MessageSummary, "content_preview"> & {
  content_text: string | null;
  metadata_json: Record<string, unknown> | null;
};

export type Run = {
  run_id: string;
  user_id: string;
  session_id: string | null;
  user_request: string;
  run_type: string;
  status: RunStatus;
  budget_limit: string | number | null;
  cost_used: string | number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ModelAttempt = {
  attempt_id: string;
  run_id: string;
  task_id: string | null;
  provider: string;
  model: string;
  request_type: string;
  retry_count: number;
  status: "started" | "completed" | "failed" | "timed_out" | "cancelled";
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  estimated_cost: string | number | null;
  latency_ms: number | null;
  error_code: string | null;
  started_at: string;
  completed_at: string | null;
};

export type RunDetail = Run & {
  tasks: Array<Record<string, unknown>>;
  attempts: ModelAttempt[];
  artifacts: Array<Record<string, unknown>>;
  tasks_has_more: boolean;
  attempts_has_more: boolean;
  artifacts_has_more: boolean;
};

export type ResponseUsage = {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_tokens: number | null;
  estimated_cost: string | null;
};

export type ResponseResult = {
  id: string;
  object: "response";
  status: "completed";
  provider: ProviderName;
  model: string;
  output_text: string | null;
  tool_calls: Array<Record<string, unknown>>;
  structured_output: Record<string, unknown> | Array<unknown> | null;
  finish_reason: string;
  usage: ResponseUsage;
  latency_ms: number;
  provider_request_id: string | null;
};

export type StreamEvent = {
  type: string;
  sequence: number;
  data: {
    delta?: string;
    attempt_id?: string;
    response?: ResponseResult;
    usage?: ResponseUsage;
    error?: { type?: string; message?: string };
    [key: string]: unknown;
  };
};
