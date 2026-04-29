import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "flash_auto.py"
FIXTURES = ROOT / "tests" / "fixtures"


def make_image_dir(td: Path, mtimes: dict):
    """Create empty .img files with the given mtimes."""
    for name, mtime in mtimes.items():
        f = td / name
        f.write_bytes(b"\x00")
        os.utime(f, (mtime, mtime))


class TestFlashAuto(unittest.TestCase):
    def test_first_run_no_state_returns_indicator(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            make_image_dir(td, {"boot.img": 1745568000})
            r = subprocess.run(
                ["python3", str(SCRIPT),
                 "--image-dir", str(td),
                 "--state-file", str(td / "state.json"),
                 "--print-only"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("FIRST_RUN", r.stdout)

    def test_second_run_no_changes(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            make_image_dir(td, {"boot.img": 1745568000})
            state = td / "state.json"
            state.write_text(json.dumps({
                "image_dir": str(td),
                "files": {"boot.img": {"mtime": 1745568000}},
            }))
            r = subprocess.run(
                ["python3", str(SCRIPT),
                 "--image-dir", str(td),
                 "--state-file", str(state),
                 "--print-only"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("NO_CHANGES", r.stdout)

    def test_changed_partition_listed(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            make_image_dir(td, {"boot.img": 1745568000, "dtbo.img": 1745999999})
            state = td / "state.json"
            state.write_text(json.dumps({
                "image_dir": str(td),
                "files": {
                    "boot.img": {"mtime": 1745568000},
                    "dtbo.img": {"mtime": 1745568000},
                },
            }))
            r = subprocess.run(
                ["python3", str(SCRIPT),
                 "--image-dir", str(td),
                 "--state-file", str(state),
                 "--print-only"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("dtbo", r.stdout)
            self.assertNotIn("boot ", r.stdout)  # space prevents matching dtbo


if __name__ == "__main__":
    unittest.main()
