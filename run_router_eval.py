"""Run without installing: python run_router_eval.py --router-src ../fde-hybrid-router/src [--out RESULTS.md]"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from eval_harness.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
