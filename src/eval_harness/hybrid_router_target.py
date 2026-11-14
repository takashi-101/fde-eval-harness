"""Adapter that runs fde-hybrid-router (loaded from a path, not vendored) as an evaluation target.

Each case gets a fresh SQLite DB. The cloud and local backends are wrapped so that every text the
router hands to a backend is recorded; the backends themselves are the router's own:
cloud = its fixed-response sample, local = Ollama with its sample as fallback.
"""

from __future__ import annotations

import importlib
import subprocess  # nosec B404 - only runs "git" with fixed arguments
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from .routing_eval import Observation, RoutingCase, Target


class TargetNotFound(RuntimeError):
    pass


def _git_commit(repo_dir: Path) -> str:
    try:
        head = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return head + ("（未コミットの変更あり）" if dirty else "")


def load_hybrid_router_target(
    router_src: Path | str,
    model: Optional[str] = None,
    ollama_url: Optional[str] = None,
    threshold: float = 0.80,
    timeout: float = 120.0,
) -> Tuple[Target, Dict[str, str]]:
    src = Path(router_src).resolve()
    if not (src / "hybrid_router" / "__init__.py").is_file():
        raise TargetNotFound(f"fde-hybrid-router の src が見つかりません：{src}")
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    hr = importlib.import_module("hybrid_router")
    backends = importlib.import_module("hybrid_router.backends")

    ollama = backends.OllamaLocalBackend(base_url=ollama_url, model=model, timeout=timeout)
    local_with_fallback = backends.FallbackBackend(ollama, backends.sample_local_backend)

    def target(case: RoutingCase) -> Observation:
        sent_cloud, sent_local = [], []

        def cloud(category_hint: str, text: str):
            sent_cloud.append(f"{category_hint}\n{text}")
            return backends.sample_cloud_backend(category_hint, text)

        def local(category_hint: str, text: str):
            sent_local.append(f"{category_hint}\n{text}")
            return local_with_fallback(category_hint, text)

        with tempfile.TemporaryDirectory() as tmp:
            repo = hr.InquiryRepository(Path(tmp) / "eval.db")
            repo.insert_inquiry(hr.InquiryRecord(case.case_id, case.category, case.body))
            router = hr.HybridFDERouter(
                repo, confidence_threshold=threshold, cloud_backend=cloud, local_backend=local
            )
            outcome = router.process_pending()[0]
            rows = {r["ticket_id"]: r for r in repo.fetch_results()}
            queue = {r["ticket_id"] for r in repo.fetch_human_queue()}

        row = rows.get(case.case_id)
        return Observation(
            classification=outcome.decision.classification.value,
            route=outcome.decision.target.value,
            review=outcome.review_status.value,
            confidence=outcome.confidence,
            threshold=threshold,
            answered_by=outcome.answered_by,
            fallback_reason=outcome.fallback_reason,
            draft_answer=outcome.draft_answer,
            sent_to_cloud=tuple(sent_cloud),
            sent_to_local=tuple(sent_local),
            recorded_answered_by=row["answered_by"] if row else None,
            in_human_queue=case.case_id in queue,
        )

    meta = {
        "target_name": "fde-hybrid-router",
        "target_commit": _git_commit(src.parent),
        "llm": f"Ollama `{ollama.base_url}`、モデル `{ollama.model}`",
    }
    return target, meta
