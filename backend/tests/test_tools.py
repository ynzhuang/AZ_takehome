from __future__ import annotations

from datetime import date

import pytest

from app.schemas import ApprovalTierParams, ExceptionsParams, InvoiceCheckParams, PolicyClausesParams, SpendParams
from app.tools import (
    InvalidParams, NeedsClarification, NotFound, OutOfPeriod, approval_tier, exceptions, invoice_check,
    policy_clauses, spend,
)


def total(ctx, **kw) -> float:
    return spend(ctx, SpendParams(**kw)).result["total_eur"]


# ----------------------------------------------------------------------------- spend


def test_spend_partitions_add_up(ctx):
    grand = total(ctx)
    countries = ctx.data.catalog()["countries"]
    assert abs(sum(total(ctx, countries=[c]) for c in countries) - grand) < 0.01
    by_vendor = spend(ctx, SpendParams(group_by="vendor")).result["groups"]
    assert abs(sum(g["total_eur"] for g in by_vendor) - grand) < 0.01
    assert sum(g["invoice_count"] for g in by_vendor) == 140


def test_spend_is_net_of_credit_notes(ctx):
    inv = ctx.data.invoices
    helix = spend(ctx, SpendParams(vendor_ids=["V-1005"]))
    assert helix.result["total_eur"] == round(inv.loc[inv["vendor_id"] == "V-1005", "amount_eur"].sum(), 2)
    assert any("credit note" in a for a in helix.assumptions)


def test_top_n_evidence_is_rows_of_shown_groups(ctx):
    out = spend(ctx, SpendParams(group_by="vendor", top_n=5))
    keys = [g["key"] for g in out.result["groups"]]
    assert len(keys) == 5 and set(out.rows["vendor_id"]) == set(keys)
    assert abs(out.rows["amount_eur"].sum() - out.result["shown_total_eur"]) < 0.01
    totals = [g["total_eur"] for g in out.result["groups"]]
    assert totals == sorted(totals, reverse=True)


def test_spain_includes_inactive_vendor_with_disclosure(ctx):
    out = spend(ctx, SpendParams(countries=["Spain"]))
    assert out.result["invoice_count"] == 49
    assert set(out.rows["vendor_id"]) == {"V-1002", "V-1006", "V-1009"}
    assert any("INACTIVE: 17" in lim for lim in out.limitations)


def test_unknown_filter_values_are_rejected(ctx):
    with pytest.raises(InvalidParams):
        spend(ctx, SpendParams(countries=["Atlantis"]))
    with pytest.raises(InvalidParams):
        spend(ctx, SpendParams(top_n=3))  # top_n without group_by


@pytest.mark.parametrize("d_from, d_to", [(date(2024, 1, 1), date(2024, 12, 31)), (date(2024, 7, 1), date(2025, 6, 30))])
def test_periods_not_fully_covered_are_declined(ctx, d_from, d_to):
    with pytest.raises(OutOfPeriod):
        spend(ctx, SpendParams(date_from=d_from, date_to=d_to))


# ----------------------------------------------------------------------------- exceptions


def test_missing_po(ctx):
    out = exceptions(ctx, ExceptionsParams(check="missing_po"))
    assert out.result["count"] == 21 and out.rows["po_number"].isna().all()
    assert (out.rows["amount_eur"] >= 1000).all()
    assert out.result["recurring_service_count"] == 10
    assert any("potential exceptions, not confirmed violations" in lim for lim in out.limitations)
    assert out.clause_ids == ["2.1", "2.2", "2.3", "9.1"]


def test_missing_approver(ctx):
    out = exceptions(ctx, ExceptionsParams(check="missing_approver"))
    assert out.result["count"] == 25
    assert out.result["also_missing_po"] == ["INV-20042", "INV-20123", "INV-20128"]


def test_non_active_vendor(ctx):
    out = exceptions(ctx, ExceptionsParams(check="non_active_vendor"))
    assert out.result["count"] == 29
    assert {g["key"]: g["invoice_count"] for g in out.result["groups"]} == {"V-1006": 17, "V-1010": 12}
    assert any("undated snapshot" in lim for lim in out.limitations)


# ----------------------------------------------------------------------------- approval tier / invoice check


def test_approval_tier_eur(ctx):
    out = approval_tier(ctx, ApprovalTierParams(amount=60000, currency="EUR"))
    assert out.result["required_role"] == "Finance Director" and out.clause_ids == ["3"]
    assert any("no individual can be named" in lim for lim in out.limitations)


def test_approval_tier_assumes_eur_when_currency_missing(ctx):
    out = approval_tier(ctx, ApprovalTierParams(amount=60000))
    assert out.result["required_role"] == "Finance Director"
    assert out.assumptions == ["No currency stated; EUR assumed."]


def test_approval_tier_non_eur_without_date(ctx):
    with pytest.raises(NeedsClarification):  # USD 55,000 spans 50k across 2025 rates
        approval_tier(ctx, ApprovalTierParams(amount=55000, currency="USD"))
    out = approval_tier(ctx, ApprovalTierParams(amount=10000, currency="USD"))  # same tier at any rate
    assert out.result["required_role"] == "Department Head" and out.result["amount_eur_range"]


def test_approval_tier_non_eur_with_date(ctx):
    out = approval_tier(ctx, ApprovalTierParams(amount=55000, currency="USD", invoice_date=date(2025, 1, 13)))
    assert out.result["amount_eur"] > 50000 and out.result["required_role"] == "Finance Director"


def test_invoice_check_never_concludes_on_authority(ctx):
    out = invoice_check(ctx, InvoiceCheckParams(invoice_number="INV-20008"))
    assert out.result["amount_eur"] == 60435.49 and out.result["required_role"] == "Finance Director"
    authority = next(c for c in out.result["checks"] if c["clause"] == "3")
    assert authority["result"] == "cannot_determine"


def test_invoice_check_credit_note(ctx):
    out = invoice_check(ctx, InvoiceCheckParams(invoice_number="INV-20029"))
    assert out.result["required_role"] is None
    assert out.result["checks"][0]["clause"] == "6.1" and out.result["checks"][0]["result"] == "cannot_determine"


def test_invoice_check_not_found(ctx):
    with pytest.raises(NotFound):
        invoice_check(ctx, InvoiceCheckParams(invoice_number="INV-99999"))


def test_policy_clauses_reject_invented_ids(ctx):
    assert policy_clauses(ctx, PolicyClausesParams(clause_ids=["6.1"])).clause_ids == ["6.1"]
    with pytest.raises(InvalidParams):
        policy_clauses(ctx, PolicyClausesParams(clause_ids=["6.4"]))


def test_invoice_check_labels_do_not_overclaim(ctx):
    out = invoice_check(ctx, InvoiceCheckParams(invoice_number="INV-20008"))
    labels = {c["result"]: c["label"] for c in out.result["checks"]}
    assert labels["ok"] == "No issue detected in checked fields"
    assert labels["cannot_determine"] == "Cannot determine from available data"


def test_top_n_notes_describe_the_groups_shown(ctx):
    """Notes (FX, credit notes, vendor status) must describe the evidence rows, not the whole dataset."""
    out = spend(ctx, SpendParams(group_by="vendor", top_n=5))
    rows = out.rows
    notes = " ".join(out.assumptions + out.limitations)
    assert (rows["amount"] < 0).sum() == 0  # both credit notes belong to Helix (V-1005), not in the top 5
    assert "Includes 2 credit note" not in notes
    assert "from vendors not shown" in notes  # the overall total still includes them, and says so
    non_active = rows[rows["vendor_status"] != "ACTIVE"]
    assert len(non_active) == 17
    assert any(lim.startswith("Includes 17 invoice(s) (EUR 1,084,833.66)") and "ON_HOLD" not in lim for lim in out.limitations)
    assert f"{int((rows['currency'] != 'EUR').sum())} non-EUR invoice(s) converted" in notes
    assert "cover all 140 invoices" in notes


def test_missing_po_for_one_cost_centre(ctx):
    """Follow-up regression (T17): the deterministic answer the follow-up should produce."""
    out = exceptions(ctx, ExceptionsParams(check="missing_po", cost_centers=["CC-FIN-100"]))
    assert out.result["count"] == 10 and out.result["total_eur"] == 383083.04
    assert set(out.rows["cost_center"]) == {"CC-FIN-100"} and out.rows["po_number"].isna().all()


# ----------------------------------------------------------------------------- third cold review: missing values


def test_invoice_check_flags_missing_po_and_approver_for_all_140_invoices(ctx):
    """Missing values load as NaN, which is truthy: every invoice is checked against the raw CSV, not the app's frame."""
    import pandas as pd

    from .conftest import DATA_DIR

    raw = pd.read_csv(DATA_DIR / "invoices_2025_clean.csv", dtype=str, keep_default_na=False).set_index("invoice_number")
    status = pd.read_csv(DATA_DIR / "vendor_master.csv", dtype=str).set_index("vendor_id")["status"]
    assert len(raw) == 140
    for number, row in raw.iterrows():
        out = invoice_check(ctx, InvoiceCheckParams(invoice_number=number))
        checks = {c["clause"]: c for c in out.result["checks"]}
        details = " ".join(c["detail"] for c in out.result["checks"]).lower()
        assert "nan" not in details.replace(".", " ").split(), number
        assert checks["4.1"]["result"] == ("ok" if status[row.vendor_id] == "ACTIVE" else "flag"), number
        if float(row.amount) < 0:
            continue  # credit notes: no approval or PO check (§6.1)
        assert checks["3.1"]["result"] == ("ok" if row.approved_by else "flag"), number
        assert out.result["approved_by"] == (row.approved_by or None), number
        expected_po = ("ok" if row.po_number else "flag") if out.result["amount_eur"] >= 1000 else "not_applicable"
        assert checks["2.1"]["result"] == expected_po, number


def test_invoice_check_inv_20005_missing_approver(ctx):
    checks = {c["clause"]: c for c in invoice_check(ctx, InvoiceCheckParams(invoice_number="INV-20005")).result["checks"]}
    assert checks["3.1"]["result"] == "flag" and checks["3.1"]["label"] == "Flag for review"
    assert "No approver recorded" in checks["3.1"]["detail"]
    assert checks["2.1"]["result"] == "ok" and "PO-79125" in checks["2.1"]["detail"]


def test_invoice_check_inv_20077_missing_po(ctx):
    checks = {c["clause"]: c for c in invoice_check(ctx, InvoiceCheckParams(invoice_number="INV-20077")).result["checks"]}
    assert checks["2.1"]["result"] == "flag" and "No PO number" in checks["2.1"]["detail"]
    assert checks["3.1"]["result"] == "ok" and "l.chen" in checks["3.1"]["detail"]
    assert checks["4.1"]["result"] == "flag"  # Crown Travel Partners is ON_HOLD
