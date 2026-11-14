FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY frontend ./frontend
COPY eval ./eval
ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT DB_PATH=/data/polartrap.db RESULTS_PATH=/data/results.json CORPUS_DIR=/data/corpus
RUN useradd -m app && mkdir -p /data && chown app /data
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
