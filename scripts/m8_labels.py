"""Agreement between the two blind labelling passes of the parser test set (docs/m8/).

    python scripts/m8_labels.py        -> data/reports/m8_label_agreement.csv, disagreements printed

Pass 1: docs/m8/cases_200.csv (drafted with the messages). Pass 2: docs/m8/cases_200_review2.csv (a second
annotator who saw only LABEL_RULES.md and the messages). Final labels: docs/m8/cases_200_final.csv, where every
disagreement was settled by the written rules (the `resolution` column says how).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import pandas as pd

from regimecast.parsing.evaluate import agreement, cohen_kappa, same
from regimecast.parsing.schema import FIELDS

ROOT = pathlib.Path(__file__).resolve().parents[1]
M8 = ROOT / "docs" / "m8"
OUT = ROOT / "data" / "reports"


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(M8 / name, keep_default_na=True).set_index("id")


def main():
    a, b = load("cases_200.csv"), load("cases_200_review2.csv")
    tab = agreement(a, b)
    kappa = cohen_kappa(a["intent"].tolist(), b["intent"].tolist())
    cells = [(i, f, a.at[i, f], b.at[i, f]) for i in a.index for f in FIELDS if not same(f, a.at[i, f], b.at[i, f])]
    cases = sorted({c[0] for c in cells})
    OUT.mkdir(parents=True, exist_ok=True)
    tab.to_csv(OUT / "m8_label_agreement.csv", index=False)
    print(tab.to_string(index=False))
    print(f"\nintent kappa {kappa:.3f}; cases with any disagreement: {len(cases)} of {len(a)}; cells: {len(cells)}")
    for i, f, x, y in cells:
        print(f"  {i:>3} {f:<14} pass1={x!s:<10} pass2={y!s:<10} | {a.at[i, 'text']}")
    final = M8 / "cases_200_final.csv"
    if final.exists():
        fin = load("cases_200_final.csv")
        bad = [(i, f) for i, f, x, y in cells if not (same(f, fin.at[i, f], x) or same(f, fin.at[i, f], y))]
        print(f"\nfinal labels: {len(fin)} cases; settled cells not matching either pass: {bad or 'none'}")


if __name__ == "__main__":
    main()
