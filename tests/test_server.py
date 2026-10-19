"""Tests for the MCP stdio server (JSON-RPC 2.0 framing, tool dispatch)."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp_spatial_critic.server import TOOLS, handle_request, presets_dir, serve


def tool_call(name, arguments=None, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def decode(result):
    """Pull the JSON payload out of an MCP tools/call result."""
    return json.loads(result["content"][0]["text"])


class HandleRequestTests(unittest.TestCase):
    def test_initialize_reports_protocol_and_server_info(self):
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(response["jsonrpc"], "2.0")
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "mcp-spatial-critic")
        self.assertIn("tools", result["capabilities"])

    def test_notifications_initialized_produces_no_response(self):
        self.assertIsNone(
            handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )

    def test_ping_returns_empty_result(self):
        response = handle_request({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(response["result"], {})

    def test_tools_list_exposes_three_tools(self):
        response = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(names, ["audit_stl", "generate_reference_box", "list_presets"])
        for tool in TOOLS:
            self.assertIn("inputSchema", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_method_returns_method_not_found(self):
        response = handle_request({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("resources/list", response["error"]["message"])

    def test_audit_without_path_is_an_error_result(self):
        result = handle_request(tool_call("audit_stl"))["result"]
        self.assertTrue(result["isError"])
        self.assertIn("Missing required argument", result["content"][0]["text"])

    def test_audit_missing_file_is_an_error_result(self):
        result = handle_request(
            tool_call("audit_stl", {"path": "nope-not-here.stl"}), root=Path.cwd()
        )["result"]
        self.assertTrue(result["isError"])
        self.assertIn("File not found", result["content"][0]["text"])

    def test_unknown_tool_is_an_error_result(self):
        result = handle_request(tool_call("slice_and_print"))["result"]
        self.assertTrue(result["isError"])
        self.assertIn("Unknown tool", result["content"][0]["text"])

    def test_list_presets_finds_bundled_files(self):
        result = handle_request(tool_call("list_presets"))["result"]
        self.assertFalse(result["isError"])
        listing = decode(result)
        names = {preset["name"] for preset in listing["presets"]}
        self.assertIn("esp32_mount.scad", names)
        self.assertIn("m3_heatset_boss.scad", names)
        self.assertIn("pagoda_roof_tier.py", names)

    def test_presets_dir_env_override(self):
        with mock.patch.dict(os.environ, {"MCP_SPATIAL_CRITIC_PRESETS": "C:/elsewhere/presets"}):
            self.assertEqual(presets_dir(), Path("C:/elsewhere/presets"))


class ServeTests(unittest.TestCase):
    def test_stdio_round_trip_generate_then_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "box.stl")
            requests = [
                tool_call("generate_reference_box", {"out_path": target}, 1),
                tool_call("audit_stl", {"path": target}, 2),
                tool_call("list_presets", {}, 3),
            ]
            stdin = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
            stdout = io.StringIO()

            exit_code = serve(stdin, stdout, root=tmp)

            self.assertEqual(exit_code, 0)
            lines = [line for line in stdout.getvalue().splitlines() if line]
            self.assertEqual(len(lines), 3)
            responses = [json.loads(line) for line in lines]
            self.assertEqual([r["id"] for r in responses], [1, 2, 3])

            generated = decode(responses[0]["result"])
            self.assertTrue(generated["approved"])
            self.assertEqual(generated["critic_score"], 10.0)

            audited = decode(responses[1]["result"])
            self.assertTrue(audited["approved"])
            self.assertEqual(audited["file"], "box.stl")

            self.assertTrue(Path(target).is_file())

    def test_parse_error_does_not_kill_the_loop(self):
        line = '{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n{broken json\n'
        stdout = io.StringIO()

        serve(io.StringIO(line), stdout)

        responses = [json.loads(item) for item in stdout.getvalue().splitlines()]
        self.assertEqual(responses[0]["result"], {})
        self.assertEqual(responses[1]["error"]["code"], -32700)
        self.assertIsNone(responses[1]["id"])

    def test_blank_lines_and_notifications_are_skipped(self):
        stdin = io.StringIO(
            '\n\n{"jsonrpc": "2.0", "method": "notifications/initialized"}\n'
            '{"jsonrpc": "2.0", "id": 9, "method": "ping"}\n\n'
        )
        stdout = io.StringIO()

        serve(stdin, stdout)

        responses = [json.loads(item) for item in stdout.getvalue().splitlines() if item]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["id"], 9)

    def test_unapproved_mesh_is_flagged_as_tool_error(self):
        stl = (
            "solid broken\n"
            "  facet normal 0 0 0\n    outer loop\n"
            "      vertex 0 0 0\n      vertex 1 0 0\n      vertex 0 1 0\n"
            "    endloop\n  endfacet\n"
            "endsolid broken\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "broken.stl").write_text(stl, encoding="utf-8")
            stdout = io.StringIO()
            serve(
                io.StringIO(json.dumps(tool_call("audit_stl", {"path": "broken.stl"})) + "\n"),
                stdout,
                root=tmp,
            )
            result = json.loads(stdout.getvalue())["result"]

        self.assertTrue(result["isError"])
        self.assertIn("NON_MANIFOLD", decode(result)["errors"])


if __name__ == "__main__":
    unittest.main()
