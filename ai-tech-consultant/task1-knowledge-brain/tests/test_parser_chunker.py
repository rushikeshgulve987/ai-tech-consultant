from ingestion.parser import parse_article
from retrieval.chunker import chunk_article, split_text
from tests.conftest import article


HTML = article("Refunds", "<p>Intro text.</p><h2>Issue a refund</h2><p>Open Payments.</p><ul><li>Click Refund</li></ul>"
               "<img src='/img/refund-dialog.png' alt='dialog'><h2>Limits</h2><table><tr><th>Plan</th><th>Max</th></tr><tr><td>Pro</td><td>500</td></tr></table>"
               "<img src='/img/logo.png'>")


def test_parser_strips_noise_and_keeps_structure():
    a = parse_article("https://h.com/refunds", HTML)
    text = " ".join(s.text for s in a.sections)
    assert "MENU" not in text and "FOOT" not in text
    assert [s.heading for s in a.sections] == ["Refunds", "Issue a refund", "Limits"]
    assert "Plan: Pro, Max: 500" in text  # table header travels with its row


def test_parser_links_image_to_section_context_and_skips_logos():
    a = parse_article("https://h.com/refunds", HTML)
    assert [i.url for i in a.images] == ["https://h.com/img/refund-dialog.png"]
    assert a.images[0].section == "Issue a refund" and "Click Refund" in a.images[0].context


def test_hash_changes_only_when_content_changes():
    a1, a2 = parse_article("u", HTML), parse_article("u", HTML)
    a3 = parse_article("u", HTML.replace("Open Payments", "Open Billing"))
    assert a1.content_hash == a2.content_hash != a3.content_hash


def test_chunk_ids_are_deterministic_and_carry_titles():
    a = parse_article("https://h.com/refunds", HTML)
    c1 = chunk_article(a, {"https://h.com/img/refund-dialog.png": "Screenshot: Refund dialog"})
    c2 = chunk_article(a, {"https://h.com/img/refund-dialog.png": "Screenshot: Refund dialog"})
    assert [c.id for c in c1] == [c.id for c in c2]
    assert all(c.text.startswith("Refunds > ") for c in c1)
    assert any(c.kind == "image" and c.image_url for c in c1)


def test_split_text_overlap_and_oversize():
    groups = split_text(["a" * 50, "b" * 50, "c" * 50], max_chars=100, overlap_blocks=1)
    assert groups[0] == ["a" * 50, "b" * 50] and groups[1][0] == "b" * 50
    assert split_text(["x" * 500], max_chars=100) == [["x" * 500]]
