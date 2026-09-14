"""Column mapping: raw export headers -> canonical field names.

Auto-detection is a convenience, never an authority. Every guess is shown in the UI
and can be overridden, and a missing required field blocks analysis rather than
silently producing a wrong number.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from . import ingest
from .schema import ROLES, FieldSpec, fields_for, required_fields

FUZZY_THRESHOLD = 0.82
PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


def normalize(header: str) -> str:
    """Lowercase and strip everything that is not a letter or digit."""
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def match_field(header: str, spec: FieldSpec) -> float:
    """Confidence in [0, 1] that ``header`` is ``spec``."""
    h = normalize(header)
    if not h:
        return 0.0
    candidates = (spec.name,) + spec.aliases
    best = 0.0
    for cand in candidates:
        c = normalize(cand)
        if h == c:
            return 1.0
        # A short alias fully contained in a longer header is a strong signal
        # ("customerid" inside "shiptocustomerid"), but only when it is specific.
        if len(c) >= 6 and (c in h or h in c):
            best = max(best, 0.93)
        best = max(best, _similarity(h, c))
    return best


def auto_map(headers: list[str], role: str) -> dict[str, str | None]:
    """Best-effort header assignment for one dataset role.

    Resolves greedily by descending confidence so a header is never claimed by two
    fields, and the strongest overall match wins its column.
    """
    specs = fields_for(role)
    scored: list[tuple[float, str, str]] = []
    for spec in specs:
        for header in headers:
            score = match_field(header, spec)
            if score >= FUZZY_THRESHOLD:
                scored.append((score, spec.name, header))
    scored.sort(key=lambda t: (-t[0], t[1]))

    mapping: dict[str, str | None] = {s.name: None for s in specs}
    used: set[str] = set()
    for _score, fname, header in scored:
        if mapping[fname] is None and header not in used:
            mapping[fname] = header
            used.add(header)
    return mapping


def score_role(headers: list[str], role: str) -> float:
    """How well a file's headers fit a role.

    Satisfying every required field dominates, then the role that explains more of
    the file's columns wins. Without that second term a role with few required
    fields (payments) would beat a richer one (invoices) on the same file, since
    both would score a bare 1.0.
    """
    mapping = auto_map(headers, role)
    specs = fields_for(role)
    req = required_fields(role)
    if not req:
        return 0.0

    matched_required = sum(mapping[f] is not None for f in req)
    matched_total = sum(v is not None for v in mapping.values())

    base = 2.0 if matched_required == len(req) else 0.4 * (matched_required / len(req))
    # Small enough never to outrank a role that has all its required fields.
    coverage = 0.1 * matched_total
    specificity = 0.05 * sum(
        1 for spec in specs
        if mapping[spec.name] is not None and not spec.required)
    return base + coverage + specificity


def guess_role(headers: list[str], exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    options = [r for r in ROLES if r not in exclude] or list(ROLES)
    return max(options, key=lambda r: score_role(headers, r))


@dataclass
class MappingResult:
    frame: pd.DataFrame
    role: str
    mapping: dict[str, str | None]
    missing_required: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing_required


def apply_mapping(df: pd.DataFrame, role: str, mapping: dict[str, str | None],
                  dayfirst: bool = False) -> MappingResult:
    """Project a raw frame onto the canonical schema, coercing each column's type."""
    specs = fields_for(role)
    out = pd.DataFrame(index=df.index)
    notes: list[str] = []

    for spec in specs:
        source = mapping.get(spec.name)
        if source is None or source not in df.columns:
            continue
        coerced = ingest.coerce(df[source], spec.dtype, dayfirst=dayfirst)
        # Warn when coercion destroyed data the user can see in their file.
        present = df[source].notna().sum()
        survived = coerced.notna().sum()
        if present and survived < present * 0.9:
            notes.append(
                f"{role}.{spec.name}: only {survived:,} of {present:,} values in "
                f"'{source}' parsed as {spec.dtype}. Check the column mapping or date format."
            )
        out[spec.name] = coerced

    missing = [f for f in required_fields(role) if f not in out.columns]
    out = out.dropna(how="all").reset_index(drop=True)
    return MappingResult(out, role, mapping, missing, notes)


# --------------------------------------------------------------------------------------
# Mapping profiles -- so next month's export is one click.
# --------------------------------------------------------------------------------------

def profile_path(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_\- ]", "", name).strip() or "profile"
    return PROFILE_DIR / f"{safe}.json"


def save_profile(name: str, mappings: dict[str, dict[str, str | None]]) -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    path = profile_path(name)
    path.write_text(json.dumps(mappings, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_profile(name: str) -> dict[str, dict[str, str | None]]:
    path = profile_path(name)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def list_profiles() -> list[str]:
    if not PROFILE_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILE_DIR.glob("*.json"))


def combine(frames: list[pd.DataFrame], role: str) -> tuple[pd.DataFrame, list[str]]:
    """Merge several files that play the same role into one frame.

    A QuickBooks book is commonly split across exports -- open items in the A/R
    Ageing Detail, settled ones in the Invoices and Received Payments ledger, and
    the Collections Report repeating the past-due subset. Stacking them gives the
    history that DSO and CEI need, but the same invoice must not be counted twice.

    De-duplication is **across files only**. Two identical lines inside one export
    are two real postings (QuickBooks splits a journal entry across lines, and a
    document number is reused across customers), so within-file multiplicity is
    preserved by matching each row against the same occurrence in another file.
    """
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(), []
    if len(frames) == 1:
        return frames[0], []

    marked = []
    for source, frame in enumerate(frames):
        part = frame.copy()
        part["_source"] = source
        marked.append(part)
    combined = pd.concat(marked, ignore_index=True, sort=False)
    notes = [f"Combined {len(frames)} files into the {role} dataset "
             f"({len(combined):,} rows)."]

    if role == "invoices" and "invoice_no" in combined.columns:
        before = len(combined)
        # A document number alone is not unique, so match on the whole line.
        key = [c for c in ("invoice_no", "customer_id", "invoice_date", "invoice_amount")
               if c in combined.columns]
        # Nth identical line within a file only ever matches the Nth in another file.
        combined["_occurrence"] = combined.groupby(["_source"] + key,
                                                   dropna=False).cumcount()
        if "open_balance" in combined.columns:
            # Stable sort so a row carrying a real balance wins, order else preserved.
            combined = combined.assign(
                _has_balance=combined["open_balance"].notna().astype(int)
            ).sort_values("_has_balance", ascending=False,
                          kind="mergesort").drop(columns="_has_balance")
        combined = (combined.drop_duplicates(subset=key + ["_occurrence"], keep="first")
                            .sort_index())
        removed = before - len(combined)
        if removed:
            notes.append(f"Removed {removed:,} row(s) that appeared in more than one "
                         f"file, keeping the copy that carries an open balance.")

    combined = combined.drop(columns=[c for c in ("_source", "_occurrence")
                                      if c in combined.columns])
    return combined.reset_index(drop=True), notes
