import subprocess
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "boot_verify.py"
FIXTURES = ROOT / "tests" / "fixtures"


def run(log_path):
    return subprocess.run(
        ["python3", str(SCRIPT), "--log", str(log_path), "--json"],
        capture_output=True, text=True)


class TestBootVerify(unittest.TestCase):
    def test_success_log_all_milestones_hit(self):
        r = run(FIXTURES / "boot_log_success.txt")
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(set(data["milestones_hit"]),
                         {"maskrom_or_ddr", "u_boot", "kernel", "init_done"})

    def test_stuck_at_maskrom(self):
        r = run(FIXTURES / "boot_log_stuck_maskrom.txt")
        self.assertEqual(r.returncode, 2)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "fail")
        self.assertIn("u_boot", data["milestones_missed"])

    def test_kernel_panic(self):
        r = run(FIXTURES / "boot_log_kernel_panic.txt")
        self.assertEqual(r.returncode, 2)
        data = json.loads(r.stdout)
        self.assertIn("kernel_panic", data["fail_reasons"])


if __name__ == "__main__":
    unittest.main()
