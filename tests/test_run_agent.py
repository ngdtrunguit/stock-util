from __future__ import annotations

import importlib.util
from pathlib import Path
import os
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
    def test_build_analysis_prompt_normalizes_ticker(self) -> None:
        prompt = run_agent.build_analysis_prompt(' tsla ', days=90, context='Focus on momentum.')

        self.assertEqual(prompt, 'Analyze TSLA with 90-day context. Focus on momentum.')

    def test_resolve_prompt_turns_bare_ticker_into_analysis_prompt(self) -> None:
        prompt = run_agent.resolve_prompt('msft', days=30, context='Keep it concise.')

        self.assertEqual(prompt, 'Analyze MSFT with 30-day context. Keep it concise.')

    def test_resolve_prompt_preserves_full_prompt_text(self) -> None:
        prompt = run_agent.resolve_prompt('Analyze AMD for near-term earnings risk.')

        self.assertEqual(prompt, 'Analyze AMD for near-term earnings risk.')

    def test_resolve_test_prompts_prefers_explicit_values(self) -> None:
        prompts = run_agent.resolve_test_prompts(['Analyze CRM', 'Analyze ORCL'])

        self.assertEqual(prompts, ['Analyze CRM', 'Analyze ORCL'])

    def test_resolve_test_prompts_reads_environment_override(self) -> None:
        with patch.dict(os.environ, {'AZURE_AI_TEST_PROMPTS': 'Analyze IBM\nAnalyze INTC'}, clear=False):
            prompts = run_agent.resolve_test_prompts()

        self.assertEqual(prompts, ['Analyze IBM', 'Analyze INTC'])

    def test_prompt_mentions_ticker_matches_expected_invalid_symbol(self) -> None:
        self.assertTrue(run_agent.prompt_mentions_ticker('Analyze BADTICKERZZZZ', {'BADTICKERZZZZ'}))
        self.assertFalse(run_agent.prompt_mentions_ticker('Analyze NVDA', {'BADTICKERZZZZ'}))

    def test_prompt_requires_tools_for_analysis_requests(self) -> None:
        self.assertTrue(run_agent.prompt_requires_tools('Analyze TSLA'))
        self.assertFalse(run_agent.prompt_requires_tools('Hello there'))

    def test_resolve_tool_choice_requires_tools_for_analysis_requests(self) -> None:
        self.assertEqual(run_agent.resolve_tool_choice('Analyze TSLA', 'auto'), 'required')
        self.assertEqual(run_agent.resolve_tool_choice('Hello there', 'auto'), 'auto')

    def test_extract_tool_call_names_reads_openapi_calls(self) -> None:
        response = {
            'output': [
                {'type': 'reasoning'},
                {'type': 'openapi_call', 'call_id': '1', 'name': 'stock_tools_api_get_price_history_price_history_post'},
                {'type': 'openapi_call_output', 'call_id': '1', 'name': 'stock_tools_api_get_price_history_price_history_post'},
                {'type': 'openapi_call', 'call_id': '2', 'name': 'stock_tools_api_compute_technicals_technicals_post'},
            ]
        }

        self.assertEqual(
            run_agent.extract_tool_call_names(response),
            [
                'stock_tools_api_get_price_history_price_history_post',
                'stock_tools_api_compute_technicals_technicals_post',
            ],
        )

    def test_is_agent_not_found_error_matches_404_application_message(self) -> None:
        exc = RuntimeError(
            "Error code: 404 - {'error': {'code': 'not_found', 'message': \"Application 'stock-forecast-agent' not found\"}}"
        )

        self.assertTrue(run_agent.is_agent_not_found_error(exc))

    def test_is_agent_not_found_error_ignores_unrelated_errors(self) -> None:
        exc = RuntimeError('temporary network timeout')

        self.assertFalse(run_agent.is_agent_not_found_error(exc))

    @patch.object(run_agent, 'get_agent_definition')
    def test_check_agent_available_fetches_agent_definition(self, mock_get_agent_definition: Mock) -> None:
        mock_get_agent_definition.return_value = run_agent.AgentDefinition(model='gpt-5.1-chat', instructions='Be helpful.')

        run_agent.check_agent_available()

        mock_get_agent_definition.assert_called_once_with(run_agent.AGENT_NAME)

    @patch.object(run_agent, 'run_agent_prompt')
    @patch.object(run_agent, 'parse_args', return_value=SimpleNamespace(check_agent=False, tests=False, stream=False, prompt='aapl', ticker=None, days=45, context='Use a cautious tone.'))
    def test_main_builds_prompt_from_bare_ticker_input(self, _mock_args: Mock, mock_run_agent_prompt: Mock) -> None:
        mock_run_agent_prompt.return_value = run_agent.AgentRunResult(
            prompt='Analyze AAPL with 45-day context. Use a cautious tone.',
            output_text='stub response',
            tool_calls=[],
        )

        run_agent.main()

        mock_run_agent_prompt.assert_called_once_with(
            prompt='Analyze AAPL with 45-day context. Use a cautious tone.',
            stream=False,
        )

    @patch.object(run_agent, 'check_agent_available', side_effect=RuntimeError("Application 'stock-forecast-agent' not found"))
    @patch.object(run_agent, 'parse_args', return_value=SimpleNamespace(check_agent=True, tests=False, stream=False, prompt='ignored'))
    def test_main_returns_exit_code_10_when_agent_is_missing(self, _mock_args: Mock, _mock_check: Mock) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run_agent.main()

        self.assertEqual(ctx.exception.code, 10)
