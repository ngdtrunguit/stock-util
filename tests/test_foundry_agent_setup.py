from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'infra' / 'azure' / 'foundry-agent-setup.py'
SPEC = importlib.util.spec_from_file_location('foundry_agent_setup', MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)




class _FakeDeployments:
    def __init__(self, deployments):
        self._deployments = deployments

    def list(self):
        return list(self._deployments)


class _FakeConnections:
    def __init__(self, connections):
        self._connections = connections

    def list(self, connection_type=None):
        return list(self._connections)


class _FakeProjectClient:
    def __init__(self, deployments=None, connections=None):
        self.deployments = _FakeDeployments(deployments or [])
        self.connections = _FakeConnections(connections or [])

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

    def test_resolve_model_deployment_prefers_known_models(self) -> None:
        client = _FakeProjectClient(
            deployments=[
                {'name': 'custom-model', 'modelName': 'phi-4'},
                {'name': 'gpt5-chat-prod', 'modelName': 'gpt-5.1-chat'},
            ]
        )

        resolved = MODULE.resolve_model_deployment(client, '')

        self.assertEqual(resolved, 'gpt5-chat-prod')

    def test_resolve_openapi_connection_id_prefers_exact_target_match(self) -> None:
        client = _FakeProjectClient(
            connections=[
                {'name': 'other', 'id': 'conn-other', 'target': 'https://example.test/', 'is_default': False},
                {
                    'name': 'stock-tools',
                    'id': 'conn-stock-tools',
                    'target': 'https://stock-tools-api-dev-app.calmstone-a9644956.eastus.azurecontainerapps.io/',
                    'is_default': False,
                },
            ]
        )

        resolved = MODULE.resolve_openapi_connection_id(
            client,
            explicit_id='',
            explicit_name='',
            openapi_spec_url='https://stock-tools-api-dev-app.calmstone-a9644956.eastus.azurecontainerapps.io/openapi.json',
        )

        self.assertEqual(resolved, 'conn-stock-tools')

    def test_resolve_openapi_connection_id_uses_named_connection(self) -> None:
        client = _FakeProjectClient(
            connections=[
                {'name': 'stock-tools', 'id': 'conn-stock-tools', 'target': 'https://example.test/', 'is_default': False},
            ]
        )

        resolved = MODULE.resolve_openapi_connection_id(
            client,
            explicit_id='',
            explicit_name='stock-tools',
            openapi_spec_url='https://irrelevant.test/openapi.json',
        )

        self.assertEqual(resolved, 'conn-stock-tools')

    def test_foundry_agent_setup_source_uses_required_tool_choice(self) -> None:
        source = MODULE_PATH.read_text()
        self.assertIn('tool_choice="required"', source)


if __name__ == '__main__':
    unittest.main()
