"""Verify global IPMI commands and fault protection without hardware writes."""
import copy
import unittest
from unittest.mock import patch
from core import DEFAULT
from ipmi_daemon import Controller, set_duty, restore_auto

class Tests(unittest.TestCase):
    def test_all_fans_selector_and_decimal_command(self):
        with patch('ipmi_daemon.ipmi') as run:
            set_duty(80)
        self.assertEqual(run.call_count, 6)
        self.assertEqual([call.args[-1] for call in run.call_args_list], ['0xb8','0xb9','0xba','0xbb','0xbc','0xbd'])
        self.assertEqual(run.call_args.args[:-1], ('raw','0x2e','0x10','0x0a','0x3c','0x00','64','1','0xcc'))

    def test_maximum_avoids_firmware_ff_regression(self):
        with patch('ipmi_daemon.ipmi') as run:
            set_duty(100)
        self.assertTrue(all(call.args[-2] == '0xfc' for call in run.call_args_list))

    def test_invalid_duty_never_sends_command(self):
        with patch('ipmi_daemon.ipmi') as run:
            for duty in (0,29,101,True,50.5):
                with self.assertRaises(ValueError): set_duty(duty)
            run.assert_not_called()

    def test_restore_auto_command(self):
        with patch('ipmi_daemon.ipmi') as run: restore_auto()
        self.assertEqual(run.call_args.args[-4:], ('64','0','0','0'))

    def controller(self):
        with patch('ipmi_daemon.CONFIG') as path:
            path.exists.return_value=False
            return Controller()

    def test_sensor_failure_forces_full_speed(self):
        c=self.controller()
        with patch('ipmi_daemon.sample_temperatures',side_effect=ValueError('GPU missing')), patch('ipmi_daemon.set_duty') as duty, patch('ipmi_daemon.ipmi',return_value='RPM'):
            c.tick()
        duty.assert_called_once_with(100)
        self.assertEqual(c.duty,100)
        self.assertIsNotNone(c.state['error'])

    def test_all_fans_follow_highest_temperature(self):
        c=self.controller()
        reading={'cpu':50,'gpu':90,'sensors':[('cpu',50),('gpu',90)]}
        with patch('ipmi_daemon.sample_temperatures',return_value=reading), patch('ipmi_daemon.set_duty') as duty, patch('ipmi_daemon.ipmi',return_value='RPM'):
            c.tick()
        duty.assert_called_once_with(100)

    def test_disabled_mode_does_not_override_automatic(self):
        c=self.controller();c.config['enabled']=False
        with patch('ipmi_daemon.sample_temperatures',side_effect=ValueError('missing')), patch('ipmi_daemon.set_duty') as duty, patch('ipmi_daemon.ipmi',return_value='RPM'):
            c.tick()
        duty.assert_not_called()

if __name__ == '__main__': unittest.main()
