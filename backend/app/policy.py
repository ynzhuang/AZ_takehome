"""Procurement policy loading (design §2.2, §2.4).

The policy markdown is parsed into clauses with stable IDs ("2.1", "3", "4.1", ...)
so that every citation can be checked. The approval matrix (§3) and the PO threshold
(§2.1) are read from the document itself and validated strictly at startup; if the
document no longer parses, the app refuses to start rather than apply stale rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class PolicyParseError(Exception):
    pass


@dataclass(frozen=True)
class Clause:
    id: str
    section: str
    text: str


@dataclass(frozen=True)
class ApprovalTier:
    lower_exclusive: float  # amount must be > this
    upper_inclusive: float | None  # amount must be <= this (None = no upper bound)
    role: str
    label: str  # as written in the policy table


@dataclass(frozen=True)
class Policy:
    document_id: str
    version: str
    effective: str
    clauses: dict[str, Clause]
    approval_tiers: list[ApprovalTier]
    po_threshold_eur: float
    text: str

    @property
    def version_label(self) -> str:
        return f"{self.document_id} v{self.version}"

    def tier_for(self, amount_eur: float) -> ApprovalTier:
        if amount_eur <= 0:
            raise ValueError("Approval tiers apply to positive amounts only")
        for tier in self.approval_tiers:
            if tier.upper_inclusive is None or amount_eur <= tier.upper_inclusive:
                return tier
        raise AssertionError("unreachable: last tier is open-ended")

    def clause(self, clause_id: str) -> Clause:
        return self.clauses[clause_id]


_SECTION = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
_CLAUSE = re.compile(r"^(\d+\.\d+)\s+(.*)$")


def _num(s: str) -> float:
    return float(s.replace(",", "").strip())


def _parse_tier_label(label: str) -> tuple[float, float | None]:
    label = label.strip()
    if m := re.fullmatch(r"Up to ([\d,.]+)", label):
        return 0.0, _num(m.group(1))
    if m := re.fullmatch(r"([\d,.]+)\s*[–-]\s*([\d,.]+)", label):
        # "5,000.01 – 50,000": lower bound is inclusive at cent precision
        return round(_num(m.group(1)) - 0.01, 2), _num(m.group(2))
    if m := re.fullmatch(r"Above ([\d,.]+)", label):
        return _num(m.group(1)), None
    raise PolicyParseError(f"Unrecognised approval threshold: {label!r}")


def parse_policy(text: str) -> Policy:
    header = {
        k: (m.group(1).strip() if (m := re.search(rf"\*\*{k}:\*\*\s*([^·\n]+)", text)) else None)
        for k in ("Document ID", "Version", "Effective")
    }
    if not header["Document ID"] or not header["Version"]:
        raise PolicyParseError("Policy header (Document ID / Version) not found")

    clauses: dict[str, list] = {}
    section_num = section_title = None
    current: str | None = None
    table_rows: list[str] = []
    for line in text.splitlines():
        if m := _SECTION.match(line):
            section_num, section_title = m.group(1), m.group(2)
            current = None
            continue
        if section_num is None or not line.strip():
            continue
        if m := _CLAUSE.match(line):
            current = m.group(1)
            clauses[current] = [section_title, [m.group(2)]]
            continue
        target = current or section_num  # unnumbered content belongs to the section itself
        clauses.setdefault(target, [section_title, []])[1].append(line)
        if section_num == "3" and line.startswith("|"):
            table_rows.append(line)

    parsed = {cid: Clause(cid, sec, "\n".join(lines).strip()) for cid, (sec, lines) in clauses.items()}

    # Approval matrix (§3): skip header and separator rows.
    tiers: list[ApprovalTier] = []
    for row in table_rows[2:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 2:
            raise PolicyParseError(f"Unexpected approval table row: {row!r}")
        lo, hi = _parse_tier_label(cells[0])
        tiers.append(ApprovalTier(lo, hi, cells[1], cells[0]))
    if not tiers:
        raise PolicyParseError("Approval matrix (§3) not found")
    for prev, nxt in zip(tiers, tiers[1:]):
        if prev.upper_inclusive is None or abs(nxt.lower_exclusive - prev.upper_inclusive) > 1e-9:
            raise PolicyParseError("Approval tiers are not contiguous")
    if tiers[-1].upper_inclusive is not None:
        raise PolicyParseError("Highest approval tier must be open-ended")

    po = re.search(r"value of \*\*EUR ([\d,]+) or above\*\*", parsed.get("2.1", Clause("", "", "")).text)
    if not po:
        raise PolicyParseError("PO threshold (§2.1) not found")

    return Policy(
        document_id=header["Document ID"],
        version=header["Version"],
        effective=header["Effective"] or "",
        clauses=parsed,
        approval_tiers=tiers,
        po_threshold_eur=_num(po.group(1)),
        text=text,
    )


def load_policy(path: Path) -> Policy:
    return parse_policy(path.read_text(encoding="utf-8"))
