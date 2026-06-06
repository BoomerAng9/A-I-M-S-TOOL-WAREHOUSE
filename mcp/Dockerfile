# A.I.M.S. Tool Warehouse MCP server — hosted streamable-HTTP image.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8090

WORKDIR /app
COPY pyproject.toml ./
COPY aims_warehouse_mcp ./aims_warehouse_mcp
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 1001 mcp
USER mcp

EXPOSE 8090
CMD ["aims-warehouse-mcp", "--http", "--host", "0.0.0.0", "--port", "8090"]
