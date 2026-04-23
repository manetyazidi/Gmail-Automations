FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY *.py ./

RUN useradd -r -u 1001 app && chown -R app:app /app
USER app

VOLUME ["/app/data"]
ENV DB_PATH=/app/data/nudges.db \
    GMAIL_TOKEN_PATH=/app/data/token.json \
    GMAIL_CREDENTIALS_PATH=/app/data/credentials.json

CMD ["python", "main.py"]
