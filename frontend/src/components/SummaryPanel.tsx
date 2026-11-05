import { useCallback, useEffect, useState } from "react";
import { fmtEur, fmtNum, getSummary, type Summary } from "../api";

type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: Summary };

interface Props {
  onAsk: (question: string) => void;
  busy: boolean;
}

/** Loads GET /summary on startup (no LLM involved), so there is something on screen before the first question. */
export function SummaryPanel({ onAsk, busy }: Props) {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    setState({ kind: "loading" });
    getSummary()
      .then((data) => setState({ kind: "ready", data }))
      .catch((e: Error) => setState({ kind: "error", message: e.message }));
  }, []);

  useEffect(load, [load]);

  if (state.kind === "loading") {
    return (
      <section className="panel summary" aria-busy="true">
        <h2>2025 invoice overview</h2>
        <p className="muted">Loading summary…</p>
      </section>
    );
  }
  if (state.kind === "error") {
    return (
      <section className="panel summary">
        <h2>2025 invoice overview</h2>
        <div className="callout error" role="alert">
          <strong>Summary unavailable.</strong> {state.message}
          <div>
            <button onClick={load}>Retry</button>
          </div>
        </div>
      </section>
    );
  }

  const s = state.data;
  const flags = [
    { n: s.control_flags.missing_po_at_or_above_threshold, label: `no PO at or above ${fmtEur(s.control_flags.po_threshold_eur)} (§2.1)`,
      q: "Which invoices of EUR 1,000 or more have no PO, and who should they be flagged to?" },
    { n: s.control_flags.missing_approver, label: "no approver recorded (§3.1)", q: "Which invoices are missing an approver?" },
    { n: s.control_flags.non_active_vendor, label: "from INACTIVE / ON_HOLD vendors (§4.1)",
      q: "Which invoices are from vendors that are not active?" },
  ];

  return (
    <section className="panel summary">
      <h2>2025 invoice overview</h2>
      <div className="kpi">
        <div className="kpi-value">{fmtEur(s.total_eur)}</div>
        <div className="muted">
          net spend · {s.invoice_count} invoices · {s.vendor_count} vendors · {s.period.start} to {s.period.end}
        </div>
        <div className="muted small">
          Includes {s.credit_notes.invoice_count} credit notes ({fmtEur(s.credit_notes.total_eur)}).
        </div>
      </div>

      <h3>Invoices needing follow-up</h3>
      <ul className="flags">
        {flags.map((f) => (
          <li key={f.label}>
            <span className="flag-count">{f.n}</span> {f.label}{" "}
            <button className="link" disabled={busy} onClick={() => onAsk(f.q)}>
              Show
            </button>
          </li>
        ))}
      </ul>

      <h3>By currency</h3>
      <table>
        <thead>
          <tr><th>Currency</th><th className="num">Invoices</th><th className="num">Native total</th><th className="num">EUR</th></tr>
        </thead>
        <tbody>
          {s.by_currency.map((c) => (
            <tr key={c.currency}>
              <td>{c.currency}</td><td className="num">{c.invoice_count}</td>
              <td className="num">{fmtNum(c.native_total)}</td><td className="num">{fmtNum(c.total_eur)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>By vendor status</h3>
      <table>
        <thead>
          <tr><th>Status</th><th className="num">Vendors</th><th className="num">Invoices</th><th className="num">EUR</th></tr>
        </thead>
        <tbody>
          {s.by_vendor_status.map((v) => (
            <tr key={v.vendor_status}>
              <td>{v.vendor_status}</td><td className="num">{v.vendor_count}</td>
              <td className="num">{v.invoice_count}</td><td className="num">{fmtNum(v.total_eur)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Vendor status is an undated snapshot from the vendor master.</p>

      <details>
        <summary>Definitions and sources</summary>
        <ul className="small">
          {s.definitions.map((d) => <li key={d}>{d}</li>)}
          <li>
            {s.fx.source}: {s.fx.converted_invoices} invoices converted, {s.fx.prior_rate_used} using the most recent
            prior rate.
          </li>
          <li>Policy {s.versions.policy} · data {s.versions.data} · FX {s.versions.fx}</li>
        </ul>
      </details>
    </section>
  );
}
