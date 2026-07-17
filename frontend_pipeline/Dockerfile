# FUTUR // COMMAND CENTER — image du dashboard PWA (FastAPI + uvicorn).
# Le code n'est PAS copié : le dépôt est bind-monté sur /app par docker-compose
# (reports/, data/ et configs/ sont produits en continu par les services hôte).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# versions alignées sur le venv hôte (.venv) pour éviter toute dérive de comportement
RUN pip install \
    fastapi==0.115.6 \
    "uvicorn[standard]==0.30.6" \
    pydantic==2.10.6 \
    pandas==2.0.3 \
    numpy==1.24.4 \
    pyarrow==15.0.2 \
    PyYAML==6.0.2 \
    pymongo==4.10.1

WORKDIR /app
EXPOSE 80
CMD ["uvicorn", "frontend_pipeline.command_center:app", "--host", "0.0.0.0", "--port", "80"]
