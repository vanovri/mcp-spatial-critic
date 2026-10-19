# Good first issues — mcp-spatial-critic

Nine scoped, test-first tasks. Each one lists the files to touch and the assertion
that must pass. Comment on the GitHub issue to claim it; no assignment needed.

---

## 1. Add a build-volume check

**Labels:** `good first issue`, `core`, `cad`

Currently `bounding_box_mm` is reported but never compared to the printer envelope,
so a 400 mm part sails through on a 220 mm bed.

* `core.py` → `audit_stl_mesh(..., build_volume_mm=(220.0, 220.0, 250.0))`
* New error code `EXCEEDS_BUILD_VOLUME`, penalty −4.0
* Return `build_volume_fit: bool`

**Acceptance:** `tests/test_core.py::test_part_larger_than_build_volume_is_rejected`
constructs a 300 mm box and asserts the error code and a score of 6.0.

---

## 2. Add a human-facing `audit` CLI

The package only exposes the MCP server, so a human cannot quickly check a file.

```
python -m mcp_spatial_critic.audit build/bracket.stl --min-wall 1.6
```

* New module `mcp_spatial_critic/audit.py` with `argparse`
* Exit code `0` when approved, `1` when rejected, `2` on unreadable file
* `--json` prints the raw report

**Acceptance:** `tests/test_audit_cli.py` runs the module as a subprocess on a
temp file and asserts the exit codes. Follow the `subprocess` + `sys.executable`
pattern in `agent-referee/tests/test_cli.py`.

---

## 3. Replace the bounding-box thin-wall heuristic with Z-slice sampling

`THIN_FEATURE` currently compares `min(width, depth, height)` to `min_wall_mm`,
which misses a hollow 40 × 40 × 20 mm box with 0.4 mm walls.

* Sample N horizontal planes (`slice_count`, default 32)
* For each plane, collect the segments produced by triangles crossing it
* Find the nearest parallel segment pair; if the gap < `min_wall_mm`, flag

**Acceptance:** a hollow-box fixture STL is rejected, while the existing solid
reference box still scores 10.0. Keep the old heuristic as `min_wall_mm` fallback
when `slice_count=0`.

**Difficulty:** medium — this is the flagship issue, ask for help in Discussions.

---

## 4. Support 3MF input

3MF is a ZIP with `3D/3dmodel.model` XML inside. No third-party dependencies —
`zipfile` + `xml.etree.ElementTree` are in the standard library.

* `parse_3mf(raw: bytes)` → triangle list, applying the `<build>` transform
* `audit_stl_mesh` becomes `audit_mesh` with an extension-based dispatcher
  (keep the old name as an alias so nothing breaks)

**Acceptance:** a fixture 3MF containing one unit cube parses to 12 triangles and
audits as approved.

---

## 5. Support OBJ input

* Handle `v`, `f`, negative indices, and `f v/vt/vn` triplet syntax
* Triangulate quads with a fan
* New error `UNSUPPORTED_FORMAT` for anything else

**Acceptance:** an OBJ cube parses to 12 triangles with the same signed volume as
the STL reference box.

---

## 6. Expose audit history as MCP resources

MCP clients can read `resources/list` + `resources/read`; the server currently
answers `-32601` for both.

* Keep a bounded deque (max 5) of reports inside the process
* `resources/list` → `spatial-critic://audit/last`
* `resources/read` → the JSON report as a `text` resource

**Acceptance:** `handle_request({"method": "resources/list"})` returns one resource
after an audit, and `-32601` before any audit has run.

---

## 7. Return copy-pasteable repair hints

Diagnosis without a fix wastes an agent turn. Add `repair_hints: List[str]` to every
report, one concrete suggestion per error code, e.g. for `FLOATING_BASE`:

```
translate([0, 0, -5.0]) your_model();   // measured min_z = 5.0
```

**Acceptance:** every existing error code has at least one hint, asserted by a test
that iterates `ERROR_CODES`.

---

## 8. Benchmark and speed up large meshes

`edge_counts` allocates a dict entry per edge; a 1 M-triangle STL takes seconds.

* Add `benchmarks/bench_large_stl.py` generating a 200 × 200 grid mesh
* Cache edge keys per vertex tuple instead of rebuilding them
* Target: 1 M triangles under 5 seconds on a laptop

**Acceptance:** the benchmark script prints elapsed ms and exits non-zero if the
budget is exceeded. Do **not** add numpy.

---

## 9. Ship a composite GitHub Action

```yaml
- uses: vanovri/mcp-spatial-critic@v0.1.0
  with:
    path: build/part.stl
    min-wall-mm: "1.6"
```

* `action.yml` with `path`, `min-wall-mm`, `python-version` inputs
* Fails the job when `approved` is false
* Write the JSON report to `$GITHUB_STEP_SUMMARY`

**Acceptance:** a workflow in `.github/workflows/selftest.yml` runs the action
against `presets/` output and asserts it fails on a deliberately broken STL.

---

## House rules

1. One issue per PR.
2. The test must fail on `main` before your fix.
3. No new runtime dependencies — this package is standard library only by design.
4. Keep output deterministic; no timestamps, no `set` iteration in emitted JSON.
