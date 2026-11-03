"""Tests for the dependency-free YAML reader used by the YAML checkers."""

from pinstack.yaml_scan import ITEM, scalars


def _rows(text):
    # type: (str) -> list
    return [(".".join(s.path), s.value, s.line, s.alias) for s in scalars(text)]


class TestBlockStyle:
    def test_nested_mappings_and_sequences(self):
        text = "a:\n  b: 1\n  c:\n    - x\n    - d: 2\n      e: 3\n"
        assert _rows(text) == [
            ("a.b", "1", 2, False),
            ("a.c.-", "x", 4, False),
            ("a.c.-.d", "2", 5, False),
            ("a.c.-.e", "3", 6, False),
        ]

    def test_sequence_at_key_indentation(self):
        text = "steps:\n- uses: a\n- uses: b\nnext: c\n"
        assert _rows(text) == [
            ("steps.-.uses", "a", 2, False),
            ("steps.-.uses", "b", 3, False),
            ("next", "c", 4, False),
        ]

    def test_quoted_keys_values_and_comments(self):
        text = "\"uses\": 'a@b' # tail\n'k': \"v # not a comment\"\n# whole line\n"
        assert _rows(text) == [
            ("uses", "a@b", 1, False),
            ("k", "v # not a comment", 2, False),
        ]

    def test_value_on_next_line(self):
        assert _rows("image:\n  nginx:1.25\n") == [("image", "nginx:1.25", 2, False)]

    def test_colon_inside_plain_value(self):
        assert _rows("image: node:20\n") == [("image", "node:20", 1, False)]


class TestBlockScalars:
    def test_literal_content_is_a_value_not_keys(self):
        text = "run: |\n  uses: fake@v1\n  image: fake\nshell: bash\n"
        assert _rows(text) == [
            ("run", "uses: fake@v1\nimage: fake", 1, False),
            ("shell", "bash", 4, False),
        ]

    def test_folded_value_is_joined(self):
        assert _rows("uses: >-\n  owner/act@v1\n") == [
            ("uses", "owner/act@v1", 1, False)
        ]

    def test_block_scalar_at_end_of_file(self):
        assert _rows("- run: |\n    echo hi\n") == [("-.run", "echo hi", 1, False)]


class TestFlowStyle:
    def test_flow_mapping_in_sequence(self):
        assert _rows("steps:\n  - {uses: a@v1, with: {x: 1}}\n") == [
            ("steps.-.uses", "a@v1", 2, False),
            ("steps.-.with.x", "1", 2, False),
        ]

    def test_nested_flow_on_one_line(self):
        assert _rows("services: {web: {image: nginx:1.25}, db: {ports: [1, 2]}}\n") == [
            ("services.web.image", "nginx:1.25", 1, False),
            ("services.db.ports.-", "1", 1, False),
            ("services.db.ports.-", "2", 1, False),
        ]

    def test_flow_spanning_lines(self):
        text = '- {\n    name: n,\n    uses: "a@v1"\n  }\nafter: x\n'
        assert _rows(text) == [
            ("-.name", "n", 2, False),
            ("-.uses", "a@v1", 3, False),
            ("after", "x", 5, False),
        ]

    def test_quoted_flow_key(self):
        assert _rows('{"uses": a@v1}\n') == [("uses", "a@v1", 1, False)]


class TestAnchorsAndDocuments:
    def test_alias_resolves_to_anchored_scalar(self):
        assert _rows("a: &x val\nb: *x\nc: *nope\n") == [
            ("a", "val", 1, False),
            ("b", "val", 2, False),
            ("c", "*nope", 3, True),
        ]

    def test_merge_key_expands_anchored_mapping(self):
        assert _rows("base: &b\n  image: i\nsvc:\n  <<: *b\n") == [
            ("base.image", "i", 2, False),
            ("svc.image", "i", 4, False),
        ]

    def test_documents_reset_the_path(self):
        assert _rows("a:\n  b: 1\n---\nc: 2\n") == [
            ("a.b", "1", 2, False),
            ("c", "2", 4, False),
        ]

    def test_item_marker(self):
        assert ITEM == "-"


def _copied(text):
    # type: (str) -> list
    return [(".".join(s.path), s.value, s.line) for s in scalars(text) if s.copied]


class TestCollectionAliases:
    """Codex review of #19: an alias of a mapping or sequence is expanded where used."""

    def test_anchor_alone_on_item_names_its_mapping(self):
        text = "a:\n  - &x\n    k: v\nb:\n  - *x\n"
        assert _rows(text) == [("a.-.k", "v", 3, False), ("b.-.k", "v", 5, False)]
        assert _copied(text) == [("b.-.k", "v", 5)]

    def test_anchored_sequence(self):
        text = "a: &s\n  - k: 1\n  - k: 2\nb: *s\n"
        assert _copied(text) == [("b.-.k", "1", 4), ("b.-.k", "2", 4)]

    def test_flow_anchor(self):
        text = "a: {x: &f {image: i}, y: *f}\n"
        assert _rows(text) == [
            ("a.x.image", "i", 1, False),
            ("a.y.image", "i", 1, False),
        ]

    def test_merge_list(self):
        text = "a: &a\n  p: 1\nb: &b\n  q: 2\nc:\n  <<: [*a, *b]\n"
        assert _copied(text) == [("c.p", "1", 6), ("c.q", "2", 6)]

    def test_nested_aliases_expand(self):
        text = "a: &a\n  k: v\nb: &b\n  m: *a\nc: *b\n"
        assert _copied(text)[-1] == ("c.m.k", "v", 5)

    def test_alias_inside_its_own_anchor_is_unresolved(self):
        assert _rows("a: &a\n  b: *a\n") == [("a.b", "*a", 2, True)]

    def test_scalar_anchor_replaces_collection_anchor(self):
        text = "a: &x\n  k: v\nb: &x s\nc: *x\n"
        assert _rows(text)[-1] == ("c", "s", 4, False)

    def test_expansion_is_bounded(self):
        """Nested aliases cannot make the output grow without bound."""
        from pinstack.yaml_scan import _MAX_COPIED

        lines = ["l0: &l0 [" + ", ".join(["x"] * 10) + "]"]
        for n in range(1, 8):
            lines.append(
                "l{}: &l{} [{}]".format(n, n, ", ".join(["*l{}".format(n - 1)] * 10))
            )
        rows = scalars("\n".join(lines) + "\n")
        assert sum(s.copied for s in rows) <= _MAX_COPIED
        assert any(s.alias for s in rows)


class TestFlowDepth:
    """Deep flow nesting is reported, not a crash."""

    def test_too_deep_raises(self):
        import pytest

        from pinstack.yaml_scan import MAX_FLOW_DEPTH, YamlTooDeep

        with pytest.raises(YamlTooDeep) as exc:
            scalars("a: 1\nb: " + "[" * 5000 + "]" * 5000 + "\n")
        assert exc.value.line == 2
        depth = MAX_FLOW_DEPTH
        assert _rows("a: " + "[" * depth + "x" + "]" * depth + "\n") == [
            ("a" + ".-" * depth, "x", 1, False)
        ]

    def test_anchored_nesting_counts_once_per_collection(self):
        from pinstack.yaml_scan import MAX_FLOW_DEPTH

        depth = MAX_FLOW_DEPTH
        text = "a: " + "&n [" * depth + "x" + "]" * depth + "\n"
        assert [s.value for s in scalars(text)] == ["x"]


class TestAnchorReview:
    """Local review of the collection-alias change."""

    def test_anchor_naming_scalar_on_next_line(self):
        text = "x-img: &img\n  nginx:latest\ny: *img\n"
        assert _rows(text)[-1] == ("y", "nginx:latest", 3, False)

    def test_later_inner_anchor_wins(self):
        text = "x: &a\n  y: &a inner\nz: *a\n"
        assert _rows(text)[-1] == ("z", "inner", 3, False)
        text = "x: &a\n  y: &a\n    uses: u\nz:\n  - *a\n"
        assert _copied(text) == [("z.-.uses", "u", 5)]

    def test_anchors_do_not_cross_documents(self):
        assert _rows("a: &x v\n---\nb: *x\n")[-1] == ("b", "*x", 3, True)

    def test_properties_before_a_key(self):
        assert _rows("s:\n  - &c uses: u\n  - !!str k: v\n") == [
            ("s.-.uses", "u", 2, False),
            ("s.-.k", "v", 3, False),
        ]
        assert _rows("x: &c\n  !!str uses: u\n") == [("x.uses", "u", 2, False)]

    def test_properties_before_a_flow_item(self):
        assert _rows("s:\n  - &c {uses: u}\n") == [("s.-.uses", "u", 2, False)]
        assert _rows("s: [&c {uses: u}, !!map {k: v}, *c]\n") == [
            ("s.-.uses", "u", 1, False),
            ("s.-.k", "v", 1, False),
            ("s.-.uses", "u", 1, False),
        ]
        assert _rows("m: {&k a: 1}\n") == [("m.a", "1", 1, False)]

    def test_budget_counts_path_length(self):
        """Deeply nested copies are charged for their whole path."""
        from pinstack.yaml_scan import _MAX_COPIED

        deep = "".join("  " * i + "k{}:\n".format(i) for i in range(400))
        items = "".join("  " * 400 + "- v\n" for _ in range(100))
        anchor = "a: &a\n" + "".join(
            "  " + line + "\n" for line in (deep + items).splitlines()
        )
        aliases = "".join("b{}: *a\n".format(n) for n in range(100))
        rows = scalars(anchor + aliases)
        assert sum(len(r.path) for r in rows if r.copied) <= _MAX_COPIED
        assert any(r.alias for r in rows)
