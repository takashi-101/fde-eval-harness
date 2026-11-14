import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eval_harness import EvalCase, run_evaluation
from eval_harness.runner import estimate_tokens, evaluate_output, load_cases_jsonl


class TestEvalHarness(unittest.TestCase):
    def test_eval_passes_and_catches_failures(self) -> None:
        cases = [
            EvalCase("C1", "期限は？", ["14日"], ["推測"], ["deadline"]),
            EvalCase("C2", "外部送信は？", ["禁止"], ["許可"], []),
        ]

        def fake_model(prompt: str) -> str:
            if "期限" in prompt:
                return '{"deadline": "14日以内"}'
            return "外部送信は許可します"  # intentional failure

        report = run_evaluation(cases, fake_model)
        self.assertEqual(report.total_cases, 2)
        self.assertEqual(report.passed_cases, 1)
        self.assertEqual(report.pass_rate, 0.5)
        self.assertFalse(report.results[1].passed)
        self.assertIn("missing_required:禁止", report.results[1].reasons)
        self.assertIn("forbidden_present:許可", report.results[1].reasons)

    def test_token_and_cost_estimate_is_chars_div_2_times_given_rate(self) -> None:
        self.assertEqual(estimate_tokens("あいうえお"), 2)  # 5 chars // 2
        self.assertEqual(estimate_tokens(""), 1)
        case = EvalCase("C3", "a" * 1000, [], [], [])
        result = evaluate_output(case, "b" * 1000, jpy_per_1k_tokens=2.0)
        self.assertEqual(result.input_tokens_est, 500)
        self.assertEqual(result.output_tokens_est, 500)
        self.assertEqual(result.estimated_cost_jpy, 2.0)

    def test_invalid_json_and_missing_keys_are_reported(self) -> None:
        case = EvalCase("C4", "p", [], [], ["deadline", "basis"])
        self.assertIn("invalid_json", evaluate_output(case, "not json").reasons)
        self.assertIn("missing_json_key:basis", evaluate_output(case, '{"deadline": "14日"}').reasons)

    def test_example_golden_cases_load(self) -> None:
        cases = load_cases_jsonl(Path(__file__).resolve().parents[1] / "examples" / "golden_cases.jsonl")
        self.assertEqual([c.case_id for c in cases], ["E-01", "E-02"])
        self.assertEqual(cases[0].expected_json_keys, ["deadline", "basis"])


if __name__ == "__main__":
    unittest.main()
