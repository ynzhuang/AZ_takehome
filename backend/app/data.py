"""Invoice and vendor data: load, validate, join, convert to EUR (design §2.1, §2.3).

Validation runs at startup. Structural problems (missing columns, duplicates, unknown
vendors, bad dates/amounts, unsupported currencies) stop the app from starting.
A missing FX rate does not: the invoice keeps amount_eur = None and any tool that
needs it fails with an explicit error instead of returning an incomplete total.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from .fx import FxRateMissing, FxTable, to_eur

INVOICE_COLUMNS = [
    "invoice_number", "invoice_date", "vendor_id", "vendor_name", "description",
    "amount", "currency", "cost_center", "po_number", "approved_by",
]
VENDOR_COLUMNS = ["vendor_id", "vendor_name", "country", "category", "status"]
VENDOR_STATUSES = {"ACTIVE", "INACTIVE", "ON_HOLD"}


class DataValidationError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("Data validation failed:\n- " + "\n- ".join(problems))
        self.problems = problems


class MissingFxError(Exception):
    """Raised by tools when invoices they need could not be converted to EUR."""

    def __init__(self, invoice_numbers: list[str]):
        super().__init__(
            f"No ECB rate available for {len(invoice_numbers)} invoice(s): {', '.join(invoice_numbers[:10])}"
        )
        self.invoice_numbers = invoice_numbers


@dataclass
class Dataset:
    invoices: pd.DataFrame  # one row per invoice, enriched with vendor attributes and EUR amounts
    vendors: pd.DataFrame
    period_start: date
    period_end: date
    version: str

    def catalog(self) -> dict[str, list[str]]:
        """Allowed filter values, used to constrain and validate LLM tool parameters."""
        inv = self.invoices
        return {
            "vendor_ids": sorted(self.vendors["vendor_id"]),
            "countries": sorted(self.vendors["country"].unique()),
            "categories": sorted(self.vendors["category"].unique()),
            "vendor_statuses": sorted(VENDOR_STATUSES),
            "currencies": sorted(inv["currency"].unique()),
            "cost_centers": sorted(inv["cost_center"].unique()),
            "descriptions": sorted(inv["description"].unique()),
        }

    def vendor_label(self, vendor_id: str) -> str:
        name = self.vendors.set_index("vendor_id").at[vendor_id, "vendor_name"]
        return f"{name} ({vendor_id})"


def _read_csv(path: Path, columns: list[str], problems: list[str]) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in columns if c not in df.columns]
    if missing:
        problems.append(f"{path.name}: missing columns {missing}")
    return df.apply(lambda s: s.str.strip())


def load_dataset(data_dir: Path, fx: FxTable) -> Dataset:
    problems: list[str] = []
    inv_path, ven_path = data_dir / "invoices_2025_clean.csv", data_dir / "vendor_master.csv"
    inv = _read_csv(inv_path, INVOICE_COLUMNS, problems)
    ven = _read_csv(ven_path, VENDOR_COLUMNS, problems)
    if problems:
        raise DataValidationError(problems)

    # Vendors
    if ven["vendor_id"].duplicated().any():
        problems.append(f"vendor_master: duplicate vendor_id {sorted(ven.loc[ven['vendor_id'].duplicated(), 'vendor_id'])}")
    bad_status = sorted(set(ven["status"]) - VENDOR_STATUSES)
    if bad_status:
        problems.append(f"vendor_master: unknown status values {bad_status}")

    # Invoices
    if (inv["invoice_number"] == "").any():
        problems.append("invoices: empty invoice_number")
    dups = sorted(inv.loc[inv["invoice_number"].duplicated(), "invoice_number"])
    if dups:
        problems.append(f"invoices: duplicate invoice_number {dups}")
    dates = pd.to_datetime(inv["invoice_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        problems.append(f"invoices: invalid invoice_date for {list(inv.loc[dates.isna(), 'invoice_number'])}")
    amounts = pd.to_numeric(inv["amount"], errors="coerce")
    if amounts.isna().any():
        problems.append(f"invoices: non-numeric amount for {list(inv.loc[amounts.isna(), 'invoice_number'])}")
    supported = fx.currencies | {"EUR"}
    bad_ccy = inv.loc[~inv["currency"].isin(supported)]
    if len(bad_ccy):
        problems.append(f"invoices: unsupported currency for {list(bad_ccy['invoice_number'])} (supported: {sorted(supported)})")
    unknown = inv.loc[~inv["vendor_id"].isin(ven["vendor_id"])]
    if len(unknown):
        problems.append(f"invoices: vendor_id not in vendor master for {list(unknown['invoice_number'])}")
    if problems:
        raise DataValidationError(problems)

    inv = inv.assign(
        invoice_date=dates.dt.date,
        amount=amounts.astype(float),
        po_number=inv["po_number"].replace("", None),
        approved_by=inv["approved_by"].replace("", None),
    )
    # Vendor attributes come from the vendor master (source of truth), not the invoice file.
    inv = inv.drop(columns=["vendor_name"]).merge(
        ven.rename(columns={"status": "vendor_status"}), on="vendor_id", how="left", validate="many_to_one"
    )

    # EUR conversion per invoice (ECB rate on invoice date, prior-rate fallback).
    rate, rate_date, eur = [], [], []
    for ccy, d, amt in zip(inv["currency"], inv["invoice_date"], inv["amount"]):
        try:
            r = fx.lookup(ccy, d)
            rate.append(r.rate), rate_date.append(r.rate_date), eur.append(to_eur(amt, r))
        except FxRateMissing:
            rate.append(None), rate_date.append(None), eur.append(None)
    inv = inv.assign(fx_rate=rate, fx_rate_date=rate_date, amount_eur=eur)
    inv = inv.sort_values("invoice_number").reset_index(drop=True)

    digest = hashlib.sha256(inv_path.read_bytes() + ven_path.read_bytes()).hexdigest()[:12]
    return Dataset(
        invoices=inv,
        vendors=ven,
        # The extract covers whole calendar years (the 2025 invoice file), not just the first/last invoice date.
        period_start=date(min(inv["invoice_date"]).year, 1, 1),
        period_end=date(max(inv["invoice_date"]).year, 12, 31),
        version=f"invoices+vendors@{digest}",
    )


def require_eur(df: pd.DataFrame) -> None:
    """Fail closed if any selected invoice has no EUR amount (design §2.3, decision 7)."""
    missing = df.loc[df["amount_eur"].isna(), "invoice_number"].tolist()
    if missing:
        raise MissingFxError(missing)
