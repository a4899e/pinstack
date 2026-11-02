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

    def test_anchor_on_mapping_keeps_nesting(self):
        assert _rows("base: &b\n  image: i\nsvc:\n  <<: *b\n") == [
            ("base.image", "i", 2, False),
            ("svc.<<", "*b", 4, True),
        ]

    def test_documents_reset_the_path(self):
        assert _rows("a:\n  b: 1\n---\nc: 2\n") == [
            ("a.b", "1", 2, False),
            ("c", "2", 4, False),
        ]

    def test_item_marker(self):
        assert ITEM == "-"
