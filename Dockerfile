# Adona Style Generator — container for free hosting (Hugging Face Spaces, Docker SDK).
FROM python:3.13-slim

# Hugging Face runs Spaces as user 1000; everything the app writes must belong to it.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/tmp/numba-cache \
    LAK_PUBLIC=1 \
    LAK_RETENTION_DAYS=3 \
    LAK_DATA_DIR=/home/user/app/data \
    PORT=7860
WORKDIR /home/user/app

COPY --chown=user backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir --user -r backend/requirements.txt

COPY --chown=user backend/app backend/app
COPY --chown=user frontend frontend

# Warm numba's JIT cache so the first visitor's analysis isn't slowed by compilation.
RUN python -c "import numpy as np, librosa; y=np.random.default_rng(0).standard_normal(22050*3).astype('float32'); librosa.beat.beat_track(y=y, sr=22050); librosa.feature.chroma_cqt(y=y, sr=22050)" || true

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT}"]
