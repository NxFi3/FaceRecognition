import unittest
from src.inference_provider import create_inference_provider


class InferenceProviderTests(unittest.TestCase):
    def test_local_default_preserves_inputs_outputs_and_call_count(self):
        observation, result = object(), object()
        calls = []
        def operation(value):
            calls.append(value)
            return result
        provider = create_inference_provider(operation)
        self.assertEqual(provider.location, 'local')
        self.assertIs(provider.infer(observation), result)
        self.assertEqual(calls, [observation])
    def test_cloud_is_unavailable_and_never_calls_local_operation_or_uploads(self):
        calls = []
        with self.assertRaises(NotImplementedError):
            create_inference_provider(calls.append, provider='cloud')
        self.assertFalse(calls)
    def test_unknown_provider_fails_explicitly(self):
        with self.assertRaises(ValueError):
            create_inference_provider(lambda value: value, provider='other')
    def test_local_exceptions_propagate_without_retries_or_duplicate_execution(self):
        calls = []
        def operation(value):
            calls.append(value)
            raise RuntimeError('local model unavailable')
        provider = create_inference_provider(operation)
        with self.assertRaisesRegex(RuntimeError, 'local model unavailable'):
            provider.infer('synthetic')
        self.assertEqual(calls, ['synthetic'])
