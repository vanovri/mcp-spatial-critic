"""Tests for the deterministic STL critic."""

import importlib.util
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from mcp_spatial_critic.core import (
    audit_stl_file,
    audit_stl_mesh,
    bounding_box,
    count_shells,
    degenerate_triangle_count,
    edge_counts,
    generate_watertight_box_stl,
    parse_ascii_stl,
    parse_binary_stl,
    parse_stl,
    signed_volume,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = REPO_ROOT / "presets"


def serialize(triangles, name="mesh"):
    lines = [f"solid {name}"]
    for v1, v2, v3 in triangles:
        lines += ["  facet normal 0 0 0", "    outer loop"]
        lines += ["      vertex {} {} {}".format(*v) for v in (v1, v2, v3)]
        lines += ["    endloop", "  endfacet"]
    lines.append(f"endsolid {name}")
    return "\n".join(lines) + "\n"


def to_binary(triangles):
    body = b""
    for tri in triangles:
        coords = [c for vertex in tri for c in vertex]
        body += struct.pack("<12fH", 0.0, 0.0, 0.0, *coords, 0)
    return b"\0" * 80 + struct.pack("<I", len(triangles)) + body


def box_triangles(width=20.0, depth=20.0, height=10.0):
    return parse_ascii_stl(generate_watertight_box_stl(width, depth, height))


def shifted(triangles, dx=0.0, dy=0.0, dz=0.0):
    return [tuple((v[0] + dx, v[1] + dy, v[2] + dz) for v in tri) for tri in triangles]


def flipped(triangles):
    return [(v1, v3, v2) for v1, v2, v3 in triangles]


class ParseTests(unittest.TestCase):
    def test_ascii_box_has_twelve_triangles(self):
        self.assertEqual(len(box_triangles()), 12)

    def test_ascii_parser_tolerates_crlf_and_extra_whitespace(self):
        text = generate_watertight_box_stl().replace("\n", "\r\n")
        text = text.replace("      vertex", "\t vertex  ")
        self.assertEqual(len(parse_ascii_stl(text)), 12)

    def test_ascii_parser_ignores_malformed_vertex_lines(self):
        text = "solid x\n vertex 1 2\n vertex 1 2 3\n vertex 4 5 6\n vertex 7 8 9\nendsolid x\n"
        self.assertEqual(len(parse_ascii_stl(text)), 1)

    def test_binary_stl_round_trip(self):
        triangles = box_triangles()
        parsed = parse_binary_stl(to_binary(triangles))
        self.assertEqual(len(parsed), 12)
        self.assertAlmostEqual(signed_volume(parsed), 4000.0, places=2)

    def test_parse_stl_dispatches_binary_by_default(self):
        self.assertEqual(len(parse_stl(to_binary(box_triangles()))), 12)
        self.assertEqual(len(parse_stl(generate_watertight_box_stl())), 12)

    def test_binary_parser_rejects_short_header(self):
        with self.assertRaises(ValueError):
            parse_binary_stl(b"too short")


class TopologyTests(unittest.TestCase):
    def test_every_edge_shared_by_two_triangles(self):
        counts = edge_counts(box_triangles())
        self.assertEqual(len(counts), 18)  # Euler: 8 vertices, 12 tris, 18 edges
        self.assertTrue(all(count == 2 for count in counts.values()))

    def test_single_shell_for_closed_box(self):
        self.assertEqual(count_shells(box_triangles()), 1)

    def test_two_separate_bodies_count_two_shells(self):
        combined = box_triangles() + shifted(box_triangles(), dx=50.0)
        self.assertEqual(count_shells(combined), 2)

    def test_volume_of_reference_box(self):
        self.assertAlmostEqual(signed_volume(box_triangles()), 4000.0, places=3)

    def test_flipped_winding_is_negative_volume(self):
        self.assertAlmostEqual(signed_volume(flipped(box_triangles())), -4000.0, places=3)

    def test_bounding_box_dimensions(self):
        bbox = bounding_box(box_triangles(width=25.0, depth=30.0, height=12.0))
        self.assertEqual(bbox["width_x"], 25.0)
        self.assertEqual(bbox["depth_y"], 30.0)
        self.assertEqual(bbox["height_z"], 12.0)
        self.assertEqual(bbox["min_z"], 0.0)

    def test_no_degenerate_triangles_in_reference_box(self):
        self.assertEqual(degenerate_triangle_count(box_triangles()), 0)


class AuditTests(unittest.TestCase):
    def test_reference_box_is_approved_with_perfect_score(self):
        report = audit_stl_mesh(generate_watertight_box_stl())
        self.assertEqual(report["critic_score"], 10.0)
        self.assertTrue(report["approved"])
        self.assertTrue(report["watertight"])
        self.assertEqual(report["shell_count"], 1)
        self.assertEqual(report["triangle_count"], 12)
        self.assertEqual(report["vertex_count"], 8)
        self.assertEqual(report["critiques"], [])
        self.assertEqual(report["errors"], [])
        self.assertAlmostEqual(report["volume_mm3"], 4000.0, places=2)

    def test_open_mesh_is_rejected_as_non_manifold(self):
        report = audit_stl_mesh(serialize(box_triangles()[:-1]))
        self.assertFalse(report["watertight"])
        self.assertFalse(report["approved"])
        self.assertIn("NON_MANIFOLD", report["errors"])
        self.assertEqual(report["critic_score"], 5.0)
        self.assertGreater(report["non_manifold_edge_count"], 0)

    def test_floating_base_loses_two_points(self):
        report = audit_stl_mesh(serialize(shifted(box_triangles(), dz=5.0)))
        self.assertIn("FLOATING_BASE", report["errors"])
        self.assertEqual(report["critic_score"], 8.0)
        self.assertFalse(report["approved"])
        self.assertEqual(report["bounding_box_mm"]["min_z"], 5.0)

    def test_disconnected_shells_are_reported(self):
        combined = box_triangles() + shifted(box_triangles(), dx=50.0)
        report = audit_stl_mesh(serialize(combined))
        self.assertEqual(report["shell_count"], 2)
        self.assertEqual(report["non_manifold_edge_count"], 0)
        self.assertIn("MULTIPLE_SHELLS", report["errors"])
        self.assertEqual(report["critic_score"], 8.0)

    def test_inverted_normals_are_not_approved(self):
        report = audit_stl_mesh(serialize(flipped(box_triangles())))
        self.assertIn("INVERTED_OR_ZERO_VOLUME", report["errors"])
        self.assertFalse(report["approved"])
        self.assertLess(report["volume_mm3"], 0)
        self.assertEqual(report["critic_score"], 9.0)

    def test_thin_plate_triggers_printability_warning(self):
        report = audit_stl_mesh(generate_watertight_box_stl(height=0.8))
        self.assertIn("THIN_FEATURE", report["errors"])
        self.assertFalse(report["approved"])
        self.assertEqual(report["critic_score"], 8.5)

    def test_degenerate_facet_is_counted(self):
        degenerate = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        report = audit_stl_mesh(serialize(box_triangles() + [degenerate]))
        self.assertEqual(report["degenerate_triangle_count"], 1)
        self.assertIn("DEGENERATE_TRIANGLES", report["errors"])

    def test_empty_payload_is_zero_score(self):
        report = audit_stl_mesh("solid empty\nendsolid empty\n")
        self.assertEqual(report["critic_score"], 0.0)
        self.assertFalse(report["approved"])
        self.assertEqual(report["errors"], ["EMPTY_MESH"])

    def test_audit_file_adds_path_and_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bracket.stl"
            target.write_text(generate_watertight_box_stl(), encoding="utf-8")
            report = audit_stl_file(target)
        self.assertEqual(report["file"], "bracket.stl")
        self.assertTrue(report["path"].endswith("bracket.stl"))
        self.assertTrue(report["approved"])

    def test_audit_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            audit_stl_file(REPO_ROOT / "does-not-exist.stl")


class PresetTests(unittest.TestCase):
    def _load_pagoda(self):
        """Import presets/pagoda_roof_tier.py by path (it is not a package)."""
        spec = importlib.util.spec_from_file_location(
            "pagoda_roof_tier", PRESETS_DIR / "pagoda_roof_tier.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_scad_presets_are_present(self):
        esp32 = (PRESETS_DIR / "esp32_mount.scad").read_text(encoding="utf-8")
        boss = (PRESETS_DIR / "m3_heatset_boss.scad").read_text(encoding="utf-8")
        self.assertIn("module esp32_mount()", esp32)
        self.assertIn("usb_cutout", esp32)
        self.assertIn("module m3_heatset_boss()", boss)
        self.assertIn("insert_depth", boss)

    def test_pagoda_generator_emits_openscad(self):
        module = self._load_pagoda()
        output = module.render_pagoda()
        self.assertIn("module tier_1()", output)
        self.assertIn("module tier_4()", output)
        self.assertIn("pagoda_roof();", output)
        self.assertEqual(module.total_height_mm(module.DEFAULT_TIERS), 30.0)
        self.assertLessEqual(max(t.step_overhang_mm for t in module.DEFAULT_TIERS), 2.5)

    def test_tier_validation_rejects_bad_geometry(self):
        module = self._load_pagoda()
        with self.assertRaises(ValueError):
            module.TierSpec(radius_mm=0.0, height_mm=5.0).validate()
        with self.assertRaises(ValueError):
            module.TierSpec(radius_mm=10.0, height_mm=5.0, steps=0).validate()


if __name__ == "__main__":
    unittest.main()
