"""Decode recorded CI metadata buffers without inspecting live processes."""
import importlib.util
from pathlib import Path
import struct
import unittest

SPEC = importlib.util.spec_from_file_location('warm_process_evidence',
    Path(__file__).resolve().parents[1] / 'packaging/warm_process_evidence.py')
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def recorded(arguments, environment):
    return (struct.pack('=i', len(arguments)) + b'/runtime/wine\0\0\0' +
            b'\0'.join(arguments) + b'\0\0' + b'\0'.join(environment) + b'\0')


class KernelEnvironmentTests(unittest.TestCase):
    def test_argv_and_nul_delimited_environment_remain_separate(self):
        data = recorded([b'services.exe', b'WINEPREFIX=/decoy'],
                        [b'WINEPREFIX=/fixture/prefix', b'WINELOADER=/runtime/wine'])
        self.assertEqual(evidence.parse_process_environment(data),
                         {'WINEPREFIX': '/fixture/prefix', 'WINELOADER': '/runtime/wine'})

    def test_spaces_equals_and_backslashes_are_preserved_exactly(self):
        data = recorded([b'wine', b'argument with spaces'],
                        [b'WINEPREFIX=/fixture/prefix with spaces', b'OTHER=a=b \\ value'])
        values = evidence.parse_process_environment(data)
        self.assertEqual(values['WINEPREFIX'], '/fixture/prefix with spaces')
        self.assertEqual(values['OTHER'], 'a=b \\ value')

    def test_empty_arguments_do_not_become_environment_entries(self):
        data = recorded([b'wine', b'', b'tail'], [b'WINEPREFIX=/fixture'])
        self.assertEqual(evidence.parse_process_environment(data), {'WINEPREFIX': '/fixture'})

    def test_missing_prefix_is_not_inferred_from_the_command_title(self):
        data = recorded([b'WINEPREFIX=/fake'], [b'WINELOADER=/runtime/wine'])
        self.assertNotIn('WINEPREFIX', evidence.parse_process_environment(data))

    def test_truncated_buffers_and_invalid_counts_fail_closed(self):
        for data in (b'', struct.pack('=i', -1) + b'\0',
                     struct.pack('=i', 1000000) + b'\0',
                     recorded([b'wine'], [b'WINEPREFIX=/fixture'])[:-1],
                     struct.pack('=i', 3) + b'/wine\0wine\0'):
            with self.subTest(data=data):
                with self.assertRaises(RuntimeError):
                    evidence.parse_process_environment(data)

    def test_conflicting_duplicate_environment_values_fail_closed(self):
        data = recorded([b'wine'], [b'WINEPREFIX=/one', b'WINEPREFIX=/two'])
        with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
            evidence.parse_process_environment(data)


if __name__ == '__main__':
    unittest.main()
