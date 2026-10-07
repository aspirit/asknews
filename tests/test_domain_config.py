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
