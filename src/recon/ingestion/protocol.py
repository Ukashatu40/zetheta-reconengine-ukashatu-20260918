# src/recon/ingestion/protocol.py
"""Parser Protocol: the common interface every format-specific parser
(CSVParser, MT940Parser, CAMT053Parser) implements.

PDF Day 1 groups these conceptually as:

    Parser
    ├── CSVParser
    ├── MT940Parser
    └── CAMT053Parser

Kept as a structural Protocol (not an ABC) — a parser only needs to match
the shape, no inheritance required.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import IO, Protocol

from recon.config.models import BankConfig
from recon.ingestion.parsed_row import ParsedRow


class Parser(Protocol):
    def parse(self, stream: IO[str], config: BankConfig) -> Iterator[ParsedRow]: ...
