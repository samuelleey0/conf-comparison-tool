import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DIRECTORY_SERVICE_PATH = ROOT_DIR / "directory_service.py"
DIRECTORY_SERVICE_SPEC = importlib.util.spec_from_file_location(
    "directory_service", DIRECTORY_SERVICE_PATH
)
if DIRECTORY_SERVICE_SPEC is None or DIRECTORY_SERVICE_SPEC.loader is None:
    raise ImportError(f"Unable to load {DIRECTORY_SERVICE_PATH}")

directory_service = importlib.util.module_from_spec(DIRECTORY_SERVICE_SPEC)
DIRECTORY_SERVICE_SPEC.loader.exec_module(directory_service)
save_log_entry = directory_service.save_log_entry
sync_unified_logs_to_mirror = directory_service.sync_unified_logs_to_mirror


class MirrorSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.docs_dir = self.root / "Documents"
        self.engine_dir = self.root / "comparison_engine" / "students"
        self.session_dir = self.docs_dir / "ClassA" / "TutorA" / "TimeA"
        self.session_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def mirror_files(self, student="1001", device="R1"):
        target = self.engine_dir / "ClassA" / "TutorA" / "TimeA" / student / device
        return sorted(path for path in target.glob("*.txt") if path.is_file())

    def sync(self):
        return sync_unified_logs_to_mirror(
            docs_dir=self.docs_dir, engine_students_dir=self.engine_dir
        )

    def test_serial_collection_syncs_to_mirror(self):
        save_log_entry(
            "1001",
            "R1",
            "show running-config",
            "hostname R1\n",
            "serial",
            session_dir=self.session_dir,
        )

        result = self.sync()

        self.assertTrue(result["success"])
        self.assertEqual(result["synced_count"], 1)
        self.assertEqual(len(self.mirror_files()), 1)

    def test_manual_paste_syncs_to_mirror(self):
        save_log_entry(
            "1001",
            "R1",
            "show ip route",
            "Gateway of last resort is not set\n",
            "manual",
            session_dir=self.session_dir,
        )

        result = self.sync()

        self.assertTrue(result["success"])
        self.assertEqual(result["synced_count"], 1)
        self.assertEqual(len(self.mirror_files()), 1)

    def test_mixed_serial_and_manual_sync(self):
        save_log_entry(
            "1001",
            "R1",
            "show running-config",
            "hostname R1\n",
            "serial",
            session_dir=self.session_dir,
        )
        save_log_entry(
            "1001",
            "R1",
            "show ip interface brief",
            "Interface IP-Address OK? Method Status Protocol\n",
            "manual",
            session_dir=self.session_dir,
        )

        result = self.sync()

        self.assertTrue(result["success"])
        self.assertEqual(result["synced_count"], 2)
        self.assertEqual(len(self.mirror_files()), 2)

    def test_empty_manual_log_is_rejected(self):
        with self.assertRaises(ValueError):
            save_log_entry(
                "1001",
                "R1",
                "show running-config",
                "   ",
                "manual",
                session_dir=self.session_dir,
            )

    def test_existing_mirrored_files_are_not_duplicated(self):
        save_log_entry(
            "1001",
            "R1",
            "show running-config",
            "hostname R1\n",
            "serial",
            session_dir=self.session_dir,
        )

        first = self.sync()
        second = self.sync()

        self.assertEqual(first["synced_count"], 1)
        self.assertEqual(second["synced_count"], 0)
        self.assertEqual(len(self.mirror_files()), 1)

    def test_empty_sync_reports_no_valid_logs(self):
        result = self.sync()

        self.assertFalse(result["success"])
        self.assertEqual(result["synced_count"], 0)
        self.assertEqual(result["message"], "No valid logs available for mirror sync")


if __name__ == "__main__":
    unittest.main()
