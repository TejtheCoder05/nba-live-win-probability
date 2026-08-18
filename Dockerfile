FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    NBA_APP_ENV=production \
    NBA_APP_MODE=replay \
    HOST=0.0.0.0 \
    PORT=5000 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app

COPY requirements-runtime.txt ./
RUN python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.13.0 \
    && python -m pip install --no-cache-dir -r requirements-runtime.txt

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --chown=app:app src ./src
COPY --chown=app:app templates ./templates
COPY --chown=app:app static ./static
COPY --chown=app:app scripts/verify_model_artifact.py scripts/verify_runtime.py ./scripts/
COPY --chown=app:app tests/fixtures/live/playbyplay_0022000001.json ./tests/fixtures/live/playbyplay_0022000001.json
COPY --chown=app:app tests/fixtures/live/game_details_0022000001.json ./tests/fixtures/live/game_details_0022000001.json
COPY --chown=app:app artifacts/win_probability_v1/best_model.pt ./artifacts/win_probability_v1/best_model.pt
COPY --chown=app:app artifacts/win_probability_v1/model_config.json ./artifacts/win_probability_v1/model_config.json
COPY --chown=app:app artifacts/win_probability_v1/preprocessing.json ./artifacts/win_probability_v1/preprocessing.json

RUN python scripts/verify_runtime.py

USER app
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json, os, urllib.request; response=json.load(urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '5000') + '/api/health', timeout=3)); assert response['status'] == 'ready' and response['model_loaded'] and response['service_ready']"

CMD ["gunicorn", "--config", "python:src.api.gunicorn_config", "src.api.wsgi:app"]
