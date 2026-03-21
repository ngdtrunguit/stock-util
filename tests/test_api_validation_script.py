from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock, patch

import requests

import test_api_calls


class ApiValidationScriptTests(TestCase):
    @patch('test_api_calls.time.sleep')
    @patch('test_api_calls.requests.request')
    def test_request_with_retries_retries_timeouts_before_success(self, mock_request: Mock, mock_sleep: Mock) -> None:
        response = Mock()
        mock_request.side_effect = [
            requests.exceptions.ReadTimeout('slow start'),
            response,
        ]

        result = test_api_calls._request_with_retries('GET', '/health', attempts=3, backoff_seconds=2)

        self.assertIs(result, response)
        self.assertEqual(mock_request.call_count, 2)
        mock_sleep.assert_called_once_with(2)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs['timeout'], (test_api_calls.CONNECT_TIMEOUT, test_api_calls.READ_TIMEOUT))

    @patch('test_api_calls.requests.request')
    def test_request_with_retries_raises_last_retryable_error(self, mock_request: Mock) -> None:
        mock_request.side_effect = requests.exceptions.ConnectionError('unreachable')

        with self.assertRaises(requests.exceptions.ConnectionError):
            test_api_calls._request_with_retries('POST', '/price_history', attempts=2)

        self.assertEqual(mock_request.call_count, 2)
