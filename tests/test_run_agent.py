from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'run_agent.py'
SPEC = importlib.util.spec_from_file_location('run_agent_module', MODULE_PATH)
assert SPEC and SPEC.loader
run_agent = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_agent
SPEC.loader.exec_module(run_agent)


class RunAgentTests(TestCase):
    def test_is_agent_not_found_error_matches_404_application_message(self) -> None:
        exc = RuntimeError(
            "Error code: 404 - {'error': {'code': 'not_found', 'message': \"Application 'stock-forecast-agent' not found\"}}"
        )

        self.assertTrue(run_agent.is_agent_not_found_error(exc))

    def test_is_agent_not_found_error_ignores_unrelated_errors(self) -> None:
        exc = RuntimeError('temporary network timeout')

        self.assertFalse(run_agent.is_agent_not_found_error(exc))

    @patch.object(run_agent, 'create_openai_client')
    def test_check_agent_available_calls_responses_create(self, mock_create_openai_client: Mock) -> None:
        responses = Mock()
        client = SimpleNamespace(responses=responses)
        mock_create_openai_client.return_value = client

        run_agent.check_agent_available()

        responses.create.assert_called_once_with(input='Respond with OK.')

    @patch.object(run_agent, 'check_agent_available', side_effect=RuntimeError("Application 'stock-forecast-agent' not found"))
    @patch.object(run_agent, 'parse_args', return_value=SimpleNamespace(check_agent=True, tests=False, stream=False, prompt='ignored'))
    def test_main_returns_exit_code_10_when_agent_is_missing(self, _mock_args: Mock, _mock_check: Mock) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run_agent.main()

        self.assertEqual(ctx.exception.code, 10)
