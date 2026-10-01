"""Assemble the logical list document from README.md and its ``list/`` part files.

GitHub only renders the first 500 KiB of a Markdown file, so the list is stored as
a thin ``README.md`` plus part files under ``list/``. README.md pulls a part in at
the position of a marker line::

    <!-- part: list/engines.md -->

Every tool that needs "the whole list" (structure check, contents block, site
builder, link checks, discovery) works on the *assembled* document: README.md with
each marker line replaced by the lines of its part file. Cross-file links are
stored in the source as real relative links so they work on GitHub
(``list/engines.md#unity``); assembly normalises them back to plain ``#unity``
anchors so the assembled document looks exactly like the old single README.

This module is standard-library only: it is copied to the public branch.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

README_NAME = "README.md"
PART_MARKER_RE = re.compile(r"^<!-- part: (list/[A-Za-z0-9._-]+\.md) -->\s*$")
# A link whose target is a heading in README.md or in a part file.
_FILE_TARGET = r"(?:\.\./|\./)*(?:list/)?[A-Za-z0-9._-]+\.md"
_QUALIFIED_LINK_RE = re.compile(r"\]\(" + _FILE_TARGET + r"#([^)\s]*)\)")
_FILE_TARGET_RE = re.compile(_FILE_TARGET)
_LINK_RE = re.compile(r"\]\(([^)\s]*)#([^)\s]*)\)")


@dataclass(frozen=True)
class Assembled:
    """The logical document plus where every line physically came from."""

    lines: list[str]  # normalised, no line endings
    origins: list[tuple[str, int]]  # (repo-relative file, 1-based line) per line
    eol: str  # README.md's dominant line ending
    parts: list[str]  # part files in document order
    raw: dict[str, list[str]]  # un-normalised lines per physical file
    readme_text: str  # README.md exactly as read, line endings included

    @property
    def text(self) -> str:
        return self.eol.join(self.lines) + self.eol

    def locate(self, line_number: int) -> str:
        """``file:line`` of a 1-based line in the assembled document."""

        if 1 <= line_number <= len(self.origins):
            path, local = self.origins[line_number - 1]
            return f"{path}:{local}"
        return f"line {line_number}"


def _dominant_eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf >= lf else "\n"


def _split(text: str) -> list[str]:
    # Only \n and \r\n end a line, matching GitHub and update_toc.mjs (str.splitlines
    # would also split on form feeds and Unicode separators).
    lines = re.split(r"\r?\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def normalise_links(line: str) -> str:
    """``](list/x.md#slug)`` / ``](../README.md#slug)`` -> ``](#slug)``."""

    return _QUALIFIED_LINK_RE.sub(r"](#\1)", line)


def assemble_texts(readme_text: str, read_part: Callable[[str], str]) -> Assembled:
    """Assemble from README text and a loader for part files (repo-relative path)."""

    readme_lines = _split(readme_text)
    lines: list[str] = []
    origins: list[tuple[str, int]] = []
    parts: list[str] = []
    raw: dict[str, list[str]] = {README_NAME: readme_lines}
    for number, line in enumerate(readme_lines, 1):
        match = PART_MARKER_RE.match(line)
        if match is None:
            lines.append(normalise_links(line))
            origins.append((README_NAME, number))
            continue
        path = match.group(1)
        if path in raw:
            raise ValueError(f"{README_NAME}:{number}: part {path} is included twice")
        part_lines = _split(read_part(path))
        raw[path] = part_lines
        parts.append(path)
        for local, part_line in enumerate(part_lines, 1):
            lines.append(normalise_links(part_line))
            origins.append((path, local))
    return Assembled(lines, origins, _dominant_eol(readme_text), parts, raw, readme_text)


def assemble_path(readme_path: Path) -> Assembled:
    """Assemble from a README.md on disk; parts are resolved next to it."""

    base = readme_path.parent
    text = readme_path.read_bytes().decode("utf-8")
    return assemble_texts(text, lambda rel: (base / rel).read_bytes().decode("utf-8"))


def assemble_git(rev: str, repo_root: Path, readme: str = README_NAME) -> Assembled:
    """Assemble README.md and its parts as committed at ``rev``."""

    def show(path: str) -> str:
        result = subprocess.run(
            ["git", "show", f"{rev}:{path}"],
            cwd=repo_root,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            reason = result.stderr.decode("utf-8", "replace")
            raise ValueError(f"git show {rev}:{path} failed: {reason}")
        return result.stdout.decode("utf-8")

    return assemble_texts(show(readme), show)


def canonical_link(from_file: str, to_file: str, slug: str) -> str:
    """The link target that works on GitHub from ``from_file`` to a heading."""

    if from_file == to_file:
        return f"#{slug}"
    relative = posixpath.relpath(to_file, posixpath.dirname(from_file) or ".")
    return f"{relative}#{slug}"


def link_errors(asm: Assembled, heading_files: dict[str, str]) -> list[str]:
    """Check that every cross-document anchor link is written in its GitHub form.

    ``heading_files`` maps heading slug -> physical file. A bare ``#slug`` is only
    valid when the heading lives in the same file; otherwise the link must be the
    canonical relative path to the file that does contain it.
    """

    errors: list[str] = []
    for path, raw_lines in asm.raw.items():
        fence = False
        for number, line in enumerate(raw_lines, 1):
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
                continue
            if fence:
                continue
            for match in _LINK_RE.finditer(line):
                target, slug = match.group(1), match.group(2)
                if target and _FILE_TARGET_RE.fullmatch(target) is None:
                    continue  # external link: not ours to check
                owner = heading_files.get(slug)
                if owner is None:
                    continue  # unresolved anchors are reported by the structure check
                expected = canonical_link(path, owner, slug)
                actual = f"{target}#{slug}"
                if actual != expected:
                    errors.append(
                        f"{path}:{number}: link ]({actual}) should be ]({expected})"
                    )
    return errors
