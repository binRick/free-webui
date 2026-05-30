# Backend image for the integration harness. The same image runs the FastAPI
# app (the `backend` service) and the pytest suite (the `tests` service) — both
# only need the backend's Python deps, which already include pytest + httpx.
FROM python:3.12-slim

WORKDIR /app

# argon2-cffi / cffi / pypdf all ship manylinux wheels, so no compiler needed.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# A writable dir for the SQLite DB (DB path is overridden via env in compose).
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1 \
    FREE_WEBUI_DB_PATH=/data/free-webui.db

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
