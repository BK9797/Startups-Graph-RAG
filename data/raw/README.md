# `data/raw/` — expected input files

This folder is intentionally empty in the repository. It's the drop-in
location for the raw, messy source-system exports that `scripts/clean_data.py`
reads; the small sample dataset that was originally here has been removed
so the repo doesn't ship a copy of someone else's data by default.

Put your own CSVs here using the exact filenames and columns below, then
run `make clean` (`python scripts/clean_data.py`) followed by `make load-graph`.

| File | Required columns |
|---|---|
| `companies.csv` | `name, founded, sector, headquarters, valuation_billion, description` |
| `founders.csv` | `name, born, role, hometown` — covers both founders and non-founder executives; `role` is free text (e.g. `Founder`, `CEO`, `CTO`) |
| `investors.csv` | `name, founded, type, hq` |
| `products.csv` | `name, category, launch_year` |
| `awards.csv` | `name, category, year` |
| `founded.csv` | `founder_name, company_name, year` |
| `works_at.csv` | `person_name, company_name, role, current` — `current` accepts `yes/y/true/1` and `no/n/false/0`, case-insensitively |
| `invested_in.csv` | `investor_name, company_name, round, amount_million, year` |
| `acquired.csv` | `acquirer_company, target_company, year, amount_million` |
| `develops.csv` | `company_name, product_name` |
| `won.csv` | `entity_name, entity_type, award_name` — `entity_type` is `person` or `company` |

`scripts/clean_data.py` tolerates the kind of messiness real exports have
(inconsistent casing, duplicate rows, mixed boolean/numeric formats, and
relationship rows that reference an entity missing from its own node
file) — see the module docstring there and `data/schema/ontology.md` for
exactly how each case is handled.
