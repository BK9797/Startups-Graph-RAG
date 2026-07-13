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

**Tech / Startups GraphRAG** is a lightweight Retrieval-Augmented Generation (RAG) application built on top of a **Neo4j Knowledge Graph**.

Instead of asking an LLM to invent database queries, the system first retrieves the most relevant graph entities using **embedding similarity** and **Neo4j Full-Text Search**, builds a graph context, and finally asks **Groq LLM** to generate an answer **grounded only in the retrieved data**.

This architecture provides:

- ⚡ Fast retrieval
- 🎯 Accurate graph-based answers
- 🔒 No hallucinated database queries
- 🧠 Context-aware natural language responses

---

# ✨ Features

- 💬 Natural language question answering
- 🕸️ Neo4j Knowledge Graph backend
- 🔎 Embedding-based entity retrieval
- 📚 Neo4j Full-Text Search fallback
- 🤖 Groq LLM answer synthesis
- 🚀 FastAPI REST API
- 🎨 Interactive Streamlit frontend
- 🧪 Automated testing with Pytest
- ☁️ Ready for Railway deployment

---

# 🏗️ System Architecture

```text
                Natural Language Question
                          │
                          ▼
                 Embedding Similarity Search
                          │
                          ▼
                Neo4j Full-Text Search (Fallback)
                          │
                          ▼
              Retrieve Relevant Graph Entities
                          │
                          ▼
                  Build Graph Context
                          │
                          ▼
              Groq LLM (Grounded Generation)
                          │
                          ▼
                    Natural Language Answer
```

---

# 🛠️ Tech Stack

| Category | Technology |
|----------|------------|
| **Backend** | FastAPI |
| **Database** | Neo4j |
| **Vector Retrieval** | Embedding Similarity |
| **Search** | Neo4j Full-Text Index |
| **LLM** | Groq |
| **Frontend** | Streamlit |
| **Testing** | Pytest |
| **Deployment** | Railway |

---

# 📂 Project Structure

```text
Startups-Graph-RAG
│
├── app
│   ├── api
│   │   └── routes.py
│   ├── core
│   │   ├── embedding.py
│   │   ├── graph_rag.py
│   │   └── llm.py
│   ├── db
│   │   └── neo4j_client.py
│   ├── frontend
│   │   └── streamlit_app.py
│   └── main.py
│
├── scripts
│   └── load_neo4j.py
│
├── tests
│
├── requirements.txt
└── README.md
```

---

# ⚡ Quick Start

## 1️⃣ Clone the Repository

```bash
git clone https://github.com/BK9797/Startups-Graph-RAG.git

cd Startups-Graph-RAG
```

---

## 2️⃣ Create a Virtual Environment

```bash
python -m venv .venv

source .venv/bin/activate      # Linux / macOS

# Windows
.venv\Scripts\activate
```

---

## 3️⃣ Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 4️⃣ Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` and provide:

```text
NEO4J_URI=
NEO4J_USERNAME=
NEO4J_PASSWORD=
GROQ_API_KEY=
```

---

## 5️⃣ Run the Backend

```bash
make api
```

The API will be available at

```
http://localhost:8000
```

Interactive Swagger documentation:

```
http://localhost:8000/docs
```

---

# 🎨 Streamlit Frontend

Launch the interactive interface:

```bash
streamlit run app/frontend/streamlit_app.py
```

The Streamlit application provides an intuitive chat interface for querying the graph using natural language.

---

# 🧪 Run Tests

Execute the complete test suite:

```bash
make test
```

---

# 💬 Example API Request

```json
{
    "question": "Who founded NovaPay?"
}
```

---

# ✅ Example Response

```json
{
    "answer": "NovaPay was founded by Elena Rossi in 2016.",
    "template_id": "embedding"
}
```

---


# 📌 Key Design Decisions

### 🔹 Embedding-First Retrieval

The system first retrieves semantically similar entities using vector embeddings before falling back to Neo4j Full-Text Search.

### 🔹 Grounded Generation

Groq receives **only the retrieved graph context**, ensuring responses remain faithful to the underlying knowledge graph.

### 🔹 Lightweight Graph Context

Only the most relevant nodes and relationships are included in the prompt, keeping responses fast and reducing unnecessary context.

### 🔹 No LLM-Generated Cypher

The language model never writes Cypher queries or directly interacts with the database.

---

# 👨‍💻 Author

**Bishoy Kamel**

AI Engineer

- 💼 LinkedIn: https://www.linkedin.com/in/bishoy-kamel-5b53a6254/
- 🐙 GitHub: https://github.com/BK9797

---

<div align="center">

### ⭐ If you found this project useful, consider giving it a Star!

</div>