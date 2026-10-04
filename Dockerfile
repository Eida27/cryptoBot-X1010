FROM python:3.12.15-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.23 && groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --create-home bot
COPY pyproject.toml uv.lock /app/
COPY src /app/src
RUN uv sync --locked --no-dev --no-editable && mkdir -p state data reports backups && chown -R bot:bot /app
COPY --chown=bot:bot config /app/config
USER 10001:10001
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["cbot", "serve", "--config", "config/container-paper.toml"]
