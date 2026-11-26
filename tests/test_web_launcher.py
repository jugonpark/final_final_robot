"""Launcher port ownership checks; no robot connections or external requests."""
import unittest
from unittest.mock import patch
from http.client import BadStatusLine

from python_controller.web_launcher import select_port,main,port_state


class WebLauncherTests(unittest.TestCase):
    def test_prefers_a_running_control_server_over_a_free_fallback(self):
        states={8766:'busy',8771:'busy',8772:'control'}
        self.assertEqual(select_port(probe=lambda p:states.get(p,'free')),(8772,True))

    def test_unresponsive_default_and_preview_are_never_reused(self):
        self.assertEqual(select_port(probe=lambda p:'busy' if p in (8766,8771) else 'free'),(8767,False))

    def test_explicit_port_and_configuration_are_honored(self):
        with self.assertRaises(ValueError):select_port(8770,explicit=True,probe=lambda p:'busy')
        self.assertEqual(select_port(8770,explicit=True,probe=lambda p:'free'),(8770,False))
        self.assertEqual(select_port(reuse=False,probe=lambda p:'control' if p==8766 else 'free'),(8767,False))
        with self.assertRaises(ValueError):select_port(0)

    def test_opens_existing_server_without_starting_another_controller(self):
        with patch('python_controller.web_launcher.select_port',return_value=(8772,True)),patch('python_controller.web_launcher.webbrowser.open',return_value=True) as opened:
            self.assertEqual(main([]),0)
        opened.assert_called_once_with('http://127.0.0.1:8772/')

    def test_non_http_service_and_invalid_explicit_port(self):
        with patch('python_controller.web_launcher.socket.socket') as sock,patch('python_controller.web_launcher.build_opener') as opener:
            sock.return_value.__enter__.return_value.bind.side_effect=OSError('occupied')
            opener.return_value.open.side_effect=BadStatusLine('not HTTP')
            self.assertEqual(port_state(8766),'busy')
        self.assertEqual(main(['--web-port','0']),1)

    def test_new_server_retains_control_defaults_and_custom_options(self):
        with patch('python_controller.web_launcher.select_port',return_value=(8767,False)),patch('python_controller.main.main',return_value=0) as run:
            self.assertEqual(main(['--hardware-profile','PCB_V19']),0)
        run.assert_called_once_with(['--ui','web','--web-control','--web-port','8767','--agent-enabled','--open-web','--hardware-profile','PCB_V19'])


if __name__=='__main__':unittest.main()
