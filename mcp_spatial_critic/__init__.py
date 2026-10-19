"""mcp_spatial_critic — deterministic 3D mesh critic for headless CAD agents."""

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

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "audit_stl_file",
    "audit_stl_mesh",
    "bounding_box",
    "count_shells",
    "degenerate_triangle_count",
    "edge_counts",
    "generate_watertight_box_stl",
    "parse_ascii_stl",
    "parse_binary_stl",
    "parse_stl",
    "signed_volume",
]
