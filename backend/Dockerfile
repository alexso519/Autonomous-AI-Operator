# FastAPI Personal AI Agent Workspace - Backend
# Single-stage build with C++ tools for chroma-hnswlib

FROM python:3.12-slim

WORKDIR /app

# Install build dependencies for chroma-hnswlib (required by crewai/chromadb)
RUN apt-get update && \
    apt-get install -y --no-install-recommends g++ gcc make && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip wheel && \
    pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]