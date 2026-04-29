import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRECHECK = ROOT / "scripts" / "precheck.py"
FIXTURES = ROOT / "tests" / "fixtures"


def run(args, env=None):
    return subprocess.run(
        ["python3", str(PRECHECK), *args],
        capture_output=True, text=True, env=env)


class TestPrecheck(unittest.TestCase):
    def test_missing_image_dir(self):
        r = run(["--image-dir", "/nonexistent/path", "--mode", "full"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", r.stdout + r.stderr)

    def test_bad_magic_aborts(self):
        # Set up a tmp dir with bad parameter.txt + dummy MiniLoaderAll.bin
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(FIXTURES / "parameter_bad_magic.txt",
                        Path(td) / "parameter.txt")
            (Path(td) / "MiniLoaderAll.bin").write_bytes(b"\x00")
            r = run(["--image-dir", td, "--mode", "full", "--no-adb-check"])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("MAGIC", r.stdout + r.stderr)

    def test_ok_param_passes_when_no_adb_check(self):
        import tempfile, shutil
        with tempfile.TemporaryDirectory() as td:
            shutil.copy(FIXTURES / "parameter_ok.txt",
                        Path(td) / "parameter.txt")
            (Path(td) / "MiniLoaderAll.bin").write_bytes(b"\x00")
            for img in ["boot.img", "dtbo.img", "super.img", "recovery.img",
                        "vbmeta.img", "uboot.img", "misc.img", "resource.img",
                        "baseparameter.img", "security.img", "trust.img",
                        "metadata.img", "cache.img", "backup.img"]:
                (Path(td) / img).write_bytes(b"\x00")
            r = run(["--image-dir", td, "--mode", "full", "--no-adb-check"])
            self.assertEqual(r.returncode, 0, msg=r.stderr)


if __name__ == "__main__":
    unittest.main()
