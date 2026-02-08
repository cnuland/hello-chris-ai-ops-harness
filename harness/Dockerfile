FROM python:3.12-slim

WORKDIR /app

COPY runner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runner/ ./runner/

VOLUME /outputs
VOLUME /config

ENTRYPOINT ["python", "-m", "runner.main"]
