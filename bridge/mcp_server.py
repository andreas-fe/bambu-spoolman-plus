"""Opt-in local MCP stdio client for the Bambu-Spoolman-Plus bridge.

Run separately from the web server, e.g. python -m bridge.mcp_server.
All requests use a dedicated long secret. Never bind an MCP TCP endpoint.
"""
from __future__ import annotations
import os
import requests
from mcp.server.fastmcp import FastMCP

URL = os.getenv("MCP_BRIDGE_URL", "http://127.0.0.1:7913").rstrip("/")
TOKEN = os.getenv("MCP_BRIDGE_TOKEN", "")
if len(TOKEN) < 32:
    raise RuntimeError("MCP_BRIDGE_TOKEN must be at least 32 characters.")
if not (URL.startswith("http://127.0.0.1:") or URL.startswith("http://localhost:")):
    raise RuntimeError("Only loopback MCP bridge URLs are allowed; use a local sidecar/tunnel.")

mcp = FastMCP("Bambu Spoolverwaltung", instructions=(
    "Read-only status, inventory, costs and conservative 3MF checks. "
    "A power request never switches anything; the user must approve it in the web UI. "
    "Never claim a print is authorized or a power action succeeded unless the tool confirms it."
))


def call(method, path, **params):
    headers = {"X-MCP-Bridge-Token": TOKEN}
    try:
        response = requests.request(method, URL + path, headers=headers,
                                    params=params if method == "GET" else None,
                                    json=params if method == "POST" else None,
                                    timeout=(3, 20))
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError("Lokale Bridge nicht erreichbar oder Anfrage zurückgewiesen.") from exc


@mcp.tool()
def printer_status() -> dict:
    """Current printer progress, AMS slots, connectivity and Tapo on/off status."""
    return call("GET", "/api/assistant/status")


@mcp.tool()
def filament_stock() -> dict:
    """Physical spool inventory, remaining grams, material and colors."""
    return call("GET", "/api/assistant/stock")


@mcp.tool()
def print_journal() -> dict:
    """Recent print consumption journal entries; no raw logs."""
    return call("GET", "/api/assistant/journal")


@mcp.tool()
def print_costs() -> dict:
    """Recent print costs and energy estimates."""
    return call("GET", "/api/assistant/costs")


@mcp.tool()
def printer_files() -> dict:
    """List files on printer storage and return short-lived opaque file tokens."""
    return call("GET", "/api/assistant/files")


@mcp.tool()
def check_print_preparation(file: str, plate: int, mapping: dict[str, str] | None = None) -> dict:
    """Simulate 3MF plate/AMS/inventory compatibility (no command, no print start).

    Requires file token from printer_files and metadata previously inspected in the
    web file-detail dialog. Mapping is slicer filament ID to physical AMS slot.
    """
    return call("POST", "/api/assistant/preflight", file=file, plate=plate, mapping=mapping or {})


@mcp.tool()
def request_tapo_power(action: str) -> dict:
    """Request on/off only: NEVER switches immediately, requires web UI approval.

    Only 'on' or 'off' is allowed. Print safety is rechecked on the bridge when
    the owner approves the request in the Tapo power page.
    """
    return call("POST", "/api/assistant/power/request", action=action)


@mcp.tool()
def tapo_power_request_result(request_id: str) -> dict:
    """Check whether a human approved/denied a power request and if it succeeded."""
    return call("GET", "/api/assistant/power/result", request_id=request_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
