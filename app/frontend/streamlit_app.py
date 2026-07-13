"""
streamlit_app.py
==================
Interactive frontend for the Tech/Startups GraphRAG API.

Run locally:
    streamlit run app/frontend/streamlit_app.py

Configure the backend location via the BACKEND_URL environment variable
or the sidebar field (useful when the API is deployed separately on
Railway from the frontend).
"""

from __future__ import annotations

import os
import time

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

DEFAULT_BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")

EXAMPLE_QUESTIONS = [
    "Who founded NovaPay and when?",
    "What companies has Sequoia Trail invested in?",
    "Which fintech companies are valued over $10 billion?",
    "Who are the current executives at SecureLayer?",
    "What has Marcus Chen founded and won awards for?",
    "Which company made the most acquisitions?",
    "What products does DataForge develop?",
    "Which investors backed GreenGrid?",
    "Who acquired SecureLayer?",
    "What awards has NovaPay won?",
    "Who are the serial founders?",
]

NODE_COLORS = {
    "Person": "#5B8DEF",
    "Company": "#F2994A",
    "Investor": "#27AE60",
    "Product": "#9B51E0",
    "Award": "#EB5757",
}

st.set_page_config(
    page_title="Startups GraphRAG Explorer",
    page_icon="🕸️",
    layout="wide",
    initial_sidebar_state="expanded",
)

if "history" not in st.session_state:
    st.session_state.history = []
if "backend_url" not in st.session_state:
    st.session_state.backend_url = DEFAULT_BACKEND
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None


# ----------------------------- helpers -----------------------------

def call_health(backend_url: str) -> dict | None:
    try:
        resp = requests.get(f"{backend_url}/health", timeout=8)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        return {"status": "down", "components": [], "error": str(exc)}


def call_answer(backend_url: str, question: str, top_k: int, include_subgraph: bool, model: str | None) -> dict:
    payload = {
        "question": question,
        "top_k": top_k,
        "include_subgraph": include_subgraph,
    }
    if model:
        payload["model"] = model
    resp = requests.post(f"{backend_url}/answer", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def render_subgraph(subgraph: dict) -> str | None:
    nodes = subgraph.get("nodes", [])
    edges = subgraph.get("edges", [])
    if not nodes:
        return None
    net = Network(height="450px", width="100%", bgcolor="#0e1117", font_color="white", directed=True)
    net.barnes_hut(gravity=-4000, spring_length=120)
    for n in nodes:
        color = NODE_COLORS.get(n["label"], "#BBBBBB")
        title_lines = [f"{k}: {v}" for k, v in n.get("properties", {}).items() if v is not None]
        net.add_node(
            n["id"],
            label=n["name"],
            title="\n".join(title_lines) or n["name"],
            color=color,
            shape="dot",
            size=18,
        )
    node_ids = {n["id"] for n in nodes}
    for e in edges:
        if e["source"] in node_ids and e["target"] in node_ids:
            net.add_edge(e["source"], e["target"], label=e["type"], color="#888888", arrows="to")
    return net.generate_html(notebook=False)


def status_badge(status: str) -> str:
    return {"ok": "🟢 healthy", "degraded": "🟡 degraded", "down": "🔴 down"}.get(status, "⚪ unknown")


# ----------------------------- sidebar -----------------------------

with st.sidebar:
    st.title("🕸️ GraphRAG Console")
    st.caption("Neo4j + Groq · Tech/Startups Knowledge Graph")

    st.session_state.backend_url = st.text_input("Backend URL", value=st.session_state.backend_url)

    if st.button("🔄 Check backend health", use_container_width=True):
        st.session_state.last_health = call_health(st.session_state.backend_url)

    if "last_health" not in st.session_state:
        st.session_state.last_health = call_health(st.session_state.backend_url)

    health = st.session_state.last_health
    if health:
        st.markdown(f"**Overall:** {status_badge(health.get('status', 'unknown'))}")
        for comp in health.get("components", []):
            st.markdown(f"- `{comp['name']}` — {status_badge(comp['status'])}  \n  <sub>{comp.get('detail','')}</sub>", unsafe_allow_html=True)
        if health.get("error"):
            st.error(health["error"])

    st.divider()
    st.subheader("⚙️ Query settings")
    top_k = st.slider("Max rows returned", min_value=5, max_value=100, value=25, step=5)
    include_subgraph = st.checkbox("Render graph visualization", value=True)
    model_override = st.text_input(
        "Groq answer-model override (optional)",
        value="",
        placeholder="llama-3.3-70b-versatile",
        help="Only affects the natural-language answer step. Cypher is matched from a fixed template library, never LLM-generated.",
    )

    st.divider()
    st.subheader("💡 Try an example")
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, use_container_width=True, key=f"ex_{q}"):
            st.session_state.pending_question = q

    st.divider()
    with st.expander("📖 Graph ontology"):
        st.markdown(
            """
**Nodes:** `Person`, `Company`, `Investor`, `Product`, `Award`

**Relationships:**
- `(Person)-[FOUNDED {year}]->(Company)`
- `(Person)-[WORKS_AT {role, current}]->(Company)`
- `(Investor)-[INVESTED_IN {round, amount_million, year}]->(Company)`
- `(Company)-[ACQUIRED {year, amount_million}]->(Company)`
- `(Company)-[DEVELOPS]->(Product)`
- `(Person|Company)-[WON]->(Award)`
            """
        )

    if st.session_state.history and st.button("🗑️ Clear conversation", use_container_width=True):
        st.session_state.history = []
        st.rerun()


# ----------------------------- main -----------------------------

st.title("Tech / Startups Knowledge Graph — Ask Anything")
st.caption(
    "Ask a natural-language question. It's matched against a fixed library of read-only Cypher "
    "templates (no LLM writes Cypher), run against Neo4j, and answered by an LLM grounded "
    "strictly in the retrieved graph data."
)

for turn in st.session_state.history:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        st.markdown(turn["answer"])
        for w in turn.get("warnings", []):
            st.warning(w)
        cols = st.columns(3)
        cols[0].caption(f"⏱️ {turn.get('latency_ms', 0)} ms")
        cols[1].caption(f"🧠 {turn.get('model_used', '')}")
        cols[2].caption(f"📄 {turn.get('row_count', 0)} row(s)")

        tab_cypher, tab_results, tab_graph = st.tabs(["🔎 Matched Cypher Query", "📊 Raw Results", "🕸️ Graph View"])
        with tab_cypher:
            template_id = turn.get("template_id")
            if template_id:
                st.caption(f"Template: `{template_id}` — {turn.get('template_description', '')}")
            st.code(turn.get("cypher", ""), language="cypher")
            if turn.get("cypher_params"):
                st.json(turn["cypher_params"])
            st.caption("✅ Passed read-only validation" if turn.get("cypher_valid") else "❌ Failed validation")
        with tab_results:
            results = turn.get("results", [])
            if results:
                st.dataframe(pd.DataFrame(results), use_container_width=True)
            else:
                st.info("No rows returned.")
        with tab_graph:
            subgraph = turn.get("subgraph")
            html = render_subgraph(subgraph) if subgraph else None
            if html:
                components.html(html, height=470, scrolling=False)
            else:
                st.info("No subgraph available for this answer.")

question = st.chat_input("Ask about founders, companies, investors, products, or awards…")
if st.session_state.pending_question:
    question = st.session_state.pending_question
    st.session_state.pending_question = None

if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("Matching a Cypher template, querying Neo4j, and synthesizing an answer…"):
        try:
            t0 = time.time()
            data = call_answer(
                st.session_state.backend_url,
                question,
                top_k,
                include_subgraph,
                model_override or None,
            )
            st.markdown(data["answer"])
            for w in data.get("warnings", []):
                st.warning(w)
            cols = st.columns(3)
            cols[0].caption(f"⏱️ {data.get('latency_ms', 0)} ms")
            cols[1].caption(f"🧠 {data.get('model_used', '')}")
            cols[2].caption(f"📄 {data.get('row_count', 0)} row(s)")

            tab_cypher, tab_results, tab_graph = st.tabs(["🔎 Matched Cypher Query", "📊 Raw Results", "🕸️ Graph View"])
            with tab_cypher:
                template_id = data.get("template_id")
                if template_id:
                    st.caption(f"Template: `{template_id}` — {data.get('template_description', '')}")
                st.code(data.get("cypher", ""), language="cypher")
                if data.get("cypher_params"):
                    st.json(data["cypher_params"])
                st.caption("✅ Passed read-only validation" if data.get("cypher_valid") else "❌ Failed validation")
            with tab_results:
                results = data.get("results", [])
                if results:
                    st.dataframe(pd.DataFrame(results), use_container_width=True)
                else:
                    st.info("No rows returned.")
            with tab_graph:
                html = render_subgraph(data["subgraph"]) if data.get("subgraph") else None
                if html:
                    components.html(html, height=470, scrolling=False)
                else:
                    st.info("No subgraph available for this answer.")

            st.session_state.history.append({"question": question, **data})
        except requests.RequestException as exc:
            st.error(f"Could not reach the backend at `{st.session_state.backend_url}`: {exc}")
