"""Vault write paths: atomic_write, filename sanitising, frontmatter, archiving."""

import os
from datetime import date
from pathlib import Path

import pytest
import yaml

from backend.utils.sanitize import sanitize_filename
from backend.vault.tasks import (
    archive_completed_lines,
    dump_frontmatter,
    parse_frontmatter,
    parse_project_steps,
    parse_task_line,
)
from backend.vault.writer import atomic_write


class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "note.md"
        atomic_write(str(target), "hello")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "note.md"
        target.write_text("old", encoding="utf-8")
        atomic_write(str(target), "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_creates_missing_parent_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "note.md"
        atomic_write(str(target), "deep")
        assert target.read_text(encoding="utf-8") == "deep"

    def test_leaves_no_temp_files_behind(self, tmp_path):
        atomic_write(str(tmp_path / "note.md"), "x")
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "note.md"]
        assert leftovers == []

    def test_binary_mode(self, tmp_path):
        target = tmp_path / "file.bin"
        atomic_write(str(target), b"\x00\x01\x02", is_binary=True)
        assert target.read_bytes() == b"\x00\x01\x02"

    def test_unicode_survives_round_trip(self, tmp_path):
        target = tmp_path / "note.md"
        atomic_write(str(target), "📅 🔔 café — ünïcode")
        assert target.read_text(encoding="utf-8") == "📅 🔔 café — ünïcode"

    def test_failed_write_does_not_destroy_existing_file(self, tmp_path):
        target = tmp_path / "note.md"
        target.write_text("original", encoding="utf-8")
        with pytest.raises(TypeError):
            atomic_write(str(target), object())  # not writable content
        assert target.read_text(encoding="utf-8") == "original"
        assert [p.name for p in tmp_path.iterdir()] == ["note.md"]


class TestSanitizeFilename:
    @pytest.mark.parametrize("raw,expected", [
        ("normal name", "normal name"),
        ("bad:name*here?", "badnamehere"),
        ("  collapse   spaces  ", "collapse spaces"),
        ("trailing dots...", "trailing dots"),
    ])
    def test_cleaning(self, raw, expected):
        assert sanitize_filename(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "...", "///", None])
    def test_unusable_input_falls_back(self, raw):
        # Previously returned "", which made callers write to the directory itself.
        assert sanitize_filename(raw) == "Untitled"

    @pytest.mark.parametrize("raw,expected", [
        ("../../etc/passwd", "passwd"),
        ("a/b/c.md", "c.md"),
        ("..\\..\\windows", "windows"),
    ])
    def test_collapses_to_a_single_path_segment(self, raw, expected):
        assert sanitize_filename(raw) == expected
        assert os.sep not in sanitize_filename(raw)

    def test_reserved_device_names(self):
        assert sanitize_filename("CON") == "Untitled"

    def test_length_is_capped(self):
        assert len(sanitize_filename("x" * 500)) <= 100

    def test_custom_default(self):
        assert sanitize_filename("", default="Fallback") == "Fallback"


class TestFrontmatter:
    def test_round_trip(self):
        data = {"title": "My Note", "tags": ["a", "b"], "status": "active"}
        assert parse_frontmatter(dump_frontmatter(data)) == data

    @pytest.mark.parametrize("title", [
        'Title with "quotes"',
        "Title: with a colon",
        "Title with 'single quotes'",
        "Title with #hash and [brackets]",
        "Title — with em dash and émoji 📅",
    ])
    def test_awkward_titles_survive(self, title):
        """f-string interpolation produced invalid YAML for all of these."""
        rendered = dump_frontmatter({"title": title, "type": "project"})
        assert parse_frontmatter(rendered)["title"] == title
        # and must be loadable by the plain yaml parser the librarian uses
        assert yaml.safe_load(rendered.split("---")[1])["title"] == title

    def test_missing_frontmatter_returns_empty_dict(self):
        assert parse_frontmatter("# Just a heading\n\nBody") == {}

    def test_malformed_yaml_returns_empty_dict_rather_than_raising(self):
        assert parse_frontmatter("---\n: : : bad\n---\nbody") == {}

    def test_non_mapping_frontmatter_returns_empty_dict(self):
        assert parse_frontmatter("---\n- a\n- b\n---\nbody") == {}


class TestParseProjectSteps:
    NOTE = """---
title: "Garage"
---
# Garage

## Project Steps
- [ ] Clear the floor
- [x] Buy shelving
- [ ] Install shelving

## Active Actions
- [ ] Should not be picked up
"""

    def test_returns_only_unchecked_steps_in_the_steps_section(self):
        assert parse_project_steps(self.NOTE) == ["Clear the floor", "Install shelving"]

    def test_absent_section_returns_empty(self):
        assert parse_project_steps("# Note\n\nNo steps here") == []

    def test_empty_section_returns_empty(self):
        assert parse_project_steps("## Project Steps\n\n## Active Actions\n") == []


class TestArchiveCompletedLines:
    def test_writes_parseable_completion_format(self, tasks_dir):
        assert archive_completed_lines(["- [ ] Buy milk"]) == 1
        content = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        line = next(ln for ln in content.splitlines() if ln.startswith("- [x]"))
        task = parse_task_line(line)
        assert task.checked is True
        assert task.completed_on == date.today()
        assert "Buy milk" in task.raw

    def test_newest_first(self, tasks_dir):
        archive_completed_lines(["- [ ] First"])
        archive_completed_lines(["- [ ] Second"])
        lines = [ln for ln in (tasks_dir / "z_Completed.md").read_text(encoding="utf-8").splitlines()
                 if ln.startswith("- [x]")]
        assert "Second" in lines[0] and "First" in lines[1]

    def test_preserves_existing_entries(self, tasks_dir):
        archive_completed_lines(["- [ ] A", "- [ ] B"])
        archive_completed_lines(["- [ ] C"])
        content = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert all(name in content for name in ("A", "B", "C"))

    def test_writes_the_archive_header(self, tasks_dir):
        archive_completed_lines(["- [ ] A"])
        assert (tasks_dir / "z_Completed.md").read_text(encoding="utf-8").startswith(
            "# ✅ Completed Tasks Archive")

    def test_empty_input_is_a_noop(self, tasks_dir):
        before = (tasks_dir / "z_Completed.md").read_text(encoding="utf-8")
        assert archive_completed_lines([]) == 0
        assert (tasks_dir / "z_Completed.md").read_text(encoding="utf-8") == before

    def test_archives_to_z_completed_not_completed(self, tasks_dir):
        """Regression: /reflect read 01_Tasks/Completed.md, which never existed."""
        archive_completed_lines(["- [ ] Buy milk"])
        assert (tasks_dir / "z_Completed.md").exists()
        assert not (Path(tasks_dir) / "Completed.md").exists()


class TestAtomicWritePreservesPermissions:
    """mkstemp creates 0600 and os.replace keeps the temp file's mode, so atomic_write
    silently stripped group/other read bits from every file it touched."""

    def test_existing_file_mode_is_preserved(self, tmp_path):
        target = tmp_path / "note.md"
        target.write_text("old", encoding="utf-8")
        os.chmod(target, 0o644)
        atomic_write(str(target), "new")
        assert oct(target.stat().st_mode & 0o777) == oct(0o644)

    def test_unusual_existing_mode_is_preserved(self, tmp_path):
        target = tmp_path / "note.md"
        target.write_text("old", encoding="utf-8")
        os.chmod(target, 0o664)
        atomic_write(str(target), "new")
        assert oct(target.stat().st_mode & 0o777) == oct(0o664)

    def test_new_file_respects_umask_not_0600(self, tmp_path):
        target = tmp_path / "fresh.md"
        atomic_write(str(target), "content")
        umask = os.umask(0)
        os.umask(umask)
        assert oct(target.stat().st_mode & 0o777) == oct(0o666 & ~umask)

    def test_new_file_is_group_readable_under_a_normal_umask(self, tmp_path):
        old = os.umask(0o022)
        try:
            target = tmp_path / "fresh2.md"
            atomic_write(str(target), "content")
            assert target.stat().st_mode & 0o044, "should be group/other readable"
        finally:
            os.umask(old)

    def test_binary_write_also_preserves_mode(self, tmp_path):
        target = tmp_path / "f.bin"
        target.write_bytes(b"x")
        os.chmod(target, 0o644)
        atomic_write(str(target), b"yy", is_binary=True)
        assert oct(target.stat().st_mode & 0o777) == oct(0o644)
