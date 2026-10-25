"""Generates docs/API.md from the running application's OpenAPI schema, so the endpoint
reference cannot drift from the code.

    python scripts/gen_api_docs.py            # rewrites docs/API.md
    python scripts/gen_api_docs.py --check    # exits 1 if the committed file is out of date
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from recon.api import auth
from recon.api.main import app

_OUT = Path(__file__).resolve().parents[1] / "docs" / "API.md"
_ROLE_BY_DEPENDENCY = {
    auth.require_viewer: "VIEWER",
    auth.require_analyst: "ANALYST",
    auth.require_admin: "ADMIN",
}
_ROLE_ORDER = {"public": 0, "VIEWER": 1, "ANALYST": 2, "ADMIN": 3}


def _roles(dependant: Dependant) -> list[str]:
    found = [_ROLE_BY_DEPENDENCY[dependant.call]] if dependant.call in _ROLE_BY_DEPENDENCY else []
    for sub in dependant.dependencies:
        found.extend(_roles(sub))
    return found


def _required_role(route: APIRoute, router_dependencies: list[Any]) -> str:
    roles = _roles(route.dependant)
    roles += [
        _ROLE_BY_DEPENDENCY[d.dependency]
        for d in router_dependencies
        if d.dependency in _ROLE_BY_DEPENDENCY
    ]
    return max(roles, key=_ROLE_ORDER.__getitem__) if roles else "public"


def _all_routes() -> dict[tuple[str, str], str]:
    """(method, path) -> required role, across the app and every included router."""
    found: dict[tuple[str, str], str] = {}
    for top in app.routes:
        router = getattr(top, "original_router", None)  # an included router
        routes = router.routes if router is not None else [top]
        shared = router.dependencies if router is not None else []
        for route in routes:
            if isinstance(route, APIRoute):
                for method in route.methods:
                    found[(method, route.path)] = _required_role(route, shared)
    return found


def _params(op: dict[str, Any]) -> str:
    names = [
        f"`{p['name']}`" + ("*" if p.get("required") else "")
        for p in op.get("parameters", [])
        if p["in"] in {"query", "header"}
    ]
    return ", ".join(names) or "none"


def render() -> str:
    spec = app.openapi()
    roles = _all_routes()
    lines = [
        "# API reference",
        "",
        f"{spec['info']['title']}, version {spec['info']['version']}.",
        "",
        "Generated from the application's OpenAPI schema by `scripts/gen_api_docs.py`;",
        "do not edit by hand. The interactive reference is served at `/docs` (Swagger UI)",
        "and `/openapi.json`.",
        "",
        "**Authentication.** Every `/api/v1` endpoint takes an `X-API-Key` header. Roles are",
        "cumulative: VIEWER reads, ANALYST also resolves exceptions, uploads files and starts",
        "runs, ADMIN also administers users, bank configurations and overrides.",
        "`/health/*` is public.",
        "Every state-changing endpoint is written to the audit trail.",
        "",
        "**Conventions.** Lists return `{items, total, limit, offset}`. Errors are `{detail}` with",
        "400/401/403/404/409/422/429. Run creation requires an `Idempotency-Key` header.",
        "Parameters marked `*` are required. Deprecated routes are kept as aliases of the",
        "spec-named ones.",
        "",
    ]
    by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            tag = (op.get("tags") or ["other"])[0]
            by_tag.setdefault(tag, []).append((method.upper(), path, op))
    for tag in sorted(by_tag):
        lines += [
            f"## {tag}",
            "",
            "| Method | Path | Role | Parameters | Summary |",
            "|---|---|---|---|---|",
        ]
        for method, path, op in by_tag[tag]:
            summary = op.get("summary", "") + (" (deprecated)" if op.get("deprecated") else "")
            lines.append(
                f"| {method} | `{path}` | {roles[(method, path)]} | {_params(op)} | {summary} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        current = _OUT.read_text(encoding="utf-8") if _OUT.exists() else ""
        if current != text:
            sys.stderr.write("docs/API.md is out of date; run scripts/gen_api_docs.py\n")
            return 1
        return 0
    _OUT.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {_OUT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
