FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANGGRAPH_STRICT_MSGPACK=true \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace/backend
COPY backend/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/pyproject.toml backend/alembic.ini ./
COPY backend/app/ ./app/
COPY backend/alembic/ ./alembic/
COPY policies/ /workspace/policies/
RUN pip install --no-cache-dir --no-deps . \
    && groupadd --system resolveai \
    && useradd --system --gid resolveai --no-create-home resolveai

USER resolveai
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
