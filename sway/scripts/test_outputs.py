"""Transitions that must keep a usable display and the intended primary."""
import copy
import os
import io
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock
import unittest

from outputs import PREFERRED, next_display_mode, plan, plan_workspaces, notify_ready, main


def screen(name, x, width, identity=("Other", "Monitor", "123"), active=True, height=768, y=0):
    return dict(name=name, make=identity[0], model=identity[1], serial=identity[2],
                active=active, rect=dict(x=x, y=y, width=width, height=height),
                modes=[dict(width=width, height=height)], scale=1)


class OutputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.panel = screen("eDP-1", 0, 1366, y=312)
        self.acer = screen("HDMI-A-1", 1366, 1920, (*PREFERRED, "EXAMPLE000001"), height=1080)
        self.other = screen("HDMI-A-1", 1366, 2560)

    def test_known_monitor_is_preferred(self):
        commands, primary = plan([self.panel, self.acer], False)
        self.assertEqual(primary, "HDMI-A-1")
        self.assertIn('focus output "HDMI-A-1"', commands)

    def test_identity_follows_monitor_to_another_connector(self):
        self.acer["name"] = "DP-2"
        self.assertEqual(plan([self.panel, self.acer], False)[1], "DP-2")

    def test_other_monitor_on_same_hdmi_is_secondary(self):
        commands, primary = plan([self.panel, self.other], False)
        self.assertEqual(primary, "eDP-1")
        self.assertIn('output "eDP-1" pos 0 0', commands)

    def test_preferred_model_is_selected_regardless_of_serial(self):
        self.acer["serial"] = "ANOTHER-UNIT"
        self.assertEqual(plan([self.panel, self.acer], False)[1], "HDMI-A-1")

    def test_laptop_only_never_disables_only_display(self):
        commands, primary = plan([self.panel], True)
        self.assertEqual(primary, "eDP-1")
        self.assertFalse(any("disable" in c for c in commands))

    def test_closing_lid_disables_internal_display(self):
        commands, primary = plan([self.panel, self.acer], True, "HDMI-A-1")
        self.assertEqual(primary, "HDMI-A-1")
        self.assertIn('output "eDP-1" disable', commands)

    def test_unknown_external_is_usable_with_lid_closed(self):
        commands, primary = plan([self.panel, self.other], True, "eDP-1")
        self.assertEqual(primary, "HDMI-A-1")
        self.assertIn('output "eDP-1" disable', commands)

    def test_opening_lid_restores_panel_without_stealing_acer_focus(self):
        self.panel["active"] = False
        commands, primary = plan([self.panel, self.acer], False, "HDMI-A-1")
        self.assertIn('output "eDP-1" enable', commands)
        self.assertEqual(primary, "HDMI-A-1")
        self.assertFalse(any(c.startswith("focus") for c in commands))

    def test_unplug_with_lid_closed_recovers_disabled_panel(self):
        self.panel["active"] = False
        commands, primary = plan([self.panel], True, "HDMI-A-1")
        self.assertEqual(primary, "eDP-1")
        self.assertIn('output "eDP-1" enable', commands)
        self.assertIn('output "eDP-1" pos 0 0', commands)

    def test_stable_configuration_does_not_loop_or_steal_focus(self):
        self.assertEqual(plan([self.panel, self.acer], False, "HDMI-A-1"), ([], "HDMI-A-1"))

    def test_laptop_left_with_bottom_alignment(self):
        self.panel["rect"].update(x=1920, y=0)
        self.acer["rect"]["x"] = 0
        commands, primary = plan([self.panel, self.acer], False, "HDMI-A-1")
        self.assertEqual(primary, "HDMI-A-1")
        self.assertIn('output "eDP-1" pos 0 312', commands)
        self.assertIn('output "HDMI-A-1" pos 1366 0', commands)

    def test_scaled_external_uses_logical_height(self):
        self.acer["scale"] = 2
        self.acer["rect"].update(width=1920, height=1080)
        self.acer["modes"] = [dict(width=3840, height=2160)]
        self.panel["rect"]["y"] = 0
        self.assertIn('output "eDP-1" pos 0 312', plan([self.panel, self.acer], False)[0])

    def test_closed_lid_external_moves_to_origin(self):
        commands, _ = plan([self.panel, self.acer], True, "HDMI-A-1")
        self.assertIn('output "HDMI-A-1" pos 0 0', commands)

    def test_reopening_panel_uses_mode_when_rect_is_empty(self):
        self.panel.update(active=False, rect=dict(x=0, y=0, width=0, height=0))
        self.acer["rect"]["x"] = 0
        commands, _ = plan([self.panel, self.acer], False, "HDMI-A-1")
        self.assertIn('output "eDP-1" pos 0 312', commands)
        self.assertIn('output "HDMI-A-1" pos 1366 0', commands)

    def test_additional_display_does_not_replace_acer(self):
        other = copy.deepcopy(self.other)
        other["name"] = "DP-1"
        self.assertEqual(plan([other, self.panel, self.acer], False)[1], "HDMI-A-1")

    def test_external_mode_disables_panel_and_places_monitor_at_origin(self):
        commands, primary = plan([self.panel, self.acer], False, "HDMI-A-1", "external")
        self.assertEqual(primary, "HDMI-A-1")
        self.assertIn('output "eDP-1" disable', commands)
        self.assertIn('output "HDMI-A-1" pos 0 0', commands)

    def test_internal_mode_enables_panel_before_disabling_external(self):
        self.panel["active"] = False
        commands, primary = plan([self.panel, self.acer], False, "HDMI-A-1", "internal")
        self.assertEqual(primary, "eDP-1")
        self.assertLess(commands.index('output "eDP-1" enable'),
                        commands.index('output "HDMI-A-1" disable'))

    def test_extended_mode_restores_disabled_external(self):
        self.acer["active"] = False
        commands, primary = plan([self.panel, self.acer], False, "eDP-1", "extended")
        self.assertEqual(primary, "HDMI-A-1")
        self.assertIn('output "HDMI-A-1" enable', commands)
        self.assertFalse(any("disable" in command for command in commands))

    def test_unplug_in_external_mode_recovers_disabled_panel(self):
        self.panel["active"] = False
        commands, primary = plan([self.panel], True, "HDMI-A-1", "external")
        self.assertEqual(primary, "eDP-1")
        self.assertIn('output "eDP-1" enable', commands)
        self.assertFalse(any("disable" in command for command in commands))

    def test_closed_lid_overrides_internal_mode(self):
        self.acer["active"] = False
        commands, primary = plan([self.panel, self.acer], True, "eDP-1", "internal")
        self.assertEqual(primary, "HDMI-A-1")
        self.assertLess(commands.index('output "HDMI-A-1" enable'),
                        commands.index('output "eDP-1" disable'))

    def test_internal_mode_without_internal_panel_keeps_external_usable(self):
        commands, primary = plan([self.acer], False, "HDMI-A-1", "internal")
        self.assertEqual(primary, "HDMI-A-1")
        self.assertFalse(any("disable" in command for command in commands))

    def test_display_key_cycles_three_modes(self):
        outputs = [self.panel, self.acer]
        self.assertEqual(next_display_mode(outputs, "auto"), "external")
        self.assertEqual(next_display_mode(outputs, "external"), "internal")
        self.assertEqual(next_display_mode(outputs, "internal"), "extended")

    def test_display_key_with_one_output_keeps_it_usable(self):
        self.assertEqual(next_display_mode([self.panel], "external"), "internal")
        self.assertEqual(next_display_mode([self.acer], "internal"), "external")

    def test_stable_explicit_mode_does_not_emit_changes(self):
        self.panel["active"] = False
        self.acer["rect"].update(x=0, y=0)
        self.assertEqual(plan([self.panel, self.acer], False, "HDMI-A-1", "external"),
                         ([], "HDMI-A-1"))


class WorkspacePolicyTests(unittest.TestCase):
    def setUp(self):
        self.outputs = [screen("eDP-1", 0, 1366), screen("HDMI-A-1", 1366, 1920)]
        self.workspaces = [
            dict(name="1", num=1, output="HDMI-A-1", visible=True, focused=True),
            dict(name="2", num=2, output="eDP-1", visible=True, focused=False),
        ]

    def test_secondary_keeps_its_windows_without_consuming_number_two(self):
        commands = plan_workspaces(self.outputs, self.workspaces, "HDMI-A-1")
        self.assertIn('rename workspace "2" to "isolated:eDP-1"', commands)
        self.assertIn('workspace 2 output "HDMI-A-1"', commands)
        self.assertFalse(any(c.startswith(('move ', 'workspace --')) for c in commands))

    def test_stable_policy_does_not_rename_or_steal_focus(self):
        self.workspaces[1].update(name="isolated:eDP-1", num=-1, focused=True)
        self.workspaces[0]["focused"] = False
        commands = plan_workspaces(self.outputs, self.workspaces, "HDMI-A-1")
        self.assertFalse(any(c.startswith(('rename ', 'move ', 'workspace --')) for c in commands))

    def test_reconnect_returns_isolated_workspace_and_restores_focus(self):
        self.workspaces[1].update(name="isolated:eDP-1", num=-1, output="HDMI-A-1", visible=False)
        commands = plan_workspaces(self.outputs, self.workspaces, "HDMI-A-1")
        self.assertIn('move workspace to output "eDP-1"', commands)
        self.assertEqual(commands[-1], 'workspace --no-auto-back-and-forth "1"')

    def test_numbered_workspace_on_secondary_moves_to_primary(self):
        self.workspaces.append(dict(name="isolated:eDP-1", num=-1, output="eDP-1", visible=False, focused=False))
        commands = plan_workspaces(self.outputs, self.workspaces, "HDMI-A-1")
        self.assertIn('workspace --no-auto-back-and-forth "2"', commands)
        self.assertIn('move workspace to output "HDMI-A-1"', commands)
        self.assertNotIn('rename workspace "2" to "isolated:eDP-1"', commands)

    def test_unplug_makes_former_secondary_available_through_numbered_button(self):
        self.workspaces[0]["output"] = "eDP-1"
        self.workspaces[0]["visible"] = False
        self.workspaces[1].update(name="isolated:eDP-1", num=-1, focused=True)
        commands = plan_workspaces(self.outputs[:1], self.workspaces, "eDP-1")
        self.assertIn('rename workspace "isolated:eDP-1" to "2"', commands)
        self.assertIn('workspace 1 output "eDP-1"', commands)

    def test_single_output_does_not_create_an_isolated_workspace(self):
        commands = plan_workspaces(self.outputs[:1], self.workspaces[1:], "eDP-1")
        self.assertFalse(any('isolated:' in c for c in commands))

    def test_unplug_numbers_hidden_isolated_workspace_too(self):
        self.workspaces[0].update(output="eDP-1")
        self.workspaces[1].update(name="isolated:eDP-1", num=-1, visible=False)
        commands = plan_workspaces(self.outputs[:1], self.workspaces, "eDP-1", "HDMI-A-1")
        self.assertIn('rename workspace "isolated:eDP-1" to "2"', commands)
        self.assertIn('workspace 2 output "eDP-1"', commands)

    def test_multiple_stranded_workspaces_get_distinct_numbers(self):
        self.workspaces[0].update(output="eDP-1")
        self.workspaces[1].update(name="isolated:eDP-1", num=-1, visible=False)
        self.workspaces.append(dict(name="isolated:DP-2", num=-1, output="eDP-1",
                                    visible=False, focused=False))
        commands = plan_workspaces(self.outputs[:1], self.workspaces, "eDP-1", "HDMI-A-1")
        self.assertIn('rename workspace "isolated:eDP-1" to "2"', commands)
        self.assertIn('rename workspace "isolated:DP-2" to "3"', commands)

    def test_reconnect_moves_numbered_workspaces_instead_of_isolating_them(self):
        self.workspaces[0].update(output="eDP-1")
        self.workspaces[1].update(visible=False)
        commands = plan_workspaces(self.outputs, self.workspaces, "HDMI-A-1", "eDP-1")
        self.assertNotIn('rename workspace "1" to "isolated:eDP-1"', commands)
        for name in ("1", "2"):
            self.assertIn(f'workspace --no-auto-back-and-forth "{name}"', commands)
        self.assertEqual(commands.count('move workspace to output "HDMI-A-1"'), 2)
        self.assertEqual(commands[-1], 'workspace --no-auto-back-and-forth "1"')

    def test_reconnect_restores_old_primary_selection_over_new_empty_workspace(self):
        self.workspaces[0].update(output="eDP-1", focused=False)
        self.workspaces[1].update(visible=False)
        self.workspaces.append(dict(name="3", num=3, output="HDMI-A-1",
                                    focused=True, visible=True))
        commands = plan_workspaces(self.outputs, self.workspaces, "HDMI-A-1", "eDP-1")
        self.assertEqual(commands[-1], 'workspace --no-auto-back-and-forth "1"')

    def test_full_numbered_set_preserves_disconnected_workspace_as_overflow(self):
        original = [dict(name=str(i), num=i, output='eDP-1', visible=i == 1,
                         focused=i == 1) for i in range(1, 10)]
        original.append(dict(name='isolated:HDMI-A-1', num=-1, output='eDP-1',
                             visible=False, focused=False))
        before = copy.deepcopy(original)
        commands = plan_workspaces(self.outputs[:1], original, 'eDP-1', 'HDMI-A-1')
        self.assertIn('rename workspace "isolated:HDMI-A-1" to "10"', commands)
        self.assertFalse(any(command.startswith(('kill', 'move container')) for command in commands))
        self.assertEqual(original, before)

    def test_watcher_notifies_after_native_initial_tick_and_primary_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Mock()
            process.stdout = io.StringIO('{"first": true, "payload": ""}\n')
            process.wait.return_value = 0
            context = Mock()
            context.__enter__ = Mock(return_value=process)
            context.__exit__ = Mock(return_value=False)
            steps = []
            def reconcile(runtime):
                steps.append('primary')
                (runtime / 'primary-output').write_text('HDMI-A-1\n')
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}), \
                    patch('outputs.sys.argv', ['outputs.py', '--watch']), \
                    patch('outputs.subprocess.Popen', return_value=context) as popen, \
                    patch('outputs.reconcile', side_effect=reconcile), \
                    patch('outputs.notify_ready', side_effect=lambda: steps.append('ready')):
                with self.assertRaisesRegex(RuntimeError, 'subscription closed'):
                    main()
            self.assertEqual(steps, ['primary', 'ready'])
            self.assertIn('["output", "tick"]', popen.call_args.args[0])
            process.terminate.assert_called_once_with()

    def test_failed_subscription_never_publishes_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Mock()
            process.stdout = io.StringIO('{"success": false}\n')
            context = Mock()
            context.__enter__ = Mock(return_value=process)
            context.__exit__ = Mock(return_value=False)
            with patch.dict(os.environ, {'XDG_RUNTIME_DIR': directory}), \
                    patch('outputs.sys.argv', ['outputs.py', '--watch']), \
                    patch('outputs.subprocess.Popen', return_value=context), \
                    patch('outputs.reconcile') as reconcile, \
                    patch('outputs.notify_ready') as notify:
                with self.assertRaisesRegex(RuntimeError, 'subscription failed'):
                    main()
                reconcile.assert_not_called()
                notify.assert_not_called()

    def test_ready_notification_uses_owner_socket_without_external_process(self):
        with tempfile.TemporaryDirectory() as directory:
            address = str(Path(directory) / 'notify')
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
                listener.bind(address)
                listener.settimeout(1)
                with patch.dict(os.environ, {'NOTIFY_SOCKET': address}):
                    notify_ready()
                self.assertEqual(listener.recv(64), b'READY=1')

    def test_numbered_workspace_policy_has_no_limit_of_ten(self):
        self.workspaces.append(dict(name="37", num=37, output="HDMI-A-1", visible=False, focused=False))
        commands = plan_workspaces(self.outputs, self.workspaces, "HDMI-A-1")
        self.assertIn('workspace 37 output "HDMI-A-1"', commands)


if __name__ == "__main__":
    unittest.main()
