from __future__ import annotations

import shutil
from datetime import date

import pytest

from app.data import DataValidationError, load_dataset
from app.fx import FxRateMissing, FxTable, to_eur
from app.policy import PolicyParseError, parse_policy

from .conftest import DATA_DIR

# ----------------------------------------------------------------------------- FX


def test_fx_exact_date_and_direction(fx):
    r = fx.lookup("GBP", date(2025, 2, 13))
    assert (r.rate, r.rate_date, r.is_fallback) == (0.83313, date(2025, 2, 13), False)
    # ECB quotes GBP per EUR, so EUR = GBP / rate
    assert to_eur(50350.62, r) == 60435.49


@pytest.mark.parametrize("requested, expected", [
    (date(2025, 1, 18), date(2025, 1, 17)),  # Saturday
    (date(2025, 4, 18), date(2025, 4, 17)),  # Good Friday (TARGET holiday)
    (date(2025, 4, 21), date(2025, 4, 17)),  # Easter Monday
    (date(2025, 12, 26), date(2025, 12, 24)),  # Boxing Day after Christmas
])
def test_fx_uses_most_recent_prior_rate(fx, requested, expected):
    r = fx.lookup("USD", requested)
    assert r.rate_date == expected and r.is_fallback


def test_fx_never_guesses(fx):
    with pytest.raises(FxRateMissing):  # 2025-01-01: no 2025 rate yet, and no 2024 rates on file
        fx.lookup("USD", date(2025, 1, 1))
    with pytest.raises(FxRateMissing):
        fx.lookup("JPY", date(2025, 3, 3))


def test_eur_is_identity(fx):
    assert fx.lookup("EUR", date(2025, 6, 1)).rate == 1.0


# ----------------------------------------------------------------------------- policy


def test_policy_parses_matrix_threshold_and_clauses(ctx):
    pol = ctx.policy
    assert pol.version_label == "FIN-POL-014 v3.2"
    assert pol.po_threshold_eur == 1000
    assert [(t.lower_exclusive, t.upper_inclusive, t.role) for t in pol.approval_tiers] == [
        (0.0, 5000.0, "Cost Center Owner"),
        (5000.0, 50000.0, "Department Head"),
        (50000.0, 250000.0, "Finance Director"),
        (250000.0, None, "CFO + Procurement Excellence review"),
    ]
    assert {"1", "2.1", "2.2", "2.3", "3", "3.1", "4.1", "5.1", "6.1", "7.2", "8.2", "9.1"} <= set(pol.clauses)


@pytest.mark.parametrize("amount, role", [
    (5000.00, "Cost Center Owner"), (5000.01, "Department Head"), (50000.00, "Department Head"),
    (50000.01, "Finance Director"), (250000.00, "Finance Director"), (250000.01, "CFO + Procurement Excellence review"),
])
def test_tier_boundaries_are_inclusive(ctx, amount, role):
    assert ctx.policy.tier_for(amount).role == role


def test_policy_change_flows_through():
    text = (DATA_DIR / "procurement_policy.md").read_text().replace("**Version:** 3.2", "**Version:** 3.3")
    text = text.replace("| 5,000.01 – 50,000 | Department Head |", "| 5,000.01 – 75,000 | Department Head |")
    text = text.replace("| 50,000.01 – 250,000 | Finance Director |", "| 75,000.01 – 250,000 | Finance Director |")
    pol = parse_policy(text)
    assert pol.version == "3.3" and pol.tier_for(60000).role == "Department Head"


def test_policy_with_gap_in_tiers_is_rejected():
    text = (DATA_DIR / "procurement_policy.md").read_text().replace("| 50,000.01 – 250,000 |", "| 60,000.01 – 250,000 |")
    with pytest.raises(PolicyParseError):
        parse_policy(text)


# ----------------------------------------------------------------------------- data


def test_dataset_loads_and_converts_everything(ctx):
    inv = ctx.data.invoices
    assert len(inv) == 140 and inv["amount_eur"].notna().all()
    assert (ctx.data.period_start, ctx.data.period_end) == (date(2025, 1, 1), date(2025, 12, 31))
    assert inv["po_number"].isna().sum() == 21 and inv["approved_by"].isna().sum() == 25
    assert (inv["currency"] != "EUR").sum() == 51
    assert ((inv["currency"] != "EUR") & (inv["fx_rate_date"] != inv["invoice_date"])).sum() == 16


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s + "INV-20001,2025-03-09,V-1002,ClinSupply Iberia S.L.,x,1.0,EUR,CC-IT-550,,a\n", "duplicate invoice_number"),
    (lambda s: s + "INV-29999,2025-03-09,V-9999,Unknown,x,1.0,EUR,CC-IT-550,,a\n", "vendor_id not in vendor master"),
    (lambda s: s + "INV-29998,2025-03-09,V-1002,ClinSupply Iberia S.L.,x,1.0,JPY,CC-IT-550,,a\n", "unsupported currency"),
    (lambda s: s + "INV-29997,2025-13-09,V-1002,ClinSupply Iberia S.L.,x,1.0,EUR,CC-IT-550,,a\n", "invalid invoice_date"),
    (lambda s: s + "INV-29996,2025-03-09,V-1002,ClinSupply Iberia S.L.,x,abc,EUR,CC-IT-550,,a\n", "non-numeric amount"),
])
def test_invalid_data_stops_startup(tmp_path, fx, mutate, message):
    shutil.copytree(DATA_DIR, tmp_path / "data")
    path = tmp_path / "data" / "invoices_2025_clean.csv"
    path.write_text(mutate(path.read_text()))
    with pytest.raises(DataValidationError, match=message):
        load_dataset(tmp_path / "data", fx)


def test_missing_rate_leaves_eur_empty_instead_of_guessing(fx):
    rates = {c: {d: v for d, v in fx._rates[c].items() if d.month != 2} for c in fx._rates}  # drop February
    data = load_dataset(DATA_DIR, FxTable(rates, version="test", source="test"))
    feb_non_eur = data.invoices[(data.invoices["currency"] != "EUR")
                                & data.invoices["invoice_date"].map(lambda d: d.month == 2 and d.day > 7)]
    assert len(feb_non_eur) > 0 and feb_non_eur["amount_eur"].isna().all()
