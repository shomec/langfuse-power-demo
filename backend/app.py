#!/usr/bin/env python3
"""
Langfuse Power Demo — RAG Backend
OpenAI-compatible FastAPI endpoint that:
  - Retrieves context from Weaviate (LlamaIndex)
  - Calls Ollama qwen3.5:cloud for generation
  - Traces every step in Langfuse (traces, spans, token usage)
  - Supports demo scenarios: happy | slow | hallucinate
  - Use case: Daffy Duck College Enrollment Chatbot
"""

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import weaviate
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langfuse import Langfuse
from langfuse.model import ModelUsage
from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.settings import Settings
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.weaviate import WeaviateVectorStore
import httpx

# ── Configuration ────────────────────────────────────────────────────────────
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
WEAVIATE_URL      = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
LLM_MODEL         = os.getenv("LLM_MODEL", "qwen3.5:cloud")
EMBED_MODEL       = os.getenv("EMBED_MODEL", "qwen3-embedding:4b")
COLLECTION_NAME   = "EnrollmentFAQ"

LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-demo-secret-key")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-demo-public-key")
LANGFUSE_HOST       = os.getenv("LANGFUSE_HOST", "http://langfuse-server:3000")

# Prompt name tracked in Langfuse
PROMPT_NAME = "enrollment-support-v1"

# System prompt (also registered in Langfuse as a named prompt on startup)
BASE_SYSTEM_PROMPT = """You are a friendly and knowledgeable enrollment advisor for Daffy Duck College.
Answer questions accurately and concisely based ONLY on the provided context.
If the context does not contain the answer, say: "I'm sorry, I don't have that information. Please contact our Enrollment Office at enroll@daffyduck.edu or call (555) 325-3393 for further assistance."
Never make up information that is not in the context.
Context:
{context}"""

# ── Global singletons ─────────────────────────────────────────────────────────
langfuse: Langfuse | None = None
query_engine = None
weaviate_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global langfuse, query_engine, weaviate_client

    print("🚀  Starting Langfuse Power Demo Backend…")

    # Init Langfuse
    langfuse = Langfuse(
        secret_key=LANGFUSE_SECRET_KEY,
        public_key=LANGFUSE_PUBLIC_KEY,
        host=LANGFUSE_HOST,
    )

    # Register / get named prompt in Langfuse
    try:
        langfuse.create_prompt(
            name=PROMPT_NAME,
            prompt=BASE_SYSTEM_PROMPT,
            labels=["production"],
        )
        print(f"✅  Registered Langfuse prompt '{PROMPT_NAME}'")
    except Exception as e:
        print(f"ℹ️   Langfuse prompt may already exist: {e}")

    # Connect to Weaviate
    weaviate_url_stripped = WEAVIATE_URL.replace("http://", "").replace("https://", "")
    host, _, port = weaviate_url_stripped.partition(":")
    port_int = int(port) if port else 8080
    weaviate_client = weaviate.connect_to_custom(
        http_host=host,
        http_port=port_int,
        http_secure=False,
        grpc_host=host,
        grpc_port=50051,
        grpc_secure=False,
    )

    # LlamaIndex settings
    embed = OllamaEmbedding(model_name=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
    llm   = Ollama(model=LLM_MODEL, base_url=OLLAMA_BASE_URL, request_timeout=120.0)
    Settings.embed_model = embed
    Settings.llm         = llm

    vector_store    = WeaviateVectorStore(
        weaviate_client=weaviate_client,
        index_name=COLLECTION_NAME,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index           = VectorStoreIndex.from_vector_store(
        vector_store, storage_context=storage_context
    )
    query_engine = index.as_query_engine(similarity_top_k=3)
    print(f"✅  Connected to Weaviate collection '{COLLECTION_NAME}'")
    print(f"✅  LLM: {LLM_MODEL}  |  Embed: {EMBED_MODEL}")

    yield

    # Cleanup
    if weaviate_client:
        weaviate_client.close()
    if langfuse:
        langfuse.flush()
    print("👋  Backend shutdown.")


app = FastAPI(title="Langfuse Power Demo — Daffy Duck College Enrollment Bot", lifespan=lifespan)


# ── Helpers ──────────────────────────────────────────────────────────────────

def detect_demo_mode(messages: list[dict]) -> str:
    """
    Detect demo mode from the last user message prefix.
      [SLOW]        → artificial latency scenario
      [HALLUCINATE] → off-domain question to trigger hallucination
    Returns: 'slow' | 'hallucinate' | 'happy'
    """
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    upper = last_user.strip().upper()
    if upper.startswith("[SLOW]"):
        return "slow"
    if upper.startswith("[HALLUCINATE]"):
        return "hallucinate"
    return "happy"


def strip_demo_prefix(content: str) -> str:
    for prefix in ("[SLOW]", "[HALLUCINATE]"):
        if content.upper().startswith(prefix):
            return content[len(prefix):].strip()
    return content


def estimate_tokens(text: str) -> int:
    """Rough token estimate (≈4 chars per token)."""
    return max(1, len(text) // 4)


async def evaluate_response_async(
    trace,
    user_query: str,
    context: str,
    answer: str,
) -> None:
    """
    LLM-as-a-judge: call Ollama to score the generated answer on:
      - faithfulness     : does the answer only use info from context? (1=faithful)
      - groundedness     : is the answer supported by the context?    (1=grounded)
      - hallucination_score: does the answer contain invented info?   (1=hallucinated)
    Posts scores directly to the Langfuse trace.
    Runs as a background task so it does not block the HTTP response.
    """
    eval_prompt = f"""You are an objective AI evaluator. Evaluate the following AI-generated answer for a college enrollment chatbot.

Student Question: {user_query}

Retrieved Context (the ONLY source the AI should use):
{context}

AI-Generated Answer:
{answer}

Score each dimension from 0.0 to 1.0. Return ONLY a valid JSON object — no extra text.

- faithfulness: Does the answer contain ONLY information from the context? 1.0 = perfectly faithful, 0.0 = completely fabricated.
- groundedness: Is every claim in the answer directly supported by the context? 1.0 = fully grounded, 0.0 = not grounded at all.
- hallucination_score: Does the answer contain information NOT present in the context? 1.0 = fully hallucinated, 0.0 = no hallucination.

Return exactly this JSON (replace values):
{{"faithfulness": 0.0, "groundedness": 0.0, "hallucination_score": 0.0}}"""

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": eval_prompt}],
                    "stream": False,
                    "format": "json",
                },
            )
        raw = resp.json().get("message", {}).get("content", "{}")
        scores = json.loads(raw)

        valid_keys = {"faithfulness", "groundedness", "hallucination_score"}
        for name, value in scores.items():
            if name in valid_keys:
                clamped = max(0.0, min(1.0, float(value)))
                comment_map = {
                    "faithfulness": (
                        "High: answer stays within context" if clamped >= 0.7
                        else "Low: answer may contain off-context information"
                    ),
                    "groundedness": (
                        "High: claims well-supported by retrieved docs" if clamped >= 0.7
                        else "Low: claims lack support in retrieved docs"
                    ),
                    "hallucination_score": (
                        "High: answer appears hallucinated ⚠️" if clamped >= 0.5
                        else "Low: answer appears grounded"
                    ),
                }
                trace.score(
                    name=name,
                    value=clamped,
                    comment=f"LLM-as-a-judge ({LLM_MODEL}) — {comment_map[name]}",
                )
        print(f"✅  Evaluation scores posted: {scores}")
    except Exception as exc:
        print(f"⚠️  LLM-as-judge evaluation failed: {exc}")


# ── Main chat endpoint ────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages: list[dict] = body.get("messages", [])
    stream: bool         = body.get("stream", False)
    model: str           = body.get("model", "rag")

    # Session / user tracking (OpenWebUI passes these headers)
    session_id = request.headers.get("X-Session-Id") or str(uuid.uuid4())
    user_id    = request.headers.get("X-User-Id", "anonymous")

    demo_mode = detect_demo_mode(messages)

    # Get last user message (stripped of demo prefix)
    user_query_raw = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    user_query = strip_demo_prefix(user_query_raw)

    # ── For hallucinate mode substitute an off-domain question ───────────────
    if demo_mode == "hallucinate":
        user_query = user_query or "What are the admission requirements for Harvard Medical School and how does Daffy Duck College compare to MIT in research output rankings?"

    # ── Start Langfuse Trace ─────────────────────────────────────────────────
    trace = langfuse.trace(
        name="enrollment-advisor-chat",
        session_id=session_id,
        user_id=user_id,
        input={"query": user_query, "demo_mode": demo_mode},
        tags=[demo_mode, f"model:{model}"],
        metadata={"llm_model": LLM_MODEL, "embed_model": EMBED_MODEL, "college": "Daffy Duck College"},
    )

    try:
        # ── Retrieval span ───────────────────────────────────────────────────
        retrieval_span = trace.span(name="weaviate-retrieval", input={"query": user_query})
        t_retrieval_start = time.perf_counter()

        retrieved_nodes  = query_engine.retrieve(user_query)
        retrieval_latency = time.perf_counter() - t_retrieval_start

        context_chunks = [n.node.get_content() for n in retrieved_nodes]
        context_text   = "\n\n".join(context_chunks)
        retrieval_score = float(retrieved_nodes[0].score) if retrieved_nodes else 0.0

        retrieval_span.end(
            output={
                "num_chunks": len(retrieved_nodes),
                "top_score": retrieval_score,
                "preview": context_text[:300],
            },
            metadata={"latency_ms": round(retrieval_latency * 1000, 2)},
        )

        # ── Slow scenario: artificial delay ──────────────────────────────────
        if demo_mode == "slow":
            delay_span = trace.span(name="artificial-latency-delay")
            await asyncio.sleep(4)          # 4-second delay
            delay_span.end(metadata={"injected_delay_ms": 4000})

        # ── Fetch named prompt from Langfuse ─────────────────────────────────
        try:
            lf_prompt = langfuse.get_prompt(PROMPT_NAME)
            system_prompt_template = lf_prompt.prompt
        except Exception:
            system_prompt_template = BASE_SYSTEM_PROMPT

        system_content = system_prompt_template.replace("{context}", context_text)

        # Build final message list to send to Ollama
        ollama_messages = [{"role": "system", "content": system_content}]
        # Include prior conversation turns (exclude the raw user message)
        for m in messages[:-1]:
            if m.get("role") in ("user", "assistant"):
                ollama_messages.append(m)
        ollama_messages.append({"role": "user", "content": user_query})

        # ── Generation span ──────────────────────────────────────────────────
        generation_span = trace.generation(
            name="ollama-generation",
            model=LLM_MODEL,
            input=ollama_messages,
            prompt=lf_prompt if "lf_prompt" in dir() else None,
            metadata={
                "demo_mode": demo_mode,
                "retrieval_score": retrieval_score,
            },
        )
        t_gen_start = time.perf_counter()

        # ── Call Ollama ───────────────────────────────────────────────────────
        async with httpx.AsyncClient(timeout=180.0) as client:
            ollama_resp = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": LLM_MODEL,
                    "messages": ollama_messages,
                    "stream": False,
                },
            )
        ollama_data = ollama_resp.json()
        gen_latency = time.perf_counter() - t_gen_start

        assistant_text = ollama_data.get("message", {}).get("content", "")
        ollama_usage   = ollama_data.get("prompt_eval_count", 0), ollama_data.get("eval_count", 0)
        prompt_tokens, completion_tokens = ollama_usage

        generation_span.end(
            output=assistant_text,
            usage=ModelUsage(
                input=prompt_tokens,
                output=completion_tokens,
                unit="TOKENS",
            ),
            metadata={
                "latency_ms": round(gen_latency * 1000, 2),
                "retrieval_latency_ms": round(retrieval_latency * 1000, 2),
            },
        )

        # ── Retrieval confidence score (always posted for score distribution) ─
        trace.score(
            name="retrieval-confidence",
            value=retrieval_score,
            comment=(
                "Good retrieval match" if retrieval_score >= 0.5
                else "Low retrieval score — possible hallucination risk"
            ),
        )

        # ── End trace ─────────────────────────────────────────────────────────
        trace.update(
            output=assistant_text,
            metadata={
                "total_latency_ms": round((time.perf_counter() - t_retrieval_start) * 1000, 2),
                "demo_mode": demo_mode,
            },
        )

        # ── LLM-as-judge evaluation (async background — does not block response)
        asyncio.create_task(
            evaluate_response_async(
                trace=trace,
                user_query=user_query,
                context=context_text,
                answer=assistant_text,
            )
        )

    except Exception as exc:
        trace.update(output=f"ERROR: {exc}", level="ERROR")
        raise

    # ── Return OpenAI-compatible response ─────────────────────────────────────
    response_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    response_body = {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": assistant_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }

    if stream:
        # Minimal SSE streaming wrapper
        async def sse_generator() -> AsyncIterator[str]:
            chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"content": assistant_text}, "finish_reason": None}
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            done = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse_generator(), media_type="text/event-stream")

    return JSONResponse(content=response_body)


@app.get("/health")
async def health():
    return {"status": "ok", "llm": LLM_MODEL, "embed": EMBED_MODEL}


@app.get("/v1/models")
async def list_models():
    """OpenAI-compatible model list so OpenWebUI can discover the backend."""
    return {
        "object": "list",
        "data": [
            {
                "id": "rag",
                "object": "model",
                "created": 1700000000,
                "owned_by": "langfuse-demo",
                "display_name": "Daffy Duck College Enrollment Advisor (RAG)",
            }
        ],
    }
