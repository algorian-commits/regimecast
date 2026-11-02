"""Popular trader tracker (13D): run the Modal jobs and bring the results into the repo.

Run from the repo root, with MODAL_PROFILE=saeed355ahmed and PYTHONUTF8=1 set::

    modal run --detach regimecast/jobs/modal_traders.py::trader_fills     # the fill download, many hours, resumes
    python scripts/traders_run.py gate       # how many accounts have 6+ months of perp fills
    python scripts/traders_run.py score      # scorecards -> regimecast/traders/scorecards.json.gz
    python scripts/traders_run.py copytest   # the pre-registered test, ONCE -> regimecast/traders/copy_test.json
    python scripts/traders_run.py fetch      # copy the result files from the Volume again

The copy test logs its commit: commit first, and do not change the test code before it has run.
A rerun refuses to start unless ``--rerun-reason "<the bug>"`` names what made the first run invalid.
"""
import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FILES = {"traders/out/gate.json": "data/reports/traders_gate.json",
         "traders/out/traders_scorecards.csv": "data/reports/traders_scorecards.csv",
         "traders/out/traders_copy_selection.csv": "data/reports/traders_copy_selection.csv",
         "traders/out/copy_test_runs.jsonl": "data/reports/traders_copy_test_runs.jsonl",
         "traders/out/scorecards.json.gz": "regimecast/traders/scorecards.json.gz",
         "traders/out/copy_test.json": "regimecast/traders/copy_test.json"}


def fetch() -> None:
    for remote, local in FILES.items():
        r = subprocess.run(["modal", "volume", "get", "regimecast-data", remote, str(ROOT / local), "--force"],
                           capture_output=True, text=True)
        print(("ok      " if r.returncode == 0 else "missing ") + local)


def modal_call(fn: str, *args: str) -> None:
    subprocess.run(["modal", "run", f"regimecast/jobs/modal_traders.py::{fn}", *args], cwd=ROOT, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["gate", "score", "copytest", "fetch"])
    ap.add_argument("--rerun-reason", default="")
    a = ap.parse_args()
    if a.cmd == "gate":
        modal_call("trader_gate")
    elif a.cmd == "score":
        modal_call("trader_scorecards")
    elif a.cmd == "copytest":
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        modal_call("trader_copy_test", "--commit", commit, "--rerun-reason", a.rerun_reason)
    fetch()
    if a.cmd == "gate":
        g = json.loads((ROOT / "data/reports/traders_gate.json").read_text())
        print(f"{g['accounts_6m']} of {g['downloaded']} downloaded accounts have 6+ months of perp fills; {g['needed']} needed")


if __name__ == "__main__":
    main()
