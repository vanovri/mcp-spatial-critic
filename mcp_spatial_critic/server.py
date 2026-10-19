"""Minimal Model Context Protocol server (stdio JSON-RPC 2.0), zero dependencies.

Implements just enough of MCP for a headless CAD agent to ask for deterministic
geometry critique:

* ``initialize`` / ``notifications/initialized``
* ``tools/list`` / ``tools/call``
* ``ping``

Tools: ``audit_stl``, ``generate_reference_box``, ``list_presets``.

Run it with ``mcp-spatial-critic`` (or ``python -m mcp_spatial_critic.server``)
and register that command as an MCP stdio server in your agent's config.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Union

from .core import audit_stl_file, generate_watertight_box_stl

SERVER_NAME = "mcp-spatial-critic"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "audit_stl",
        "description": (
            "Audit a STL mesh for watertightness/manifoldness, build-plate "
            "grounding, disconnected shells, inverted normals and thin features."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to a .stl file"},
                "min_wall_mm": {"type": "number", "default": 2.0},
                "min_z_tolerance_mm": {"type": "number", "default": 0.01},
            },
            "required": ["path"],
        },
    },
    {
        "name": "generate_reference_box",
        "description": "Write a known-watertight reference box and audit it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_path": {"type": "string", "default": "reference_box.stl"},
                "width": {"type": "number", "default": 20.0},
                "depth": {"type": "number", "default": 20.0},
                "height": {"type": "number", "default": 10.0},
            },
        },
    },
    {
        "name": "list_presets",
        "description": "List the bundled parametric CAD presets (OpenSCAD / generator).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def presets_dir() -> Path:
    """Location of the ``presets/`` directory bundled next to the package."""
    override = os.environ.get("MCP_SPATIAL_CRITIC_PRESETS")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "presets"


def _tool_result(payload: Any, is_error: bool = False) -> Dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _resolve(root: Path, target: str) -> Path:
    path = Path(target).expanduser()
    return path if path.is_absolute() else (root / path)


def call_tool(name: str, arguments: Dict[str, Any], root: Path) -> Dict[str, Any]:
    """Dispatch one tool call. Returns an MCP ``tools/call`` result object."""
    if name == "audit_stl":
        target = arguments.get("path")
        if not target:
            return _tool_result("Missing required argument: path", is_error=True)
        stl_path = _resolve(root, str(target))
        if not stl_path.is_file():
            return _tool_result(f"File not found: {stl_path}", is_error=True)
        report = audit_stl_file(
            stl_path,
            min_wall_mm=float(arguments.get("min_wall_mm", 2.0)),
            min_z_tolerance_mm=float(arguments.get("min_z_tolerance_mm", 0.01)),
        )
        return _tool_result(report, is_error=not bool(report["approved"]))

    if name == "generate_reference_box":
        out_path = _resolve(root, str(arguments.get("out_path", "reference_box.stl")))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        stl = generate_watertight_box_stl(
            width=float(arguments.get("width", 20.0)),
            depth=float(arguments.get("depth", 20.0)),
            height=float(arguments.get("height", 10.0)),
        )
        out_path.write_text(stl, encoding="utf-8")
        report = audit_stl_file(out_path)
        report["path"] = str(out_path)
        return _tool_result(report, is_error=not bool(report["approved"]))

    if name == "list_presets":
        directory = presets_dir()
        presets = (
            [
                {"name": item.name, "size_bytes": item.stat().st_size}
                for item in sorted(directory.iterdir())
                if item.is_file() and not item.name.startswith(".")
            ]
            if directory.is_dir()
            else []
        )
        return _tool_result({"presets_dir": str(directory), "presets": presets})

    return _tool_result(f"Unknown tool: {name}", is_error=True)


def handle_request(
    request: Dict[str, Any], root: Optional[Union[str, Path]] = None
) -> Optional[Dict[str, Any]]:
    """Handle a single JSON-RPC request. Returns ``None`` for notifications."""
    root_path = Path(root) if root is not None else Path.cwd()
    method = request.get("method")
    request_id = request.get("id")

    if method == "notifications/initialized":
        return None

    if method == "initialize":
        result: Dict[str, Any] = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif request_id is None:
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        result = call_tool(
            str(params.get("name", "")),
            dict(params.get("arguments") or {}),
            root_path,
        )
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(
    stdin: TextIO,
    stdout: TextIO,
    root: Optional[Union[str, Path]] = None,
) -> int:
    """Line-delimited JSON-RPC loop — the MCP stdio transport."""
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response: Optional[Dict[str, Any]] = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc.msg}"},
            }
        else:
            response = handle_request(request, root)
        if response is None:
            continue
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
    return 0


def main() -> int:
    return serve(sys.stdin, sys.stdout, Path.cwd())


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
