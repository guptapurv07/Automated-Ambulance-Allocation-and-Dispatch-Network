# The dispatch server's image. docker-compose.yml runs it together with MySQL and Redis.

FROM python:3.13-slim

# Log lines appear at once; no .pyc files or pip cache in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# The libraries first, in their own layer: it is rebuilt only when
# requirements.txt changes, not on every change to the code.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# The server does not need to run as root.
RUN useradd --create-home dispatcher
USER dispatcher

EXPOSE 8000

# Answers when the server is up: what `docker compose ps` shows as healthy.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["sh", "docker/start.sh"]
