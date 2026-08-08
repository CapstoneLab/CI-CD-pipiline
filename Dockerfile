# syntax=docker/dockerfile:1.7

FROM ghcr.io/gitleaks/gitleaks:v8.30.1 AS gitleaks

FROM python:3.11-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        docker.io \
        git \
        gradle \
        maven \
        nodejs \
        npm \
        openjdk-17-jdk-headless \
        openssh-client \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/runs /app/workspace \
    && gitleaks version \
    && semgrep --version \
    && python -m compileall -q app main.py

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "app.poller"]
