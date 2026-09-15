import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from paper_agent.models.vllm import (
    VllmModelConfig, VllmResponseError, VllmStructuredClient,
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


def invoke(transport):
    return VllmStructuredClient(
        VllmModelConfig('https://example.invalid', 'model'), transport
    ).generate_json(
        system_prompt='extract', user_prompt='source', schema_name='test', schema=SCHEMA)


def test_explicit_unsupported_schema_negotiates_json_and_preserves_messages():
    transport = Transport({'status': 'ok'})

    assert invoke(transport) == {'status': 'ok'}

    first, second = transport.requests
    assert first['response_format']['type'] == 'json_schema'
    assert second['response_format'] == {'type': 'json_object'}
    assert second['messages'][1:] == first['messages']
    assert json.dumps(SCHEMA) in second['messages'][0]['content']


@pytest.mark.parametrize('payload', [{}, {'status':'wrong'}, {'status':'ok','extra':1}, []])
def test_compatibility_output_must_pass_local_schema(payload):
    with pytest.raises(VllmResponseError) as caught:
        invoke(Transport(payload))

    assert 'provider-secret' not in str(caught.value)
    assert caught.value.__context__ is None


def test_unrelated_bad_request_does_not_trigger_fallback():
    transport = Transport({'status':'ok'}, error='invalid schema syntax')

    with pytest.raises(VllmResponseError):
        invoke(transport)

    assert len(transport.requests) == 1


def test_prose_before_json_is_extracted():
    content = '结论如下：三点局限性。\n\n{"status": "ok"}'

    assert invoke(ProseTransport(content)) == {'status': 'ok'}


def test_fenced_json_is_extracted():
    content = 'Answer in prose.\n```json\n{"status": "ok"}\n```'

    assert invoke(ProseTransport(content)) == {'status': 'ok'}


def test_prose_without_json_fails():
    with pytest.raises(VllmResponseError):
        invoke(ProseTransport('纯散文回答，没有结构化数据。'))


def test_native_schema_mode_also_extracts_embedded_json():
    transport = ProseTransport(
        'prose { "unrelated": true } then {"status": "ok"}', fail_first=False
    )

    assert invoke(transport) == {'status': 'ok'}
    assert len(transport.requests) == 1
