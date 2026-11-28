"""Настройки читаются из переменных окружения при старте процесса."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESSDATA = Path(os.getenv("RAG_TESSDATA_DIR", ROOT / ".ocr-models"))


@dataclass(frozen=True)
class Config:
    data: Path = field(default_factory=lambda: Path(os.getenv("RAG_DATA_DIR", ROOT / ".rag")).resolve())
    documents: Path = field(default_factory=lambda: Path(os.getenv("RAG_DOCS_DIR", ROOT / "documents")).resolve())
    ollama: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"))
    model: str = field(default_factory=lambda: os.getenv("RAG_MODEL", "qwen3:8b-q8_0"))
    reranker: str = field(default_factory=lambda: os.getenv("RAG_RERANKER", "Qwen/Qwen3-Reranker-4B"))
    embedding: str = field(default_factory=lambda: os.getenv("RAG_EMBED_MODEL", "bge-m3"))
    qdrant_url: str = field(default_factory=lambda: os.getenv("QDRANT_URL", ""))
    collection: str = field(default_factory=lambda: os.getenv("QDRANT_COLLECTION", "regulations_v1"))
    reviewer_token: str = field(default_factory=lambda: os.getenv("RAG_REVIEW_TOKEN", ""))
    top_k: int = field(default_factory=lambda: max(1, min(12, int(os.getenv("RAG_TOP_K", "6")))))
    max_clarifications: int = 2
    context_messages: int = field(default_factory=lambda: max(1, int(os.getenv("RAG_CONTEXT_MESSAGES", "5"))))
