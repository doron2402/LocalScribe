FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

RUN apt-get update \
 && apt-get install -y --no-install-recommends pulseaudio pulseaudio-utils xvfb x11vnc novnc websockify python3.12-venv \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY localscribe ./localscribe
# A venv, because pip can't replace the Debian-owned packages in the base image.
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir ".[bot]"
ENV PATH=/opt/venv/bin:$PATH

COPY --chmod=755 docker/entrypoint-bot.sh /usr/local/bin/entrypoint-bot.sh
RUN mkdir -p /data && chown pwuser:pwuser /data

USER pwuser
ENV LOCALSCRIBE_DATA_DIR=/data HF_HOME=/data/models
ENTRYPOINT ["/usr/local/bin/entrypoint-bot.sh"]
CMD ["run"]
