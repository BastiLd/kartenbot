# Kartenbot Web — Backend + Oberfläche in einem Container.
#
# Wichtig: Der Bauort ist das Projekt-Wurzelverzeichnis (nicht web/), weil die
# Kartenliste des Bots mit hineinkopiert wird. Damit kann die Website niemals
# eine Karte vergeben, die es im Spiel gar nicht gibt.
#
#   docker build -f web/Dockerfile -t kartenbot-web .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

# Die Website selbst
COPY web/app /app/web/app
COPY web/static /app/web/static
COPY web/VERSION /app/web/VERSION

# Nur die Bot-Module, die für die Kartenprüfung gebraucht werden
COPY karten.py items.py /app/
COPY services/__init__.py services/card_variants.py services/card_validation.py /app/services/

ENV KARTENBOT_DB_PATH=/data/kartenbot.db \
    KARTENBOT_LOG_PATH=/data/bot.log \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8090

EXPOSE 8090
WORKDIR /app/web

# Bewusst OHNE --proxy-headers/--forwarded-allow-ips: uvicorn würde damit die
# Absenderadresse durch den Inhalt von X-Forwarded-For ersetzen. Dann könnte
# sich jeder von außen als Heimnetz ausgeben und die Netzstufen aushebeln.
# Stattdessen wertet netguard.py die echte Absenderadresse UND den Header aus
# und nimmt immer die schwächste Stufe der ganzen Kette.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
