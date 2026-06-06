"""Entry point: local stdio (default) or hosted streamable-HTTP.

  stdio (Claude Desktop / Cursor / IDEs):  aims-warehouse-mcp
  hosted HTTP:                             aims-warehouse-mcp --http --host 0.0.0.0 --port 8090
"""
from __future__ import annotations

import argparse
import os

from .server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="aims-warehouse-mcp", description="A.I.M.S. Tool Warehouse MCP server")
    parser.add_argument("--http", action="store_true", help="serve hosted streamable-HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8090")))
    args = parser.parse_args()

    if args.http:
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.settings.stateless_http = True  # each request independent — proxy/replica friendly
        # Allow the public host the reverse proxy forwards (DNS-rebinding allowlist),
        # plus loopback. Configurable for other deployments via MCP_PUBLIC_HOST.
        public_host = os.environ.get("MCP_PUBLIC_HOST", "warehouse.aimanagedsolutions.cloud")
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host, "127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=[
                f"https://{public_host}",
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        )
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
