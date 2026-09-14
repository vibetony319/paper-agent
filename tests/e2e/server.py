"""Offline E2E app. Run from the repo root with uvicorn tests.e2e.server:app.

Only the OpenAI transport is replaced: parsing, profiles, graph construction,
tool execution, model-answer parsing, notes and deletion use production code.
"""

from contextlib import asynccontextmanager
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import fitz
from fastapi import FastAPI
from fastapi.responses import Response

from paper_agent.app import create_app
from paper_agent.config import Settings
from paper_agent.model_profiles import ModelCapabilities, ModelProfile


EXPLANATION = '这段文字说明作者使用负载均衡损失来分配专家。'
TRANSLATION = '我们提出了一种用于稀疏专家的路由负载均衡损失。'


def fixture_pdf_bytes() -> bytes:
    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 85), 'Routing Paper', fontsize=24)
        page.insert_text((72, 115), 'A deterministic research fixture', fontsize=11)
        page.insert_textbox(fitz.Rect(72, 150, 450, 245),
                            'We introduce a routing load balancing loss for sparse experts.', fontsize=14)
        page.insert_text((72, 285), '1  Introduction', fontsize=18)
        page.insert_textbox(fitz.Rect(72, 315, 540, 600),
                            'Sparse expert models route tokens to specialists.\n'
                            'A balanced router distributes work across experts.\n'
                            'This fixture demonstrates selectable text and source citations.\n\n'
                            'The reading workflow connects highlights, local notes,\n'
                            'model explanations and evidence-grounded conversations.', fontsize=13)
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 85), '2  Evaluation', fontsize=20)
        page.insert_textbox(fitz.Rect(72, 125, 540, 300),
                            'Routing balance improves the distribution of expert workloads.\n'
                            'All text in this document is synthetic test data.\n'
                            'No private paper or model service is required.', fontsize=14)
        return document.tobytes()


def _response(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=tool_calls,
    ))])


def _tool(name, arguments):
    return _response(tool_calls=[SimpleNamespace(
        id=f'e2e-{name}', function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )])


class FakeOpenAI:
    """Stateless completions based on the current request, including real tool results."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=self)

    def create(self, *, messages, stream=False, tools=None, response_format=None, **_kwargs):
        if stream:
            text = TRANSLATION if '翻译' in messages[0]['content'] else EXPLANATION
            return iter(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part))])
                        for part in (text[:10], text[10:]))

        if tools is not None:
            if tools[0]['function']['name'] == 'paper_agent_tool_health':
                return _tool('paper_agent_tool_health', {})
            # Persisted history contains text only; tool results belong to this turn.
            if not any(message['role'] == 'tool' for message in messages):
                return _tool('search_paper', {'query': 'routing', 'limit': 5})
            return _response()

        if response_format is None:
            return _response('OK')
        schema = response_format['json_schema']['name']
        if schema == 'capability_status':
            payload = {'status': 'ok'}
        elif schema == 'paper_graph_nodes':
            source = json.loads(messages[-1]['content'])['source_elements'][0]
            payload = {'nodes': [{
                'local_id': 'routing', 'node_type': 'method', 'name': '负载均衡路由',
                'summary': '将工作分配给稀疏专家。', 'evidence_element_ids': [source['id']],
            }]}
        elif schema in {'paper_graph_edges', 'paper_graph_cross_section_edges'}:
            payload = {'edges': []}
        else:
            results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
            evidence = [element for result in results for element in result['evidence_element_ids']]
            has_notes = any('<note_data>' in (m.get('content') or '') for m in messages if m['role'] == 'system')
            answer = ('结合笔记，' if has_notes else '') + '论文使用路由负载均衡损失，将工作分配给稀疏专家。'
            payload = {'status': 'grounded' if evidence else 'insufficient_evidence',
                       'paper_answer': answer, 'citation_element_ids': evidence[:1],
                       'background_explanation': None}
        return _response(json.dumps(payload, ensure_ascii=False))


def create_e2e_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(wrapper):
        # Creation is deferred until startup; imports never create a database.
        with TemporaryDirectory(prefix='paper-agent-e2e-') as owned:
            directory = Path(owned)
            production = create_app(Settings(
                data_dir=directory, database_url=f'sqlite:///{directory / "paper-agent.db"}',
            ))
            wrapper.state = production.state
            production.state.reasoning_client_provider.client_factory = lambda _config: FakeOpenAI()
            for index, (name, model) in enumerate((('测试 Qwen', 'e2e-qwen'), ('测试 DeepSeek', 'e2e-deepseek')), 1):
                production.state.model_profile_repository.create(ModelProfile(
                    id=f'10000000-0000-0000-0000-{index:012d}', display_name=name,
                    base_url='http://127.0.0.1:9/v1', model_name=model, is_default=index == 1,
                    capabilities=ModelCapabilities(basic_chat=True, structured_output=True, tool_calling=True),
                ))
            wrapper.mount('/', production)
            mount = wrapper.routes[-1]
            try:
                yield
            finally:
                wrapper.routes.remove(mount)
                production.state.paper_repository.engine.dispose()
                production.state.model_profile_repository.engine.dispose()

    wrapper = FastAPI(lifespan=lifespan)

    @wrapper.get('/__e2e__/fixture.pdf')
    def fixture():
        return Response(fixture_pdf_bytes(), media_type='application/pdf')

    return wrapper


app = create_e2e_app()
