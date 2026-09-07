# Build from this directory only; API sources, environment files and user files
# must not be sent to the builder. The launcher records the resolved image ID.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_INDEX=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp LANG=C.UTF-8

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/file-runtime/requirements.txt
RUN PIP_NO_INDEX=0 python -m pip install --no-cache-dir -r /opt/file-runtime/requirements.txt \
    && python -m pip check \
    && python -m pip freeze --all > /opt/file-runtime/installed-packages.txt \
    && dpkg-query -W > /opt/file-runtime/installed-system-packages.txt

COPY check_runtime.py /opt/file-runtime/check_runtime.py
WORKDIR /tmp
USER 65532:65532
ENTRYPOINT ["python", "-I", "/opt/file-runtime/check_runtime.py"]
