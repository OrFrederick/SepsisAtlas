FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN pip install --upgrade pip && pip install \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "httpx>=0.27" \
        "pydantic>=2.7" \
        "pyyaml>=6.0"

ARG TOOL_MODULE
ARG TOOL_PORT=9100
ENV TOOL_MODULE=${TOOL_MODULE} TOOL_PORT=${TOOL_PORT}

EXPOSE ${TOOL_PORT}

# tools/ is bind-mounted by docker-compose; this image stays generic.
CMD ["sh", "-c", "uvicorn ${TOOL_MODULE}:app --host 0.0.0.0 --port ${TOOL_PORT}"]
