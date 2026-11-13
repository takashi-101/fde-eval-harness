"""Automated Evals & Token-Cost (AI FinOps) harness for FDE deployments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    prompt: str
    required_keywords: List[str]
    forbidden_keywords: List[str]
    expected_json_keys: List[str]


@dataclass(frozen=True)
class EvalResult:
    case_id: str
    passed: bool
    reasons: List[str]
    input_tokens_est: int
    output_tokens_est: int
    estimated_cost_jpy: float


@dataclass(frozen=True)
class EvalReport:
    total_cases: int
    passed_cases: int
    pass_rate: float
    total_cost_jpy: float
    results: List[EvalResult]


def estimate_tokens(text: str) -> int:
    # Conservative char-based approximation for mixed JP/EN offline estimation
    return max(1, len(text) // 2)


def evaluate_output(
    case: EvalCase,
    raw_output: str,
    jpy_per_1k_tokens: float = 0.45,
) -> EvalResult:
    reasons: List[str] = []

    for kw in case.required_keywords:
        if kw not in raw_output:
            reasons.append(f"missing_required:{kw}")
    for kw in case.forbidden_keywords:
        if kw in raw_output:
            reasons.append(f"forbidden_present:{kw}")

    if case.expected_json_keys:
        try:
            parsed = json.loads(raw_output)
            if not isinstance(parsed, dict):
                reasons.append("json_not_object")
            else:
                for k in case.expected_json_keys:
                    if k not in parsed:
                        reasons.append(f"missing_json_key:{k}")
        except json.JSONDecodeError:
            reasons.append("invalid_json")

    in_tok = estimate_tokens(case.prompt)
    out_tok = estimate_tokens(raw_output)
    cost = round(((in_tok + out_tok) / 1000.0) * jpy_per_1k_tokens, 4)

    return EvalResult(
        case_id=case.case_id,
        passed=(len(reasons) == 0),
        reasons=reasons,
        input_tokens_est=in_tok,
        output_tokens_est=out_tok,
        estimated_cost_jpy=cost,
    )


def run_evaluation(
    cases: List[EvalCase],
    predictor: Callable[[str], str],
    jpy_per_1k_tokens: float = 0.45,
) -> EvalReport:
    results = [evaluate_output(c, predictor(c.prompt), jpy_per_1k_tokens) for c in cases]
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    return EvalReport(
        total_cases=total,
        passed_cases=passed,
        pass_rate=round(passed / total, 4) if total else 0.0,
        total_cost_jpy=round(sum(r.estimated_cost_jpy for r in results), 4),
        results=results,
    )


def load_cases_jsonl(path: Path | str) -> List[EvalCase]:
    cases: List[EvalCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        cases.append(
            EvalCase(
                case_id=obj["case_id"],
                prompt=obj["prompt"],
                required_keywords=obj.get("required_keywords", []),
                forbidden_keywords=obj.get("forbidden_keywords", []),
                expected_json_keys=obj.get("expected_json_keys", []),
            )
        )
    return cases
