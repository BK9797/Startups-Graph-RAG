"""
Centralized application settings, loaded from environment variables
(or a local .env file — see .env.example for the full list).
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Neo4j ---
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_username: str = Field(default="neo4j", alias="NEO4J_USERNAME")
    neo4j_password: str = Field(default="", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    # --- Groq LLM ---
    # Used for exactly one thing: synthesizing a natural-language answer
    # from already-retrieved graph rows. Cypher is never LLM-generated —
    # see app/core/cypher_library.py.
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_answer_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_ANSWER_MODEL")
    groq_temperature_answer: float = Field(default=0.2, alias="GROQ_TEMPERATURE_ANSWER")

    # --- App ---
    app_env: str = Field(default="development", alias="APP_ENV")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")
    max_cypher_rows: int = Field(default=50, alias="MAX_CYPHER_ROWS")
    request_timeout_seconds: int = Field(default=30, alias="REQUEST_TIMEOUT_SECONDS")

    # --- Frontend (used by the Streamlit app, not the API itself) ---
    backend_url: str = Field(default="http://localhost:8000", alias="BACKEND_URL")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
