import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimflow.synth.generator import Generator  # noqa: E402

AS_OF = date(2026, 10, 27)


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    """A small generated dataset shared by all tests."""
    out = tmp_path_factory.mktemp("data")
    summary = Generator(n_claims=80, n_members=80, seed=7, as_of=AS_OF).run(out)
    records = [json.loads(line) for line in open(out / "ground_truth.jsonl")]
    return {"dir": out, "summary": summary, "records": records}
