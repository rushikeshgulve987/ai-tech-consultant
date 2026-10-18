from ingestion.sitemap import diff_manifest, parse_sitemap
from tests.conftest import sitemap


def test_parse_dedupes_and_validates():
    xml = sitemap({"https://x.com/a": "2026-01-01", "https://x.com/b": None}) .replace("</urlset>", "<url><loc>https://x.com/a</loc></url><url><loc>ftp://bad</loc></url></urlset>")
    entries, children = parse_sitemap(xml)
    assert [e.url for e in entries] == ["https://x.com/a", "https://x.com/b"]
    assert entries[0].lastmod == "2026-01-01" and children == []


def test_diff_new_changed_unchanged_removed():
    entries, _ = parse_sitemap(sitemap({"https://x.com/new": "1", "https://x.com/same": "1", "https://x.com/chg": "2"}))
    known = {"https://x.com/same": dict(lastmod="1", status="ok", active=1),
             "https://x.com/chg": dict(lastmod="1", status="ok", active=1),
             "https://x.com/gone": dict(lastmod="1", status="ok", active=1)}
    d = diff_manifest(known, entries)
    assert [e.url for e in d.new] == ["https://x.com/new"]
    assert [e.url for e in d.changed] == ["https://x.com/chg"]
    assert [e.url for e in d.unchanged] == ["https://x.com/same"]
    assert d.removed == ["https://x.com/gone"]


def test_failed_pages_are_retried_not_skipped():
    entries, _ = parse_sitemap(sitemap({"https://x.com/f": "1"}))
    d = diff_manifest({"https://x.com/f": dict(lastmod="1", status="failed", active=1)}, entries)
    assert len(d.new) == 1 and not d.unchanged
