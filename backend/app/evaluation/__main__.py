"""CLI: python -m app.evaluation [--judge mock|cloudflare] [--out PATH] [--case KEY ...] [--persist]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .runner import run_suite


async def _main(argv: list[str]) -> int:
    import logging

    logging.basicConfig(level=logging.ERROR)
    ap = argparse.ArgumentParser(prog="python -m app.evaluation")
    ap.add_argument("--judge", choices=["mock", "cloudflare"], default="mock")
    ap.add_argument("--out", default="")
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--persist", action="store_true", help="store the run in DATABASE_URL")
    args = ap.parse_args(argv)

    judge = None
    if args.judge == "cloudflare":
        from ..config import Settings
        from ..providers.factory import build_judge_llm
        from .judge import LLMJudge

        s = Settings(judge_provider="cloudflare")
        llm = build_judge_llm(s)
        if llm is None:
            print(
                "cloudflare judge requested but CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN are not set", file=sys.stderr
            )
            return 2
        judge = LLMJudge(llm)
    report = await run_suite(args.case or None, judge)
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  {r['key']:<28} {r['title']}")
        for c in r["invariants"]:
            if not c["passed"]:
                print(f"        x {c['name']}: {c['detail']}")
    print(f"\n{report['passed']}/{report['total']} cases passed  (judge: {report['judge']['provider']})")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if args.persist:
        from ..config import get_settings
        from ..persistence.db import init_schema, make_engine
        from ..persistence.repository import Repository
        from .store import save_report

        s = get_settings()
        engine = make_engine(s.database_url)
        await init_schema(engine, s.database_url, True)
        await save_report(Repository(engine), report)
        await engine.dispose()
    return 0 if report["passed"] == report["total"] else 1


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
