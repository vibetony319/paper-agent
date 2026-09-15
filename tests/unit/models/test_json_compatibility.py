import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from paper_agent.models.vllm import (
    VllmModelConfig, VllmStructuredClient, VllmToolCallingClient,
    VllmResponseError, VllmToolCallingError,
)

SCHEMA = {'type': 'object', 'properties': {'status': {'const': 'ok'}},
          'required': ['status'], 'additionalProperties': False}


class Transport:
    def __init__(self, payload, error='json_schema is not supported by this model'):
        self.payload, self.error, self.requests = payload, error, []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            raise BadRequestError('provider-secret', response=httpx.Response(
                400, request=httpx.Request('POST', 'https://example.invalid')),
                body={'message': self.error})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))])


class ProseTransport:
    """Provider whose model answers in prose around the requested JSON object."""

    def __init__(self, content, fail_first=True):
        self.content, self.fail_first, self.requests = content, fail_first, []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.fail_first and len(self.requests) == 1:
            raise BadRequestError('provider-secret', response=httpx.Response(
                400, request=httpx.Request('POST', 'https://example.invalid')),
                body={'message': 'json_schema is not supported by this model'})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def invoke(kind, transport):
    config = VllmModelConfig('https://example.invalid', 'model')
    messages = [{'role': 'user', 'content': 'source'}, {'role': 'assistant', 'content': 'history'}]
    if kind == 'graph':
        return VllmStructuredClient(config, transport).generate_json(
            system_prompt='extract', user_prompt='source', schema_name='test', schema=SCHEMA)
    return VllmToolCallingClient(config, transport).generate_json_messages(
        messages=messages, schema_name='test', schema=SCHEMA)


@pytest.mark.parametrize('kind', ['graph', 'agent'])
def test_explicit_unsupported_schema_negotiates_json_and_preserves_messages(kind):
    transport = Transport({'status': 'ok'})
    assert invoke(kind, transport) == {'status': 'ok'}
    first, second = transport.requests
    assert first['response_format']['type'] == 'json_schema'
    assert second['response_format'] == {'type': 'json_object'}
    assert second['messages'][1:] == first['messages']
    assert json.dumps(SCHEMA) in second['messages'][0]['content']


@pytest.mark.parametrize('kind', ['graph', 'agent'])
@pytest.mark.parametrize('payload', [{}, {'status':'wrong'}, {'status':'ok','extra':1}, []])
def test_compatibility_output_must_pass_local_schema(kind, payload):
    with pytest.raises((VllmResponseError, VllmToolCallingError)) as caught:
        invoke(kind, Transport(payload))
    assert 'provider-secret' not in str(caught.value)
    assert caught.value.__context__ is None


@pytest.mark.parametrize('kind', ['graph', 'agent'])
def test_unrelated_bad_request_does_not_trigger_fallback(kind):
    transport = Transport({'status':'ok'}, error='invalid schema syntax')
    with pytest.raises((VllmResponseError, VllmToolCallingError)):
        invoke(kind, transport)
    assert len(transport.requests) == 1


@pytest.mark.parametrize('kind', ['graph', 'agent'])
def test_prose_before_json_is_extracted(kind):
    content = '论文的结论如下：三点局限性。\n\n{"status": "ok"}'
    assert invoke(kind, ProseTransport(content)) == {'status': 'ok'}


@pytest.mark.parametrize('kind', ['graph', 'agent'])
def test_fenced_json_is_extracted(kind):
    content = 'Answer in prose.\n```json\n{"status": "ok"}\n```'
    assert invoke(kind, ProseTransport(content)) == {'status': 'ok'}


@pytest.mark.parametrize('kind', ['graph', 'agent'])
def test_prose_without_json_fails(kind):
    with pytest.raises((VllmResponseError, VllmToolCallingError)):
        invoke(kind, ProseTransport('纯散文回答，没有结构化数据。'))


@pytest.mark.parametrize('kind', ['graph', 'agent'])
def test_native_schema_mode_also_extracts_embedded_json(kind):
    content = 'prose { "unrelated": true } then {"status": "ok"}'
    transport = ProseTransport(content, fail_first=False)
    assert invoke(kind, transport) == {'status': 'ok'}
    assert len(transport.requests) == 1
