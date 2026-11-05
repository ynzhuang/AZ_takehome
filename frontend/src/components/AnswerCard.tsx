import type { AskResponse, Check, Status } from "../api";
import { fmtEur } from "../api";
import { ClauseList, FxTable, InvoiceTable } from "./EvidenceTables";

export type Turn =
  | { id: number; question: string; state: "pending" }
  | { id: number; question: string; state: "done"; response: AskResponse }
  | { id: number; question: string; state: "failed"; error: string };

interface Props {
  turn: Turn;
  busy: boolean;
  onAsk: (question: string) => void;
  onEdit: (question: string) => void;
}

const STATUS_TEXT: Record<Status, string> = {
  answered: "Answered",
  clarification_needed: "Needs clarification",
  declined: "Cannot answer",
  error: "Error: no answer produced",
};

const TOOL_TEXT: Record<string, string> = {
  spend: "Spend calculation",
  exceptions: "Policy exception list",
  approval_tier: "Approval tier lookup",
  invoice_check: "Invoice check",
  policy_clauses: "Policy quote",
  clarify: "Clarification",
  decline: "Decline",
};

const ROUTE_TEXT: Record<string, string> = {
  data: "invoice data",
  policy: "policy",
  "data+policy": "invoice data + policy",
  none: "no data used",
};

function paramText(v: unknown): string {
  return Array.isArray(v) ? v.join(", ") : String(v);
}

export function AnswerCard({ turn, busy, onAsk, onEdit }: Props) {
  return (
    <article className="turn">
      <div className="question">
        <span className="who">You</span> {turn.question}
      </div>
      {turn.state === "pending" && (
        <div className="card pending" aria-busy="true">
          <span className="spinner" aria-hidden="true" /> Interpreting the question and running the calculation…
        </div>
      )}
      {turn.state === "failed" && (
        <div className="card status-error" role="alert">
          <span className="badge status-error">Error: no answer produced</span>
          <p>{turn.error}</p>
          <button disabled={busy} onClick={() => onAsk(turn.question)}>Retry</button>
        </div>
      )}
      {turn.state === "done" && <Response r={turn.response} busy={busy} onAsk={onAsk} onEdit={onEdit} />}
    </article>
  );
}

function Response({ r, busy, onAsk, onEdit }: { r: AskResponse; busy: boolean; onAsk: Props["onAsk"]; onEdit: Props["onEdit"] }) {
  const it = r.interpretation;
  const params = Object.entries(it.params ?? {}).filter(([k]) => !["question", "options", "explanation"].includes(k));
  const checks = r.result?.checks as Check[] | undefined;

  return (
    <div className={`card status-${r.status}`}>
      <div className="card-head">
        <span className={`badge status-${r.status}`}>{STATUS_TEXT[r.status]}</span>
        {it.tool && (
          <span className="interp" title="How the question was interpreted (structured, from the LLM's tool call)">
            Interpreted as: <strong>{TOOL_TEXT[it.tool] ?? it.tool}</strong>
            {it.route && it.route !== "none" && <> using {ROUTE_TEXT[it.route] ?? it.route}</>}
            {params.map(([k, v]) => (
              <code key={k} className="chip">{k} = {paramText(v)}</code>
            ))}
          </span>
        )}
      </div>

      {it.follow_up_to && (
        <div className="followup">
          Follow-up to: “{it.follow_up_to}”. The settings above include what was carried over.
        </div>
      )}

      {/* 1. Key findings. For invoice checks the per-rule lines are shown as a table instead of repeated text. */}
      <pre className="answer">{checks ? r.answer.split("\n")[0] : r.answer}</pre>

      {checks && <ChecksTable checks={checks} />}

      {r.status === "clarification_needed" && r.clarification && (
        <div className="clarify">
          {r.clarification.options.length > 0 ? (
            <>
              <p className="muted">Choose what you meant (each option is sent as a new question):</p>
              {r.clarification.options.map((o) => (
                <button key={o} className="option" disabled={busy} onClick={() => onAsk(o)}>{o}</button>
              ))}
            </>
          ) : (
            <button disabled={busy} onClick={() => onEdit(r.question)}>Edit my question</button>
          )}
        </div>
      )}

      {r.status === "error" && (
        <div className="error-actions">
          <p className="small muted">
            Error code <code>{r.error?.code}</code>. Nothing above is a partial result.
          </p>
          <button disabled={busy} onClick={() => onAsk(r.question)}>Retry</button>
        </div>
      )}

      {/* 2. Limitations: what the answer cannot tell you */}
      {r.limitations.length > 0 && (
        <div className="callout warning">
          <strong>{r.status === "declined" ? "Why" : "Limitations: what this answer cannot tell you"}</strong>
          <ul>{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        </div>
      )}

      {/* 3. Assumptions and definitions */}
      {r.assumptions.length > 0 && (
        <div className="callout info">
          <strong>Assumptions and definitions applied</strong>
          <ul>{r.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
        </div>
      )}

      {/* 4. Detailed evidence, collapsed by default */}
      {r.evidence.row_count > 0 && (
        <details>
          <summary>
            Invoice rows used ({r.evidence.row_count}
            {typeof r.result?.shown_total_eur === "number" && <>, {fmtEur(r.result.shown_total_eur as number)}</>})
          </summary>
          <InvoiceTable rows={r.evidence.rows} />
        </details>
      )}
      {r.evidence.clauses.length > 0 && (
        <details>
          <summary>Policy clauses cited ({r.evidence.clauses.map((c) => `§${c.id}`).join(", ")})</summary>
          <ClauseList clauses={r.evidence.clauses} />
        </details>
      )}
      {r.evidence.fx_rates.length > 0 && (
        <details>
          <summary>Exchange rates used ({r.evidence.fx_rates.length})</summary>
          <FxTable rates={r.evidence.fx_rates} source={r.evidence.fx_source} />
        </details>
      )}
      <details className="tech">
        <summary>Technical details</summary>
        <ul className="small">
          <li>Interpreter: {it.interpreter}{it.latency_ms != null && <> · {it.latency_ms} ms</>}
            {it.usage?.prompt_tokens != null && <> · {it.usage.prompt_tokens} + {it.usage.completion_tokens} tokens</>}
            {it.usage?.cost != null && <> · ${it.usage.cost.toFixed(5)}</>}</li>
          <li>Tool call: <code>{it.tool ?? "none"}({JSON.stringify(it.params)})</code></li>
          <li>Versions: policy {r.versions.policy} · data {r.versions.data} · FX {r.versions.fx}</li>
          <li>Trace ID: <code>{r.trace_id}</code></li>
        </ul>
      </details>
    </div>
  );
}

function ChecksTable({ checks }: { checks: Check[] }) {
  return (
    <table className="checks">
      <thead>
        <tr><th>Policy</th><th>Rule</th><th>Result</th><th>Detail</th></tr>
      </thead>
      <tbody>
        {checks.map((c) => (
          <tr key={c.clause + c.rule}>
            <td>§{c.clause}</td>
            <td>{c.rule}</td>
            <td><span className={`result result-${c.result}`}>{c.label}</span></td>
            <td>{c.detail}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
