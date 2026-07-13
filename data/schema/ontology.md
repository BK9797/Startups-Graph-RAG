# Ontology — Tech / Startups Knowledge Graph

This is the schema the LLM is grounded on when generating Cypher (see
`app/core/ontology.py`, which encodes this same information as a Python
constant injected into prompts).

## Node labels

| Label      | Key property | Other properties                                                        | Notes |
|------------|--------------|---------------------------------------------------------------------------|-------|
| `Person`   | `name`       | `born` (int), `hometown` (string, nullable), `primary_role` (string)     | Covers founders **and** executives — the source "Founders" sheet actually mixes both, so role is intentionally *not* the primary label; role is contextual and lives on `FOUNDED`/`WORKS_AT` edges. |
| `Company`  | `name`       | `founded` (int), `sector` (string), `headquarters` (string), `valuation_billion` (float, nullable), `description` (string), `data_quality` (`"clean"`\|`"placeholder"`) |
| `Investor` | `name`       | `founded` (int, nullable), `type` (string), `hq` (string), `data_quality` |
| `Product`  | `name`       | `category` (string), `launch_year` (int, nullable) |
| `Award`    | `name`       | `category` (string), `year` (int) |

`data_quality: "placeholder"` marks a node that was auto-created because a
relationship referenced it but it never appeared in its own source sheet
(a dangling reference in the raw export, e.g. investor "Phantom Capital" or
company "GhostApp"). This keeps the edge instead of dropping it, while
staying honest about data completeness.

## Relationship types

| Relationship | From → To | Properties | Cardinality note |
|---|---|---|---|
| `FOUNDED` | `Person → Company` | `year` | A person can found multiple companies; a company can have multiple founders. |
| `WORKS_AT` | `Person → Company` | `role`, `current` (bool) | A person can have multiple `WORKS_AT` edges to the same company over time (different `role`), or to different companies. |
| `INVESTED_IN` | `Investor → Company` | `round`, `amount_million`, `year` | Multiple rounds per investor/company pair. |
| `ACQUIRED` | `Company → Company` | `year`, `amount_million` | Directed acquirer → target. |
| `DEVELOPS` | `Company → Product` | — | |
| `WON` | `Person \| Company → Award` | — | Both people and companies can win awards. |

## Why this shape (design rationale)

- **Role is an edge property, not a node label or a node property that
  defines identity.** The raw "Founders" node sheet conflates two very
  different populations (actual company founders and later-hired
  executives like CTOs/CMOs) under one table. Modeling them as a single
  `Person` label with `FOUNDED` and `WORKS_AT` edges lets us ask questions
  like "who founded and later left the company" or "who joined as an
  executive but never founded anything" — which would be impossible if
  role were baked into the node.
- **`WON` is polymorphic** over `Person` and `Company` because the source
  `Won` sheet has an explicit `entity_type` discriminator column. Cypher
  pattern `(n)-[:WON]->(:Award)` with `n` unioned over both labels handles
  this without needing a generic `Entity` supertype.
- **`data_quality` flag instead of silently dropping bad rows.** Given the
  assignment explicitly says the export has dangling references, the
  loader treats "keep the edge, flag the endpoint" as the safer default
  for a knowledge graph than "drop the edge because one side wasn't in
  its home sheet."

## Example Cypher patterns

Find all current executives of a company:
```cypher
MATCH (p:Person)-[r:WORKS_AT {current: true}]->(c:Company {name: "NovaPay"})
RETURN p.name, r.role
```

Find companies an investor has backed across all rounds:
```cypher
MATCH (i:Investor {name: "Sequoia Trail"})-[r:INVESTED_IN]->(c:Company)
RETURN c.name, r.round, r.amount_million, r.year ORDER BY r.year
```

Find acquisition chains:
```cypher
MATCH (a:Company)-[r:ACQUIRED]->(t:Company)
RETURN a.name, t.name, r.year, r.amount_million ORDER BY r.year
```

Find everything a founder is connected to (founding + employment + awards):
```cypher
MATCH (p:Person {name: "Marcus Chen"})-[r]-(x)
RETURN type(r), labels(x)[0], x.name
```
