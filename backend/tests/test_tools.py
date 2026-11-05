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
