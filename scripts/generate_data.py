"""Generate synthetic claims data.

    python scripts/generate_data.py --claims 300 --seed 42
    python scripts/generate_data.py --claims 300 --seed 42 --preview
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimflow.synth.generator import Generator  # noqa: E402
from claimflow.synth.receipts import contact_sheet  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--claims", type=int, default=300, help="number of claims to submit (default 300)")
    parser.add_argument("--members", type=int, default=200, help="number of regular members (default 200)")
    parser.add_argument("--seed", type=int, default=42, help="random seed; same seed gives same data")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                        help="pretend today is this date (YYYY-MM-DD)")
    parser.add_argument("--out", type=Path, default=Path("data"), help="output folder (default data/)")
    parser.add_argument("--preview", action="store_true", help="also save preview.png, a grid of sample receipts")
    args = parser.parse_args()

    summary = Generator(args.claims, args.members, args.seed, args.as_of).run(args.out)
    print(json.dumps(summary, indent=2))

    if args.preview:
        records = [json.loads(line) for line in open(args.out / "ground_truth.jsonl")]
        picks = [r for r in records if r["scenario"] == "clean"][:6] + \
                [r for r in records if r["scenario"] == "poor_scan"][:2]
        sheet = contact_sheet([args.out / r["document"] for r in picks])
        sheet.save(args.out / "preview.png")
        print(f"Preview saved to {args.out / 'preview.png'}")


if __name__ == "__main__":
    main()
