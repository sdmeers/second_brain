"""The cleaner must not create notes whose names differ from existing ones only by case.

Two such files break Syncthing on Android/Windows/macOS (case-insensitive filesystems).
"""
from backend.brain_cleaner import find_missing_concepts


def _links(*topics):
    return {t.lower(): {"topic": t, "count": 1, "snippets": []} for t in topics}


def test_link_differing_only_by_case_is_not_missing(tmp_path):
    concepts = tmp_path / "Concepts"
    concepts.mkdir()
    (concepts / "Dual-Use Technology.md").write_text("x")

    missing = find_missing_concepts(_links("Dual-use Technology"), [str(concepts)])

    assert missing == {}


def test_genuinely_missing_link_is_reported(tmp_path):
    concepts = tmp_path / "Concepts"
    concepts.mkdir()

    missing = find_missing_concepts(_links("Flow State"), [str(concepts), str(tmp_path / "absent")])

    assert list(missing) == ["flow state"]


def test_match_in_any_directory_counts(tmp_path):
    concepts, atlas = tmp_path / "Concepts", tmp_path / "Atlas"
    concepts.mkdir()
    atlas.mkdir()
    (atlas / "OODA Loop.md").write_text("x")

    assert find_missing_concepts(_links("ooda loop"), [str(concepts), str(atlas)]) == {}
