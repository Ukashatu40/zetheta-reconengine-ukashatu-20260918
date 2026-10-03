# src/recon/cli/create_api_key.py
"""Creates an API key and prints it ONCE. Only its hash is stored."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy.exc import IntegrityError

from recon.persistence.models import ApiKey
from recon.persistence.models.security import ROLE_VALUES
from recon.persistence.session import get_engine, get_session_factory
from recon.security.api_keys import generate_api_key, hash_api_key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an API key")
    parser.add_argument("--name", required=True, help="unique label; appears in the audit trail")
    parser.add_argument("--role", required=True, choices=ROLE_VALUES)
    args = parser.parse_args(argv)

    raw_key = generate_api_key()
    with get_session_factory(get_engine())() as session:
        session.add(ApiKey(key_hash=hash_api_key(raw_key), name=args.name, role=args.role))
        try:
            session.commit()
        except IntegrityError:
            print(f"a key named {args.name!r} already exists", file=sys.stderr)
            return 1
    print(raw_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
