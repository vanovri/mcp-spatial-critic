# mcp-spatial-critic

[![PyPI](https://img.shields.io/badge/pypi-coming%20soon-orange)](https://pypi.org/project/mcp-spatial-critic/)
[![tests](https://img.shields.io/badge/tests-40%20passing-brightgreen)](./tests)
[![license](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](./pyproject.toml)

An **MCP server** (Model Context Protocol, stdio JSON-RPC 2.0) that gives headless
CAD agents a deterministic 3D-printing critic. Zero dependencies — pure Python
standard library, no numpy, no trimesh, no Blender, no OpenSCAD at runtime.

> An LLM can write OpenSCAD that looks correct in a screenshot and still exports a
> mesh that is inside-out, floating 5 mm above the plate, split into three bodies,
> or full of zero-area slivers. `mcp-spatial-critic` fails the build **before** the
> printer does.

```text
agent → generate STL → mcp-spatial-critic → {"approved": false, "errors": ["NON_MANIFOLD"]}
                                          → agent repairs → re-audit → {"approved": true}
```

## Why agents need this

Text-only agents cannot see geometry. Vision models see a render, not a manifold.
The failure modes below all render beautifully and all fail in a slicer:

| Defect | What the render shows | What the slicer does |
| --- | --- | --- |
| Non-manifold edges | Normal shade | Refuses to slice, or silently deletes shells |
| Floating base | Normal shade | Starts printing in mid-air |
| Disconnected shells | Normal shade | Prints 3 loose parts instead of one |
| Inverted normals | Normal shade (mostly) | Treats solid as void, prints nothing |
| Sub-nozzle features | Normal shade | Vase mode collapse / missing wall |

## The five invariants

Every audit returns a `critic_score` from `0.0` to `10.0` and a list of critiques.

| Check | Penalty | Error code |
| --- | --- | --- |
| Every edge shared by exactly 2 triangles | −5.0 | `NON_MANIFOLD` |
| Lowest Z sits on the build plate | −2.0 | `FLOATING_BASE` |
| Exactly one connected shell | −2.0 | `MULTIPLE_SHELLS` |
| Smallest bounding dimension ≥ `min_wall_mm` | −1.5 | `THIN_FEATURE` |
| Positive signed volume (outward normals) | −1.0 | `INVERTED_OR_ZERO_VOLUME` |
| No zero-area facets | −1.0 | `DEGENERATE_TRIANGLES` |

`approved` is `true` only when the error list is empty, the mesh is watertight and
there is a single shell. The score is a ranking signal for repair loops; `approved`
is the gate.

## Install

```bash
git clone https://github.com/vanovri/mcp-spatial-critic
cd mcp-spatial-critic
pip install -e .
```

Requires Python 3.10+. No third-party runtime packages.

## Register the MCP server

Add this to your agent's MCP configuration (Claude Desktop `claude_desktop_config.json`,
Claude Code `.mcp.json`, or any MCP-compatible client):

```json
{
  "mcpServers": {
    "spatial-critic": {
      "command": "mcp-spatial-critic",
      "args": []
    }
  }
}
```

The server resolves relative paths against the directory it was launched from. Set
`MCP_SPATIAL_CRITIC_PRESETS` to override where `list_presets` looks.

## Tools

### `audit_stl`

```json
{
  "name": "audit_stl",
  "arguments": { "path": "build/bracket.stl", "min_wall_mm": 2.0 }
}
```

Response payload (also usable directly as a Python dict via `audit_stl_file`):

```json
{
  "file": "bracket.stl",
  "path": "build/bracket.stl",
  "triangle_count": 12,
  "vertex_count": 8,
  "watertight": true,
  "non_manifold_edge_count": 0,
  "shell_count": 1,
  "bounding_box_mm": {
    "width_x": 20.0, "depth_y": 20.0, "height_z": 10.0,
    "min_z": 0.0, "max_z": 10.0
  },
  "volume_mm3": 4000.0,
  "degenerate_triangle_count": 0,
  "critic_score": 10.0,
  "approved": true,
  "critiques": [],
  "errors": []
}
```

`isError` is set on the MCP result whenever `approved` is false, so a tool-using
agent sees the failure as a tool error rather than a success.

### `generate_reference_box`

Writes a known-good watertight box and audits it. Use it as a control experiment
when an agent claims its exporter is broken.

```json
{ "out_path": "reference_box.stl", "width": 20, "depth": 20, "height": 10 }
```

### `list_presets`

Lists the parametric presets bundled in [`presets/`](./presets):

| Preset | What it demonstrates |
| --- | --- |
| `esp32_mount.scad` | PCB standoff walls, snap lips, USB-C cutout, vent slots |
| `m3_heatset_boss.scad` | M3 brass insert seat with strengthening ribs |
| `pagoda_roof_tier.py` | Generator that splits >45° eaves into printable steps |

```bash
python presets/pagoda_roof_tier.py > pagoda.scad
openscad -o pagoda.stl pagoda.scad
```

## Talk to it without an agent

The transport is line-delimited JSON-RPC 2.0 on stdio, so you can drive it by hand:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"generate_reference_box","arguments":{"out_path":"box.stl"}}}' \
  | mcp-spatial-critic
```

## Library use

```python
from mcp_spatial_critic import audit_stl_file, audit_stl_mesh, generate_watertight_box_stl

report = audit_stl_file("build/bracket.stl", min_wall_mm=1.6)
if not report["approved"]:
    for critique in report["critiques"]:
        print("FAIL:", critique)

# or audit an in-memory mesh
report = audit_stl_mesh(generate_watertight_box_stl(height=1.0))
assert "THIN_FEATURE" in report["errors"]
```

## How it works

* **ASCII + binary STL parsers** (`core.py`) — binary uses `struct` with a
  50-byte stride; ASCII tolerates CRLF, tabs and malformed vertex lines.
* **Manifold test** — canonicalised undirected edge keys (vertices rounded to
  1e-5 mm) counted in a dict; any count ≠ 2 is a non-manifold edge.
* **Shell count** — union-find with path compression over shared vertices.
* **Orientation** — signed volume via the divergence theorem,
  `Σ dot(v1, cross(v2, v3)) / 6`.
* **Deterministic output** — no timestamps, no random ordering; the same STL
  always produces the same bytes on the wire.
* **Server** — `handle_request()` is a pure function (`dict → dict | None`), so
  the whole protocol is unit-testable without starting a process.

```bash
python -m unittest discover -s tests -v   # 40 tests
```

## Known limitations (deliberate, tracked as issues)

1. `min_wall_mm` is compared against the **bounding box**, not per-wall ray casts.
   A 40 × 40 × 20 mm hollow box with 0.4 mm walls passes this check. Real thin-wall
   detection needs slice-plane sampling — see `GOOD_FIRST_ISSUES.md` #3.
2. Only STL is supported. 3MF and OBJ are open tasks.
3. The critic **diagnoses**, it never repairs. Auto-repair belongs in the agent loop.
4. Binary STL headers that begin with `solid` are disambiguated by looking for the
   `facet` keyword in the first 512 bytes. A pathological header could fool it.
5. No build-volume check yet (the bbox is reported, the comparison is not).

## Contributing

See [`GOOD_FIRST_ISSUES.md`](./GOOD_FIRST_ISSUES.md) for scoped, test-covered
starter tasks. Every PR needs a test that fails before the fix. Run the suite with
`python -m unittest discover -s tests -v`.

## License

MIT — see [`LICENSE`](./LICENSE).
