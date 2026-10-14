import subprocess
import unittest
from unittest.mock import patch

from gluon import steam


class SteamProcessTests(unittest.TestCase):
    def test_only_no_match_means_closed(self):
        for code, expected in ((0, True), (1, False)):
            with self.subTest(code=code), patch.object(
                    steam.subprocess, "run", return_value=subprocess.CompletedProcess([], code)):
                self.assertEqual(steam.is_running(), expected)

    def test_denied_process_inspection_fails_closed(self):
        with patch.object(steam.subprocess, "run", return_value=subprocess.CompletedProcess([], 2)):
            with self.assertRaises(steam.SteamError):
                steam.is_running()

    def test_missing_process_tool_fails_closed(self):
        with patch.object(steam.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(steam.SteamError):
                steam.is_running()


if __name__ == "__main__":
    unittest.main()
