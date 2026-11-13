import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eval_harness import EvalCase, run_evaluation


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


if __name__ == "__main__":
    unittest.main()
