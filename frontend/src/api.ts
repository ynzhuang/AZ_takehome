// Types mirror backend/app/schemas.py (AskResponse) and service.summary().

export type Status = "answered" | "clarification_needed" | "declined" | "error";

export interface Interpretation {
  tool: string | null;
  route: string | null;
  params: Record<string, unknown>;
  interpreter: string;
  latency_ms: number | null;
  usage: { prompt_tokens?: number; completion_tokens?: number; cost?: number } | null;
  /** Set when the interpreter built on the previous question sent as context. */
  follow_up_to: string | null;
}

/** The last answered question and its interpretation, sent so follow-ups ("how about just CC-FIN-100?") work. */
export interface PreviousTurn {
  question: string;
  tool: string;
  params: Record<string, unknown>;
}

export interface EvidenceRow {
  invoice_number: string;
  invoice_date: string;
  vendor_id: string;
  vendor_name: string;
  country: string;
  vendor_status: string;
  description: string;
  amount: number;
  currency: string;
  fx_rate: number | null;
  fx_rate_date: string | null;
  amount_eur: number | null;
  cost_center: string;
  po_number: string | null;
  approved_by: string | null;
}

export interface ClauseRef {
  id: string;
  section: string;
  text: string;
}

export interface FxRateRef {
  currency: string;
  rate_date: string;
  rate: number;
  invoice_count: number | null;
}

export interface Check {
  clause: string;
  rule: string;
  result: "ok" | "flag" | "cannot_determine" | "not_applicable";
  label: string;
  detail: string;
}

export interface AskResponse {
  trace_id: string;
  status: Status;
  question: string;
  answer: string;
  result: Record<string, unknown> & { checks?: Check[] } | null;
  interpretation: Interpretation;
  evidence: { rows: EvidenceRow[]; row_count: number; clauses: ClauseRef[]; fx_rates: FxRateRef[]; fx_source: string | null };
  assumptions: string[];
  limitations: string[];
  clarification: { question: string; options: string[] } | null;
  error: { code: string; message: string } | null;
  versions: { data: string; policy: string; fx: string };
}

interface Block {
  invoice_count: number;
  total_eur: number;
}

export interface Summary extends Block {
  period: { start: string; end: string };
  vendor_count: number;
  credit_notes: Block;
  by_currency: (Block & { currency: string; native_total: number })[];
  by_vendor_status: (Block & { vendor_status: string; vendor_count: number })[];
  control_flags: { po_threshold_eur: number; missing_po_at_or_above_threshold: number; missing_approver: number; non_active_vendor: number };
  fx: { source: string; converted_invoices: number; prior_rate_used: number };
  definitions: string[];
  versions: { data: string; policy: string; fx: string };
}

export interface Health {
  status: string;
  interpreter: string;
  versions: { data: string; policy: string; fx: string };
}

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const ASK_TIMEOUT_MS = 60_000;

/** Error for anything that did not produce a well-formed API body (network down, proxy error, timeout). */
export class ApiUnavailable extends Error {}

async function getJson<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`);
  } catch {
    throw new ApiUnavailable("Could not reach the API. Is the backend running on port 8000?");
  }
  const body = await res.json().catch(() => null);
  if (body === null) throw new ApiUnavailable(`No valid response from the API (HTTP ${res.status}). Is the backend running?`);
  if (!res.ok) throw new ApiUnavailable(`The API returned an error: ${body?.error?.message ?? `HTTP ${res.status}`}`);
  return body as T;
}

export const getSummary = () => getJson<Summary>("/summary");
export const getHealth = () => getJson<Health>("/health");

/**
 * POST /ask. The backend returns an AskResponse body for every outcome, including errors
 * (HTTP 502/503), so those are returned as data. Only transport failures throw.
 */
export async function ask(question: string, context?: PreviousTurn): Promise<AskResponse> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ASK_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${BASE}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(context ? { question, context } : { question }),
      signal: controller.signal,
    });
  } catch (e) {
    throw new ApiUnavailable(
      controller.signal.aborted
        ? `No response within ${ASK_TIMEOUT_MS / 1000}s. No answer was produced.`
        : "Could not reach the API. Is the backend running on port 8000?",
    );
  } finally {
    clearTimeout(timer);
  }
  const body = await res.json().catch(() => null);
  if (body && typeof body.status === "string" && "interpretation" in body) return body as AskResponse;
  if (res.status === 422) throw new ApiUnavailable("The question was rejected: it must be 1–500 characters.");
  throw new ApiUnavailable(
    `No valid response from the API (HTTP ${res.status}). Is the backend running? No answer was produced.`,
  );
}

export const fmtEur = (n: number) =>
  `EUR ${n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const fmtNum = (n: number) => n.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
