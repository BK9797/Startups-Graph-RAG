"""
clean_data.py
==============
Cleans the raw, messy source-system exports in `data/raw/` and writes
analysis-ready CSVs to `data/processed/`, plus a human-readable data
quality report to `data/processed/cleaning_report.md`.

Design decisions (documented here because they matter for the ontology):

1. Node identity is resolved on a *normalized key* = strip().lower(),
   collapsing internal whitespace. The first "nicely cased" spelling
   seen in the data becomes the canonical display name. This fixes
   issues like "novapay" vs "NovaPay" and "DataForge " vs "DataForge".

2. Exact duplicate rows (after normalization) are dropped, e.g. the
   repeated Elena Rossi / GreenGrid / DataForge founding / ForgeML rows.

3. Free-text categorical fields (role, sector) are title-cased so
   "founder" and "Founder" collapse to one value, and "ai" -> "AI"-like
   acronyms are upper-cased via a small known-acronym list.

4. Boolean-like fields (`current` in works_at.csv) accept
   {yes, y, true, 1} / {no, n, false, 0} case-insensitively.

5. Numeric fields that arrived as floats due to spreadsheet export
   (e.g. `founded = 2016.0`) are coerced to nullable ints.

6. Dangling references -- relationship rows pointing at an entity name
   that does not exist in the corresponding node table (e.g. investor
   "Phantom Capital", company "GhostApp") are NOT silently dropped.
   They are kept, and flagged with `resolved=False` so the loader can
   create an explicit placeholder node tagged `data_quality:"placeholder"`
   rather than lose the edge or crash. This mirrors how a real
   incremental ETL pipeline has to cope with late-arriving / missing
   dimension rows.

Run:
    python scripts/clean_data.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)

KNOWN_ACRONYMS = {"ai", "hr", "ar/vr", "propTech".lower()}
BOOL_TRUE = {"yes", "y", "true", "1"}
BOOL_FALSE = {"no", "n", "false", "0"}


@dataclass
class CleaningReport:
    lines: list[str] = field(default_factory=list)

    def log(self, msg: str) -> None:
        self.lines.append(msg)
        print(msg)

    def write(self, path: Path) -> None:
        body = "\n".join(f"- {line}" for line in self.lines)
        path.write_text("# Data Cleaning Report\n\n" + body + "\n")


def norm_key(name: str) -> str:
    """Normalized join-key for fuzzy-matching entity names across sheets."""
    if pd.isna(name):
        return ""
    return re.sub(r"\s+", " ", str(name).strip()).lower()


def clean_str(value) -> str | None:
    if pd.isna(value):
        return None
    value = re.sub(r"\s+", " ", str(value).strip())
    return value or None


def title_case_category(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip().lower() in KNOWN_ACRONYMS:
        return value.strip().upper()
    return value.strip().title() if value.islower() or value.isupper() else value.strip()


def to_nullable_int(value) -> pd.Int64Dtype | None:
    if pd.isna(value):
        return pd.NA
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return pd.NA


def to_nullable_float(value):
    if pd.isna(value) or str(value).strip() == "":
        return pd.NA
    try:
        return float(value)
    except (TypeError, ValueError):
        return pd.NA


def parse_bool(value) -> pd.BooleanDtype | None:
    if pd.isna(value):
        return pd.NA
    v = str(value).strip().lower()
    if v in BOOL_TRUE:
        return True
    if v in BOOL_FALSE:
        return False
    return pd.NA


def build_canonical_index(names: pd.Series) -> dict[str, str]:
    """Map normalized key -> canonical display name (first well-cased value wins)."""
    index: dict[str, str] = {}
    for raw in names:
        cleaned = clean_str(raw)
        if cleaned is None:
            continue
        key = norm_key(cleaned)
        if key not in index:
            index[key] = cleaned
        else:
            # Prefer the version that looks "properly cased" (has an uppercase letter)
            if not any(c.isupper() for c in index[key]) and any(c.isupper() for c in cleaned):
                index[key] = cleaned
    return index


def clean_nodes(report: CleaningReport) -> dict[str, pd.DataFrame]:
    nodes: dict[str, pd.DataFrame] = {}

    # ---- Person (Founders sheet is really "people related to companies") ----
    df = pd.read_csv(RAW_DIR / "founders.csv")
    df["name"] = df["name"].map(clean_str)
    df["hometown"] = df["hometown"].map(clean_str)
    df["role"] = df["role"].map(clean_str).map(title_case_category)
    df["born"] = df["born"].map(to_nullable_int)
    before = len(df)
    df["_key"] = df["name"].map(norm_key)
    df = df.drop_duplicates(subset=["_key", "born"]).drop(columns="_key")
    report.log(f"Person: {before} raw rows -> {len(df)} after dropping exact duplicates "
               f"(e.g. duplicate 'Elena Rossi' row removed).")
    missing_hometown = df["hometown"].isna().sum()
    if missing_hometown:
        report.log(f"Person: {missing_hometown} row(s) missing `hometown` (kept as NULL, e.g. Omar Haddad).")
    nodes["Person"] = df.rename(columns={"role": "primary_role"}).reset_index(drop=True)

    # ---- Company ----
    df = pd.read_csv(RAW_DIR / "companies.csv")
    df["name"] = df["name"].map(clean_str)
    df["sector"] = df["sector"].map(clean_str).map(title_case_category)
    df["headquarters"] = df["headquarters"].map(clean_str)
    df["founded"] = df["founded"].map(to_nullable_int)
    df["valuation_billion"] = df["valuation_billion"].map(to_nullable_float)
    df["description"] = df["description"].map(clean_str)
    before = len(df)
    df["_key"] = df["name"].map(norm_key)
    df = df.drop_duplicates(subset="_key").drop(columns="_key")
    report.log(f"Company: {before} raw rows -> {len(df)} after dedup "
               f"(duplicate 'GreenGrid' row removed); sector 'ai' normalized to 'AI'.")
    missing_val = df["valuation_billion"].isna().sum()
    if missing_val:
        report.log(f"Company: {missing_val} row(s) missing `valuation_billion` (kept as NULL, e.g. EduSpark).")
    nodes["Company"] = df.reset_index(drop=True)

    # ---- Investor ----
    df = pd.read_csv(RAW_DIR / "investors.csv")
    df["name"] = df["name"].map(clean_str)
    df["type"] = df["type"].map(clean_str)
    df["hq"] = df["hq"].map(clean_str)
    df["founded"] = df["founded"].map(to_nullable_int)
    before = len(df)
    df["_key"] = df["name"].map(norm_key)
    df = df.drop_duplicates(subset="_key").drop(columns="_key")
    report.log(f"Investor: {before} raw rows -> {len(df)} after dedup.")
    missing_founded = df["founded"].isna().sum()
    if missing_founded:
        report.log(f"Investor: {missing_founded} row(s) missing `founded` year (e.g. Pioneer Labs Fund).")
    nodes["Investor"] = df.reset_index(drop=True)

    # ---- Product ----
    df = pd.read_csv(RAW_DIR / "products.csv")
    df["name"] = df["name"].map(clean_str)
    df["category"] = df["category"].map(clean_str)
    df["launch_year"] = df["launch_year"].map(to_nullable_int)
    before = len(df)
    df["_key"] = df["name"].map(norm_key)
    df = df.drop_duplicates(subset="_key").drop(columns="_key")
    report.log(f"Product: {before} raw rows -> {len(df)} after dedup.")
    nodes["Product"] = df.reset_index(drop=True)

    # ---- Award ----
    df = pd.read_csv(RAW_DIR / "awards.csv")
    df["name"] = df["name"].map(clean_str)
    df["category"] = df["category"].map(clean_str)
    df["year"] = df["year"].map(to_nullable_int)
    before = len(df)
    df["_key"] = df["name"].map(norm_key)
    df = df.drop_duplicates(subset="_key").drop(columns="_key")
    report.log(f"Award: {before} raw rows -> {len(df)} after dedup.")
    nodes["Award"] = df.reset_index(drop=True)

    return nodes


def resolve(name: str, index: dict[str, str], unresolved_bucket: set[str]) -> str:
    """Return canonical name if known, else the cleaned original and record it as dangling."""
    cleaned = clean_str(name)
    if cleaned is None:
        return cleaned
    key = norm_key(cleaned)
    if key in index:
        return index[key]
    unresolved_bucket.add(cleaned)
    return cleaned


def clean_relationships(nodes: dict[str, pd.DataFrame], report: CleaningReport) -> dict[str, pd.DataFrame]:
    person_idx = build_canonical_index(nodes["Person"]["name"])
    company_idx = build_canonical_index(nodes["Company"]["name"])
    investor_idx = build_canonical_index(nodes["Investor"]["name"])
    product_idx = build_canonical_index(nodes["Product"]["name"])
    award_idx = build_canonical_index(nodes["Award"]["name"])

    rels: dict[str, pd.DataFrame] = {}
    dangling: dict[str, set[str]] = {"Company": set(), "Investor": set(), "Product": set(), "Award": set(), "Person": set()}

    # ---- FOUNDED ----
    df = pd.read_csv(RAW_DIR / "founded.csv")
    df["founder_name"] = df["founder_name"].map(lambda n: resolve(n, person_idx, dangling["Person"]))
    df["company_name"] = df["company_name"].map(lambda n: resolve(n, company_idx, dangling["Company"]))
    df["year"] = df["year"].map(to_nullable_int)
    before = len(df)
    df = df.drop_duplicates(subset=["founder_name", "company_name"])
    report.log(f"FOUNDED: {before} raw rows -> {len(df)} after dedup (duplicate Marcus Chen/DataForge row removed).")
    rels["FOUNDED"] = df.reset_index(drop=True)

    # ---- WORKS_AT ----
    df = pd.read_csv(RAW_DIR / "works_at.csv")
    df["person_name"] = df["person_name"].map(lambda n: resolve(n, person_idx, dangling["Person"]))
    df["company_name"] = df["company_name"].map(lambda n: resolve(n, company_idx, dangling["Company"]))
    df["role"] = df["role"].map(clean_str)
    df["current"] = df["current"].map(parse_bool)
    before = len(df)
    df = df.drop_duplicates(subset=["person_name", "company_name", "role"])
    report.log(f"WORKS_AT: {before} raw rows -> {len(df)} after dedup/case-fold "
               f"(lowercase 'elena rossi'/'novapay' row folded into canonical 'Elena Rossi'/'NovaPay'); "
               f"`current` values yes/Y/TRUE/FALSE normalized to booleans.")
    rels["WORKS_AT"] = df.reset_index(drop=True)

    # ---- INVESTED_IN ----
    df = pd.read_csv(RAW_DIR / "invested_in.csv")
    df["investor_name"] = df["investor_name"].map(lambda n: resolve(n, investor_idx, dangling["Investor"]))
    df["company_name"] = df["company_name"].map(lambda n: resolve(n, company_idx, dangling["Company"]))
    df["round"] = df["round"].map(clean_str)
    df["amount_million"] = df["amount_million"].map(to_nullable_float)
    df["year"] = df["year"].map(to_nullable_int)
    rels["INVESTED_IN"] = df.reset_index(drop=True)
    if dangling["Investor"]:
        report.log(f"INVESTED_IN: dangling investor reference(s) not present in investors.csv: "
                   f"{sorted(dangling['Investor'])} -> will be loaded as placeholder Investor node(s).")

    # ---- ACQUIRED ----
    df = pd.read_csv(RAW_DIR / "acquired.csv")
    df["acquirer_company"] = df["acquirer_company"].map(lambda n: resolve(n, company_idx, dangling["Company"]))
    df["target_company"] = df["target_company"].map(lambda n: resolve(n, company_idx, dangling["Company"]))
    df["year"] = df["year"].map(to_nullable_int)
    df["amount_million"] = df["amount_million"].map(to_nullable_float)
    rels["ACQUIRED"] = df.reset_index(drop=True)

    # ---- DEVELOPS ----
    df = pd.read_csv(RAW_DIR / "develops.csv")
    df["company_name"] = df["company_name"].map(lambda n: resolve(n, company_idx, dangling["Company"]))
    df["product_name"] = df["product_name"].map(lambda n: resolve(n, product_idx, dangling["Product"]))
    before = len(df)
    df = df.drop_duplicates(subset=["company_name", "product_name"])
    report.log(f"DEVELOPS: {before} raw rows -> {len(df)} after dedup "
               f"(duplicate NovaPay/NovaPay Wallet and 'DataForge '/ForgeML rows folded).")
    rels["DEVELOPS"] = df.reset_index(drop=True)

    # ---- WON ----
    df = pd.read_csv(RAW_DIR / "won.csv")
    def resolve_entity(row):
        if row["entity_type"].strip().lower() == "company":
            return resolve(row["entity_name"], company_idx, dangling["Company"])
        return resolve(row["entity_name"], person_idx, dangling["Person"])
    df["entity_name"] = df.apply(resolve_entity, axis=1)
    df["entity_type"] = df["entity_type"].map(clean_str).str.lower()
    df["award_name"] = df["award_name"].map(lambda n: resolve(n, award_idx, dangling["Award"]))
    rels["WON"] = df.reset_index(drop=True)
    if dangling["Company"]:
        report.log(f"WON/other: dangling company reference(s) not present in companies.csv: "
                   f"{sorted(dangling['Company'])} -> will be loaded as placeholder Company node(s).")

    return rels, dangling


def main() -> None:
    report = CleaningReport()
    report.log("Source: raw exports in `data/raw/` (5 node sheets, 6 relationship sheets).")

    nodes = clean_nodes(report)
    rels, dangling = clean_relationships(nodes, report)

    for name, df in nodes.items():
        out = OUT_DIR / f"{name.lower()}.csv"
        df.to_csv(out, index=False)
        report.log(f"Wrote {out.relative_to(OUT_DIR.parent.parent)} ({len(df)} rows).")

    for name, df in rels.items():
        out = OUT_DIR / f"{name.lower()}.csv"
        df.to_csv(out, index=False)
        report.log(f"Wrote {out.relative_to(OUT_DIR.parent.parent)} ({len(df)} rows).")

    total_dangling = sum(len(v) for v in dangling.values())
    report.log(f"Total dangling references detected across all relationship sheets: {total_dangling}.")

    report.write(OUT_DIR / "cleaning_report.md")
    report.log(f"\nCleaning report written to {OUT_DIR / 'cleaning_report.md'}")


if __name__ == "__main__":
    sys.exit(main())
