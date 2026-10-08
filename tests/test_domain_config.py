from pathlib import Path

from asknews.domain_config import load_speakers

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shipped_speakers_file_loads_and_matches() -> None:
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    powell = directory.find("fed", "Transcript of Chair Powell's Press Conference")
    assert powell is not None
    assert (powell.slug, powell.role) == ("powell", "Chair")
    assert directory.find("boj", "Transcript of Chair Powell's Press Conference") is None
    assert directory.get("ueda") is not None
    assert directory.get("nobody") is None
    # A one-character surname is a name only before a role word.
    masu = directory.find("boj", "増審議委員記者会見（2026年9月10日）")
    assert masu is not None
    assert masu.slug == "masu"
    adachi = directory.find("boj", "安達審議委員記者会見（増加する賃金）")
    assert (adachi.slug if adachi else None) != "masu"


def test_meeting_markers_tell_an_extraordinary_meeting_from_a_temporary_measure() -> None:
    from asknews.domain_config import load_meeting_markers

    markers = load_meeting_markers(REPO_ROOT / "domain" / "meetings.yaml")
    assert markers.is_statement("fed", "Federal Reserve issues FOMC statement", "")
    assert not markers.is_statement("fed", "Statement on Longer-Run Goals", "")
    assert markers.is_statement(
        "boj", "金融緩和の強化について", "本日、政策委員会・金融政策決定会合において"
    )
    assert markers.is_unscheduled("boj", "本日開催した臨時の金融政策決定会合において決定した")
    assert not markers.is_unscheduled("boj", "感染症の影響への対応のための臨時措置として決定した")


def test_slugs_are_unique() -> None:
    directory = load_speakers(REPO_ROOT / "domain" / "speakers.yaml")
    slugs = [s.slug for s in directory.speakers]
    assert len(slugs) == len(set(slugs))


def test_missing_optional_fields(tmp_path: Path) -> None:
    path = tmp_path / "speakers.yaml"
    path.write_text("ecb:\n  - slug: lagarde\n    name: Christine Lagarde\n", "utf-8")
    directory = load_speakers(path)
    spec = directory.get("lagarde")
    assert spec is not None
    assert (spec.role, spec.match, spec.name_ja) == (None, (), None)
