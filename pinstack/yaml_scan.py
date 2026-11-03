"""A small YAML reader for finding mapping values, with no dependencies.

pinstack has no runtime dependencies, so it cannot use a YAML library. The
checkers that read YAML need only one thing from a document: every scalar
value together with the chain of mapping keys above it (``jobs.build.steps``
then a sequence item then ``uses``), and its line number. Matching lines with
a regex misses flow style (``- {uses: actions/checkout@v4}``), quoted keys
(``"uses": ...``) and values spread over several lines, and it mistakes text
inside a ``run: |`` script for real keys. This module reads the structure
instead:

- block mappings and sequences, including sequences at the same indentation
  as their key;
- flow mappings and sequences, also when they span several lines;
- single- and double-quoted keys and values;
- ``#`` comments outside quotes;
- block scalars (``|``, ``>``, with indicators): the content is the value of
  its key and is never read as keys, so text inside a ``run: |`` script
  cannot look like a ``uses:`` entry;
- anchors (``&name``) and tags (``!tag``) before a value, and aliases
  (``*name``). An alias of a scalar is resolved to its value. An alias of a
  mapping or sequence is expanded where it is used: each scalar under the
  anchor is reported again under the alias's path (marked ``copied``), and
  a merge key (``<<: *base``) merges into the mapping that holds it. Keys the
  merging mapping overrides are not removed, so a merged value may be
  reported although the mapping replaces it. As in YAML, an alias names the
  most recent anchor of that name, and anchors do not carry across
  documents. Expansion stops once the copies hold ``_MAX_COPIED`` path
  elements in all, so nested aliases cannot make the output grow without
  bound; aliases past that point are left unresolved;
- several documents separated by ``---``.

Anything it cannot read (a complex ``?`` key, a malformed flow collection)
is skipped rather than guessed at. Callers that must fail closed should treat
an alias they cannot resolve as unverifiable. Flow collections nested more
than ``MAX_FLOW_DEPTH`` deep raise :class:`YamlTooDeep` rather than
exhausting the stack; callers should report the file as unreadable.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Optional

# A sequence item marker in a key path.
ITEM = "-"
# How checkers describe an alias whose anchor is not a scalar this reader saw.
UNRESOLVED_ALIAS = "is a YAML alias pinstack could not resolve"


@dataclass(frozen=True)
class Scalar:
    """A scalar value and where it sits in the document.

    :ivar path: the mapping keys from the document root down to this value,
        with ``ITEM`` for each sequence item; the last element is the value's
        own key (or ``ITEM`` for a bare sequence entry).
    :ivar value: the scalar text, unquoted. For an alias that could not be
        resolved it is ``*name`` and ``alias`` is True.
    :ivar line: the 1-based line the value starts on; for a copy made by
        expanding an alias, the alias's line.
    :ivar copied: True for a copy made by expanding an alias of a mapping or
        sequence; the same value was also reported where it was anchored.
    """

    path: tuple[str, ...]
    value: str
    line: int
    alias: bool = False
    copied: bool = False

    @property
    def key(self) -> str:
        return self.path[-1] if self.path else ""


_BLOCK_SCALAR_RE = re.compile(r"^[|>][0-9+-]*$")
_PROPERTY_RE = re.compile(r"^(?:[&!][^\s,\[\]{}]*\s+)+")
_ANCHOR_RE = re.compile(r"&([^\s,\[\]{}]+)")
# Node properties at a position inside flow text (no ``^``: used with pos).
_PROPERTIES_AT_RE = re.compile(r"(?:[&!][^\s,\[\]{}]*\s+)+")
# The most path elements alias expansion may copy in one stream (each copy
# costs the length of its path, so deep nesting counts against it).
_MAX_COPIED = 1_000_000
# The deepest flow nesting read; deeper raises YamlTooDeep.
MAX_FLOW_DEPTH = 100


class YamlTooDeep(ValueError):
    """Flow collections are nested deeper than ``MAX_FLOW_DEPTH``.

    :ivar line: the line the outermost flow collection starts on.
    """

    def __init__(self, line: int) -> None:
        super().__init__(
            "flow collections nested more than {} deep".format(MAX_FLOW_DEPTH)
        )
        self.line = line


def _quote_can_open(text: str, i: int) -> bool:
    """Return True if a quote character at ``text[i]`` starts a quoted scalar.

    A quoted scalar starts where a node starts: at the beginning of the text,
    or after whitespace or a flow indicator that follows a node boundary
    (``key: 'v'``, ``- 'v'``, ``[a, 'b']``, ``{'k': v}``). An apostrophe
    inside a plain scalar (``Don't``) is just a character.
    """
    if i == 0:
        return True
    if text[i - 1] not in " \t\r\n{[,":
        return False
    j = i - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    return j < 0 or text[j] in "{[,:-?"


def _scan_quotes(text: str, quote: str = "") -> tuple[list[tuple[int, str]], str]:
    """Return the characters of ``text`` outside quoted scalars, and the quote
    still open at its end.

    This is the one place quoting is decided, so comment stripping, key
    splitting and bracket counting always agree on where a string is.

    :param quote: the quote character open at the start of ``text`` (a quoted
        scalar continuing from an earlier line), or ``""``.
    """
    outside: list[tuple[int, str]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if quote == '"' and ch == "\\":
                i += 2
                continue
            if ch == quote:
                if quote == "'" and text[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                quote = ""
            i += 1
            continue
        if ch in ("'", '"') and _quote_can_open(text, i):
            quote = ch
            i += 1
            continue
        outside.append((i, ch))
        i += 1
    return outside, quote


def _strip_comment(line: str, quote: str = "") -> str:
    """Remove a ``#`` comment that is outside quotes.

    A ``#`` starts a comment at the start of the line or after whitespace.
    """
    for i, ch in _scan_quotes(line, quote)[0]:
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        out: list[str] = []
        i = 1
        while i < len(text) - 1:
            ch = text[i]
            if ch == "\\" and i + 1 < len(text) - 1:
                nxt = text[i + 1]
                out.append(
                    {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/"}.get(nxt, nxt)
                )
                i += 2
                continue
            out.append(ch)
            i += 1
        return "".join(out)
    return text


def _split_key(text: str) -> Optional[tuple[str, str]]:
    """Split ``key: value`` at the first ``:`` that ends a key, outside quotes.

    A ``:`` ends a key when it is followed by whitespace or the end of text.
    Returns None when the text is not a mapping entry.
    """
    if text[:1] in ("{", "["):
        return None
    for i, ch in _scan_quotes(text)[0]:
        if ch == ":" and (i + 1 == len(text) or text[i + 1] in " \t"):
            key = text[:i].strip()
            if not key or key.startswith("?"):
                return None
            return _unquote(key), text[i + 1 :].strip()
    return None


def _bracket_balance(text: str, quote: str = "") -> tuple[int, str]:
    """Return the net ``{``/``[`` opened in ``text`` outside quotes, and the
    quote still open at its end."""
    depth = 0
    outside, quote = _scan_quotes(text, quote)
    for _, ch in outside:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth, quote


def _quote_closes(text: str) -> bool:
    """Return True if the quoted scalar that starts ``text`` ends in it."""
    return _scan_quotes(text[1:], text[0])[1] == ""


class _Reader:
    def __init__(self) -> None:
        self.out: list[Scalar] = []
        self.anchors: dict[str, str] = {}
        # Anchored mappings and sequences: name -> (the anchor's path, the
        # start and end in ``out`` of the scalars under it, and the number of
        # path elements below the anchor in them). ``out`` only grows, so the
        # range stays valid.
        self.collections: dict[str, tuple[tuple[str, ...], int, int, int]] = {}
        self.copied = 0
        # The order in which anchors open, so that a collection anchor,
        # recorded only when it closes, does not replace a later one.
        self._seq = 0
        self._latest: dict[str, int] = {}
        self._depth = 0  # flow collections currently open
        self._newlines: list[int] = []  # offsets of "\n" in the flow text

    def open_anchor(self, name: str) -> int:
        """Start a definition of ``&name``; return its place in document order.

        Until it is recorded, an alias of the name is unresolved: an alias
        inside its own anchor cannot be expanded.
        """
        self._seq += 1
        self._latest[name] = self._seq
        self.anchors.pop(name, None)
        self.collections.pop(name, None)
        return self._seq

    def reset_anchors(self) -> None:
        """Forget every anchor: a new document starts."""
        self.anchors.clear()
        self.collections.clear()
        self._latest.clear()

    def anchor_collection(
        self, name: str, path: tuple[str, ...], start: int, seq: int
    ) -> None:
        """Record that the scalars emitted since ``start`` sit under ``&name``."""
        if self._latest.get(name) != seq:
            return  # a later anchor of the same name opened since
        end = len(self.out)
        if end - start == 1 and self.out[start].path == path:
            # The anchor named a scalar on the following line ("k: &a" then
            # an indented value): it is a scalar anchor.
            item = self.out[start]
            if not item.alias:
                self.anchors[name] = item.value
            return
        depth = len(path)
        below = sum(len(item.path) - depth for item in self.out[start:end])
        self.collections[name] = (path, start, end, below)

    def _expand(self, path: tuple[str, ...], name: str, line: int) -> bool:
        """Copy an anchored collection's scalars to ``path``; False if over budget."""
        anchor_path, start, end, below = self.collections[name]
        # A merge key merges into the mapping that holds it.
        if path[-1:] == ("<<",):
            path = path[:-1]
        elif path[-2:] == ("<<", ITEM):
            path = path[:-2]
        cost = (end - start) * len(path) + below
        if self.copied + cost > _MAX_COPIED:
            return False
        self.copied += cost
        depth = len(anchor_path)
        for item in self.out[start:end]:
            # A copy is marked only if it sits below the anchor, where the
            # original was reported under the same last key.
            copied = len(item.path) > depth
            self.out.append(
                Scalar(path + item.path[depth:], item.value, line, item.alias, copied)
            )
        return True

    # -- values -----------------------------------------------------------

    def emit(self, path: tuple[str, ...], raw: str, line: int) -> None:
        """Record one scalar, handling anchors, tags and aliases."""
        raw = raw.strip()
        props = _PROPERTY_RE.match(raw)
        anchor = None
        if props:
            m = _ANCHOR_RE.search(props.group(0))
            anchor = m.group(1) if m else None
            raw = raw[props.end() :]
        if not raw:
            return
        if raw.startswith("*"):
            name = raw[1:].strip()
            if name in self.anchors:
                self.out.append(Scalar(path, self.anchors[name], line))
            elif name not in self.collections or not self._expand(path, name, line):
                self.out.append(Scalar(path, raw, line, alias=True))
            return
        value = _unquote(raw)
        if anchor:
            self.open_anchor(anchor)
            self.anchors[anchor] = value
        self.out.append(Scalar(path, value, line))

    # -- flow collections ---------------------------------------------------

    def flow(self, path: tuple[str, ...], text: str, line: int) -> None:
        """Read a flow mapping or sequence that starts at ``text[0]``.

        The text may begin with node properties (``&name {...}``).
        """
        self._newlines = [i for i, ch in enumerate(text) if ch == "\n"]
        self._depth = 0
        try:
            self._flow_node(path, text, 0, line)
        except RecursionError:
            # Only reachable if the interpreter's limit is below MAX_FLOW_DEPTH.
            raise YamlTooDeep(line) from None

    def _flow_node(self, path: tuple[str, ...], text: str, pos: int, line: int) -> int:
        """Parse the flow node at ``pos``; return the index after it."""
        pos = self._skip_space(text, pos)
        if pos >= len(text):
            return pos
        if text[pos] in "&!":
            props = _PROPERTIES_AT_RE.match(text, pos)
            if props and text[props.end() : props.end() + 1] in ("{", "["):
                anchor = _ANCHOR_RE.search(props.group(0))
                seq = self.open_anchor(anchor.group(1)) if anchor else 0
                start = len(self.out)
                end = self._flow_node(path, text, props.end(), line)
                if anchor:
                    self.anchor_collection(anchor.group(1), path, start, seq)
                return end
        opener = text[pos]
        if opener not in "{[":
            end = self._scalar_end(text, pos)
            self.emit(
                path, text[pos:end], line + bisect.bisect_left(self._newlines, pos)
            )
            return end
        self._depth += 1
        if self._depth > MAX_FLOW_DEPTH:
            raise YamlTooDeep(line)
        try:
            return self._flow_collection(path, text, pos, line)
        finally:
            self._depth -= 1

    def _flow_collection(
        self, path: tuple[str, ...], text: str, pos: int, line: int
    ) -> int:
        """Parse the flow mapping or sequence opening at ``pos``."""
        opener = text[pos]
        closer = "}" if opener == "{" else "]"
        pos += 1
        while True:
            pos = self._skip_space(text, pos)
            if pos >= len(text):
                return pos
            if text[pos] == closer:
                return pos + 1
            if text[pos] == ",":
                pos += 1
                continue
            # Properties before a key belong to the key, not its value.
            key_start = self._after_properties(text, pos)
            if opener == "{":
                key_end = self._key_end(text, key_start)
                if key_end is None:
                    # A key with no value ({a, b: c}) is a null entry.
                    end = self._scalar_end(text, pos)
                    if end < len(text) and text[end] in ",}":
                        pos = end
                        continue
                    return len(text)
                key = _unquote(text[key_start:key_end])
                pos = self._skip_space(text, key_end + 1)
                if pos < len(text) and text[pos] in ",}":
                    continue  # key with no value
                new_pos = self._flow_node(path + (key,), text, pos, line)
                if new_pos <= pos:
                    return len(text)  # malformed; stop rather than loop
                pos = new_pos
            else:
                key_end = (
                    None
                    if text[key_start : key_start + 1] in ("{", "[")
                    else self._key_end(text, key_start)
                )
                if key_end is not None:
                    # An implicit single-pair mapping: [key: value].
                    key = _unquote(text[key_start:key_end])
                    new_pos = self._flow_node(
                        path + (ITEM, key), text, key_end + 1, line
                    )
                else:
                    new_pos = self._flow_node(path + (ITEM,), text, pos, line)
                if new_pos <= pos:
                    return len(text)  # malformed; stop rather than loop
                pos = new_pos

    @staticmethod
    def _after_properties(text: str, pos: int) -> int:
        """Return the index after any node properties (``&a !t``) at ``pos``."""
        if text[pos] not in "&!":
            return pos
        props = _PROPERTIES_AT_RE.match(text, pos)
        return props.end() if props else pos

    @staticmethod
    def _skip_space(text: str, pos: int) -> int:
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        return pos

    @staticmethod
    def _scalar_end(text: str, pos: int) -> int:
        """Return the end of a flow scalar starting at ``pos``."""
        if text[pos] in ("'", '"'):
            quote = text[pos]
            i = pos + 1
            while i < len(text):
                if quote == '"' and text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    if quote == "'" and text[i + 1 : i + 2] == "'":
                        i += 2
                        continue
                    return i + 1
                i += 1
            return len(text)
        i = pos
        while i < len(text) and text[i] not in ",]}":
            i += 1
        return i

    @staticmethod
    def _key_end(text: str, pos: int) -> Optional[int]:
        """Return the index of the ``:`` ending the flow key at ``pos``."""
        if text[pos] in ("'", '"'):
            end = _Reader._scalar_end(text, pos)
            colon = _Reader._skip_space(text, end)
            return colon if colon < len(text) and text[colon] == ":" else None
        i = pos
        while i < len(text):
            ch = text[i]
            if ch == ":" and (i + 1 == len(text) or text[i + 1] in " \t\r\n,}]"):
                return i
            if ch in ",}]":
                return None
            i += 1
        return None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def scalars(content: str) -> list[Scalar]:
    """Return every scalar in a YAML document stream, with its key path.

    :param content: the file text.
    """
    reader = _Reader()
    if content.startswith("\ufeff"):
        content = content[1:]
    raw_lines = content.splitlines()
    # (column, name, is_sequence_item, anchor). The anchor of a mapping or
    # sequence is (name, its path, len(reader.out) when it opened, its order);
    # it is recorded when the entry is popped, once all its content is read.
    stack: list[
        tuple[int, str, bool, Optional[tuple[str, tuple[str, ...], int, int]]]
    ] = []

    def pop() -> None:
        anchor = stack.pop()[3]
        if anchor is not None:
            reader.anchor_collection(*anchor)

    def pop_all() -> None:
        while stack:
            pop()

    def names() -> tuple[str, ...]:
        return tuple(entry[1] for entry in stack)

    # An open block scalar: (indent it must exceed, path, start line, folded,
    # content lines).
    block: Optional[tuple[int, tuple[str, ...], int, bool, list[str]]] = None
    i = 0

    def deeper_lines(owner: int) -> list[str]:
        """Take the following lines indented deeper than ``owner`` (and blank
        lines between them): the continuation of the current scalar."""
        nonlocal i
        taken: list[str] = []
        while i < len(raw_lines):
            nxt = raw_lines[i]
            if nxt.strip() and _indent(nxt) <= owner:
                break
            taken.append(nxt)
            i += 1
        return taken

    while i < len(raw_lines):
        lineno = i + 1
        raw = raw_lines[i]
        i += 1
        stripped = raw.strip()
        indent = _indent(raw)

        if block is not None:
            if not stripped or indent > block[0]:
                block[4].append(raw)
                continue
            _emit_block(reader, block)
            block = None

        # Document markers and directives count only at column 0.
        if indent == 0 and (stripped in ("---", "...") or stripped.startswith("%")):
            pop_all()
            reader.reset_anchors()
            continue
        if indent == 0 and stripped.startswith("--- "):
            # Content after the document marker starts the new document.
            pop_all()
            reader.reset_anchors()
            raw = stripped[4:].lstrip()
            stripped = raw.strip()
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = _indent(line)

        # 1. Sequence item markers: "- ", possibly several ("- - x").
        col = indent
        rest = line[indent:]
        dash_col: Optional[int] = None
        while rest == "-" or rest.startswith(("- ", "-\t")):
            while stack and (
                stack[-1][0] > col or (stack[-1][0] == col and stack[-1][2])
            ):
                pop()
            stack.append((col, ITEM, True, None))
            dash_col = col
            step = 1 + len(rest[1:]) - len(rest[1:].lstrip())
            col += step
            rest = rest[step:]
        if not rest:
            continue

        # 2. A mapping entry, or a bare scalar / flow collection. ``owner``
        #    is the column a continuation line must be deeper than.
        # Properties before a key belong to the key ("- &a uses: x" is the
        # key uses); before anything else they stay with the value.
        key_props = _PROPERTY_RE.match(rest)
        entry = _split_key(rest[key_props.end() :] if key_props else rest)
        if entry is None:
            while stack and stack[-1][0] >= col and not stack[-1][2]:
                pop()
            path = names()
            value = rest
            if dash_col is not None:
                owner = dash_col
            else:
                owner = stack[-1][0] if stack else -1
        else:
            key, value = entry
            while stack and stack[-1][0] >= col:
                pop()
            path = names() + (key,)
            owner = col

        # 3. The value: nothing (a nested block follows), a block scalar,
        #    a flow collection, a quoted scalar or a plain scalar. The last
        #    three may continue on deeper lines.
        bare = _PROPERTY_RE.sub("", value + " ", count=1).strip() if value else ""
        if not bare:
            # Nothing, or only properties: a nested block (if any) follows,
            # and an anchor here names that block.
            anchor_m = _ANCHOR_RE.search(value) if value else None
            anchor = None
            if anchor_m:
                name = anchor_m.group(1)
                anchor = (name, path, len(reader.out), reader.open_anchor(name))
            if entry is not None:
                stack.append((col, path[-1], False, anchor))
            elif anchor and dash_col is not None and stack and stack[-1][2]:
                # "- &name" alone: the anchor names the item's content.
                c, n, item, _ = stack.pop()
                stack.append((c, n, item, anchor))
            continue
        if _BLOCK_SCALAR_RE.match(bare):
            block = (owner, path, lineno, bare.startswith(">"), [])
            continue
        if bare.startswith(("{", "[")):
            pieces = [value]
            balance, quote = _bracket_balance(value)
            while (balance > 0 or quote) and i < len(raw_lines):
                nxt = raw_lines[i]
                nxt_text = nxt.strip()
                # A line at or left of the owner ends the collection, unless it
                # only closes it (a JSON-style "]" back at the key's column).
                if (
                    nxt_text
                    and _indent(nxt) <= owner
                    and not quote
                    and not nxt_text.startswith(("]", "}"))
                ):
                    break
                piece = _strip_comment(nxt, quote)
                delta, quote = _bracket_balance(piece, quote)
                pieces.append(piece)
                balance += delta
                i += 1
            reader.flow(path, "\n".join(pieces), lineno)
            continue
        if bare[:1] in ("'", '"') and not _quote_closes(bare):
            # A quoted scalar that continues to its closing quote.
            pieces = [value]
            quote = bare[0]
            while quote and i < len(raw_lines):
                nxt = raw_lines[i]
                if nxt.strip() and _indent(nxt) <= owner:
                    break
                pieces.append(nxt.strip())
                quote = _scan_quotes(nxt, quote)[1]
                i += 1
            value = " ".join(pieces)
        elif owner >= 0:
            # A plain scalar: deeper lines are more of the same text.
            extra = [_strip_comment(ln).strip() for ln in deeper_lines(owner)]
            extra = [ln for ln in extra if ln]
            if extra:
                value = " ".join([value] + extra)
        if entry is None and not path:
            continue  # a bare scalar document
        reader.emit(path, value, lineno)

    if block is not None:
        _emit_block(reader, block)
    pop_all()
    return reader.out


def _emit_block(
    reader: _Reader, block: tuple[int, tuple[str, ...], int, bool, list[str]]
) -> None:
    """Record a block scalar's content as its key's value.

    Literal (``|``) content keeps its line breaks; folded (``>``) content is
    joined with spaces. Either way the content is stripped of indentation.
    """
    _, path, line, folded, raw_lines = block
    lines = [ln.strip() for ln in raw_lines]
    while lines and not lines[-1]:
        lines.pop()
    text = (" " if folded else "\n").join(ln for ln in lines if ln or not folded)
    if text and path:
        reader.out.append(Scalar(path, text, line))
