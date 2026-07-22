FROM node:22.14.0-bookworm-slim AS console-builder

WORKDIR /build

COPY package.json package-lock.json ./
COPY frontend/console-next/package.json /build/frontend/console-next/package.json
RUN npm ci

COPY frontend/console-next /build/frontend/console-next
RUN npm run console:build
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

RUN sed -i \
        -e 's#http://archive.ubuntu.com/ubuntu/#https://mirrors.tuna.tsinghua.edu.cn/ubuntu/#g' \
        -e 's#http://security.ubuntu.com/ubuntu/#https://mirrors.tuna.tsinghua.edu.cn/ubuntu/#g' \
        /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        software-properties-common \
        tzdata \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 \
        python3.12-venv \
    && python3.12 -m venv /opt/portrait-hub-venv \
    && /opt/portrait-hub-venv/bin/python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
COPY requirements/prod-optional.txt /tmp/requirements-prod-optional.txt
ARG INSTALL_PROD_OPTIONAL=false
RUN /opt/portrait-hub-venv/bin/python -m pip install --no-cache-dir -r /tmp/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && if [ "$INSTALL_PROD_OPTIONAL" = "true" ]; then \
        /opt/portrait-hub-venv/bin/python -m pip install --no-cache-dir -r /tmp/requirements-prod-optional.txt -i https://pypi.tuna.tsinghua.edu.cn/simple; \
    fi

FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODELS_ROOT=/models \
    PATH=/opt/portrait-hub-venv/bin:$PATH \
    TZ=Asia/Shanghai

WORKDIR /workspace

RUN sed -i \
        -e 's#http://archive.ubuntu.com/ubuntu/#https://mirrors.tuna.tsinghua.edu.cn/ubuntu/#g' \
        -e 's#http://security.ubuntu.com/ubuntu/#https://mirrors.tuna.tsinghua.edu.cn/ubuntu/#g' \
        /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libglib2.0-0 \
        libgl1 \
        software-properties-common \
        tzdata \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.12 \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && apt-get purge -y --auto-remove software-properties-common \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/portrait-hub-venv /opt/portrait-hub-venv
COPY app /workspace/app
COPY --from=console-builder /build/frontend/console-next/dist /workspace/frontend/console-next/dist
COPY main.py /workspace/main.py
COPY models.yml /workspace/models.yml
COPY model-capabilities.yml /workspace/model-capabilities.yml
COPY .env.example /workspace/.env.example

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "100"]
