from asknews.ingest.htmltext import content_window, drop_boilerplate, pack_paragraphs, paragraphs

PAGE = """
<html><head><title>x</title><style>p{}</style></head><body>
<nav><a href="/">Home</a></nav>
<div id="article">
  <p class="article__time">September 17, 2025</p>
  <h3 class="title">Federal Reserve issues <em>FOMC</em> statement</h3>
  <p>First paragraph &amp; more.<br/>Second line.</p>
  <p>Second   paragraph with&nbsp;entity.</p>
  <ul><li>Item one</li><li>Item two</li></ul>
</div>
<div id="lastUpdate">Last Update: September 17, 2025</div>
<footer>footer text</footer>
</body></html>
"""


def test_paragraphs_split_on_blocks_and_skip_chrome() -> None:
    window = content_window(PAGE, start=['id="article"'], end=['id="lastUpdate"'])
    items = paragraphs(window)
    assert items == [
        "September 17, 2025",
        "Federal Reserve issues FOMC statement",
        "First paragraph & more.",
        "Second line.",
        "Second paragraph with entity.",
        "Item one",
        "Item two",
    ]
    assert "Home" not in " ".join(items)
    assert "footer" not in " ".join(items)


def test_content_window_without_markers_is_whole_text() -> None:
    assert content_window("abc", start=["zz"], end=["yy"]) == "abc"


def test_drop_boilerplate_matches_exact_or_prefix() -> None:
    items = ["Share", "For media inquiries, please email", "Keep me", "Shareholders matter"]
    assert drop_boilerplate(items, drop=["Share", "For media inquiries"]) == [
        "Keep me",
        "Shareholders matter",  # a one-word marker does not take a paragraph that starts with it
    ]


def test_pack_paragraphs_keeps_boundaries_and_splits_long_ones() -> None:
    short = ["a" * 300, "b" * 300, "c" * 300, "d" * 100]
    chunks = pack_paragraphs(short, max_chars=650)
    assert chunks == ["a" * 300 + "\n\n" + "b" * 300, "c" * 300 + "\n\n" + "d" * 100]
    long = ["Sentence one is here. " * 30]
    assert len(pack_paragraphs(long, max_chars=200)) > 1
