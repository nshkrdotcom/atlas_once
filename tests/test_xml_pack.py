"""Tests for the shared XML pack serializer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from atlas_once.xml_pack import (
    PackFile,
    escape_attr,
    render_file_element,
    render_pack,
    render_packs,
    wrap_cdata,
)


def test_render_pack_is_well_formed_xml():
    files = [
        PackFile(path="lib/a.ex", content="defmodule A do\nend\n", project="app"),
        PackFile(path="lib/b.ex", content="defmodule B do\nend\n", project="app"),
    ]
    xml = render_pack(files, kind="ranked", meta={"preset": "core"})
    root = ET.fromstring(xml)  # raises if malformed
    assert root.tag == "pack"
    assert root.attrib["kind"] == "ranked"
    assert root.attrib["preset"] == "core"
    assert root.attrib["file_count"] == "2"
    file_els = root.find("files").findall("file")
    assert [f.attrib["path"] for f in file_els] == ["lib/a.ex", "lib/b.ex"]
    assert "defmodule A" in file_els[0].text


def test_cdata_split_escapes_embedded_terminator():
    # Source that literally contains ]]> must not break the CDATA section.
    nasty = 'x = "]]>" # tricky\n'
    xml = render_pack([PackFile(path="p.ex", content=nasty)])
    root = ET.fromstring(xml)  # must still parse
    text = root.find("files").find("file").text
    assert "]]>" in text  # content round-trips intact
    assert "]]]]>" in wrap_cdata(nasty)  # the split-escape happened


def test_attributes_are_escaped():
    xml = render_pack([PackFile(path='a&b<c>"d".ex', content="x")])
    root = ET.fromstring(xml)
    assert root.find("files").find("file").attrib["path"] == 'a&b<c>"d".ex'
    assert escape_attr('&<>"') == "&amp;&lt;&gt;&quot;"


def test_optional_attrs_present_only_when_known():
    with_meta = render_file_element(
        PackFile(path="p", content="c", project="app", byte_size=10, token_estimate=3, rank=0.5)
    )
    assert 'project="app"' in with_meta
    assert 'bytes="10"' in with_meta
    assert 'tokens="3"' in with_meta
    assert 'rank="0.500000"' in with_meta

    bare = render_file_element(PackFile(path="p", content="c"))
    assert "project=" not in bare
    assert "bytes=" not in bare
    assert "rank=" not in bare


def test_render_packs_wraps_multiple_documents():
    p1 = render_pack([PackFile(path="a", content="1")], kind="stack")
    p2 = render_pack([PackFile(path="b", content="2")], kind="stack")
    combined = render_packs([p1, p2])
    root = ET.fromstring(combined)
    assert root.tag == "packs"
    assert root.attrib["pack_count"] == "2"
    assert len(root.findall("pack")) == 2


def test_warnings_block_is_well_formed():
    xml = render_pack(
        [PackFile(path="a.ex", content="x")],
        warnings=["missing file at render time: /gone/b.ex"],
    )
    root = ET.fromstring(xml)
    warns = root.find("warnings").findall("warning")
    assert len(warns) == 1
    assert "missing file" in warns[0].text
    assert len(root.find("files").findall("file")) == 1


def test_content_round_trips_exactly():
    content = 'a\n  b\t"c" <tag> & ampersand\n'
    xml = render_pack([PackFile(path="p.ex", content=content)])
    root = ET.fromstring(xml)
    assert root.find("files").find("file").text == content
