"""Independent expected answers for the evaluation (design §3.1).

Deliberately does NOT import the application. It re-implements the definitions from
docs/design.md §2.3 with plain pandas, reading the raw files directly:
- spend = net invoiced EUR, credit notes included, all statuses;
- EUR = amount / ECB rate on the invoice date, else the most recent prior rate (<= 7 days), rounded to cents;
- approval tiers hand-copied from procurement_policy.md §3 (upper bounds inclusive).
The only input shared with the app is the data files themselves (including the ECB rate file).
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"

# Hand-copied from procurement_policy.md §3 (not parsed): (upper bound inclusive, role)
APPROVAL_MATRIX = [(5000.00, "Cost Center Owner"), (50000.00, "Department Head"),
                   (250000.00, "Finance Director"), (float("inf"), "CFO + Procurement Excellence review")]
PO_THRESHOLD_EUR = 1000.00


def required_role(amount_eur: float) -> str:
    return next(role for upper, role in APPROVAL_MATRIX if amount_eur <= upper)


def load() -> pd.DataFrame:
    inv = pd.read_csv(DATA / "invoices_2025_clean.csv", keep_default_na=False)
    ven = pd.read_csv(DATA / "vendor_master.csv")
    ecb = pd.read_csv(DATA / "fx" / "ecb_exr_2025.csv", usecols=["CURRENCY", "TIME_PERIOD", "OBS_VALUE"])

    inv["invoice_date"] = pd.to_datetime(inv["invoice_date"])
    ecb["TIME_PERIOD"] = pd.to_datetime(ecb["TIME_PERIOD"])
    inv = inv.sort_values("invoice_date")
    ecb = ecb.sort_values("TIME_PERIOD").rename(columns={"CURRENCY": "currency", "TIME_PERIOD": "rate_date", "OBS_VALUE": "rate"})
    merged = pd.merge_asof(inv, ecb, left_on="invoice_date", right_on="rate_date", by="currency",
                           direction="backward", tolerance=pd.Timedelta(days=7))
    merged.loc[merged["currency"] == "EUR", "rate"] = 1.0
    if merged["rate"].isna().any():
        raise RuntimeError(f"Oracle: missing FX for {list(merged.loc[merged['rate'].isna(), 'invoice_number'])}")
    merged["eur"] = (merged["amount"] / merged["rate"]).round(2)
    merged = merged.merge(ven[["vendor_id", "country", "category", "status"]], on="vendor_id", how="left")
    return merged.set_index("invoice_number", drop=False)


def policy_clause_ids() -> set[str]:
    text = (DATA / "procurement_policy.md").read_text()
    ids = set(re.findall(r"^(\d+\.\d+) ", text, flags=re.M))
    ids |= set(re.findall(r"^## (\d+)\. ", text, flags=re.M))
    return ids


def expected() -> dict[str, dict]:
    df = load()
    by_vendor = df.groupby("vendor_id")["eur"].sum().round(2).sort_values(ascending=False)
    top5 = by_vendor.head(5)
    spain = df[df["country"] == "Spain"]
    no_po = df[(df["po_number"] == "") & (df["eur"] >= PO_THRESHOLD_EUR)]
    no_appr = df[df["approved_by"] == ""]
    inv8 = df.loc["INV-20008"]
    return {
        "T1": {"top5": [(v, round(float(t), 2)) for v, t in top5.items()],
               "total_eur": round(float(df["eur"].sum()), 2),
               "rows": set(df[df["vendor_id"].isin(top5.index)]["invoice_number"])},
        "T2": {"total_eur": round(spain["eur"].sum(), 2), "rows": set(spain["invoice_number"])},
        "T3": {"role": required_role(60000.00)},
        "T4": {"rows": set(no_po["invoice_number"]), "total_eur": round(no_po["eur"].sum(), 2)},
        "T6": {"amount_eur": float(inv8["eur"]), "role": required_role(float(inv8["eur"])),
               "rows": {"INV-20008"}, "approver": inv8["approved_by"]},
        "T11": {"rows": set(no_appr["invoice_number"]), "total_eur": round(no_appr["eur"].sum(), 2),
                "also_missing_po": sorted(no_appr[no_appr["po_number"] == ""]["invoice_number"])},
        "T5_category": {"total_eur": round(df[df["category"] == "Consulting"]["eur"].sum(), 2),
                        "rows": set(df[df["category"] == "Consulting"]["invoice_number"])},
        "T5_description": {"total_eur": round(df[df["description"].str.contains("consulting", case=False)]["eur"].sum(), 2),
                           "rows": set(df[df["description"].str.contains("consulting", case=False)]["invoice_number"])},
        "_eur_by_invoice": df["eur"].to_dict(),
        "_clause_ids": policy_clause_ids(),
        "_approvers": sorted(set(df["approved_by"]) - {""}),
    }


if __name__ == "__main__":
    for k, v in expected().items():
        if not k.startswith("_"):
            print(k, {kk: (f"{len(vv)} rows" if isinstance(vv, set) else vv) for kk, vv in v.items()})
