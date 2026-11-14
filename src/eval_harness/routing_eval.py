"""Golden-case evaluation of a routing target (e.g. fde-hybrid-router).

The target is any callable RoutingCase -> Observation, so the checks can be unit-tested with a stub
and run against the real router through an adapter (see hybrid_router_target.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

BLOCKED = "BLOCKED_HUMAN_ONLY"
AUTO_DRAFTED = "AUTO_DRAFTED"
NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
ESCALATED_SECURITY = "ESCALATED_SECURITY"

# Check name -> what it means (also printed in the Markdown report).
CHECK_DESCRIPTIONS: Dict[str, str] = {
    "classification": "機密区分が期待どおり",
    "route": "振り分け先が期待どおり",
    "review": "確認の状態が期待どおり（期待を書いたケースだけ）",
    "no_pii_to_cloud": "伏せるべき文字列が、クラウド側に渡した本文に含まれない",
    "no_pii_to_local": "伏せるべき文字列が、ローカルLLMに渡した本文に含まれない",
    "blocked_no_llm_call": "遮断した案件は、どのLLMにも渡していない",
    "low_conf_to_human": "確信度が閾値未満なら、人の確認待ちになっている",
    "human_queue_consistent": "自動回答以外の案件だけが、DBの確認待ちの表に入っている",
    "answered_by_recorded": "答えたバックエンドが結果とDBに記録されている",
    "answer_has_no_pii": "回答案に、伏せるべき文字列が含まれない",
}


@dataclass(frozen=True)
class RoutingCase:
    case_id: str
    category: str
    body: str
    expected_classification: str
    expected_route: str
    expected_review: Optional[str] = None
    must_not_send: Tuple[str, ...] = ()
    description: str = ""
    known_issue: str = ""  # non-empty: a known limitation of the target; failing is expected


@dataclass(frozen=True)
class Observation:
    """What the target did with one case."""

    classification: str
    route: str
    review: str
    confidence: float
    threshold: float
    answered_by: str
    fallback_reason: str
    draft_answer: str
    sent_to_cloud: Tuple[str, ...]
    sent_to_local: Tuple[str, ...]
    recorded_answered_by: Optional[str]  # value read back from the target's DB
    in_human_queue: bool


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: RoutingCase
    observation: Observation
    checks: List[CheckResult]

    @property
    def failed_checks(self) -> List[CheckResult]:
        return [c for c in self.checks if not c.passed]

    @property
    def verdict(self) -> str:
        ok = not self.failed_checks
        if self.case.known_issue:
            return "UNEXPECTED_PASS" if ok else "KNOWN_FAIL"
        return "PASS" if ok else "FAIL"


@dataclass(frozen=True)
class RoutingReport:
    results: List[CaseResult]
    counts: Dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """True when no case failed unexpectedly (known issues do not fail the run)."""
        return self.counts.get("FAIL", 0) == 0


Target = Callable[[RoutingCase], Observation]


def _leaks(secrets: Tuple[str, ...], texts: Tuple[str, ...]) -> List[str]:
    return [s for s in secrets if any(s in t for t in texts)]


def check_case(case: RoutingCase, obs: Observation) -> List[CheckResult]:
    checks: List[CheckResult] = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append(CheckResult(name, passed, "" if passed else detail))

    add("classification", obs.classification == case.expected_classification,
        f"expected {case.expected_classification}, got {obs.classification}")
    add("route", obs.route == case.expected_route, f"expected {case.expected_route}, got {obs.route}")
    if case.expected_review:
        add("review", obs.review == case.expected_review, f"expected {case.expected_review}, got {obs.review}")

    leaked = _leaks(case.must_not_send, obs.sent_to_cloud)
    add("no_pii_to_cloud", not leaked, f"sent to cloud: {leaked}")
    leaked = _leaks(case.must_not_send, obs.sent_to_local)
    add("no_pii_to_local", not leaked, f"sent to local LLM: {leaked}")

    if obs.route == BLOCKED:
        calls = len(obs.sent_to_cloud) + len(obs.sent_to_local)
        add("blocked_no_llm_call", calls == 0 and obs.review == ESCALATED_SECURITY,
            f"LLM calls={calls}, review={obs.review}")
    else:
        if obs.confidence < obs.threshold:
            add("low_conf_to_human", obs.review == NEEDS_HUMAN_REVIEW,
                f"confidence {obs.confidence} < {obs.threshold} but review={obs.review}")
        add("answered_by_recorded", bool(obs.answered_by) and obs.recorded_answered_by == obs.answered_by,
            f"answered_by={obs.answered_by!r}, in DB={obs.recorded_answered_by!r}")

    add("human_queue_consistent", obs.in_human_queue == (obs.review != AUTO_DRAFTED),
        f"review={obs.review}, in queue={obs.in_human_queue}")
    leaked = _leaks(case.must_not_send, (obs.draft_answer,))
    add("answer_has_no_pii", not leaked, f"in answer: {leaked}")
    return checks


def run_routing_eval(cases: List[RoutingCase], target: Target) -> RoutingReport:
    results = []
    for case in cases:
        obs = target(case)
        results.append(CaseResult(case, obs, check_case(case, obs)))
    counts = {k: 0 for k in ("PASS", "FAIL", "KNOWN_FAIL", "UNEXPECTED_PASS")}
    for r in results:
        counts[r.verdict] += 1
    counts["total"] = len(results)
    return RoutingReport(results, counts)


def load_routing_cases(path: Path | str) -> List[RoutingCase]:
    cases: List[RoutingCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        expect = obj["expect"]
        cases.append(
            RoutingCase(
                case_id=obj["case_id"],
                category=obj["category"],
                body=obj["body"],
                expected_classification=expect["classification"],
                expected_route=expect["route"],
                expected_review=expect.get("review"),
                must_not_send=tuple(obj.get("must_not_send", [])),
                description=obj.get("description", ""),
                known_issue=obj.get("known_issue", ""),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_VERDICT_JA = {
    "PASS": "合格",
    "FAIL": "不合格",
    "KNOWN_FAIL": "既知の不合格",
    "UNEXPECTED_PASS": "既知の問題なのに合格",
}


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_text(report: RoutingReport) -> str:
    lines = []
    for r in report.results:
        o = r.observation
        failed = ",".join(c.name for c in r.failed_checks) or "-"
        lines.append(
            f"{r.case.case_id:<6} {r.verdict:<15} {o.route:<18} {o.review:<19} "
            f"conf={o.confidence:<5} by={o.answered_by:<18} failed={failed}"
        )
    c = report.counts
    lines.append(
        f"total={c['total']} pass={c['PASS']} fail={c['FAIL']} "
        f"known_fail={c['KNOWN_FAIL']} unexpected_pass={c['UNEXPECTED_PASS']}"
    )
    return "\n".join(lines)


def render_markdown(report: RoutingReport, meta: Dict[str, str]) -> str:
    c = report.counts
    local = [r for r in report.results if r.observation.sent_to_local]
    by_llm = [r for r in local if r.observation.answered_by.startswith("ollama:")]

    out = [
        f"# 評価結果：{meta.get('target_name', '評価対象')}",
        "",
        f"このファイルは `{meta.get('command', '')}` の出力です（手で書き換えていません）。",
        "",
        "| 項目 | 値 |",
        "|---|---|",
        f"| 実行日時 | {meta.get('run_at', '')} |",
        f"| 評価対象 | {meta.get('target_name', '')} コミット `{meta.get('target_commit', '')}` |",
        f"| ローカルLLM | {meta.get('llm', '')} |",
        f"| 実行環境 | {meta.get('environment', '')} |",
        f"| ケース | `{meta.get('cases_path', '')}`（{c['total']}件、すべて架空） |",
        "",
        "## 集計",
        f"- 合格：{c['PASS']}件",
        f"- 不合格：{c['FAIL']}件",
        f"- 既知の不合格：{c['KNOWN_FAIL']}件（評価対象の既知の制約。下の「既知の問題」）",
        f"- 既知の問題なのに合格：{c['UNEXPECTED_PASS']}件",
        f"- ローカルLLM経路の{len(local)}件のうち、Ollamaが答えた件数：{len(by_llm)}件"
        + ("" if len(by_llm) == len(local) else "（残りは見本の応答。理由は fallback_reason）"),
        "",
        "## ケースごとの結果",
        "| ケース | 内容 | 判定 | 区分 | 経路 | 確認の状態 | 確信度 | 答えたもの | 不合格の検査 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report.results:
        o = r.observation
        failed = "、".join(f"{x.name}（{x.detail}）" for x in r.failed_checks) or "なし"
        out.append(
            f"| {r.case.case_id} | {_cell(r.case.description)} | {_VERDICT_JA[r.verdict]} | {o.classification} "
            f"| {o.route} | {o.review} | {o.confidence} | {o.answered_by} | {_cell(failed)} |"
        )

    known = [r for r in report.results if r.case.known_issue]
    if known:
        out += ["", "## 既知の問題"]
        out += [f"- {r.case.case_id}：{r.case.known_issue}" for r in known]

    sent = [r for r in report.results if r.observation.sent_to_cloud or r.observation.sent_to_local]
    if sent:
        out += ["", "## LLMに渡した本文（評価対象がバックエンドに渡したもの）"]
        for r in sent:
            o = r.observation
            for side, texts in (("クラウド側", o.sent_to_cloud), ("ローカルLLM", o.sent_to_local)):
                for t in texts:
                    category, _, body = t.partition("\n")
                    out.append(f"- {r.case.case_id} → {side}：区分「{_cell(category)}」、本文「{_cell(body)}」")

    answered = [r for r in report.results if r.observation.draft_answer]
    if answered:
        out += ["", "## 回答案（そのまま）"]
        for r in answered:
            o = r.observation
            out.append(f"- {r.case.case_id}（{o.answered_by}、確信度 {o.confidence}）：{_cell(o.draft_answer)}")

    fallbacks = [r for r in report.results if r.observation.fallback_reason]
    if fallbacks:
        out += ["", "## 見本に切り替えた理由"]
        out += [f"- {r.case.case_id}：{_cell(r.observation.fallback_reason)}" for r in fallbacks]

    out += ["", "## 検査の内容", "| 検査 | 内容 |", "|---|---|"]
    out += [f"| `{k}` | {v} |" for k, v in CHECK_DESCRIPTIONS.items()]
    out.append("")
    return "\n".join(out)
