FROM python:3.11-slim

WORKDIR /app

# Make /app importable as the package root so `from db.x import y` works from
# anywhere, including `python scripts/foo.py`. Without this, running a script
# under scripts/ only puts /app/scripts on sys.path (not /app), so every
# scripts/*.py that imports db.* fails with "No module named 'db'". Setting it
# here fixes that class of bug once, for every current and future script,
# instead of a per-file sys.path hack. PYTHONUNBUFFERED makes container logs
# stream in real time (useful for long jobs like the OFAC bulk load).
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt

RUN python -m spacy download en_core_web_sm

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"] 