# src/recon/ingestion/parsers/camt053/xpath.py
"""XPath expressions and namespace handling for CAMT.053 (R26/R27).

ISO 20022 CAMT.053 messages are namespace-qualified
(urn:iso:std:iso:20022:tech:xsd:camt.053.001.08 in the D10 sample data's
described version). Every XPath call in this parser goes through the
helpers here rather than hardcoding the namespace URI at each call site,
so a future statement using a different camt.053.001.0X minor version
only needs a change here.
"""

from __future__ import annotations

from lxml import etree

CAMT053_NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"
_NS = {"c": CAMT053_NAMESPACE}


def find(element: etree._Element, path: str) -> etree._Element | None:
    result = element.find(path, namespaces=_NS)
    return result


def find_all(element: etree._Element, path: str) -> list[etree._Element]:
    return element.findall(path, namespaces=_NS)


def text_of(element: etree._Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None
