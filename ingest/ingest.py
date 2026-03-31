#!/usr/bin/env python3
"""
Langfuse Power Demo — Ingestion Pipeline
Reads knowledge_base/faqs.csv, chunks the content, generates embeddings
via Ollama qwen3-embedding:4b and stores everything in Weaviate.
"""

import csv
import os
import time
import sys

import weaviate
from weaviate.classes.init import Auth
from llama_index.core import Document, VectorStoreIndex, StorageContext
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.vector_stores.weaviate import WeaviateVectorStore

# ── Configuration ────────────────────────────────────────────────────────────
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
WEAVIATE_URL    = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
EMBED_MODEL     = os.getenv("EMBED_MODEL", "qwen3-embedding:4b")
COLLECTION_NAME = "CustomerFAQ"
CSV_PATH        = os.path.join(os.path.dirname(__file__), "..", "knowledge_base", "faqs.csv")

# ── Retry helper (Ollama / Weaviate may still be starting) ───────────────────
def wait_for_service(url: str, label: str, retries: int = 30, delay: int = 5):
    import urllib.request, urllib.error
    for attempt in range(retries):
        try:
            urllib.request.urlopen(url, timeout=3)
            print(f"✅  {label} is ready.")
            return
        except Exception:
            print(f"⏳  Waiting for {label} ({attempt + 1}/{retries})…")
            time.sleep(delay)
    print(f"❌  {label} not reachable after {retries} attempts. Exiting.")
    sys.exit(1)


def load_documents(csv_path: str) -> list[Document]:
    """Read the FAQ CSV and return a list of LlamaIndex Documents."""
    docs = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            text = f"Question: {row['question']}\nAnswer: {row['answer']}"
            doc = Document(
                text=text,
                metadata={
                    "id": row["id"],
                    "category": row["category"],
                    "question": row["question"],
                },
            )
            docs.append(doc)
    print(f"📄  Loaded {len(docs)} documents from {csv_path}")
    return docs


def build_index(docs: list[Document], weaviate_client, embed_model: OllamaEmbedding):
    """Chunk documents and store embedded nodes in Weaviate."""

    # Delete existing collection so the ingest is idempotent
    if weaviate_client.collections.exists(COLLECTION_NAME):
        print(f"🗑️   Deleting existing collection '{COLLECTION_NAME}'…")
        weaviate_client.collections.delete(COLLECTION_NAME)

    vector_store   = WeaviateVectorStore(
        weaviate_client=weaviate_client,
        index_name=COLLECTION_NAME,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    splitter = SentenceSplitter(chunk_size=512, chunk_overlap=50)

    print(f"🔢  Generating embeddings with {EMBED_MODEL}…")
    index = VectorStoreIndex.from_documents(
        docs,
        storage_context=storage_context,
        embed_model=embed_model,
        transformations=[splitter],
        show_progress=True,
    )
    print(f"✅  Index built — {len(docs)} docs → Weaviate collection '{COLLECTION_NAME}'")
    return index


def main():
    # 1. Wait for dependencies
    wait_for_service(f"{OLLAMA_BASE_URL}/api/tags",  "Ollama")
    wait_for_service(f"{WEAVIATE_URL}/v1/.well-known/ready", "Weaviate")

    # 2. Load documents
    docs = load_documents(CSV_PATH)

    # 3. Build embedding model
    embed_model = OllamaEmbedding(
        model_name=EMBED_MODEL,
        base_url=OLLAMA_BASE_URL,
    )

    # 4. Connect to Weaviate (v4 client)
    weaviate_url_stripped = WEAVIATE_URL.replace("http://", "").replace("https://", "")
    host, _, port = weaviate_url_stripped.partition(":")
    port_int = int(port) if port else 8080
    client = weaviate.connect_to_custom(
        http_host=host,
        http_port=port_int,
        http_secure=False,
        grpc_host=host,
        grpc_port=50051,
        grpc_secure=False,
    )

    try:
        # 5. Build and store index
        build_index(docs, client, embed_model)
        print("🎉  Ingestion complete!")
    finally:
        client.close()


if __name__ == "__main__":
    main()
