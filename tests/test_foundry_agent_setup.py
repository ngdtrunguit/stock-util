from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'infra' / 'azure' / 'foundry-agent-setup.py'
SPEC = importlib.util.spec_from_file_location('foundry_agent_setup', MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FoundryAgentSetupTests(unittest.TestCase):
    def test_normalize_openapi_adds_servers_and_converts_nullable_anyof(self) -> None:
        original_url = MODULE.OPENAPI_SPEC_URL
        try:
            MODULE.OPENAPI_SPEC_URL = 'https://example.test/openapi.json'
            spec = {
                'openapi': '3.1.0',
                'jsonSchemaDialect': 'https://json-schema.org/draft/2020-12/schema',
                'paths': {},
                'components': {
                    'schemas': {
                        'MaybeFloat': {
                            'anyOf': [
                                {'type': 'number'},
                                {'type': 'null'},
                            ]
                        }
                    }
                },
            }

            normalized = MODULE.normalize_openapi_for_foundry(spec)
        finally:
            MODULE.OPENAPI_SPEC_URL = original_url

        self.assertEqual(normalized['openapi'], '3.0.3')
        self.assertNotIn('jsonSchemaDialect', normalized)
        self.assertEqual(normalized['servers'], [{'url': 'https://example.test/'}])
        maybe_float = normalized['components']['schemas']['MaybeFloat']
        self.assertEqual(maybe_float['type'], 'number')
        self.assertTrue(maybe_float['nullable'])

    def test_inject_api_key_security_sets_global_scheme_and_removes_manual_params(self) -> None:
        spec = {
            'openapi': '3.0.3',
            'paths': {
                '/price_history': {
                    'post': {
                        'parameters': [
                            {'name': 'X-API-Key', 'in': 'header'},
                            {'name': 'ticker', 'in': 'query'},
                            {'name': 'api_key', 'in': 'query'},
                        ]
                    }
                }
            },
        }

        secured = MODULE._inject_api_key_security(spec, 'x-api-key')

        self.assertEqual(secured['security'], [{'apiKeyHeader': []}])
        scheme = secured['components']['securitySchemes']['apiKeyHeader']
        self.assertEqual(scheme['type'], 'apiKey')
        self.assertEqual(scheme['name'], 'x-api-key')
        params = secured['paths']['/price_history']['post']['parameters']
        self.assertEqual(params, [{'name': 'ticker', 'in': 'query'}])


if __name__ == '__main__':
    unittest.main()
