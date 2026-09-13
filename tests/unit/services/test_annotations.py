import pytest

from paper_agent.annotations import (
    HIGHLIGHT_COLORS,
    Highlight,
    TextAnchor,
    TextAnchorDraft,
    TextAnchorRect,
)


def _rect(order: int, x0: float = 0.1, y0: float = 0.2) -> TextAnchorRect:
    return TextAnchorRect(order=order, x0=x0, y0=y0, x1=x0 + 0.3, y1=y0 + 0.04)


def test_text_anchor_draft_accepts_ordered_single_page_rectangles() -> None:
    draft = TextAnchorDraft(
        quote="selected paper text",
        page_number=2,
        rects=(_rect(0), _rect(1, y0=0.25)),
    )

    assert draft.page_number == 2
    assert tuple(rect.order for rect in draft.rects) == (0, 1)


@pytest.mark.parametrize(
    "coordinates",
    [
        (-0.1, 0.2, 0.8, 0.3),
        (0.8, 0.2, 0.1, 0.3),
    ],
)
def test_text_anchor_rect_rejects_invalid_normalized_geometry(
    coordinates: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError, match="normalized"):
        TextAnchorRect(
            order=0,
            x0=coordinates[0],
            y0=coordinates[1],
            x1=coordinates[2],
            y1=coordinates[3],
        )


def test_text_anchor_draft_requires_contiguous_rect_order() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        TextAnchorDraft(
            quote="selected paper text",
            page_number=1,
            rects=(_rect(0), _rect(2)),
        )


def test_text_anchor_draft_rejects_blank_or_oversized_quote() -> None:
    with pytest.raises(ValueError, match="bounded"):
        TextAnchorDraft(
            quote="   ",
            page_number=1,
            rects=(_rect(0),),
        )


def test_highlight_rejects_unknown_color() -> None:
    anchor = TextAnchor(
        paper_id="paper-a",
        quote="selected paper text",
        page_number=1,
        rects=(_rect(0),),
    )

    with pytest.raises(ValueError, match="color"):
        Highlight(anchor=anchor, color="purple")

    for color in HIGHLIGHT_COLORS:
        assert Highlight(anchor=anchor, color=color).color == color
