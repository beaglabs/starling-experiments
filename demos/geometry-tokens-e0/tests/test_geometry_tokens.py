from pathlib import Path
import unittest

from geometry_tokens import (
    GeometryTokenError,
    load_geometry_tokens,
    parse_geometry_tokens,
)


HERE = Path(__file__).resolve().parents[1]


class GeometryTokensTest(unittest.TestCase):
    def test_starling_spec_is_strict_and_complete(self):
        doc = load_geometry_tokens(HERE / "specs" / "iridescent_starling.gtok")
        self.assertEqual(doc.asset, "Iridescent Starling")
        self.assertEqual(len(doc.materials), 4)
        self.assertEqual(len(doc.primitives), 20)
        self.assertEqual(doc.primitives[0].name, "body")
        self.assertEqual(doc.primitives[-1].name, "toe_right_b")
        self.assertIn('"version":"0.1"', doc.canonical_json())

    def test_unknown_material_fails_closed(self):
        text = """\
GTOK 0.1
asset demo
material body pbr 0.1 0.2 0.3 0.0 0.5
ellipsoid sphere missing 0 0 0 1 1 1 0 0 0
"""
        with self.assertRaisesRegex(GeometryTokenError, "unknown material"):
            parse_geometry_tokens(text)

    def test_invalid_scale_fails_closed(self):
        text = """\
GTOK 0.1
asset demo
material body pbr 0.1 0.2 0.3 0.0 0.5
ellipsoid sphere body 0 0 0 1 0 1 0 0 0
"""
        with self.assertRaisesRegex(GeometryTokenError, "scales must be positive"):
            parse_geometry_tokens(text)

    def test_header_is_versioned(self):
        with self.assertRaisesRegex(GeometryTokenError, "first token"):
            parse_geometry_tokens("GTOK 9.9\nasset demo\n")


if __name__ == "__main__":
    unittest.main()
