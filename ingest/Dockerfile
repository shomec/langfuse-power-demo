FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for layer caching
COPY ingest/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy source files (build context is repo root)
COPY ingest/ingest.py /app/ingest.py
COPY knowledge_base/ /app/knowledge_base/

CMD ["python", "ingest.py"]
