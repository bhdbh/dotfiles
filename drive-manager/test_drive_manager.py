"""Exercise USB discovery, automount decisions and link ownership in temp dirs."""

import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

loader = importlib.machinery.SourceFileLoader("drive_manager", str(Path(__file__).resolve().with_name("drive-manager")))
spec = importlib.util.spec_from_loader(loader.name, loader)
dm = importlib.util.module_from_spec(spec)
loader.exec_module(dm)


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="drive-manager-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.attachment = patch.object(dm, "attachment_id", return_value=("diskseq", boot, "10")).start()
        self.addCleanup(patch.stopall)
        self.clock = patch.object(dm.time, "monotonic", return_value=100).start()
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
        self.assertEqual(current["/unlocked"]["identity"][2:], current["/sda1"]["identity"][2:])
        self.attachment.assert_any_call("/dev/sda1", self.objects["/usb"][dm.DRIVE])

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

    def manager(self):
        manager = dm.Manager.__new__(dm.Manager)
        manager.pending = False
        manager.inflight = {}
        manager.failures = {}
        manager.available = True
        manager.history = dm.MountHistory(self.base / "state/mounts.json")
        manager.links = Mock()
        manager.bus = Mock()
        manager.dbus = Mock()
        manager.GLib = Mock()
        proxy = Mock()
        proxy.GetManagedObjects.side_effect = lambda **kwargs: self.objects
        manager.dbus.Interface.return_value = proxy
        return manager, proxy

    def test_automount_once_per_attachment_respects_manual_unmount(self):
        manager, proxy = self.manager()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        proxy.Mount.assert_called_once()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = [bytes(self.mount) + b"\0"]
        manager.refresh()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, 1)
        # The same block path/UUID after replug has a new kernel diskseq.
        self.attachment.return_value = (*self.attachment.return_value[:2], "11")
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, 2)

    def test_pending_mount_is_not_duplicated_by_periodic_or_signals(self):
        manager, proxy = self.manager()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        manager.schedule()
        manager.periodic()
        manager.refresh()
        proxy.Mount.assert_called_once()

    def test_transient_failure_retries_after_backoff_then_persists_success(self):
        manager, proxy = self.manager()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        with self.assertLogs(level="ERROR"):
            proxy.Mount.call_args.kwargs["error_handler"](RuntimeError("busy"))
        manager.refresh()
        proxy.Mount.assert_called_once()
        self.clock.return_value += 5
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, 2)
        proxy.Mount.call_args.kwargs["reply_handler"](str(self.mount))
        # Success is recorded before a snapshot: an immediate manual unmount
        # and manager restart must not mount it again.
        restarted, second = self.manager()
        restarted.refresh()
        second.Mount.assert_not_called()

    def test_retry_budget_and_permanent_errors_do_not_loop(self):
        manager, proxy = self.manager()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        with self.assertLogs(level="ERROR"):
            for _ in range(manager.MAX_ATTEMPTS):
                manager.refresh()
                proxy.Mount.call_args.kwargs["error_handler"](RuntimeError("busy"))
                self.clock.return_value += 1000
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, manager.MAX_ATTEMPTS)
        manager.owner_changed(dm.SERVICE, ":1.old", ":1.new")
        manager.refresh()
        error = Mock(get_dbus_name=lambda: dm.SERVICE + ".Error.NotSupported")
        with self.assertLogs(level="ERROR"):
            proxy.Mount.call_args.kwargs["error_handler"](error)
        self.clock.return_value += 1000
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, manager.MAX_ATTEMPTS + 1)

    def test_sync_mount_exception_retries_without_aborting_reconciliation(self):
        manager, proxy = self.manager()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        proxy.Mount.side_effect = RuntimeError("disconnected")
        with self.assertLogs(level="ERROR"):
            manager.refresh()
        manager.links.sync.assert_called_once_with([])
        self.assertFalse(manager.inflight)
        proxy.Mount.side_effect = None
        self.clock.return_value += 5
        manager.refresh()
        self.assertEqual(proxy.Mount.call_count, 2)

    def test_manual_unmount_survives_udisks_and_manager_restart(self):
        manager, proxy = self.manager()
        manager.refresh()  # observes an already-mounted volume
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        manager.owner_changed(dm.SERVICE, ":1.old", "")
        before = proxy.GetManagedObjects.call_count
        manager.refresh()
        self.assertEqual(proxy.GetManagedObjects.call_count, before)
        manager.owner_changed(dm.SERVICE, "", ":1.new")
        # UDisks timestamps may change; the kernel attachment is unchanged.
        self.objects["/usb"][dm.DRIVE]["TimeMediaDetected"] = 999
        manager.refresh()
        proxy.Mount.assert_not_called()
        restarted, second = self.manager()
        restarted.refresh()
        second.Mount.assert_not_called()
        self.attachment.return_value = (*self.attachment.return_value[:2], "12")
        restarted.refresh()
        second.Mount.assert_called_once()

    def test_stale_mount_callback_cannot_mark_a_new_attachment_mounted(self):
        manager, proxy = self.manager()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        old = proxy.Mount.call_args.kwargs
        self.attachment.return_value = (*self.attachment.return_value[:2], "11")
        manager.refresh()
        old["reply_handler"](str(self.mount))
        self.assertFalse(manager.history.mounted)
        old["error_handler"](RuntimeError("old failure"))
        self.assertIn("/sda1", manager.inflight)
        proxy.Mount.call_args.kwargs["reply_handler"](str(self.mount))
        self.assertEqual(len(manager.history.mounted), 1)

    def test_owner_change_invalidates_pending_callback_only(self):
        manager, proxy = self.manager()
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        old = proxy.Mount.call_args.kwargs
        manager.owner_changed(dm.SERVICE, ":1.old", "")
        old["reply_handler"](str(self.mount))
        self.assertFalse(manager.history.mounted)

    def test_signal_coalescing_and_periodic_recovery(self):
        manager, proxy = self.manager()
        manager.schedule()
        manager.schedule()
        manager.periodic()
        manager.GLib.timeout_add.assert_called_once_with(150, manager.refresh)
        manager.refresh()
        self.assertTrue(manager.periodic())
        self.assertEqual(manager.GLib.timeout_add.call_count, 2)

    def test_history_write_failure_keeps_suppression_and_retries_persistence(self):
        manager, proxy = self.manager()
        with patch.object(dm.os, "replace", side_effect=OSError("full filesystem")):
            with self.assertLogs(level="ERROR"):
                manager.refresh()
        self.assertTrue(manager.history.mounted)
        self.assertTrue(manager.history.dirty)
        self.objects["/sda1"][dm.FILESYSTEM]["MountPoints"] = []
        manager.refresh()
        proxy.Mount.assert_not_called()
        self.assertFalse(manager.history.dirty)
        restarted, second = self.manager()
        restarted.refresh()
        second.Mount.assert_not_called()

    def test_mount_history_discards_other_boots_and_rejects_corruption(self):
        state = self.base / "mounts.json"
        state.write_text('[ ["/dev/sda1", "uuid", "diskseq", "old-boot", "10"] ]')
        self.assertFalse(dm.MountHistory(state).mounted)
        state.write_text('{"unexpected": true}')
        with self.assertRaises(ValueError):
            dm.MountHistory(state)


class AttachmentIdentityTests(unittest.TestCase):
    def test_partition_and_encrypted_backing_use_physical_diskseq(self):
        with tempfile.TemporaryDirectory(prefix="drive-manager-sysfs-") as directory:
            root = Path(directory)
            disk = root / "devices/sda"
            partition = disk / "sda1"
            partition.mkdir(parents=True)
            (partition / "partition").write_text("1")
            (disk / "diskseq").write_text("123")
            sys_block = root / "block"
            sys_block.mkdir()
            (sys_block / "sda1").symlink_to(partition)
            boot = root / "boot_id"
            boot.write_text("boot-test")
            self.assertEqual(dm.attachment_id("/dev/sda1", {}, sys_block, boot),
                             ("diskseq", "boot-test", "123"))
            (disk / "diskseq").write_text("124")
            self.assertEqual(dm.attachment_id("/dev/sda1", {}, sys_block, boot)[2], "124")
            self.assertEqual(dm.attachment_id("/dev/missing", {"TimeMediaDetected": 9}, sys_block, boot),
                             ("udisks", "boot-test", "9"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
