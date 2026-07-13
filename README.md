<div align="center">

# 🕸️ Tech / Startups GraphRAG

### 🚀 A Graph Retrieval-Augmented Generation (GraphRAG) System for Startup Intelligence

Query a **Neo4j Knowledge Graph** using natural language, retrieve relevant graph context with **Embedding + Full-Text Search**, and generate grounded answers using **Groq LLM**.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Neo4j](https://img.shields.io/badge/Neo4j-008CC1?style=for-the-badge&logo=neo4j&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-LLM-orange?style=for-the-badge)
![Railway](https://img.shields.io/badge/Railway-Deployed-6C47FF?style=for-the-badge&logo=railway&logoColor=white)

</div>

---

# 📖 Overview

This project is a lightweight GraphRAG app for asking natural-language questions about a startup/tech knowledge graph stored in Neo4j.

The flow is simple:

1. The question is matched against graph entity names using embedding similarity.
2. Relevant entities are retrieved from Neo4j.
3. A Groq model generates a grounded answer from that retrieved context.

This keeps the answers focused on the graph data instead of relying on the model to invent facts.

---

# ⚡ Quick Start

## 1. Clone and enter the repo

```bash
git clone https://github.com/BK9797/Startups-Graph-RAG.git
cd Startups-Graph-RAG
```

## 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure environment variables

```bash
cp .env.example .env
```

Set at least these values in `.env`:

```text
NEO4J_URI=
NEO4J_USERNAME=
NEO4J_PASSWORD=
GROQ_API_KEY=
```

## 5. Run the backend

```bash
make api
```

Open:
- API: http://localhost:8000
- Docs: http://localhost:8000/docs

## 6. Run the frontend

```bash
./.venv/bin/python -m streamlit run app/frontend/streamlit_app.py --server.port 8501 --server.address 127.0.0.1
```

Open: http://127.0.0.1:8501

---

# 🧪 Run Tests

```bash
make test
```

---

# 💬 Example

Request:

```json
{
  "question": "Who founded NovaPay?"
}
```

Response idea:

```json
{
  "answer": "NovaPay was founded by Elena Rossi in 2016.",
  "template_id": "embedding"
}
```

---

# 🔑 Key Points

- The app uses embedding-based retrieval first.
- Neo4j full-text search is used as a fallback helper.
- The LLM only synthesizes answers from retrieved graph context.
- It is designed to be simple, local-first, and easy to run.

---

# 👨‍💻 Author

Bishoy Kamel

AI Engineer

- LinkedIn: https://www.linkedin.com/in/bishoy-kamel-5b53a6254/
- GitHub: https://github.com/BK9797

---

<div align="center">

### ⭐ If you found this project useful, consider giving it a Star!

</div>