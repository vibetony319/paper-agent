from paper_agent.parsers.text_utils import dehyphenate


def test_merges_a_word_broken_across_lines():
    """Breaks if hyphen-broken words stay split and break exact alignment."""
    assert dehyphenate("scales its capa-\nbilities") == "scales its capabilities"


def test_tolerates_indentation_around_the_line_break():
    assert dehyphenate("capa-   \n   bilities") == "capabilities"


def test_keeps_hyphen_when_the_next_line_starts_uppercase():
    """Model names like "Deep-Seek" must not be glued together."""
    assert dehyphenate("Deep-\nSeek") == "Deep-\nSeek"


def test_keeps_text_without_line_break_hyphens_unchanged():
    assert dehyphenate("state-of-the-art routing") == "state-of-the-art routing"


def test_merges_only_one_break_per_call_site():
    assert dehyphenate(" capa-\nbilities and capa-\ncities ") == " capabilities and capacities "
