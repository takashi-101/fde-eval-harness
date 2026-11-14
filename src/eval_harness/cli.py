"""Run the golden cases against fde-hybrid-router and optionally write a Markdown report."""

from __future__ import annotations

import argparse
import os
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from .hybrid_router_target import TargetNotFound, load_hybrid_router_target
from .routing_eval import load_routing_cases, render_markdown, render_text, run_routing_eval

ENV_ROUTER_SRC = "FDE_HYBRID_ROUTER_SRC"


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate fde-hybrid-router with golden cases")
    parser.add_argument("--router-src", help=f"path to fde-hybrid-router/src (or env {ENV_ROUTER_SRC})")
    parser.add_argument("--cases", default="examples/router_cases.jsonl")
    parser.add_argument("--model", help="Ollama model (default: the router's default, qwen2.5:7b)")
    parser.add_argument("--ollama-url", help="Ollama base URL (default: the router's default)")
    parser.add_argument("--out", help="write the Markdown report here (e.g. RESULTS.md)")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    router_src = args.router_src or os.environ.get(ENV_ROUTER_SRC)
    if not router_src:
        print(f"error: --router-src か環境変数 {ENV_ROUTER_SRC} で fde-hybrid-router/src を指定してください",
              file=sys.stderr)
        return 2
    try:
        target, meta = load_hybrid_router_target(router_src, model=args.model, ollama_url=args.ollama_url)
    except TargetNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    cases = load_routing_cases(args.cases)
    started = datetime.now().astimezone()
    report = run_routing_eval(cases, target)
    print(render_text(report))

    if args.out:
        shown = ["python run_router_eval.py"] + [a.replace("\\", "/") for a in (argv if argv is not None else sys.argv[1:])]
        meta.update(
            command=" ".join(shown),
            run_at=started.strftime("%Y-%m-%d %H:%M (UTC%z)"),
            environment=f"Python {platform.python_version()}、{platform.system()} {platform.release()}",
            cases_path=args.cases,
        )
        Path(args.out).write_text(render_markdown(report, meta), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0 if report.ok else 1
