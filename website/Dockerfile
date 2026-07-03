# Kartenbot Dashboard — Container für ZimaOS/Docker
# Build-Kontext ist der PROJEKT-ROOT (nicht website/), damit karten.py und
# services/ für die Kartennamen-Validierung mit ins Image kommen:
#   docker build -f website/Dockerfile -t kartenbot-dashboard .
FROM python:3.12-slim

WORKDIR /opt/kartenbot

# Nur was das Dashboard braucht: Website + Spieldaten-Module des Bots
COPY website/requirements.txt website/requirements.txt
RUN pip install --no-cache-dir -r website/requirements.txt

COPY karten.py items.py namenconfig.py ./
COPY services/__init__.py services/card_variants.py services/battle_types.py services/
COPY website/app website/app
COPY website/static website/static

WORKDIR /opt/kartenbot/website

ENV DASHBOARD_HOST=0.0.0.0 \
    DASHBOARD_PORT=8080 \
    KARTENBOT_DB_PATH=/data/kartenbot.db \
    KARTENBOT_LOG_PATH=/data/bot.log

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
