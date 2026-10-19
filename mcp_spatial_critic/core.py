"""Deterministic STL geometry auditing — no numpy, no Blender, no network.

An autonomous CAD agent can produce a shape that *looks* right in a viewport and
still cannot be manufactured. This module applies the hard manufacturability
invariants, in pure standard library:

1. **Manifold** — every edge is shared by exactly two triangles (watertight).
2. **Grounded** — lowest Z sits on the build plate (``|min_z| <= tolerance``).
3. **Single body** — exactly one connected shell (no floating islands).
4. **Orientation** — positive signed volume (outward-facing normals).
5. **Printable** — smallest bounding dimension and every triangle are non-degenerate.
"""

from __future__ import annotations

import struct
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple, Union

Vec3 = Tuple[float, float, float]
Triangle = Tuple[Vec3, Vec3, Vec3]

PRECISION = 5
_EPS = 1e-9


def _vec(x: float, y: float, z: float) -> Vec3:
    return (round(float(x), PRECISION), round(float(y), PRECISION), round(float(z), PRECISION))


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def generate_watertight_box_stl(
    width: float = 20.0,
    depth: float = 20.0,
    height: float = 10.0,
    solid_name: str = "reference_box",
) -> str:
    """Reference solid used by tests and by the ``generate_reference_box`` tool."""
    v: List[Vec3] = [
        (0.0, 0.0, 0.0),
        (width, 0.0, 0.0),
        (width, depth, 0.0),
        (0.0, depth, 0.0),
        (0.0, 0.0, height),
        (width, 0.0, height),
        (width, depth, height),
        (0.0, depth, height),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2),      # bottom (-Z)
        (4, 5, 6), (4, 6, 7),      # top (+Z)
        (0, 1, 5), (0, 5, 4),      # front (-Y)
        (2, 3, 7), (2, 7, 6),      # back (+Y)
        (0, 4, 7), (0, 7, 3),      # left (-X)
        (1, 2, 6), (1, 6, 5),      # right (+X)
    ]
    lines = [f"solid {solid_name}"]
    for i1, i2, i3 in faces:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        for idx in (i1, i2, i3):
            lines.append("      vertex {:.6f} {:.6f} {:.6f}".format(*v[idx]))
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append(f"endsolid {solid_name}")
    return "\n".join(lines) + "\n"


def parse_ascii_stl(text: str) -> List[Triangle]:
    """Parse ASCII STL, tolerating CRLF, tabs and multiple ``solid`` blocks."""
    triangles: List[Triangle] = []
    pending: List[Vec3] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0].lower() == "vertex":
            try:
                pending.append(_vec(parts[1], parts[2], parts[3]))
            except ValueError:
                continue
            if len(pending) == 3:
                triangles.append((pending[0], pending[1], pending[2]))
                pending = []
    return triangles


def parse_binary_stl(raw: bytes) -> List[Triangle]:
    """Parse binary STL (80-byte header + uint32 count + 50 bytes/triangle)."""
    if len(raw) < 84:
        raise ValueError("Invalid binary STL: header shorter than 84 bytes.")
    count = struct.unpack("<I", raw[80:84])[0]
    triangles: List[Triangle] = []
    offset = 84
    for _ in range(count):
        if offset + 50 > len(raw):
            break
        values = struct.unpack("<12fH", raw[offset : offset + 50])
        triangles.append(
            (
                _vec(values[3], values[4], values[5]),
                _vec(values[6], values[7], values[8]),
                _vec(values[9], values[10], values[11]),
            )
        )
        offset += 50
    return triangles


def parse_stl(payload: Union[str, bytes]) -> List[Triangle]:
    """Dispatch to the ASCII or binary parser."""
    if isinstance(payload, bytes):
        if payload[:5].lower() == b"solid" and b"facet" in payload[:512].lower():
            return parse_ascii_stl(payload.decode("utf-8", errors="ignore"))
        return parse_binary_stl(payload)
    return parse_ascii_stl(payload)


def edge_counts(triangles: Iterable[Triangle]) -> Dict[Tuple[Vec3, Vec3], int]:
    """Count how many triangles share each undirected edge."""
    counts: Dict[Tuple[Vec3, Vec3], int] = defaultdict(int)
    for v1, v2, v3 in triangles:
        for a, b in ((v1, v2), (v2, v3), (v3, v1)):
            counts[(a, b) if a <= b else (b, a)] += 1
    return dict(counts)


def count_shells(triangles: Sequence[Triangle]) -> int:
    """Number of connected components (union-find over shared vertex keys)."""
    parent: Dict[Vec3, Vec3] = {}

    def find(node: Vec3) -> Vec3:
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:  # path compression
            parent[node], node = root, parent[node]
        return root

    def union(a: Vec3, b: Vec3) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for v1, v2, v3 in triangles:
        union(v1, v2)
        union(v2, v3)

    return len({find(node) for node in parent})


def signed_volume(triangles: Iterable[Triangle]) -> float:
    """Signed volume in mm^3 via the divergence theorem (positive = outward normals)."""
    total = 0.0
    for v1, v2, v3 in triangles:
        total += _dot(v1, _cross(v2, v3))
    return total / 6.0


def degenerate_triangle_count(triangles: Iterable[Triangle]) -> int:
    """Triangles whose area collapses to zero (unsliceable slivers)."""
    count = 0
    for v1, v2, v3 in triangles:
        cross = _cross(_sub(v2, v1), _sub(v3, v1))
        if _dot(cross, cross) < _EPS:
            count += 1
    return count


def bounding_box(triangles: Sequence[Triangle]) -> Dict[str, float]:
    xs = [v[0] for tri in triangles for v in tri]
    ys = [v[1] for tri in triangles for v in tri]
    zs = [v[2] for tri in triangles for v in tri]
    return {
        "width_x": round(max(xs) - min(xs), 3),
        "depth_y": round(max(ys) - min(ys), 3),
        "height_z": round(max(zs) - min(zs), 3),
        "min_z": round(min(zs), 3),
        "max_z": round(max(zs), 3),
    }


def audit_stl_mesh(
    payload: Union[str, bytes],
    min_wall_mm: float = 2.0,
    min_z_tolerance_mm: float = 0.01,
    file_label: str = "<memory>",
) -> Dict[str, object]:
    """Score a mesh from 0.0 to 10.0 and list every manufacturability defect."""
    triangles = parse_stl(payload)
    if not triangles:
        return {
            "file": file_label,
            "triangle_count": 0,
            "critic_score": 0.0,
            "approved": False,
            "watertight": False,
            "critiques": ["EMPTY_MESH: no facet parsed from payload"],
            "errors": ["EMPTY_MESH"],
        }

    counts = edge_counts(triangles)
    non_manifold = sum(1 for c in counts.values() if c != 2)
    watertight = non_manifold == 0
    shells = count_shells(triangles)
    volume = signed_volume(triangles)
    bbox = bounding_box(triangles)
    degenerate = degenerate_triangle_count(triangles)
    smallest_dim = min(bbox["width_x"], bbox["depth_y"], bbox["height_z"])

    score = 10.0
    critiques: List[str] = []
    errors: List[str] = []

    if not watertight:
        score -= 5.0
        errors.append("NON_MANIFOLD")
        critiques.append(
            f"NON_MANIFOLD_GEOMETRY: {non_manifold} edges are not shared by "
            "exactly 2 triangles; slicers will refuse or auto-repair this mesh."
        )

    if abs(bbox["min_z"]) > min_z_tolerance_mm:
        score -= 2.0
        errors.append("FLOATING_BASE")
        critiques.append(
            f"FLOATING_BASE: lowest Z is {bbox['min_z']} mm; translate the model "
            f"so it rests on the build plate (|z| <= {min_z_tolerance_mm} mm)."
        )

    if shells > 1:
        score -= 2.0
        errors.append("MULTIPLE_SHELLS")
        critiques.append(
            f"DISCONNECTED_SHELLS: {shells} separate bodies; merge them or export "
            "each part separately."
        )

    if smallest_dim < min_wall_mm:
        score -= 1.5
        errors.append("THIN_FEATURE")
        critiques.append(
            f"THIN_FEATURE_WARNING: smallest bounding dimension is {smallest_dim} mm "
            f"(< {min_wall_mm} mm FDM floor)."
        )

    if volume <= _EPS:
        score -= 1.0
        errors.append("INVERTED_OR_ZERO_VOLUME")
        critiques.append(
            f"INVERTED_NORMALS: signed volume is {volume:.3f} mm^3; flip face "
            "winding so normals point outward."
        )

    if degenerate:
        score -= 1.0
        errors.append("DEGENERATE_TRIANGLES")
        critiques.append(
            f"DEGENERATE_TRIANGLES: {degenerate} zero-area facets found."
        )

    score = round(max(0.0, score), 2)
    return {
        "file": file_label,
        "triangle_count": len(triangles),
        "vertex_count": len({v for tri in triangles for v in tri}),
        "watertight": watertight,
        "non_manifold_edge_count": non_manifold,
        "shell_count": shells,
        "bounding_box_mm": bbox,
        "volume_mm3": round(volume, 3),
        "degenerate_triangle_count": degenerate,
        "critic_score": score,
        "approved": bool(not errors and watertight and shells == 1),
        "critiques": critiques,
        "errors": errors,
    }


def audit_stl_file(
    path: Union[str, Path],
    min_wall_mm: float = 2.0,
    min_z_tolerance_mm: float = 0.01,
) -> Dict[str, object]:
    """Audit a STL file on disk (ASCII or binary)."""
    stl_path = Path(path)
    report = audit_stl_mesh(
        stl_path.read_bytes(),
        min_wall_mm=min_wall_mm,
        min_z_tolerance_mm=min_z_tolerance_mm,
        file_label=stl_path.name,
    )
    report["path"] = str(stl_path)
    return report
