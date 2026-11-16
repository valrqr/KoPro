# Image du moteur Python. Construite depuis la racine du projet :
#   docker compose build moteur
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Paris

WORKDIR /app

COPY moteur/requirements.txt moteur/requirements.txt
RUN pip install --no-cache-dir -r moteur/requirements.txt

COPY core/ core/
COPY moteur/ moteur/

# Utilisateur sans droits root
RUN useradd --create-home --uid 1000 moteur && mkdir -p /app/out && chown -R moteur /app
USER moteur

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/sante')"
CMD ["uvicorn", "moteur.api:app", "--host", "0.0.0.0", "--port", "8000"]
