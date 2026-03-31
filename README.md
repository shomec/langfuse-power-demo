# 🦆 Daffy Duck College — Enrollment Advisor Bot
### AI-Powered Enrollment Chatbot — Langfuse Observability Showcase

A fully local, Docker-composable stack that demonstrates how **Langfuse** provides end-to-end observability over a production-grade RAG chatbot. Here, the chatbot acts as an **enrollment advisor** for an imaginary college named **Daffy Duck College**, answering prospective student questions about admissions, financial aid, programs, registration, campus life, and more. Every interaction is traced, tokenized, scored, and visible in the Langfuse dashboard — side-by-side with your conversation in OpenWebUI.

---

## 🏗️ Architecture

![System Architecture Diagram](docs/architecture.png)

---

## 🧱 Tech Stack

### 1. [OpenWebUI](https://github.com/open-webui/open-webui)
**Role:** Chat frontend
OpenWebUI is an open-source, ChatGPT-like interface that connects to any OpenAI-compatible API. In this demo it serves as the student-facing enrollment chat window. Users type questions here; OpenWebUI forwards them to the RAG Backend.

---

### 2. [Ollama](https://ollama.com/)
**Role:** Local LLM + Embedding server
Ollama runs large language models locally with no cloud dependency. It exposes an OpenAI-compatible HTTP API.

| Model | Purpose |
|---|---|
| `qwen3.5:cloud` | Main conversational LLM — generates advisor responses |
| `qwen3-embedding:4b` | Converts text into vector embeddings for Weaviate |

Both models are **automatically pulled** on first `docker compose up`.

---

### 3. [Weaviate](https://weaviate.io/)
**Role:** Vector database (RAG knowledge store)
Weaviate stores the embedded FAQ chunks in a collection called `EnrollmentFAQ`. At query time, the backend converts the student's question into an embedding and performs a similarity search to retrieve the most relevant FAQ entries as context.

---

### 4. [LlamaIndex](https://www.llamaindex.ai/)
**Role:** RAG orchestration framework
LlamaIndex handles:
- **Ingestion**: reads `faqs.csv` → splits into chunks → generates embeddings → stores in Weaviate
- **Retrieval**: at query time, queries Weaviate for the top-3 similar FAQ chunks
- **Query Engine**: wraps retrieval + generation into a single pipeline

---

### 5. [Langfuse](https://langfuse.com/)
**Role:** LLM observability & AI ops platform
Langfuse is the star of this demo. It captures:
- **Traces** — one per chat message, with full input/output
- **Spans** — nested timeline of retrieval and generation steps
- **Token usage** — prompt tokens, completion tokens, totals
- **Latency** — per-span timing breakdown
- **Scores** — automatic retrieval confidence score for hallucination detection
- **Sessions** — groups traces by student conversation
- **Prompt versioning** — the system prompt is registered as a named, versioned prompt

Langfuse runs locally via Docker (Postgres + ClickHouse backend).

---

### 6. Knowledge Base (`knowledge_base/faqs.csv`)
50 enrollment FAQs across 7 categories:

| Category | Topic examples |
|---|---|
| Admissions | How to apply, deadlines, GPA requirements, test-optional policy, transfer students |
| Financial Aid | FAFSA, scholarships, payment plans, work-study, appeals |
| Programs | Undergraduate & graduate programs, online options, double major, Honors Program |
| Registration | How to register, add/drop deadlines, credit limits, withdrawal policy |
| Campus Life | Housing, meal plans, student clubs, health center, athletics |
| International Students | F-1 visa, I-20, TOEFL/IELTS requirements, on-campus work |
| Academics | Grading scale, good standing GPA, transcripts, tutoring, graduation |

---

## ⚙️ Prerequisites

| Requirement | Notes |
|---|---|
| **Docker Desktop** | v24+ — allocate **16 GB RAM** minimum (Ollama models are large) |
| **Docker Compose v2** | Included with Docker Desktop |
| **~10 GB disk space** | For model weights + Docker images |
| **Internet access** | Only on first run (model pulls + image downloads) |

---

## 🛠️ Step-by-Step Setup

### Step 1 — Navigate to the project

```bash
cd /path/to/langfuse-power-demo
```

### Step 2 — Configure environment

```bash
cp .env.example .env
# Edit .env if you want to change secret keys (defaults work out of the box)
```

### Step 3 — Start the full stack

```bash
docker compose up -d --build
```

This starts 8 containers in dependency order:
1. `postgres` + `clickhouse` (Langfuse storage)
2. `weaviate` (vector DB)
3. `ollama` (LLM server — pulls models on first boot)
4. `langfuse-server` (observability UI)
5. `ingest` (one-shot init job: embeds FAQs → Weaviate — exits when done)
6. `backend` (RAG API — waits for ingest to complete)
7. `open-webui` (chat frontend — waits for backend)

### Step 4 — Monitor startup

```bash
# Check all containers
docker compose ps

# Watch Ollama pull the models (5-10 min first time)
docker compose logs -f ollama

# Watch the knowledge base being embedded
docker compose logs -f ingest

# Watch the RAG backend start
docker compose logs -f backend
```

**Stack is ready when `docker compose ps` shows all services as `healthy` or `exited 0` (ingest).**

### Step 5 — Connect OpenWebUI to the RAG backend

> One-time manual step after first boot.

1. Open **http://localhost:3000**
2. Skip sign-in (auth is disabled for demo)
3. Click your **avatar (top-right) → Settings → Connections**
4. Under **OpenAI API**, set:
   - **API Base URL**: `http://backend:8000/v1`
   - **API Key**: `demo-key`
5. Click **Save** ✅
6. In the model selector (top of chat), choose **"rag"**

### Step 6 — Open Langfuse side-by-side

1. Open **http://localhost:3001** in a second tab
2. Login: `admin@daffyduck.edu` / `GoFightingDucks!`
3. Navigate to **Traces** — every message you send in OpenWebUI appears here in real time

---

## 🧪 Testing the Demo Scenarios

---

### ✅ Scenario 1: Happy Path (Normal Enrollment Q&A)

**What it tests:** Normal knowledge base retrieval + grounded answer generation.

**How to trigger:** Ask any question covered by the FAQ knowledge base.

**Example prompts:**
```
How do I apply to Daffy Duck College?
What is the application deadline for fall enrollment?
Are there scholarships for first-generation college students?
What meal plans are available?
Do international students need a visa?
How do I register for classes?
What GPA do I need to maintain good academic standing?
```

**What you see in Langfuse:**

1. Go to **Traces** → click the latest trace named `enrollment-advisor-chat`
2. You'll see a **timeline with two nested spans**:
   - `weaviate-retrieval` — time taken to find relevant FAQ chunks
   - `ollama-generation` — time taken by the LLM to write the response
3. Click the **Generation span** → **Usage** tab → shows prompt tokens, completion tokens, total
4. The trace **Input** shows the student's question; **Output** shows the advisor's answer
5. Navigate to **Sessions** → each OpenWebUI conversation is one session

**Langfuse features visible:** Prompt tracking, token usage, latency breakdown, session tracing

---

### 🐌 Scenario 2: Poor Latency (Slow Response)

**What it tests:** How Langfuse surfaces latency outliers and isolates which step is slow.

**How to trigger:** Prefix your message with `[SLOW]`

**Example prompts:**
```
[SLOW] How do I apply for financial aid?
[SLOW] What programs does Daffy Duck College offer?
[SLOW] How do I request an official transcript?
```

**What happens under the hood:** A 4-second artificial sleep is injected before the LLM call, mimicking a real-world scenario such as a slow embedding server or overloaded model.

**What you see in Langfuse:**

1. Go to **Traces** → the slow trace shows a visibly longer **Duration** column
2. Click the trace → **Timeline view** → a third span `artificial-latency-delay` takes ~4 seconds
3. Compare this timeline against a happy-path trace — the retrieval span is fast; the bottleneck is isolated
4. Filter by **Tag → slow** to surface all slow-mode traces in bulk
5. In production, this is where you'd use Langfuse to identify which service causes latency

**Langfuse features visible:** Latency breakdown per span, outlier detection, tag-based filtering

---

### 🤔 Scenario 3: Hallucination Detection

**What it tests:** How Langfuse helps detect when the LLM makes up information not in the knowledge base.

**How to trigger:** Prefix your message with `[HALLUCINATE]`

**Example prompts:**
```
[HALLUCINATE] How does Daffy Duck College rank against MIT?
[HALLUCINATE] What are the NBA draft prospects from Daffy Duck College?
[HALLUCINATE] Tell me about Daffy Duck College's quantum computing research lab
```

**What happens under the hood:** The backend intercepts the message and sends an out-of-scope question to the LLM with no matching context in Weaviate. The LLM may still generate a plausible-sounding but fabricated answer.

**What you see in Langfuse:**

1. Go to **Traces** → click the hallucinate-tagged trace
2. Under `weaviate-retrieval` → **Output** → `top_score` will be very low (< 0.5)
3. The trace will have a **Score** called `retrieval-confidence` with value < 0.5 and comment: _"Low retrieval score — possible hallucination risk"_
4. Look at the `preview` field in the retrieval span — unrelated or no matching chunks were found
5. The generated answer may still sound confident — this is the hallucination
6. Filter by **Tag → hallucinate** to find all flagged conversations
7. In production, hook this score to an alert or human-review queue

**Langfuse features visible:** Retrieval confidence scoring, hallucination risk flagging, annotation/scoring system

---

## 📊 Langfuse Dashboard & Metrics

Open Langfuse at **http://localhost:3001** → **Dashboard** (left menu) to see all built-in charts automatically populated as you run the demo scenarios.

### Built-in Metrics

| Metric | Dashboard location | What to look for |
|---|---|---|
| **Trace counts** | Dashboard → *Traces* chart | Volume per time bucket; spikes during test runs |
| **Latency — average** | Dashboard → *Latency* chart | Baseline ~2–5 s for happy path |
| **Latency — P95** | Dashboard → *Latency* chart (toggle percentile) | Should spike to ~9–10 s after `[SLOW]` messages |
| **Token usage** | Dashboard → *Token Usage* chart | Prompt + completion tokens per model |
| **Model usage** | Dashboard → *Model* breakdown | Confirms `qwen3.5:cloud` is logged correctly |
| **Score distributions** | Dashboard → *Scores* section | Shows histogram across all score names |

> [!TIP]
> Use the **time-range picker** (top-right of Dashboard) and the **Tag filter** (`happy`, `slow`, `hallucinate`) to slice metrics per scenario side-by-side.

---

### LLM-as-a-Judge Evaluation Scores

After every chat message the backend fires an async **LLM-as-a-judge** call back to Ollama. The same `qwen3.5:cloud` model reads the question, retrieved context, and generated answer, then returns three scores in JSON. These are posted to the Langfuse trace automatically.

| Score name | Range | Meaning |
|---|---|---|
| `faithfulness` | 0.0 – 1.0 | Does the answer use **only** information from the retrieved context? (1.0 = perfectly faithful) |
| `groundedness` | 0.0 – 1.0 | Is every claim in the answer **directly supported** by the retrieved docs? (1.0 = fully grounded) |
| `hallucination_score` | 0.0 – 1.0 | Does the answer contain information **not present** in the context? (1.0 = fully hallucinated) |
| `retrieval-confidence` | 0.0 – 1.0 | Weaviate cosine similarity of the top retrieved chunk (proxy for retrieval quality) |

#### Where to see scores in Langfuse

1. **Per-trace view** → click any trace → **Scores** tab → all four scores with their auto-generated comments
2. **Score distributions** → Dashboard → *Scores* section → histogram per score name
3. **Filtering by score** → Traces table → click **Filters** → *Score* → set `hallucination_score > 0.5` to surface suspicious traces instantly
4. **Comparing scenarios** → filter `tag=hallucinate` and note how `faithfulness` and `groundedness` drop vs `tag=happy`

#### Expected score patterns per scenario

| Scenario | `faithfulness` | `groundedness` | `hallucination_score` | `retrieval-confidence` |
|---|---|---|---|---|
| ✅ Happy path | 0.8 – 1.0 | 0.8 – 1.0 | 0.0 – 0.2 | 0.6 – 1.0 |
| 🐌 Slow | Same as happy (delay only) | Same as happy | Same as happy | Same as happy |
| 🤔 Hallucinate | 0.0 – 0.3 | 0.0 – 0.3 | 0.7 – 1.0 | 0.0 – 0.3 |

> [!NOTE]
> Evaluation scores appear **a few seconds after** the chat response because the judge call runs as an async background task — refresh the trace in Langfuse to see them populate.

---

## 🗺️ Langfuse Feature Reference


| Feature | Where to find it in the UI |
|---|---|
| **Prompt tracking** | Left menu → **Prompts** → `enrollment-support-v1` (versioned, editable) |
| **Token usage + cost** | Traces → any trace → Generation span → **Usage** tab |
| **Latency breakdown** | Traces → any trace → **Timeline** view (nested spans) |
| **Hallucination debugging** | Traces → filter `tag=hallucinate` → check **Scores** column |
| **Session tracing** | Left menu → **Sessions** → select any session → full conversation |
| **Score / annotation** | Traces → any trace → **Scores** tab |

---

## 🔬 Direct API Testing (without OpenWebUI)

```bash
# Happy path
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"rag","messages":[{"role":"user","content":"How do I apply to Daffy Duck College?"}]}' \
  | jq .choices[0].message.content

# Slow path (watch Langfuse latency spike)
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"rag","messages":[{"role":"user","content":"[SLOW] What financial aid is available?"}]}' \
  | jq .

# Hallucination path (watch Langfuse retrieval score drop)
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"rag","messages":[{"role":"user","content":"[HALLUCINATE] How does DDC rank against MIT?"}]}' \
  | jq .

# Verify Weaviate has the FAQ data
curl -s "http://localhost:8080/v1/objects?class=EnrollmentFAQ&limit=3" \
  | jq '.objects[].properties.question'

# Backend health
curl http://localhost:8000/health
```

---

## 🛑 Teardown

```bash
# Stop containers but keep data volumes (faster restart next time)
docker compose down

# Full reset — removes all volumes (models, embeddings, Langfuse traces)
docker compose down -v
```

---

## 📁 Project Structure

```
langfuse-power-demo/
├── .env.example                   Environment variable template
├── docker-compose.yml             8-service stack definition
├── README.md                      This file
├── docs/
│   └── architecture.png           System architecture diagram
│
├── knowledge_base/
│   └── faqs.csv                   50 enrollment FAQs (7 categories)
│
├── ingest/
│   ├── ingest.py                  LlamaIndex ingestion pipeline → Weaviate
│   ├── requirements.txt
│   └── Dockerfile
│
└── backend/
    ├── app.py                     FastAPI RAG backend + Langfuse tracing
    ├── requirements.txt
    └── Dockerfile
```

---

## 🩺 Troubleshooting

| Problem | Fix |
|---|---|
| Ollama models not pulled | `docker compose logs ollama` — wait for `✅ Ollama models ready` |
| Ingest container fails | `docker compose logs ingest` — Ollama may not be ready; run `docker compose restart ingest` |
| OpenWebUI shows no models | Confirm backend is healthy: `curl http://localhost:8000/health` |
| Langfuse shows no traces | Check backend logs: `docker compose logs backend` |
| `qwen3.5:cloud` not found | Requires Ollama ≥ 0.5. Run `docker compose pull ollama` to update |
