from __future__ import annotations

from pathlib import Path

from scripts.check_docs import check_docs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_check_docs_accepts_resolvable_links_assets_and_sitemap(tmp_path):
    docs = tmp_path / "docs"
    _write(docs / "404.html", '<a href="/freellmpool/">Home</a>')
    _write(
        docs / "index.html",
        (
            '<a href="guide.html#usage">Guide</a>'
            '<a href="https://0xzr.github.io/freellmpool/guide.html?from=home#usage">'
            "Absolute guide"
            "</a>"
            '<a href="http://0xzr.github.io/freellmpool/guide.html">HTTP guide</a>'
            '<a href="//0xzr.github.io/freellmpool/guide.html">Protocol-relative guide</a>'
            '<img src="assets/logo.svg">'
        ),
    )
    _write(
        docs / "guide.html",
        '<a href="/freellmpool/">Home</a><a href="https://example.com">External</a>',
    )
    _write(docs / "assets" / "logo.svg", "<svg/>")
    _write(
        docs / "sitemap.xml",
        """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://0xzr.github.io/freellmpool/</loc></url>
          <url><loc>https://0xzr.github.io/freellmpool/guide.html</loc></url>
        </urlset>
        """,
    )

    assert check_docs(docs) == []


def test_check_docs_reports_broken_same_origin_absolute_link(tmp_path):
    docs = tmp_path / "docs"
    _write(docs / "404.html", '<a href="/freellmpool/">Home</a>')
    _write(
        docs / "index.html",
        (
            '<a href="https://0xzr.github.io/freellmpool/missing.html#usage">Missing</a>'
            '<a href="http://0xzr.github.io/freellmpool/missing-http.html">Missing HTTP</a>'
            '<a href="//0xzr.github.io/freellmpool/missing-relative.html">Missing relative</a>'
            '<a href="https://example.com/freellmpool/missing.html">External</a>'
        ),
    )
    _write(
        docs / "sitemap.xml",
        """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://0xzr.github.io/freellmpool/</loc></url>
        </urlset>""",
    )

    errors = check_docs(docs)

    assert errors == [
        "index.html: missing internal target missing.html",
        "index.html: missing internal target missing-http.html",
        "index.html: missing internal target missing-relative.html",
    ]


def test_check_docs_rejects_absolute_remainder_and_resolved_root_escape(tmp_path):
    docs = tmp_path / "docs"
    _write(docs / "404.html", '<a href="/freellmpool/">Home</a>')
    _write(
        docs / "index.html",
        (
            '<a href="https://0xzr.github.io/freellmpool//etc/passwd">Bad absolute</a>'
            '<a href="/freellmpool/%2Fetc/passwd">Bad encoded absolute</a>'
        ),
    )
    _write(
        docs / "sitemap.xml",
        """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://0xzr.github.io/freellmpool/</loc></url>
        </urlset>""",
    )

    errors = check_docs(docs)

    assert len(errors) == 2
    assert all("absolute after Pages base" in error for error in errors)


def test_check_docs_rejects_symlink_that_resolves_outside_root(tmp_path):
    docs = tmp_path / "docs"
    _write(docs / "404.html", '<a href="/freellmpool/">Home</a>')
    _write(docs / "index.html", '<a href="escape/secret.txt">Escape</a>')
    _write(tmp_path / "outside" / "secret.txt", "secret")
    (docs / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    _write(
        docs / "sitemap.xml",
        """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://0xzr.github.io/freellmpool/</loc></url>
        </urlset>""",
    )

    errors = check_docs(docs)

    assert errors == [
        "index.html: internal target escapes resolved docs root: escape/secret.txt"
    ]


def test_check_docs_reports_broken_internal_links_and_sitemap_entries(tmp_path):
    docs = tmp_path / "docs"
    _write(docs / "404.html", '<a href="/freellmpool/">Home</a>')
    _write(
        docs / "index.html",
        '<a href="missing.html">Missing</a><script src="../outside.js"></script>',
    )
    _write(
        docs / "sitemap.xml",
        """<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>http://0xzr.github.io/freellmpool/index.html</loc></url>
          <url><loc>https://0xzr.github.io/freellmpool/missing.html</loc></url>
        </urlset>
        """,
    )

    errors = check_docs(docs)

    assert any("index.html: missing internal target missing.html" in error for error in errors)
    assert any("escapes docs root" in error for error in errors)
    assert any("must start with https://0xzr.github.io/freellmpool/" in error for error in errors)
    assert any("sitemap target does not exist: missing.html" in error for error in errors)


def test_check_docs_reports_duplicate_sitemap_locations(tmp_path):
    docs = tmp_path / "docs"
    _write(docs / "404.html", '<a href="/freellmpool/">Home</a>')
    _write(docs / "index.html", "<!doctype html>")
    _write(
        docs / "sitemap.xml",
        """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://0xzr.github.io/freellmpool/</loc></url>
          <url><loc>https://0xzr.github.io/freellmpool/</loc></url>
        </urlset>""",
    )

    assert any("duplicate sitemap location" in error for error in check_docs(docs))


def test_check_docs_reports_sitemap_coverage_mismatch(tmp_path):
    docs = tmp_path / "docs"
    _write(docs / "404.html", '<a href="/freellmpool/">Home</a>')
    _write(docs / "index.html", "<!doctype html>")
    _write(docs / "unlisted.html", "<!doctype html>")
    _write(docs / "nested" / "listed.html", "<!doctype html>")
    _write(
        docs / "sitemap.xml",
        """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://0xzr.github.io/freellmpool/</loc></url>
          <url><loc>https://0xzr.github.io/freellmpool/nested/listed.html</loc></url>
        </urlset>""",
    )

    errors = check_docs(docs)

    assert "sitemap.xml: deployable page is not listed: unlisted.html" in errors
    assert "sitemap.xml: non-deployable target is listed: nested/listed.html" in errors
