"""Future inference boundary; only local execution is available.

This adapter is deliberately NOT inserted into the working camera pipeline.
It owns no model/session, threads, queues, network client or biometric decisions.
Callers supply the existing opaque operation; input/output pass through unchanged.
"""
from typing import Any, Callable, Generic, Protocol, TypeVar

Result = TypeVar('Result', covariant=True)


class InferenceProvider(Protocol[Result]):
    location: str

    def infer(self, observation: Any) -> Result:
        ...


class LocalInferenceProvider(Generic[Result]):
    location = 'local'

    def __init__(self, operation: Callable[[Any], Result]):
        if not callable(operation):
            raise TypeError('A local inference operation is required')
        self._operation = operation

    def infer(self, observation: Any) -> Result:
        return self._operation(observation)


def create_inference_provider(operation: Callable[[Any], Result], *, provider: str = 'local') -> InferenceProvider[Result]:
    if provider == 'local':
        return LocalInferenceProvider(operation)
    if provider == 'cloud':
        raise NotImplementedError('Cloud inference is not available; use local inference')
    raise ValueError('Unknown inference provider')
