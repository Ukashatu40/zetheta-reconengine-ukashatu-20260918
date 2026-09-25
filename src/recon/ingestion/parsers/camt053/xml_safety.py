# src/recon/ingestion/parsers/camt053/xml_safety.py
"""Centralised, hardened lxml parser construction.

Every CAMT.053 file is parsed through get_safe_parser() and nothing else
in this codebase constructs an lxml.etree.XMLParser directly — that rule
is what makes this module the single place XXE/entity-expansion defences
live, rather than something each call site has to remember.

Defences applied:
  - resolve_entities=False   — external entities are never resolved,
                                closing the classic XXE file-read vector
                                (<!ENTITY xxe SYSTEM "file:///etc/passwd">)
  - no_network=True          — no network fetch is attempted for any
                                DTD/entity/schema reference, even if one
                                were somehow present
  - huge_tree=False          — lxml's built-in guard against extremely
                                deep/large trees, which also constrains
                                entity-expansion (billion-laughs) blast
                                radius
  - load_dtd=False, dtd_validation=False — no DTD is fetched or applied
                                at all; CAMT.053 does not require one

None of this is requested anywhere in the PDF. It is required regardless,
per this codebase's standing security posture on XML input.
"""

from __future__ import annotations

from lxml import etree


def get_safe_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        huge_tree=False,
        load_dtd=False,
        dtd_validation=False,
    )
