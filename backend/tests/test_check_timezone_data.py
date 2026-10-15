"""The import works from either the repository root or backend directory."""

import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, mock
from zoneinfo import ZoneInfoNotFoundError

source = Path(__file__).resolve().parents[1] / "scripts" / "check_timezone_data.py"
spec = importlib.util.spec_from_file_location("timezone_check", source)
assert spec and spec.loader
timezone_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timezone_check)


class TimezoneDataTests(TestCase):
    def test_success(self):
        output = io.StringIO()
        with mock.patch.object(timezone_check, "ZoneInfo") as loader, redirect_stdout(output):
            self.assertEqual(timezone_check.main(), 0)
        self.assertEqual(loader.call_args_list, [mock.call("UTC"), mock.call("Africa/Lagos")])
        self.assertIn("UTC: available", output.getvalue())
        self.assertIn("Africa/Lagos: available", output.getvalue())

    def test_missing_data(self):
        output = io.StringIO()
        with mock.patch.object(timezone_check, "ZoneInfo", side_effect=ZoneInfoNotFoundError), redirect_stdout(output):
            self.assertNotEqual(timezone_check.main(), 0)
        self.assertIn("UTC: unavailable", output.getvalue())
        self.assertIn("Africa/Lagos: unavailable", output.getvalue())
