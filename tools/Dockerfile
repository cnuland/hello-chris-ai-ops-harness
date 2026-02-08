FROM python:3.12-slim

WORKDIR /app

COPY otel_tools_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY otel_tools_server/ ./otel_tools_server/

EXPOSE 8000

CMD ["uvicorn", "otel_tools_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
