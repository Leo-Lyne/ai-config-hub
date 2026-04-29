import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lib_parameter as lp

FIXTURES = ROOT / "tests" / "fixtures"


class TestParseParameter(unittest.TestCase):
    def test_parses_ok_fixture(self):
        p = lp.parse(FIXTURES / "parameter_ok.txt")
        self.assertEqual(p.machine_model, "ATK-DLRK3568")
        self.assertEqual(p.magic, 0x5041524B)
        self.assertIn("boot", p.partitions)
        self.assertIn("userdata", p.partitions)
        self.assertIn("super", p.partitions)

    def test_validate_passes_on_good_param(self):
        p = lp.parse(FIXTURES / "parameter_ok.txt")
        self.assertTrue(p.is_magic_valid())

    def test_validate_fails_on_bad_magic(self):
        p = lp.parse(FIXTURES / "parameter_bad_magic.txt")
        self.assertFalse(p.is_magic_valid())

    def test_partitions_extracts_names_only(self):
        p = lp.parse(FIXTURES / "parameter_ok.txt")
        for name in p.partitions:
            self.assertNotIn("@", name)
            self.assertNotIn(":", name)
            self.assertNotIn("(", name)


if __name__ == "__main__":
    unittest.main()
