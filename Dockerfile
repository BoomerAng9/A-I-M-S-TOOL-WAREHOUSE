# A.I.M.S. Tool Warehouse — standalone service image.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8090

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY aims_warehouse ./aims_warehouse

# Non-root runtime.
RUN useradd --create-home --uid 1001 warehouse
USER warehouse

EXPOSE 8090

# Single worker is correct behind a load balancer that scales replicas.
CMD ["sh", "-c", "uvicorn aims_warehouse.warehouse_service:app --host 0.0.0.0 --port ${PORT}"]
