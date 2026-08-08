from types import SimpleNamespace

import paper_agent.parsers.markitdown_stage1 as stage1_module
from paper_agent.parsers.markitdown_stage1 import MarkItDownStage1Parser


class FakeMarkItDown:
    def __init__(self, calls, options, markdown):
        self.calls = calls
        self.options = options
        self.markdown = markdown

    def __call__(self, *, enable_plugins):
        self.options.append(enable_plugins)
        return self

    def convert_local(self, path):
        self.calls.append(path)
        return SimpleNamespace(text_content=self.markdown)


def test_markitdown_adapter_calls_convert_local_for_owned_pdf(monkeypatch, sample_pdf):
    """Breaks if Stage 1 uses a permissive converter or loses heading structure."""
    calls = []
    options = []
    markdown = "# Intro\n\nOpening body.\n\n## Methods ##\n\nMethod body."
    monkeypatch.setattr(
        stage1_module,
        "MarkItDown",
        FakeMarkItDown(calls, options, markdown),
        raising=False,
    )

    result = MarkItDownStage1Parser().parse(sample_pdf)

    assert calls == [sample_pdf]
    assert options == [False]
    assert [(section.title, section.order, section.page_number) for section in result.sections] == [
        ("Intro", 0, None),
        ("Methods", 1, None),
    ]
    assert [paragraph.text for paragraph in result.paragraphs] == [
        "Opening body.",
        "Method body.",
    ]
    assert [paragraph.section_id for paragraph in result.paragraphs] == [
        result.sections[0].id,
        result.sections[1].id,
    ]
    assert [paragraph.order for paragraph in result.paragraphs] == [0, 1]
