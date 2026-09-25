# tests/unit/ingestion/parsers/camt053/test_xml_safety.py
"""Proves the hardened parser actually blocks XXE and unbounded entity
expansion — not just that the flags are set, but that the attack fails.
"""

from __future__ import annotations

import io
from pathlib import Path

from lxml import etree

from recon.ingestion.parsers.camt053.xml_safety import get_safe_parser


def test_xxe_external_entity_is_not_resolved(tmp_path: Path) -> None:
    """Classic XXE: an external entity referencing a local file. With
    resolve_entities=False, lxml does not substitute the entity — the
    correct, safe outcome is that '&xxe;' either causes a well-formedness
    error, or is left unresolved and the file's content never appears
    anywhere in the parsed tree. An empty result is success, not failure.

    Uses a file we create ourselves (rather than a real system file like
    /etc/hostname) so the test is deterministic regardless of what's
    actually on the host running it.
    """
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("TOP-SECRET-VALUE-12345", encoding="utf-8")

    malicious_xml = f"""<?xml version="1.0"?>
    <!DOCTYPE root [
        <!ENTITY xxe SYSTEM "file://{secret_file}">
    ]>
    <root>&xxe;</root>
    """

    parser = get_safe_parser()
    try:
        tree = etree.parse(io.BytesIO(malicious_xml.encode()), parser=parser)
        result_text = tree.getroot().text or ""
    except etree.XMLSyntaxError:
        return  # refusing to parse at all is an acceptable, safe outcome

    assert "TOP-SECRET-VALUE-12345" not in result_text
    assert "&xxe;" not in result_text


def test_billion_laughs_entity_expansion_is_blocked() -> None:
    """Entity expansion bomb: each entity references the previous one
    multiple times, producing exponential blowup on expansion. With
    resolve_entities=False (entities never substituted at all) this
    cannot expand — parsing must fail or complete near-instantly, never
    hang or consume unbounded memory."""
    malicious_xml = """<?xml version="1.0"?>
    <!DOCTYPE lolz [
        <!ENTITY lol "lol">
        <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
        <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
    ]>
    <lolz>&lol3;</lolz>
    """

    parser = get_safe_parser()
    try:
        tree = etree.parse(io.BytesIO(malicious_xml.encode()), parser=parser)
        result_text = tree.getroot().text or ""
    except etree.XMLSyntaxError:
        return

    # If it parsed at all, the entity must NOT have expanded to its full
    # (would-be thousands-of-characters) form.
    assert len(result_text) < 100


def test_well_formed_xml_without_entities_parses_normally() -> None:
    """Sanity check: hardening must not break ordinary, well-formed XML
    that contains no DTD/entities at all — this is the normal CAMT.053
    case."""
    xml = "<root><child>value</child></root>"
    parser = get_safe_parser()
    tree = etree.parse(io.BytesIO(xml.encode()), parser=parser)
    assert tree.getroot().findtext("child") == "value"
