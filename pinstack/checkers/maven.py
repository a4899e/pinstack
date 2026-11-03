"""Maven checker: enforces explicit, non-dynamic versions in Maven build files.

Every coordinate Maven resolves from a repository is checked:

- ``<dependency>`` in any ``<dependencies>`` (project dependencies,
  ``dependencyManagement``, profiles, and a plugin's own dependencies)
- ``<plugin>`` in ``build/plugins``, ``pluginManagement`` and ``reporting``
- ``<extension>`` in ``build/extensions`` and in ``.mvn/extensions.xml``
  (core extensions, loaded before the build starts)
- ``<parent>``
- artifacts named inside a plugin's ``<configuration>`` that the plugin
  resolves itself: ``annotationProcessorPaths/path`` (compiler),
  ``tagletArtifact(s)``, ``docletArtifact(s)`` and
  ``additionalDependencies/additionalDependency`` (javadoc), and
  ``reportPlugins/plugin`` (old-style site). Nothing else inside
  ``<configuration>`` is read, since its shape belongs to the plugin.

A missing version, a ``${property}`` reference, a range, ``LATEST``/``RELEASE``
and ``-SNAPSHOT`` are findings. A plugin with no version takes the super-POM's
default or the newest release in the repository, so it is reported like a
dependency with no version. Both are excused only when the same file's
top-level ``build/pluginManagement`` or ``dependencyManagement`` declares the
coordinate, since that declaration is checked here too. A version managed by
a parent POM cannot be verified from this file and is reported as missing.
In a 4.1.0 model, Maven 4 infers the version of ``<parent>`` and of
dependencies on modules of the same build (written with no ``groupId``) from
the local POMs, so those are not reported as missing a version.

Elements are matched by local name, so any POM namespace (``POM/4.0.0``,
Maven 4's ``POM/4.1.0``) or none is read the same way.

XML parsing
-----------
Bandit flags stdlib XML parsing because it can be abused with entity
expansion (billion laughs) and external entities. pinstack keeps zero runtime
dependencies, so instead of defusedxml it parses with ``xml.parsers.expat``
directly and refuses any document with a DOCTYPE: the parser raises on the
doctype declaration, before an internal subset (where entities are declared)
is read. Parameter-entity parsing is also turned off and an entity
declaration handler also refuses, as defense in depth. A Maven build file has
no use for a DTD, so such a file is reported rather than parsed.

The file is decoded here (byte-order mark or XML spec autodetection, then
the declared encoding where Python has a text codec for it, else UTF-8) with
undecodable bytes replaced, so an unusual or wrong encoding cannot stop the
file from being checked. Given text, the parser ignores the declaration.
"""

from __future__ import annotations

import codecs
import os
import re
from dataclasses import dataclass, field
from typing import Optional
from xml.parsers import expat

from pinstack.core import Checker, Finding, FileIndex

_EXTENSIONS_FILE = "extensions.xml"
_EXTENSIONS_DIR = ".mvn"
_DEFAULT_PLUGIN_GROUP = "org.apache.maven.plugins"
_MODEL_410_RE = re.compile(r"/POM/4\.1\.0$")
_ENCODING_DECL_RE = re.compile(
    r"""^\s*<\?xml\b[^>]*?\sencoding\s*=\s*(["'])([A-Za-z0-9._-]+)\1"""
)
# Byte-order marks, longest first (UTF-32 LE starts with the UTF-16 LE mark),
# then the XML spec's autodetection of "<?" in the four 16/32-bit encodings.
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
    (b"\x00\x00\x00<", "utf-32-be"),
    (b"<\x00\x00\x00", "utf-32-le"),
    (b"\x00<\x00?", "utf-16-be"),
    (b"<\x00?\x00", "utf-16-le"),
)

# (container, element) pairs that name a coordinate, outside <configuration>.
_STRUCTURAL = {
    ("dependencies", "dependency"): "dependency",
    ("plugins", "plugin"): "plugin",
    ("extensions", "extension"): "extension",
}
# The same, for artifacts a plugin resolves from inside its <configuration>.
_CONFIGURATION = {
    ("annotationProcessorPaths", "path"): "configuration",
    ("tagletArtifacts", "tagletArtifact"): "configuration",
    ("taglet", "tagletArtifact"): "configuration",
    ("configuration", "tagletArtifact"): "configuration",
    ("docletArtifacts", "docletArtifact"): "configuration",
    ("configuration", "docletArtifact"): "configuration",
    ("additionalDependencies", "additionalDependency"): "configuration",
    ("reportPlugins", "plugin"): "plugin",
}
_MANAGEMENT = frozenset(["pluginManagement", "dependencyManagement"])
# Message prefix per kind; dependencies keep the original wording.
_KIND_LABEL = {
    "dependency": "",
    "plugin": "plugin ",
    "extension": "extension ",
    "parent": "parent ",
    "configuration": "plugin configuration artifact ",
}


@dataclass
class _Node:
    """One XML element: its local name, start line, text and child elements."""

    name: str
    line: int
    order: int  # position in document order
    chunks: list[str] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.chunks)

    def child(self, name: str) -> Optional[_Node]:
        for child in self.children:
            if child.name == name:
                return child
        return None

    def child_text(self, name: str) -> Optional[str]:
        node = self.child(name)
        return node.text.strip() if node is not None else None


class _DoctypeRefused(Exception):
    """The document has a DOCTYPE (or an entity declaration)."""


def _decode(content: bytes) -> str:
    """Decode an XML file to text.

    A byte-order mark (or the XML spec's first-bytes autodetection) wins,
    then the XML declaration's ``encoding`` when Python can decode it as
    text, then UTF-8. Undecodable bytes are replaced.
    """
    for prefix, name in _BOMS:
        if content.startswith(prefix):
            return content.decode(name, errors="replace")
    m = _ENCODING_DECL_RE.match(content[:200].decode("ascii", errors="replace"))
    if m:
        try:
            return content.decode(m.group(2), errors="replace")
        except (LookupError, UnicodeError):
            pass  # unknown, or not a text codec (base64, rot13, ...)
    return content.decode("utf-8", errors="replace")


def _parse(text: str) -> tuple[_Node, Optional[str]]:
    """Parse XML into a tree of local-named nodes with line numbers.

    :returns: The root node and the root's namespace URI (or None).
    :raises _DoctypeRefused: when the document has a DOCTYPE.
    :raises expat.ExpatError: when the document is not well-formed.
    """
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.buffer_text = True
    stack: list[_Node] = []
    roots: list[_Node] = []
    namespaces: list[Optional[str]] = []
    order: list[_Node] = []

    def refuse(*_args: object) -> None:
        raise _DoctypeRefused()

    def start(name: str, _attrs: object) -> None:
        uri, sep, local = name.rpartition("}")
        node = _Node(
            name=local if sep else name,
            line=parser.CurrentLineNumber,
            order=len(order),
        )
        order.append(node)
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
            namespaces.append(uri if sep else None)
        stack.append(node)

    def end(_name: str) -> None:
        stack.pop()

    def chars(data: str) -> None:
        if stack:
            stack[-1].chunks.append(data)

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.Parse(text, True)
    return roots[0], namespaces[0]


def _version_problem(version: Optional[str]) -> Optional[str]:
    """Return why a version string is not an exact pin, or None if it is.

    The message is completed with the coordinate by the caller.
    """
    if not version:
        return "has no <version>; specify an explicit version"
    if "${" in version:
        return (
            "version uses property reference '{}' which cannot be"
            " verified; consider using an explicit version".format(version)
        )
    if any(c in version for c in "[]()"):
        return "uses a version range '{}'; pin to an exact version".format(version)
    if version in ("LATEST", "RELEASE"):
        return "uses '{}'; pin to an exact version".format(version)
    if version.endswith("-SNAPSHOT"):
        return "uses SNAPSHOT version '{}'; pin to a released version".format(version)
    return None


def _coordinates(root: _Node) -> list[tuple[str, _Node, bool]]:
    """Return ``(kind, element, managed)`` for every coordinate Maven resolves.

    ``managed`` is True for an element inside ``pluginManagement`` or
    ``dependencyManagement``. The walk is iterative, so deeply nested files
    cannot exhaust the stack.
    """
    found: list[tuple[str, _Node, bool]] = []
    parent = root.child("parent") if root.name == "project" else None
    if parent is not None:
        found.append(("parent", parent, False))
    # (node, inside <configuration>, inside a management section)
    todo: list[tuple[_Node, bool, bool]] = [(root, False, False)]
    while todo:
        node, in_config, in_mgmt = todo.pop()
        table = _CONFIGURATION if in_config else _STRUCTURAL
        for child in node.children:
            kind = table.get((node.name, child.name))
            if kind is not None:
                found.append((kind, child, in_mgmt))
            todo.append(
                (
                    child,
                    in_config or child.name == "configuration",
                    in_mgmt or child.name in _MANAGEMENT,
                )
            )
    found.sort(key=lambda item: item[1].order)
    return found


def _coordinate_key(kind: str, elem: _Node) -> str:
    group = elem.child_text("groupId") or (
        _DEFAULT_PLUGIN_GROUP if kind == "plugin" else ""
    )
    return "{}:{}".format(group, elem.child_text("artifactId") or "")


def _managed_here(root: _Node) -> set[tuple[str, str]]:
    """Return ``(kind, group:artifact)`` declared with a version in this POM's
    top-level ``build/pluginManagement`` or ``dependencyManagement``.

    Management sections inside profiles apply only when the profile is
    active, so they do not count.
    """
    keys: set[tuple[str, str]] = set()
    sections = [
        ("plugin", root.child("build"), "pluginManagement", "plugins"),
        ("dependency", root, "dependencyManagement", "dependencies"),
    ]
    for kind, owner, section_name, container_name in sections:
        section = owner.child(section_name) if owner is not None else None
        container = section.child(container_name) if section is not None else None
        if container is None:
            continue
        for elem in container.children:
            if elem.name == kind and elem.child_text("version"):
                keys.add((kind, _coordinate_key(kind, elem)))
    return keys


def _is_model_410(root: _Node, namespace: Optional[str]) -> bool:
    if namespace and _MODEL_410_RE.search(namespace):
        return True
    return root.child_text("modelVersion") == "4.1.0"


class MavenChecker(Checker):
    name = "maven"
    description = (
        "Checks Maven POMs and core extensions for unpinned, dynamic, or SNAPSHOT"
        " dependency, plugin, extension and parent versions"
    )
    patterns: list[str] = [
        "pom.xml",
        _EXTENSIONS_DIR + "/" + _EXTENSIONS_FILE,
        "*/" + _EXTENSIONS_DIR + "/" + _EXTENSIONS_FILE,
    ]

    def check(self, index: FileIndex, root: str) -> list[Finding]:
        findings: list[Finding] = []

        for dir_path in sorted(index.keys()):
            in_mvn_dir = os.path.basename(os.path.normpath(dir_path)) == _EXTENSIONS_DIR
            for fname in sorted(index[dir_path]):
                if fname == "pom.xml":
                    expected_root = "project"
                elif fname == _EXTENSIONS_FILE and in_mvn_dir:
                    expected_root = "extensions"
                else:
                    continue
                full_path = os.path.join(dir_path, fname)
                findings.extend(
                    self._check_file(
                        full_path,
                        os.path.relpath(full_path, root),
                        fname,
                        expected_root,
                    )
                )

        return findings

    def _load(self, path: str) -> tuple[_Node, Optional[str]]:
        """Read and parse one file; errors propagate to the caller."""
        with open(path, "rb") as fh:
            return _parse(_decode(fh.read()))

    def _check_file(
        self, full_path: str, rel_path: str, fname: str, expected_root: str
    ) -> list[Finding]:
        def finding(message: str, line: int = 0) -> Finding:
            return Finding(checker=self.name, path=rel_path, line=line, message=message)

        try:
            tree, namespace = self._load(full_path)
        except OSError:
            return []
        except _DoctypeRefused:
            return [
                finding(
                    "{} has a DOCTYPE, which pinstack does not parse; remove it".format(
                        fname
                    )
                )
            ]
        except expat.ExpatError:
            return [finding("{} could not be parsed as XML".format(fname))]
        if tree.name != expected_root:
            return [
                finding(
                    "{} has root element <{}>, not <{}>, so it was not checked".format(
                        fname, tree.name, expected_root
                    ),
                    tree.line,
                )
            ]

        model_410 = _is_model_410(tree, namespace)
        managed_here = _managed_here(tree) if tree.name == "project" else set()
        findings: list[Finding] = []
        for kind, elem, managed in _coordinates(tree):
            version = elem.child_text("version")
            if not version:
                if kind == "parent" and model_410:
                    continue  # inferred from the local parent POM
                if (
                    kind == "dependency"
                    and model_410
                    and not elem.child_text("groupId")
                ):
                    continue  # a module of the same build (Maven 4)
                if not managed and (kind, _coordinate_key(kind, elem)) in managed_here:
                    continue  # this file's management section is checked
            problem = _version_problem(version)
            if problem is None:
                continue
            group = elem.child_text("groupId") or (
                _DEFAULT_PLUGIN_GROUP if kind == "plugin" else "unknown"
            )
            artifact = elem.child_text("artifactId") or "unknown"
            version_elem = elem.child("version")
            line = version_elem.line if version and version_elem else elem.line
            findings.append(
                finding(
                    "{}'{}:{}' {}".format(_KIND_LABEL[kind], group, artifact, problem),
                    line,
                )
            )
        return findings
