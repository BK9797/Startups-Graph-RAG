"""
cypher_library.py
===================
A fixed, hand-written library of parameterized, read-only Cypher queries
for the tech/startups knowledge graph, plus a small rule-based matcher
that maps a natural-language question onto ONE of these templates and
extracts its parameters (entity names, sectors, years, thresholds, ...).

This module intentionally contains NO calls to an LLM. Every query the
API can ever run is one of the fixed strings defined below (or the
single fulltext fallback query in `graph_rag.py`). Rule-based matching
means the app is fast, free, fully offline-testable, and can never
generate an unsafe or schema-violating query -- there is nothing to
generate. The full, human-readable catalog of these queries also lives
in `CYPHER.md` at the project root; keep the two in sync if you add or
change a template.

Matching strategy
------------------
Each `QueryTemplate` carries one or more compiled regexes. The question
is tried against every template's patterns, in the order the templates
are declared in `TEMPLATES` (most specific first). The first pattern
that matches wins; its named groups become the raw parameters, which
are then normalized (trimmed, cast, mapped) by the template's own
`build_params` function before being handed to Neo4j as query
parameters -- never interpolated into the query string itself, except
for a small closed set of whitelisted comparison operators (see
`_OPERATORS` below), which are never derived from free text the user
could fully control.

If nothing matches, `match_question` returns None and the caller
(`graph_rag.answer_question`) falls back to the fulltext entity search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TRAILING_PUNCT = " ?.!'\"\u2019"
_LEADING_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_TRAILING_FILLER = re.compile(
    r"\s+(and (who|what|when|where|why|how).*)?$", re.IGNORECASE
)


def _clean_entity(raw: str) -> str:
    """Normalize an extracted entity-name span into a search term."""
    s = raw.strip().strip(_TRAILING_PUNCT)
    s = _LEADING_ARTICLES.sub("", s).strip()
    s = _TRAILING_FILLER.sub("", s).strip()
    return s.strip(_TRAILING_PUNCT)


_OPERATORS: dict[str, str] = {
    "over": ">",
    "above": ">",
    "more than": ">",
    "greater than": ">",
    "at least": ">=",
    "under": "<",
    "below": "<",
    "less than": "<",
    "at most": "<=",
    "exactly": "=",
}


def _parse_amount(raw: str) -> float:
    return float(raw.replace(",", ""))


def _parse_year(raw: str) -> int:
    return int(raw)


# ---------------------------------------------------------------------------
# Template definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CypherMatch:
    template_id: str
    description: str
    cypher: str
    params: dict


@dataclass(frozen=True)
class QueryTemplate:
    id: str
    description: str
    patterns: tuple[re.Pattern, ...]
    cypher: str
    build_params: Callable[[re.Match], dict | None]
    example_questions: tuple[str, ...] = ()

    def try_match(self, question: str) -> CypherMatch | None:
        for pattern in self.patterns:
            m = pattern.search(question)
            if not m:
                continue
            params = self.build_params(m)
            if params is None:
                continue
            return CypherMatch(
                template_id=self.id,
                description=self.description,
                cypher=self.cypher,
                params=params,
            )
        return None


def _pat(*patterns: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# ---------------------------------------------------------------------------
# Templates, most-specific first
# ---------------------------------------------------------------------------

TEMPLATES: list[QueryTemplate] = []


def _register(template: QueryTemplate) -> QueryTemplate:
    TEMPLATES.append(template)
    return template


# 1. Who acquired X? (check BEFORE "what did X acquire" / generic profile)
_register(
    QueryTemplate(
        id="who_acquired_company",
        description="Which company acquired a given target company.",
        patterns=_pat(
            r"^who\s+acquired\s+(?P<name>.+)$",
            r"^which\s+compan\w*\s+acquired\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (acq:Company)-[r:ACQUIRED]->(tgt:Company) "
            "WHERE toLower(tgt.name) CONTAINS toLower($name) "
            "RETURN acq.name AS acquirer, tgt.name AS target, "
            "r.year AS year, r.amount_million AS amount_million "
            "ORDER BY r.year LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("Who acquired SecureLayer?",),
    )
)

# 2. What did X acquire?
_register(
    QueryTemplate(
        id="acquisitions_by_company",
        description="Companies acquired by a given acquirer.",
        patterns=_pat(
            r"^what\s+(?:companies\s+)?did\s+(?P<name>.+?)\s+acquire$",
            r"^which\s+compan\w*\s+did\s+(?P<name>.+?)\s+acquire$",
            r"^what\s+has\s+(?P<name>.+?)\s+acquired$",
        ),
        cypher=(
            "MATCH (acq:Company)-[r:ACQUIRED]->(tgt:Company) "
            "WHERE toLower(acq.name) CONTAINS toLower($name) "
            "RETURN tgt.name AS target, r.year AS year, "
            "r.amount_million AS amount_million ORDER BY r.year LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("What did DataForge acquire?",),
    )
)

# 3. Which company made the most acquisitions? (aggregate, no entity)
_register(
    QueryTemplate(
        id="top_acquirers",
        description="Companies ranked by number of acquisitions made.",
        patterns=_pat(
            r"(which|what)\s+compan\w*\s+(has\s+made|made|has)\s+the\s+most\s+acquisitions",
            r"top\s+acquirers?",
            r"who\s+has\s+acquired\s+the\s+most\s+companies",
        ),
        cypher=(
            "MATCH (acq:Company)-[:ACQUIRED]->(tgt:Company) "
            "RETURN acq.name AS acquirer, count(tgt) AS acquisitions "
            "ORDER BY acquisitions DESC LIMIT $limit"
        ),
        build_params=lambda m: {},
        example_questions=("Which company made the most acquisitions?",),
    )
)

# 4. Who founded X?
_register(
    QueryTemplate(
        id="founders_of_company",
        description="The founder(s) of a given company, and founding year.",
        patterns=_pat(
            r"^who\s+founded\s+(?P<name>.+)$",
            r"^who\s+(?:is|are)\s+the\s+founders?\s+of\s+(?P<name>.+)$",
            r"^who\s+started\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (p:Person)-[r:FOUNDED]->(c:Company) "
            "WHERE toLower(c.name) CONTAINS toLower($name) "
            "RETURN p.name AS founder, c.name AS company, r.year AS year "
            "ORDER BY r.year LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("Who founded NovaPay?",),
    )
)

# 5. Serial founders (aggregate, no entity) -- must precede founders_of_company's
#    broader phrasing since it has no "of <company>" clause of its own.
_register(
    QueryTemplate(
        id="serial_founders",
        description="Founders who have founded more than one company.",
        patterns=_pat(
            r"serial\s+founders?",
            r"who\s+(?:has|have)\s+founded\s+more\s+than\s+one\s+company",
            r"founders?\s+of\s+multiple\s+companies",
        ),
        cypher=(
            "MATCH (p:Person)-[:FOUNDED]->(c:Company) "
            "WITH p, count(DISTINCT c) AS num, collect(DISTINCT c.name) AS companies "
            "WHERE num > 1 "
            "RETURN p.name AS founder, num AS companies_founded, companies "
            "ORDER BY num DESC LIMIT $limit"
        ),
        build_params=lambda m: {},
        example_questions=("Who are the serial founders?",),
    )
)

# 6. What has PERSON founded and won awards for? / person bio
_register(
    QueryTemplate(
        id="person_profile",
        description="Full profile of a person: companies founded, roles, awards.",
        patterns=_pat(
            r"^what\s+has\s+(?P<name>.+?)\s+founded",
            r"^who\s+is\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (p:Person) WHERE toLower(p.name) CONTAINS toLower($name) "
            "OPTIONAL MATCH (p)-[:FOUNDED]->(fc:Company) "
            "OPTIONAL MATCH (p)-[w:WORKS_AT]->(wc:Company) "
            "OPTIONAL MATCH (p)-[:WON]->(a:Award) "
            "RETURN p.name AS person, p.born AS born, p.hometown AS hometown, "
            "collect(DISTINCT fc.name) AS founded_companies, "
            "collect(DISTINCT {company: wc.name, role: w.role, current: w.current}) AS roles, "
            "collect(DISTINCT a.name) AS awards LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("What has Marcus Chen founded and won awards for?",),
    )
)

# 7. Current executives / leadership at COMPANY
_register(
    QueryTemplate(
        id="current_team_at_company",
        description="People who currently work at a given company.",
        patterns=_pat(
            r"current\s+(?:executives?|team|leadership)\s+(?:at|of)\s+(?P<name>.+)$",
            r"who\s+(?:are|is)\s+the\s+current\s+(?:executives?|team|leadership)\s+(?:at|of)\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (p:Person)-[r:WORKS_AT {current: true}]->(c:Company) "
            "WHERE toLower(c.name) CONTAINS toLower($name) "
            "RETURN p.name AS person, r.role AS role LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("Who are the current executives at SecureLayer?",),
    )
)

# 8. Everyone who has ever worked at COMPANY (career history, not just current)
_register(
    QueryTemplate(
        id="all_employees_of_company",
        description="Everyone with a WORKS_AT edge to a given company (past or present).",
        patterns=_pat(
            r"who\s+(?:has\s+)?works?\s+at\s+(?P<name>.+)$",
            r"who\s+(?:has\s+)?worked\s+at\s+(?P<name>.+)$",
            r"employees?\s+of\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (p:Person)-[r:WORKS_AT]->(c:Company) "
            "WHERE toLower(c.name) CONTAINS toLower($name) "
            "RETURN p.name AS person, r.role AS role, r.current AS current LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("Who works at CloudNest?",),
    )
)

# 9. What companies has INVESTOR invested in?
_register(
    QueryTemplate(
        id="investor_portfolio",
        description="Companies a given investor has invested in, by round.",
        patterns=_pat(
            r"what\s+companies\s+(?:has|have)\s+(?P<name>.+?)\s+invested\s+in",
            r"which\s+companies\s+did\s+(?P<name>.+?)\s+invest\s+in",
            r"(?P<name>.+?)'?s\s+portfolio",
            r"portfolio\s+of\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (i:Investor)-[r:INVESTED_IN]->(c:Company) "
            "WHERE toLower(i.name) CONTAINS toLower($name) "
            "RETURN c.name AS company, r.round AS round, "
            "r.amount_million AS amount_million, r.year AS year "
            "ORDER BY r.year LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("What companies has Sequoia Trail invested in and how much?",),
    )
)

# 10. Which investors backed / funded COMPANY?
_register(
    QueryTemplate(
        id="company_investors",
        description="Investors that have invested in a given company.",
        patterns=_pat(
            r"which\s+investors?\s+(?:backed|invested\s+in|funded|back)\s+(?P<name>.+)$",
            r"who\s+(?:invested\s+in|funded|backs?)\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (i:Investor)-[r:INVESTED_IN]->(c:Company) "
            "WHERE toLower(c.name) CONTAINS toLower($name) "
            "RETURN i.name AS investor, r.round AS round, "
            "r.amount_million AS amount_million, r.year AS year "
            "ORDER BY r.year LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("Which investors backed GreenGrid?",),
    )
)

# 11. Investors of a given type (VC, angel network, ...)
_register(
    QueryTemplate(
        id="investors_by_type",
        description="Investors filtered by investor type (VC, angel network, etc).",
        patterns=_pat(
            r"which\s+investors?\s+are\s+(?:a|an)?\s*(?P<type>venture capital|angel network|private equity|growth equity|seed fund|accelerator)",
            r"(?P<type>venture capital|angel network|private equity|growth equity|seed fund|accelerator)\s+investors",
        ),
        cypher=(
            "MATCH (i:Investor) WHERE toLower(i.type) CONTAINS toLower($type) "
            "RETURN i.name AS investor, i.hq AS hq, i.founded AS founded LIMIT $limit"
        ),
        build_params=lambda m: {"type": m.group("type").strip()},
        example_questions=("Which investors are venture capital firms?",),
    )
)

# 12. Sector + valuation threshold, e.g. "Which fintech companies are valued over $10 billion?"
_VALUATION_RE = _pat(
    r"^(?:which\s+|what\s+)?(?P<sector>(?!which\b|what\b|the\b)[a-z][a-z/&-]*(?:\s+[a-z][a-z/&-]*)*?)"
    r"\s+companies\s+(?:are\s+|is\s+)?"
    r"(?:valued|worth)\s+(?P<op>over|above|more than|greater than|at least|"
    r"under|below|less than|at most|exactly)\s+\$?(?P<amount>[\d.]+)\s*"
    r"(?:billion|bn|b)\b",
    r"^(?:which\s+|what\s+)?companies\s+(?:are\s+|is\s+)?(?:valued|worth)\s+(?P<op>over|above|more than|"
    r"greater than|at least|under|below|less than|at most|exactly)\s+\$?(?P<amount>[\d.]+)"
    r"\s*(?:billion|bn|b)\b",
)


def _build_valuation_params(m: re.Match) -> dict | None:
    op_key = m.group("op").lower()
    operator = _OPERATORS.get(op_key)
    if operator is None:
        return None
    sector = m.groupdict().get("sector")
    return {
        "sector": sector.strip() if sector else None,
        "amount": _parse_amount(m.group("amount")),
        "_operator": operator,
    }


def _valuation_cypher(params: dict) -> str:
    if params.get("sector"):
        where = "toLower(c.sector) CONTAINS toLower($sector) AND c.valuation_billion {op} $amount"
    else:
        where = "c.valuation_billion {op} $amount"
    return (
        f"MATCH (c:Company) WHERE {where.format(op=params['_operator'])} "
        "RETURN c.name AS company, c.sector AS sector, "
        "c.valuation_billion AS valuation_billion "
        "ORDER BY c.valuation_billion DESC LIMIT $limit"
    )


class _ValuationTemplate(QueryTemplate):
    """Special-cased template: the comparison operator is baked into the
    query text from a small whitelist (`_OPERATORS`), never from raw user
    text, so this stays just as safe as a static template."""

    def try_match(self, question: str) -> CypherMatch | None:
        for pattern in self.patterns:
            m = pattern.search(question)
            if not m:
                continue
            raw_params = self.build_params(m)
            if raw_params is None:
                continue
            operator = raw_params.pop("_operator")
            cypher = _valuation_cypher({**raw_params, "_operator": operator})
            params = {k: v for k, v in raw_params.items() if not k.startswith("_")}
            return CypherMatch(
                template_id=self.id,
                description=self.description,
                cypher=cypher,
                params=params,
            )
        return None


_register(
    _ValuationTemplate(
        id="companies_by_sector_and_valuation",
        description="Companies filtered by sector and a valuation threshold.",
        patterns=_VALUATION_RE,
        cypher="",  # built dynamically, see _valuation_cypher
        build_params=_build_valuation_params,
        example_questions=("Which fintech companies are valued over $10 billion?",),
    )
)

# 13. Most valuable / top companies by valuation (aggregate, no entity)
_register(
    QueryTemplate(
        id="top_valuation_companies",
        description="Companies ranked by valuation, highest first.",
        patterns=_pat(
            r"most\s+valuable\s+companies",
            r"top\s+companies\s+by\s+valuation",
            r"highest[\s-]valued\s+(?:startups|companies)",
        ),
        cypher=(
            "MATCH (c:Company) WHERE c.valuation_billion IS NOT NULL "
            "RETURN c.name AS company, c.sector AS sector, "
            "c.valuation_billion AS valuation_billion "
            "ORDER BY c.valuation_billion DESC LIMIT $limit"
        ),
        build_params=lambda m: {},
        example_questions=("What are the most valuable companies?",),
    )
)

# 14. Companies founded in YEAR
_register(
    QueryTemplate(
        id="companies_founded_in_year",
        description="Companies founded in a given year.",
        patterns=_pat(
            r"companies\s+(?:were\s+)?founded\s+in\s+(?P<year>\d{4})",
            r"what\s+companies\s+started\s+in\s+(?P<year>\d{4})",
        ),
        cypher=(
            "MATCH (c:Company) WHERE c.founded = $year "
            "RETURN c.name AS company, c.sector AS sector LIMIT $limit"
        ),
        build_params=lambda m: {"year": _parse_year(m.group("year"))},
        example_questions=("Which companies were founded in 2016?",),
    )
)

# 15. What products does COMPANY develop?
_register(
    QueryTemplate(
        id="company_products",
        description="Products developed by a given company.",
        patterns=_pat(
            r"what\s+products?\s+does\s+(?P<name>.+?)\s+(?:develop|make|build|offer)",
            r"products?\s+(?:by|from|of)\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (c:Company)-[:DEVELOPS]->(pr:Product) "
            "WHERE toLower(c.name) CONTAINS toLower($name) "
            "RETURN pr.name AS product, pr.category AS category, "
            "pr.launch_year AS launch_year LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("What products does DataForge develop?",),
    )
)

# 16. Awards won by a person or company (polymorphic)
_register(
    QueryTemplate(
        id="entity_awards",
        description="Awards won by a given person or company.",
        patterns=_pat(
            r"what\s+awards?\s+(?:has|have)\s+(?P<name>.+?)\s+won",
            r"awards?\s+won\s+by\s+(?P<name>.+)$",
            r"awards?\s+for\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (n)-[:WON]->(a:Award) "
            "WHERE (n:Person OR n:Company) AND toLower(n.name) CONTAINS toLower($name) "
            "RETURN n.name AS entity, labels(n)[0] AS entity_type, "
            "a.name AS award, a.category AS category, a.year AS year "
            "ORDER BY a.year LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("What awards has NovaPay won?",),
    )
)

# 17. List all sectors (aggregate, no entity)
_register(
    QueryTemplate(
        id="list_sectors",
        description="Distinct sectors present in the graph.",
        patterns=_pat(
            r"what\s+sectors\s+(?:exist|are\s+there|are\s+covered)",
            r"list\s+(?:all\s+)?sectors",
            r"which\s+sectors",
        ),
        cypher=(
            "MATCH (c:Company) WHERE c.sector IS NOT NULL "
            "RETURN DISTINCT c.sector AS sector ORDER BY sector LIMIT $limit"
        ),
        build_params=lambda m: {},
        example_questions=("What sectors are covered in the graph?",),
    )
)

# 18. Generic company profile -- broad, so it stays last among named-entity
#     templates. Catches "tell me about X" / "what is X" for companies.
_register(
    QueryTemplate(
        id="company_profile",
        description="Full profile of a company: founders, investors, valuation.",
        patterns=_pat(
            r"^(?:tell\s+me\s+about|what\s+is)\s+(?P<name>.+)$",
        ),
        cypher=(
            "MATCH (c:Company) WHERE toLower(c.name) CONTAINS toLower($name) "
            "OPTIONAL MATCH (p:Person)-[:FOUNDED]->(c) "
            "OPTIONAL MATCH (i:Investor)-[:INVESTED_IN]->(c) "
            "RETURN c.name AS company, c.sector AS sector, c.founded AS founded, "
            "c.headquarters AS headquarters, c.valuation_billion AS valuation_billion, "
            "c.description AS description, collect(DISTINCT p.name) AS founders, "
            "collect(DISTINCT i.name) AS investors LIMIT $limit"
        ),
        build_params=lambda m: {"name": _clean_entity(m.group("name"))},
        example_questions=("Tell me about NovaPay.",),
    )
)


# ---------------------------------------------------------------------------
# Matching entrypoint
# ---------------------------------------------------------------------------


def match_question(question: str) -> CypherMatch | None:
    """
    Try every template in priority order and return the first match.

    "who is X" / "what has X founded" route to `person_profile`; "tell me
    about X" / "what is X" route to `company_profile`. If a person profile
    resolves to zero rows (e.g. the name is actually a company), the
    caller falls back to the fulltext search like any other zero-row
    result (see `graph_rag.answer_question`), which will surface the
    right entity either way.
    """
    # A handful of templates anchor a keyword (e.g. "acquire") to the end
    # of the string with `$`; strip trailing punctuation up front so a
    # trailing "?" or "." doesn't defeat that anchor.
    q = question.strip().rstrip(_TRAILING_PUNCT)
    if not q:
        return None
    for template in TEMPLATES:
        match = template.try_match(q)
        if match is not None:
            return match
    return None


def get_template(template_id: str) -> QueryTemplate | None:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    return None
