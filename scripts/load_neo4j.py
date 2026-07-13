"""
load_neo4j.py
===============
Loads the cleaned CSVs in `data/processed/` into Neo4j, building the
graph according to the ontology documented in `data/schema/ontology.md`.

Idempotent: every write uses MERGE, so running this script multiple
times against the same database will not create duplicates. Safe to
use as both an initial load and a re-sync after `clean_data.py` is
re-run on updated source data.

Dangling references (an edge whose endpoint entity was not present in
its node table) are materialized as a minimal placeholder node with
`data_quality: "placeholder"` so no edge is silently dropped, but the
gap remains visible and queryable.

Usage:
    python scripts/load_neo4j.py [--reset]

Environment variables (see .env.example):
    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

CONSTRAINTS = [
    "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT company_name IF NOT EXISTS FOR (c:Company) REQUIRE c.name IS UNIQUE",
    "CREATE CONSTRAINT investor_name IF NOT EXISTS FOR (i:Investor) REQUIRE i.name IS UNIQUE",
    "CREATE CONSTRAINT product_name IF NOT EXISTS FOR (pr:Product) REQUIRE pr.name IS UNIQUE",
    "CREATE CONSTRAINT award_name IF NOT EXISTS FOR (a:Award) REQUIRE a.name IS UNIQUE",
]

FULLTEXT_INDEXES = [
    (
        "entity_search",
        "CREATE FULLTEXT INDEX entity_search IF NOT EXISTS "
        "FOR (n:Person|Company|Investor|Product|Award) ON EACH [n.name, n.description]"
    ),
]


def clean_value(v):
    """Convert pandas NA/NaN to None so the Neo4j driver stores a real null."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def df_records(name: str) -> list[dict]:
    df = pd.read_csv(PROCESSED_DIR / name)
    records = df.to_dict(orient="records")
    return [{k: clean_value(v) for k, v in r.items()} for r in records]


class Neo4jLoader:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database

    def close(self):
        self.driver.close()

    def run(self, query: str, **params):
        with self.driver.session(database=self.database) as session:
            return session.run(query, **params).data()

    def reset(self):
        print("Resetting database (DETACH DELETE all nodes)...")
        self.run("MATCH (n) DETACH DELETE n")

    def apply_schema(self):
        for stmt in CONSTRAINTS:
            self.run(stmt)
        for _, stmt in FULLTEXT_INDEXES:
            self.run(stmt)
        print(f"Applied {len(CONSTRAINTS)} constraints and {len(FULLTEXT_INDEXES)} fulltext index(es).")

    # ---------------- Nodes ----------------

    def load_people(self):
        rows = df_records("person.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (p:Person {name: row.name})
            SET p.born = row.born,
                p.hometown = row.hometown,
                p.primary_role = row.primary_role
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} Person nodes.")

    def load_companies(self):
        rows = df_records("company.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (c:Company {name: row.name})
            SET c.founded = row.founded,
                c.sector = row.sector,
                c.headquarters = row.headquarters,
                c.valuation_billion = row.valuation_billion,
                c.description = row.description,
                c.data_quality = coalesce(c.data_quality, "clean")
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} Company nodes.")

    def load_investors(self):
        rows = df_records("investor.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (i:Investor {name: row.name})
            SET i.founded = row.founded,
                i.type = row.type,
                i.hq = row.hq,
                i.data_quality = coalesce(i.data_quality, "clean")
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} Investor nodes.")

    def load_products(self):
        rows = df_records("product.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (pr:Product {name: row.name})
            SET pr.category = row.category,
                pr.launch_year = row.launch_year
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} Product nodes.")

    def load_awards(self):
        rows = df_records("award.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (a:Award {name: row.name})
            SET a.category = row.category,
                a.year = row.year
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} Award nodes.")

    # ---------------- Relationships ----------------
    # Endpoints are MERGEd defensively (not just MATCHed) so a dangling
    # reference becomes a visible placeholder node instead of a dropped edge.

    def load_founded(self):
        rows = df_records("founded.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (p:Person {name: row.founder_name})
            ON CREATE SET p.data_quality = "placeholder"
            MERGE (c:Company {name: row.company_name})
            ON CREATE SET c.data_quality = "placeholder"
            MERGE (p)-[r:FOUNDED]->(c)
            SET r.year = row.year
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} FOUNDED relationships.")

    def load_works_at(self):
        rows = df_records("works_at.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (p:Person {name: row.person_name})
            ON CREATE SET p.data_quality = "placeholder"
            MERGE (c:Company {name: row.company_name})
            ON CREATE SET c.data_quality = "placeholder"
            MERGE (p)-[r:WORKS_AT {role: row.role}]->(c)
            SET r.current = row.current
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} WORKS_AT relationships.")

    def load_invested_in(self):
        rows = df_records("invested_in.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (i:Investor {name: row.investor_name})
            ON CREATE SET i.data_quality = "placeholder"
            MERGE (c:Company {name: row.company_name})
            ON CREATE SET c.data_quality = "placeholder"
            MERGE (i)-[r:INVESTED_IN {round: row.round}]->(c)
            SET r.amount_million = row.amount_million,
                r.year = row.year
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} INVESTED_IN relationships.")

    def load_acquired(self):
        rows = df_records("acquired.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (acq:Company {name: row.acquirer_company})
            ON CREATE SET acq.data_quality = "placeholder"
            MERGE (tgt:Company {name: row.target_company})
            ON CREATE SET tgt.data_quality = "placeholder"
            MERGE (acq)-[r:ACQUIRED]->(tgt)
            SET r.year = row.year,
                r.amount_million = row.amount_million
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} ACQUIRED relationships.")

    def load_develops(self):
        rows = df_records("develops.csv")
        self.run(
            """
            UNWIND $rows AS row
            MERGE (c:Company {name: row.company_name})
            ON CREATE SET c.data_quality = "placeholder"
            MERGE (pr:Product {name: row.product_name})
            ON CREATE SET pr.data_quality = "placeholder"
            MERGE (c)-[r:DEVELOPS]->(pr)
            """,
            rows=rows,
        )
        print(f"Loaded {len(rows)} DEVELOPS relationships.")

    def load_won(self):
        rows = df_records("won.csv")
        person_rows = [r for r in rows if r["entity_type"] == "person"]
        company_rows = [r for r in rows if r["entity_type"] == "company"]
        self.run(
            """
            UNWIND $rows AS row
            MERGE (p:Person {name: row.entity_name})
            ON CREATE SET p.data_quality = "placeholder"
            MERGE (a:Award {name: row.award_name})
            ON CREATE SET a.data_quality = "placeholder"
            MERGE (p)-[r:WON]->(a)
            """,
            rows=person_rows,
        )
        self.run(
            """
            UNWIND $rows AS row
            MERGE (c:Company {name: row.entity_name})
            ON CREATE SET c.data_quality = "placeholder"
            MERGE (a:Award {name: row.award_name})
            ON CREATE SET a.data_quality = "placeholder"
            MERGE (c)-[r:WON]->(a)
            """,
            rows=company_rows,
        )
        print(f"Loaded {len(rows)} WON relationships ({len(person_rows)} person, {len(company_rows)} company).")

    def summary(self):
        counts = self.run(
            """
            MATCH (n) WITH labels(n)[0] AS label, count(*) AS n
            RETURN label, n ORDER BY label
            """
        )
        rel_counts = self.run(
            """
            MATCH ()-[r]->() WITH type(r) AS rel, count(*) AS n
            RETURN rel, n ORDER BY rel
            """
        )
        placeholders = self.run(
            "MATCH (n {data_quality: 'placeholder'}) RETURN labels(n)[0] AS label, n.name AS name"
        )
        print("\n=== Node counts ===")
        for row in counts:
            print(f"  {row['label']}: {row['n']}")
        print("\n=== Relationship counts ===")
        for row in rel_counts:
            print(f"  {row['rel']}: {row['n']}")
        if placeholders:
            print("\n=== Placeholder nodes (dangling references materialized) ===")
            for row in placeholders:
                print(f"  {row['label']}: {row['name']}")


def main():
    parser = argparse.ArgumentParser(description="Load cleaned CSVs into Neo4j")
    parser.add_argument("--reset", action="store_true", help="Delete all existing graph data first")
    args = parser.parse_args()

    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USERNAME")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE", "neo4j")

    if not all([uri, user, password]):
        print("ERROR: NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD must be set (see .env.example).", file=sys.stderr)
        sys.exit(1)

    if not (PROCESSED_DIR / "person.csv").exists():
        print("ERROR: data/processed/*.csv not found. Run `python scripts/clean_data.py` first.", file=sys.stderr)
        sys.exit(1)

    loader = Neo4jLoader(uri, user, password, database)
    try:
        if args.reset:
            loader.reset()
        loader.apply_schema()

        loader.load_people()
        loader.load_companies()
        loader.load_investors()
        loader.load_products()
        loader.load_awards()

        loader.load_founded()
        loader.load_works_at()
        loader.load_invested_in()
        loader.load_acquired()
        loader.load_develops()
        loader.load_won()

        loader.summary()
        print("\nLoad complete.")
    finally:
        loader.close()


if __name__ == "__main__":
    main()
