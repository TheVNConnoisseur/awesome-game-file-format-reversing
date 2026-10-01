"""Standard-library README hierarchy and generated-contents validation.

This module deliberately has no dependency on the private maintenance tooling:
it is copied to the public branch and is also imported by the site builder.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO, cast

from site_build import list_source

START_MARKER = "<!-- START doctoc -->"
END_MARKER = "<!-- END doctoc -->"
TOC_TITLE = "## 📑 Contents"

# These are the post-migration section names, in document order.  The first
# three are the short introductory sections and the last three are the legal /
# cross-reference sections that remain at the end of README.md.
APPROVED_H2_ORDER = (
    "📖 About",
    "🗺️ How to Use This List",
    "📑 Contents",
    "👥 Communities",
    "📚 Reference & Learning",
    "🛠️ General Tools",
    "⚙️ Engines",
    "🔧 Middleware & SDKs",
    "Game & Studio Tools",
    "🔗 Related Lists",
    "📄 License",
    "🙏 Acknowledgments",
)

APPROVED_H3_GROUPS = {
    "👥 Communities": ("Forums & Communities", "Discord Servers"),
    "📚 Reference & Learning": (
        "Knowledge Bases & Format Databases",
        "Platform & SDK Documentation",
        "Game-Specific Wikis",
        "Learning Resources & Tutorials",
        "Asset Databases",
    ),
    "🛠️ General Tools": (
        "🎨 Asset Viewers & Converters",
        "📦 Archive Extractors",
        "🔊 Audio Tools",
        "🌐 Translation & Localization",
        "🔍 Hex Editors",
        "🔬 Format Analysis & Reverse Engineering",
        "💻 Development Libraries",
        "📂 Script Collections & Multi-Game Tools",
    ),
    "⚙️ Engines": (
        "Major & General-Purpose Engines",
        "Legacy & Specialized Engines",
        "RPG, Visual Novel & Adventure Engines",
        "Interactive Fiction & Dialogue Systems",
        "Other Authoring Runtimes",
    ),
    "🔧 Middleware & SDKs": (
        "Graphics, Animation & Asset Technology",
        "Audio Technology",
        "Platform SDKs & Hardware",
        "Runtimes, Services & Toolkits",
    ),
    "Game & Studio Tools": (
        "0–9",
        *tuple(chr(c) for c in range(ord("A"), ord("Z") + 1)),
        "Other / Non-Latin",
    ),
}

GAME_BANDS = ("0–9", *tuple(chr(c) for c in range(ord("A"), ord("Z") + 1)), "Other / Non-Latin")
ENTITY_SECTION = "Game & Studio Tools"
ENGINE_SECTION = "⚙️ Engines"
MIDDLEWARE_SECTION = "🔧 Middleware & SDKs"

_FENCE_START_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$")
_ANCHOR_RE = re.compile(r"\]\(#([^\s)]+)\)")


def _policy_name(text: str) -> str:
    """Remove an optional decorative emoji prefix for hierarchy comparisons."""

    value = text.strip()
    while value and not (value[0].isalnum() or value[0] == "_"):
        value = value[1:]
    return value.lstrip()


_APPROVED_H2_POLICY_ORDER = tuple(_policy_name(name) for name in APPROVED_H2_ORDER)
_APPROVED_H3_POLICY_GROUPS = {
    _policy_name(parent): tuple(_policy_name(child) for child in children)
    for parent, children in APPROVED_H3_GROUPS.items()
}
_ENTITY_SECTION_POLICY = _policy_name(ENTITY_SECTION)
_ENGINE_SECTION_POLICY = _policy_name(ENGINE_SECTION)
_MIDDLEWARE_SECTION_POLICY = _policy_name(MIDDLEWARE_SECTION)


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line: int
    slug: str
    parent: Heading | None


def _fence_start(line: str) -> tuple[str, int] | None:
    match = _FENCE_START_RE.match(line)
    if match is None:
        return None
    return match.group(1)[0], len(match.group(1))


def _closes_fence(line: str, fence: tuple[str, int]) -> bool:
    character, length = fence
    return re.match(rf"^ {{0,3}}{re.escape(character)}{{{length},}}\s*$", line) is not None


def _strip_for_slug(text: str) -> str:
    kept: list[str] = []
    for character in text:
        if character in "- ":
            kept.append(character)
            continue
        category = unicodedata.category(character)
        if category[0] in "LNM" or category == "Pc":
            kept.append(character)
    return "".join(kept)


class _Slugger:
    def __init__(self) -> None:
        self.seen: dict[str, int] = {}
        self.used: set[str] = set()

    def slug(self, text: str) -> str:
        base = _strip_for_slug(text.strip().lower()).replace(" ", "-")
        suffix = self.seen.get(base, 0)
        candidate = base if suffix == 0 else f"{base}-{suffix}"
        while candidate in self.used:
            suffix += 1
            candidate = f"{base}-{suffix}"
        self.seen[base] = suffix + 1
        self.used.add(candidate)
        return candidate


def _heading_text(line: str) -> tuple[int, str] | None:
    match = _HEADING_RE.match(line)
    if match is None:
        return None
    text = re.sub(r"[ \t]+#+[ \t]*$", "", match.group(2)).strip()
    if not text:
        return None
    return len(match.group(1)), text


def parse_headings(text: str) -> list[Heading]:
    """Parse real ATX headings, assigning GitHub-compatible slugs in order."""

    headings: list[Heading] = []
    stack: list[Heading] = []
    slugger = _Slugger()
    fence: tuple[str, int] | None = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            continue
        opening = _fence_start(line)
        if opening is not None:
            fence = opening
            continue
        parsed = _heading_text(line)
        if parsed is None:
            continue
        level, heading_text = parsed
        while stack and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1] if stack else None
        heading = Heading(level, heading_text, line_number, slugger.slug(heading_text), parent)
        headings.append(heading)
        stack.append(heading)
    return headings


def normalized_sort_key(text: str) -> tuple[str, str]:
    """Return the documented accent-insensitive, case-insensitive key."""

    folded = unicodedata.normalize("NFKD", text).casefold()
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return plain, folded


def _name_for_band(text: str) -> str:
    # Parenthetical English / transliterated names are the stable sort name for
    # entries whose display name is non-Latin.  If no such name exists, retain
    # the full heading and let game_band classify it as non-Latin.
    for candidate in re.findall(r"\(([^()]*)\)", text):
        if any("a" <= char.casefold() <= "z" for char in candidate):
            return candidate.strip()
    return text.strip()


def game_band(text: str) -> str:
    """Return the approved numeric, Latin-letter, or non-Latin band."""

    candidate = _name_for_band(text)
    key = normalized_sort_key(candidate)[0]
    if not key:
        return "Other / Non-Latin"
    first = key[0]
    if first.isdigit():
        return "0–9"
    if "a" <= first <= "z":
        return first.upper()
    return "Other / Non-Latin"


def _raw_line_records(text: str) -> list[tuple[int, str]]:
    return list(enumerate(text.splitlines(keepends=True), 1))


def _line_records(text: str) -> list[tuple[int, str]]:
    return [
        (number, line.rstrip("\r\n"))
        for number, line in _raw_line_records(text)
    ]


def _markers(text: str) -> tuple[tuple[int, str], tuple[int, str]] | None:
    starts: list[tuple[int, str]] = []
    ends: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    for number, line in _line_records(text):
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            continue
        opening = _fence_start(line)
        if opening is not None:
            fence = opening
            continue
        trimmed = line.strip()
        if trimmed == START_MARKER:
            starts.append((number, line))
        elif trimmed == END_MARKER:
            ends.append((number, line))
    if len(starts) != 1 or len(ends) != 1 or starts[0][0] > ends[0][0]:
        return None
    return starts[0], ends[0]


def _dominant_eol(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf >= lf else "\n"


def expected_toc(text: str, asm: list_source.Assembled | None = None) -> str:
    """Render the same shallow H2/H3 contents block as ``update_toc.mjs``.

    With ``asm`` (the assembled README + part files), each link points at the file
    that physically holds the heading, so the block works on GitHub.
    """

    markers = _markers(text)
    if markers is None:
        raise ValueError("README must contain exactly one ordered marker pair")
    end_line = markers[1][0]
    headings = [h for h in parse_headings(text) if h.line > end_line and h.level in (2, 3)]

    def target(h: Heading) -> str:
        if asm is None:
            return f"#{h.slug}"
        owner = asm.origins[h.line - 1][0]
        return list_source.canonical_link(list_source.README_NAME, owner, h.slug)

    lines = [START_MARKER, TOC_TITLE, ""]
    lines.extend(("  " if h.level == 3 else "") + f"- [{h.text}]({target(h)})" for h in headings)
    lines.append(END_MARKER)
    return _dominant_eol(text).join(lines)


def _toc_block(text: str, markers: tuple[tuple[int, str], tuple[int, str]]) -> str:
    start, end = markers[0][0], markers[1][0]
    block = "".join(
        line for number, line in _raw_line_records(text) if start <= number <= end
    )
    return block.removesuffix("\r\n").removesuffix("\n").removesuffix("\r")


def validate_structure(text: str, asm: list_source.Assembled | None = None) -> list[str]:
    """Return human-readable structural errors, each including a source line."""

    errors: list[str] = []
    headings = parse_headings(text)
    markers = _markers(text)
    if markers is None:
        errors.append("line 1: README must contain exactly one ordered doctoc marker pair")
    else:
        try:
            if asm is None:
                actual = _toc_block(text, markers)
            else:
                # Compare the contents block as committed (with file-qualified
                # links), not the link-normalised assembled copy.
                raw_markers = _markers(asm.readme_text)
                if raw_markers is None:
                    raise ValueError("README must contain exactly one ordered marker pair")
                actual = _toc_block(asm.readme_text, raw_markers)
            expected = expected_toc(text, asm)
        except ValueError as exc:
            errors.append(f"line {markers[0][0]}: {exc}")
        else:
            if actual != expected:
                errors.append(
                    f"line {markers[0][0]}: committed TOC block is stale; run npm run toc"
                )

    previous: Heading | None = None
    for heading in headings:
        if previous is not None and heading.level > previous.level + 1:
            errors.append(
                f"line {heading.line}: heading level H{heading.level} for {heading.text!r} "
                f"jumps from H{previous.level}"
            )
        if heading.level > 6:
            errors.append(f"line {heading.line}: heading {heading.text!r} exceeds H6")
        previous = heading

    h2s = [h for h in headings if h.level == 2]
    present = [_policy_name(h.text) for h in h2s]
    approved_h2_names = set(_APPROVED_H2_POLICY_ORDER)
    for heading in h2s:
        if _policy_name(heading.text) not in approved_h2_names:
            errors.append(
                f"line {heading.line}: unapproved H2 {heading.text!r}; "
                "remove it or add it to the approved section order"
            )
    missing = [
        name
        for name, policy_name in zip(APPROVED_H2_ORDER, _APPROVED_H2_POLICY_ORDER, strict=True)
        if policy_name not in present
    ]
    for name in missing:
        errors.append(f"line 1: required H2 {name!r} is missing")
    positions = [present.index(name) for name in _APPROVED_H2_POLICY_ORDER if name in present]
    if positions != sorted(positions):
        errors.append("line 1: H2 sections are not in the approved order")

    for heading in h2s:
        parent_name = _policy_name(heading.text)
        approved = _APPROVED_H3_POLICY_GROUPS.get(parent_name)
        if approved is None:
            continue
        actual = tuple(
            _policy_name(child.text)
            for child in headings
            if child.level == 3 and child.parent is heading
        )
        if actual != approved:
            errors.append(
                f"line {heading.line}: H2 {heading.text!r} must contain complete approved "
                f"H3 groups in order: {', '.join(approved)}"
            )

    # A generated contents title is the one heading whose parent is outside the
    # content hierarchy; all other duplicate text is ambiguous on GitHub.
    seen: dict[str, Heading] = {}
    for heading in headings:
        old = seen.get(heading.text)
        if old is not None:
            errors.append(
                f"line {heading.line}: duplicate heading {heading.text!r}; "
                f"first appears on line {old.line}"
            )
        else:
            seen[heading.text] = heading

    h2_by_heading = {h: _policy_name(h.text) for h in h2s}
    for heading in headings:
        if heading.level != 3 or heading.parent not in h2_by_heading:
            continue
        parent_name = h2_by_heading[heading.parent]
        approved = _APPROVED_H3_POLICY_GROUPS.get(parent_name)
        if approved is None:
            errors.append(
                f"line {heading.line}: H3 {heading.text!r} has unrecognized H2 parent "
                f"{heading.parent.text!r}"
            )
            continue
        heading_policy_name = _policy_name(heading.text)
        if heading_policy_name not in approved:
            errors.append(
                f"line {heading.line}: H3 {heading.text!r} has unapproved H2 parent {parent_name!r}"
            )
        else:
            prior = [
                h.text
                for h in headings
                if h.level == 3 and h.parent is heading.parent and h.line < heading.line
            ]
            if prior and approved.index(heading_policy_name) < max(
                (
                    approved.index(_policy_name(name))
                    for name in prior
                    if _policy_name(name) in approved
                ),
                default=-1,
            ):
                errors.append(
                    f"line {heading.line}: H3 {heading.text!r} is out of order under "
                    f"{parent_name!r}"
                )

    for heading in headings:
        if heading.level != 4 or heading.parent is None or heading.parent.level != 3:
            continue
        parent = heading.parent
        if (
            parent.text in GAME_BANDS
            and parent.parent is not None
            and _policy_name(parent.parent.text) == _ENTITY_SECTION_POLICY
        ):
            expected = game_band(heading.text)
            if expected != parent.text:
                errors.append(
                    f"line {heading.line}: entity {heading.text!r} belongs in {expected!r}, "
                    f"not {parent.text!r}"
                )
            siblings = [
                h
                for h in headings
                if h.level == 4 and h.parent is parent and h.line < heading.line
            ]
            if siblings and normalized_sort_key(_name_for_band(heading.text)) < normalized_sort_key(
                _name_for_band(siblings[-1].text)
            ):
                errors.append(
                    f"line {heading.line}: entity {heading.text!r} is not sorted under "
                    f"{parent.text!r}"
                )
        elif parent.parent is not None and _policy_name(parent.parent.text) in (
            _ENGINE_SECTION_POLICY,
            _MIDDLEWARE_SECTION_POLICY,
        ):
            groups = _APPROVED_H3_POLICY_GROUPS[_policy_name(parent.parent.text)]
            if _policy_name(parent.text) not in groups:
                errors.append(
                    f"line {heading.line}: H4 {heading.text!r} has unapproved H3 parent "
                    f"{parent.text!r}"
                )

    slugs = {h.slug for h in headings}
    fence: tuple[str, int] | None = None
    for line_number, line in _line_records(text):
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            continue
        opening = _fence_start(line)
        if opening is not None:
            fence = opening
            continue
        for anchor in _ANCHOR_RE.findall(line):
            if anchor not in slugs:
                errors.append(
                    f"line {line_number}: internal anchor #{anchor} does not resolve to a heading"
                )
    return errors


_LOCATION_RE = re.compile(r"^line (\d+):")


def validate_path(path: Path) -> list[str]:
    """Validate README.md plus every ``list/`` part it includes.

    Structure rules run on the assembled document; errors are re-labelled with the
    physical ``file:line``. Adds the checks that only exist because the list is
    split: cross-file links must be written in their GitHub form, and heading
    anchors must be the same whether GitHub renders the file alone or assembled.
    """

    try:
        asm = list_source.assemble_path(path)
    except (OSError, ValueError) as exc:
        return [f"{path}: {exc}"]

    errors: list[str] = []
    for error in validate_structure(asm.text, asm):
        match = _LOCATION_RE.match(error)
        if match:
            error = f"{asm.locate(int(match.group(1)))}:{error[match.end():]}"
        errors.append(error)

    headings = parse_headings(asm.text)
    heading_files = {h.slug: asm.origins[h.line - 1][0] for h in headings}
    errors.extend(list_source.link_errors(asm, heading_files))

    for name, raw_lines in asm.raw.items():
        local = {h.line: h.slug for h in parse_headings("\n".join(raw_lines))}
        for h in headings:
            origin, local_line = asm.origins[h.line - 1]
            if origin == name and local.get(local_line) != h.slug:
                errors.append(
                    f"{name}:{local_line}: heading {h.text!r} has anchor #{local.get(local_line)} "
                    f"on GitHub but #{h.slug} in the assembled list"
                )
    return errors


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        cast(TextIO, sys.stdout).reconfigure(encoding="utf-8")  # ty: ignore[unresolved-attribute]
    parser = argparse.ArgumentParser(
        description="Validate README heading structure and generated contents"
    )
    parser.add_argument(
        "path", nargs="?", type=Path, help="README path (defaults to repository README.md)"
    )
    args = parser.parse_args(argv)
    path = args.path or Path(__file__).resolve().parents[1] / "README.md"
    errors = validate_path(path)
    if errors:
        print("\n".join(errors))
        print(f"README structure check failed with {len(errors)} error(s).")
        return 1
    print("README structure check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
