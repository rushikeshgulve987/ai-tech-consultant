export interface Config {
  port: number;
  dbPath: string;
  kbUrl: string;
  kbApiKey: string | undefined;
  kbTimeoutMs: number;
  classifyModel: string;
  draftModel: string;
  llmMode: "anthropic" | "mock";
  autonomyThreshold: number;
  auditEveryN: number;
  prices: Record<string, [number, number]>; // USD per million tokens [in, out]; empty = cost not reported
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return {
    port: Number(env.PORT ?? 3002),
    dbPath: env.INBOX_DB_PATH ?? "data/inbox.sqlite3",
    kbUrl: env.KB_URL ?? "http://localhost:8001",
    kbApiKey: env.KB_API_KEY || undefined,
    kbTimeoutMs: Number(env.KB_TIMEOUT_MS ?? 20000),
    classifyModel: env.CLASSIFY_MODEL ?? "claude-haiku-5-5",
    draftModel: env.DRAFT_MODEL ?? "claude-sonnet-5-5",
    llmMode: env.LLM_MODE === "mock" || !env.ANTHROPIC_API_KEY ? "mock" : "anthropic",
    autonomyThreshold: Number(env.AUTONOMY_THRESHOLD ?? 20),
    auditEveryN: Number(env.AUDIT_EVERY_N ?? 5),
    prices: JSON.parse(env.MODEL_PRICES_JSON ?? "{}") as Record<string, [number, number]>,
  };
}
