FROM qgis/qgis:3.44.14-noble

ENV QT_QPA_PLATFORM=offscreen \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    GEOLUME_REQUIRE_QGIS=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-pytest python3-fastapi python3-multipart python3-uvicorn python3-celery python3-redis python3-psycopg2 poppler-utils fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

CMD ["python3", "-m", "pytest", "-v"]
