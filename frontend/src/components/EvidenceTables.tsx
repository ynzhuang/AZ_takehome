import type { ClauseRef, EvidenceRow, FxRateRef } from "../api";
import { fmtNum } from "../api";

const missing = <span className="missing">missing</span>;

export function InvoiceTable({ rows }: { rows: EvidenceRow[] }) {
  return (
    <div className="table-scroll">
      <table className="evidence">
        <thead>
          <tr>
            <th>Invoice</th><th>Date</th><th>Vendor</th><th>Country</th><th>Vendor status</th><th>Description</th>
            <th className="num">Amount</th><th>Ccy</th><th className="num">ECB rate (date)</th><th className="num">EUR</th>
            <th>Cost centre</th><th>PO</th><th>Approver</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.invoice_number} className={r.amount < 0 ? "credit" : undefined}>
              <td>{r.invoice_number}</td>
              <td>{r.invoice_date}</td>
              <td>{r.vendor_name} ({r.vendor_id})</td>
              <td>{r.country}</td>
              <td className={r.vendor_status !== "ACTIVE" ? "warn" : undefined}>{r.vendor_status}</td>
              <td>{r.description}</td>
              <td className="num">{fmtNum(r.amount)}</td>
              <td>{r.currency}</td>
              <td className="num">
                {r.currency === "EUR" ? "—" : r.fx_rate != null ? `${r.fx_rate} (${r.fx_rate_date})` : missing}
              </td>
              <td className="num">{r.amount_eur != null ? fmtNum(r.amount_eur) : missing}</td>
              <td>{r.cost_center}</td>
              <td>{r.po_number ?? missing}</td>
              <td>{r.approved_by ?? missing}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ClauseList({ clauses }: { clauses: ClauseRef[] }) {
  return (
    <div className="clauses">
      {clauses.map((c) => (
        <blockquote key={c.id}>
          <div className="clause-id">§{c.id} · {c.section}</div>
          <div className="clause-text">{c.text}</div>
        </blockquote>
      ))}
    </div>
  );
}

export function FxTable({ rates, source }: { rates: FxRateRef[]; source: string | null }) {
  return (
    <>
      <div className="table-scroll short">
        <table>
          <thead>
            <tr><th>Currency</th><th>ECB rate date</th><th className="num">Rate (per 1 EUR)</th><th className="num">Invoices</th></tr>
          </thead>
          <tbody>
            {rates.map((r) => (
              <tr key={`${r.currency}-${r.rate_date}`}>
                <td>{r.currency}</td><td>{r.rate_date}</td><td className="num">{r.rate}</td>
                <td className="num">{r.invoice_count ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {source && <p className="small muted">Source: {source}. EUR amount = amount ÷ rate.</p>}
    </>
  );
}
