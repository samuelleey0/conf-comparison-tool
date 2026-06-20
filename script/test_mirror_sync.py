import tempfile
import unittest
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import directory_service
from directory_service import save_log_entry, sync_unified_logs_to_mirror


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

    def test_legacy_direct_hostname_logs_sync_to_mirror(self):
        legacy_device_dir = self.session_dir / "1001" / "R1"
        legacy_device_dir.mkdir(parents=True)
        (legacy_device_dir / "show_running_config.txt").write_text(
            "hostname R1\n", encoding="utf-8"
        )

        result = self.sync()

        self.assertTrue(result["success"])
        self.assertEqual(result["synced_count"], 1)
        self.assertEqual(len(self.mirror_files()), 1)

    def test_repo_under_documents_is_not_mirrored(self):
        original_base_dir = directory_service.BASE_DIR
        repo_like_dir = (
            self.docs_dir
            / "GitHub"
            / "conf-comparison-tool"
            / "script"
            / "fake_student"
            / "fake_device"
        )
        repo_like_dir.mkdir(parents=True)
        (repo_like_dir / "not_a_student_log.txt").write_text(
            "this belongs to the repo\n", encoding="utf-8"
        )

        try:
            directory_service.BASE_DIR = self.docs_dir / "GitHub" / "conf-comparison-tool"
            result = self.sync()
        finally:
            directory_service.BASE_DIR = original_base_dir

        self.assertFalse(result["success"])
        self.assertEqual(result["synced_count"], 0)

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

    def test_sample_logs_are_not_mirrored(self):
        sample_device_dir = self.docs_dir / "sample" / "sample" / "sample" / "R1"
        sample_device_dir.mkdir(parents=True)
        (sample_device_dir / "show_running_config.txt").write_text(
            "hostname R1\n", encoding="utf-8"
        )

        result = self.sync()

        self.assertFalse(result["success"])
        self.assertEqual(result["synced_count"], 0)
        self.assertFalse((self.engine_dir / "sample").exists())


if __name__ == "__main__":
    unittest.main()
