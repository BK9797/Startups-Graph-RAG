"""
Programmatic description of the graph schema, injected verbatim into the
Cypher-generation prompt. Keeping this as a single well-annotated string
(rather than scattering schema knowledge across the prompt) makes it easy
to keep the LLM's mental model of the graph in sync with `load_neo4j.py`
and `data/schema/ontology.md` if the ontology ever changes.
"""

GRAPH_SCHEMA = """
NODE LABELS AND PROPERTIES
---------------------------
(:Person {name: string, born: int, hometown: string, primary_role: string})
    - Represents both company founders AND executives/employees. Do NOT
      assume every Person founded a company — check for a FOUNDED edge.

(:Company {name: string, founded: int, sector: string, headquarters: string,
           valuation_billion: float, description: string, data_quality: string})
    - `data_quality` is "placeholder" for companies that only exist because
      a relationship referenced them but they had no proper source row.

(:Investor {name: string, founded: int, type: string, hq: string, data_quality: string})

(:Product {name: string, category: string, launch_year: int})

(:Award {name: string, category: string, year: int})

RELATIONSHIP TYPES
------------------
(:Person)-[:FOUNDED {year: int}]->(:Company)
(:Person)-[:WORKS_AT {role: string, current: boolean}]->(:Company)
(:Investor)-[:INVESTED_IN {round: string, amount_million: float, year: int}]->(:Company)
(:Company)-[:ACQUIRED {year: int, amount_million: float}]->(:Company)
(:Company)-[:DEVELOPS]->(:Product)
(:Person)-[:WON]->(:Award)
(:Company)-[:WON]->(:Award)

NOTES
-----
- Relationship direction matters: ACQUIRED goes acquirer -> target.
- `current: true` on WORKS_AT means the person presently holds that role.
- A Person can have multiple WORKS_AT edges to the same Company with
  different `role` values (career history), and multiple FOUNDED edges
  across different companies (serial founders).
- Use `toLower()` and `CONTAINS` for fuzzy/partial name matching unless the
  question gives an exact, unambiguous name.
- Always end read queries with a LIMIT clause.
"""

FEW_SHOT_EXAMPLES = [
    {
        "question": "Who founded NovaPay?",
        "cypher": (
            "MATCH (p:Person)-[r:FOUNDED]->(c:Company) "
            "WHERE toLower(c.name) = toLower('NovaPay') "
            "RETURN p.name AS founder, r.year AS year LIMIT 25"
        ),
    },
    {
        "question": "What companies has Sequoia Trail invested in and how much?",
        "cypher": (
            "MATCH (i:Investor)-[r:INVESTED_IN]->(c:Company) "
            "WHERE toLower(i.name) = toLower('Sequoia Trail') "
            "RETURN c.name AS company, r.round AS round, r.amount_million AS amount_million, "
            "r.year AS year ORDER BY r.year LIMIT 50"
        ),
    },
    {
        "question": "Which fintech companies are valued over $10 billion?",
        "cypher": (
            "MATCH (c:Company) WHERE toLower(c.sector) = toLower('Fintech') "
            "AND c.valuation_billion > 10 "
            "RETURN c.name AS company, c.valuation_billion AS valuation_billion "
            "ORDER BY c.valuation_billion DESC LIMIT 25"
        ),
    },
    {
        "question": "Who are the current executives at SecureLayer?",
        "cypher": (
            "MATCH (p:Person)-[r:WORKS_AT {current: true}]->(c:Company) "
            "WHERE toLower(c.name) = toLower('SecureLayer') "
            "RETURN p.name AS person, r.role AS role LIMIT 25"
        ),
    },
    {
        "question": "What has Marcus Chen founded and won awards for?",
        "cypher": (
            "MATCH (p:Person) WHERE toLower(p.name) = toLower('Marcus Chen') "
            "OPTIONAL MATCH (p)-[:FOUNDED]->(c:Company) "
            "OPTIONAL MATCH (p)-[:WON]->(a:Award) "
            "RETURN p.name AS person, collect(DISTINCT c.name) AS founded_companies, "
            "collect(DISTINCT a.name) AS awards LIMIT 25"
        ),
    },
    {
        "question": "Which company acquired the most other companies?",
        "cypher": (
            "MATCH (acq:Company)-[:ACQUIRED]->(tgt:Company) "
            "RETURN acq.name AS acquirer, count(tgt) AS acquisitions "
            "ORDER BY acquisitions DESC LIMIT 10"
        ),
    },
]
