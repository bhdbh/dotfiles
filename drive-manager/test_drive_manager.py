"""Exercise USB discovery, automount decisions and link ownership in temp dirs."""

import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

loader = importlib.machinery.SourceFileLoader("drive_manager", str(Path(__file__).resolve().with_name("drive-manager")))
spec = importlib.util.spec_from_loader(loader.name, loader)
dm = importlib.util.module_from_spec(spec)
loader.exec_module(dm)


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="drive-manager-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.mount = self.base / "USB Files"
        self.mount.mkdir()
        self.objects = {
            "/usb": {dm.DRIVE: {"ConnectionBus": "usb", "TimeMediaDetected": 100}},
            "/sda1": {dm.BLOCK: {"Drive": "/usb", "Device": b"/dev/sda1\0", "IdLabel": "Files", "IdUUID": "AABB-1234", "IdUsage": "filesystem", "HintAuto": True},
                      dm.FILESYSTEM: {"MountPoints": [bytes(self.mount) + b"\0"]}},
        }
        self.links = dm.Links(self.base / "home/Drives", self.base / "state/links.json")

    def mounted(self):
        return [v for v in dm.volumes(self.objects).values() if v["mount"]]

    def test_mount_unmount_replug_and_restart(self):
        self.links.root.parent.mkdir()
        self.links.sync(self.mounted())
        link = self.links.root / "Files"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.mount)
        (self.mount / "keep.txt").write_text("keep")
        # A new process must recover ownership and remove stale links safely.
        restarted = dm.Links(self.links.root, self.links.state)
        restarted.sync([])
        self.assertFalse(self.links.root.exists())
        self.assertEqual((self.mount / "keep.txt").read_text(), "keep")
        restarted.sync(self.mounted())
        self.assertTrue(link.is_dir())

    def test_duplicate_labels_and_user_entries_are_preserved(self):
        self.links.root.mkdir(parents=True)
        (self.links.root / "Files").write_text("user file")
        other = self.base / "second"
        other.mkdir()
        self.objects["/sdb1"] = {dm.BLOCK: {**self.objects["/sda1"][dm.BLOCK], "Device": b"/dev/sdb1\0", "IdUUID": "CCDD-5678"},
                                dm.FILESYSTEM: {"MountPoints": [bytes(other) + b"\0"]}}
        self.links.sync(self.mounted())
        self.assertEqual(len(list(self.links.root.iterdir())), 3)
        self.assertEqual((self.links.root / "Files (AABB-1234)").resolve(), self.mount)
        self.assertEqual((self.links.root / "Files (CCDD-5678)").resolve(), other)
        self.links.sync([])
        self.assertEqual((self.links.root / "Files").read_text(), "user file")

    def test_internal_disks_are_excluded_and_unlocked_usb_is_included(self):
        self.objects["/internal"] = {dm.DRIVE: {"ConnectionBus": "nvme"}}
        self.objects["/nvme1"] = {dm.BLOCK: {"Drive": "/internal"}, dm.FILESYSTEM: {"MountPoints": [b"/boot\0"]}}
        self.objects["/unlocked"] = {dm.BLOCK: {"Drive": "/", "CryptoBackingDevice": "/sda1", "IdLabel": "Private", "IdUsage": "filesystem"},
                                    dm.FILESYSTEM: {"MountPoints": []}}
        current = dm.volumes(self.objects)
        self.assertEqual(set(current), {"/sda1", "/unlocked"})
        self.assertTrue(current["/unlocked"]["auto"])

    def test_labels_are_safe_and_unlabelled_volumes_use_uuid(self):
        self.objects["/sda1"][dm.BLOCK]["IdLabel"] = "../bad/name\n"
        self.assertEqual(self.mounted()[0]["label"], "_bad_name_")
        self.objects["/sda1"][dm.BLOCK]["IdLabel"] = ""
        self.assertEqual(self.mounted()[0]["label"], "AABB-1234")

    def test_replaced_managed_link_does_not_become_owned(self):
        self.links.root.parent.mkdir()
        self.links.sync(self.mounted())
        link = self.links.root / "Files"
        link.unlink()
        link.write_text("user replacement")
        self.links.sync([])
        self.assertEqual(link.read_text(), "user replacement")

    def test_refuses_a_symlink_at_drives_directory(self):
        self.links.root.parent.mkdir()
        self.links.root.symlink_to(self.mount)
        with self.assertRaises(ValueError):
            self.links.sync(self.mounted())

    def test_automount_once_per_attachment_respects_manual_unmount(self):
        manager = dm.Manager.__new__(dm.Manager)
        manager.pending = False
        manager.seen = {}
        manager.links = Mock()
        manager.bus = Mock()
        manager.dbus = Mock()
        proxy = Mock()
        proxy.GetManagedObjects.side_effect = lambda **kwargs: self.objects
        manager.dbus.Interface.return_value = proxy
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        proxy.Mount.assert_called_once()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = [bytes(self.mount) + b"\0"]
        manager.refresh()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, 1)
        volume = self.objects.pop("/sda1")
        manager.refresh()
        self.objects["/sda1"] = volume
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
