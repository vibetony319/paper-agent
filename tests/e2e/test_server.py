import json
from pathlib import Path
from uuid import uuid4

import fitz
import pytest
from fastapi.testclient import TestClient

from tests.e2e.server import create_e2e_app


@pytest.fixture
def e2e_client():
    with TestClient(create_e2e_app()) as client:
        yield client


def upload(client):
    pdf = client.get('/__e2e__/fixture.pdf')
    response = client.post('/api/papers', files={'file': ('routing.pdf', pdf.content, 'application/pdf')})
    assert response.status_code == 201, response.text
    return response.json()['id']


def profiles(client):
    return client.get('/api/model-profiles').json()


def assist(client, paper_id, action='explain', profile_index=0):
    response = client.post(f'/api/papers/{paper_id}/selection-assists', json={
        'quote': 'We introduce a routing load balancing loss for sparse experts.',
        'page_number': 1,
        'rects': [{'order': 0, 'x0': .1, 'y0': .2, 'x1': .8, 'y1': .25}],
        'action': action, 'model_profile_id': profiles(client)[profile_index]['id'],
        'request_id': str(uuid4()),
    })
    assert response.status_code == 200, response.text
    frames = [frame.splitlines() for frame in response.text.strip().split('\n\n')]
    events = [(lines[0].removeprefix('event: '), json.loads(lines[1].removeprefix('data: '))) for lines in frames]
    assert events[0][0] == 'started'
    assert events[-1][0] == 'completed', events
    assert any(name == 'delta' for name, _ in events)
    return events[-1][1]['note']


def test_e2e_server_exposes_selectable_pdf_and_two_model_profiles(e2e_client):
    pdf = e2e_client.get('/__e2e__/fixture.pdf')
    assert pdf.status_code == 200
    assert pdf.headers['content-type'].startswith('application/pdf')
    with fitz.open(stream=pdf.content, filetype='pdf') as document:
        assert 'routing' in document[0].get_text().lower()
        assert document.page_count >= 2
    assert [p['display_name'] for p in profiles(e2e_client)] == ['测试 Qwen', '测试 DeepSeek']
    assert e2e_client.get('/health').status_code == 200


@pytest.mark.parametrize(('action', 'body', 'note_type'), [
    ('explain', '这段文字说明作者使用负载均衡损失来分配专家。', 'explanation'),
    ('translate', '我们提出了一种用于稀疏专家的路由负载均衡损失。', 'translation'),
])
def test_assists_stream_and_save_selected_model_note(e2e_client, action, body, note_type):
    paper_id = upload(e2e_client)
    note = assist(e2e_client, paper_id, action, profile_index=1)
    assert note['body'] == body
    assert note['note_type'] == note_type
    assert note['model']['display_name'] == '测试 DeepSeek'
    assert note['ai_generated'] is True
    notes = e2e_client.get(f'/api/papers/{paper_id}/annotations').json()['notes']
    assert [saved['id'] for saved in notes] == [note['id']]


def test_graph_uses_real_uploaded_evidence(e2e_client):
    paper_id = upload(e2e_client)
    response = e2e_client.post(f'/api/papers/{paper_id}/graph/core', json={
        'model_profile_id': profiles(e2e_client)[1]['id'], 'request_id': str(uuid4()),
    })
    assert response.status_code == 200, response.text
    graph = e2e_client.get(f'/api/papers/{paper_id}/graph').json()
    assert len(graph['nodes']) == 1
    elements = e2e_client.get(f'/api/papers/{paper_id}/document').json()['elements']
    assert set(graph['nodes'][0]['evidence_element_ids']) <= {item['id'] for item in elements}
    assert graph['nodes'][0]['evidence_element_ids']


def test_agent_reads_note_memory_and_switches_model_in_same_conversation(e2e_client):
    paper_id = upload(e2e_client)
    note = assist(e2e_client, paper_id)
    conversation_id = None
    for profile in profiles(e2e_client):
        response = e2e_client.post(f'/api/papers/{paper_id}/agent/messages', json={
            'content': '负载均衡损失如何分配专家？',
            'model_profile_id': profile['id'], 'request_id': str(uuid4()),
            'conversation_id': conversation_id,
        })
        assert response.status_code == 200, response.text
        answer = response.json()
        assert answer['status'] == 'grounded'
        assert answer['citations']
        assert answer['note_references'][0]['note_id'] == note['id']
        assert answer['model']['profile_id'] == profile['id']
        assert '结合笔记' in answer['paper_answer']
        if conversation_id:
            assert answer['conversation_id'] == conversation_id
        conversation_id = answer['conversation_id']
    history = e2e_client.get(f'/api/papers/{paper_id}/agent/conversations/{conversation_id}').json()
    assert len(history['messages']) == 4
    assert [m['model']['display_name'] for m in history['messages'][1::2]] == ['测试 Qwen', '测试 DeepSeek']


def test_server_owns_and_cleans_only_its_temporary_directory():
    app = create_e2e_app()
    with TestClient(app) as client:
        directory = client.app.state.settings.data_dir.resolve()
        assert directory.name.startswith('paper-agent-e2e-')
        assert not directory.is_relative_to(Path.cwd())
        upload(client)
        assert list(directory.rglob('*.pdf'))
    assert not directory.exists()
    with TestClient(app) as client:
        assert client.app.state.settings.data_dir.resolve() != directory
        assert client.get('/api/papers').json() == []
