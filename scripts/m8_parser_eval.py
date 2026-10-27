"""Parser accuracy on the 200-message test set (M8): the rule reader and Claude with prompts v1 and v2.

    python scripts/m8_parser_eval.py                    # all three (Claude needs ANTHROPIC_API_KEY)
    python scripts/m8_parser_eval.py --parsers rules    # offline part only

Writes data/reports/m8_parser_accuracy.csv (field by field, 95% intervals), m8_parser_errors.csv (every wrong
field), m8_parser_predictions.csv (raw answers; reused on the next run unless --fresh, so the paid calls run once)
and m8_parser_summary.json (retries, failures, tokens, cost, time). Costs about $0.15 for both prompts.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from regimecast.parsing import claude, prompts, rules
from regimecast.parsing.evaluate import score
from regimecast.parsing.schema import FIELDS, ParsedTrade, respond

ROOT = pathlib.Path(__file__).resolve().parents[1]
M8, OUT = ROOT / "docs" / "m8", ROOT / "data" / "reports"
PRICE_IN, PRICE_OUT = 0.10, 0.50          # Claude Haiku 5.5, USD per million tokens (prompts under 100k tokens)


def run_rules(text: str) -> dict:
    t0 = time.perf_counter()
    t = rules.parse(text)
    return {**t.model_dump(), "attempts": 0, "input_tokens": 0, "output_tokens": 0,
            "seconds": time.perf_counter() - t0, "error": ""}


def run_claude(version: str, client):
    def go(text: str) -> dict:
        t0 = time.perf_counter()
        try:
            r = claude.parse(text, version=version, client=client)
            return {**r.trade.model_dump(), "attempts": r.attempts, "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens, "seconds": time.perf_counter() - t0,
                    "error": " | ".join(r.errors)}
        except claude.ParseFailed as e:
            return {**{f: None for f in FIELDS}, "attempts": claude.MAX_ATTEMPTS, "input_tokens": 0,
                    "output_tokens": 0, "seconds": time.perf_counter() - t0, "error": f"FAILED: {e}"}
    return go


def outcome(row) -> str:
    """What the app does with these fields: refuse, ask for X, or go to the confirm step."""
    try:
        t = ParsedTrade.model_validate({f: (None if pd.isna(row[f]) else row[f]) for f in FIELDS})
    except Exception:
        return "invalid"
    r = respond(t, "eval")
    return r.status + (":" + ",".join(r.missing) if r.missing else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsers", default="rules,v1,v2")
    ap.add_argument("--fresh", action="store_true", help="ignore saved answers and call the parsers again")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    texts = pd.read_csv(M8 / "cases_200.csv").set_index("id")["text"]
    gold = pd.read_csv(M8 / "cases_200_final.csv").set_index("id")[FIELDS]
    pred_file = OUT / "m8_parser_predictions.csv"
    saved = pd.read_csv(pred_file) if pred_file.exists() and not args.fresh else pd.DataFrame(columns=["parser"])
    client = claude.make_client(timeout=30) if claude.has_key() else None

    preds = {}
    for name in args.parsers.split(","):
        old = saved[saved["parser"] == name]
        if len(old) == len(gold):
            preds[name] = old.set_index("id")
            print(f"{name}: reusing saved answers")
            continue
        if name == "rules":
            fn = run_rules
        elif name in prompts.PROMPTS:
            if client is None:
                print(f"{name}: skipped (no ANTHROPIC_API_KEY)")
                continue
            fn = run_claude(name, client)
        else:
            raise SystemExit(f"unknown parser {name}")
        with ThreadPoolExecutor(args.workers if name != "rules" else 1) as ex:
            rows = list(ex.map(fn, texts.tolist()))
        df = pd.DataFrame(rows, index=texts.index)
        df.index.name = "id"
        df.insert(0, "parser", name)
        preds[name] = df
        print(f"{name}: {len(df)} answers, {df['error'].str.startswith('FAILED').sum()} failed")

    if not preds:
        return
    OUT.mkdir(parents=True, exist_ok=True)
    keep = saved[~saved["parser"].isin(preds)] if len(saved) else saved
    pd.concat([keep] + [p.reset_index() for p in preds.values()]).to_csv(pred_file, index=False)

    tables, errors, summary = [], [], {}
    gold_outcome = gold.apply(outcome, axis=1)
    for name, p in preds.items():
        tab, right = score(gold, p[FIELDS])
        out_right = (p.reindex(gold.index).apply(outcome, axis=1) == gold_outcome)
        tab = pd.concat([tab, pd.DataFrame([{"field": "app_outcome", "right": int(out_right.sum()), "n": len(gold),
                                             "accuracy": out_right.mean()}])])
        tab.insert(0, "parser", name)
        tables.append(tab)
        for i in gold.index:
            for f in FIELDS:
                ok = right.at[i, f]
                if ok is not pd.NA and not ok:      # NA = not scored (a trade field on a non-trade case)
                    errors.append({"parser": name, "id": i, "field": f, "gold": gold.at[i, f],
                                   "pred": p.at[i, f], "text": texts[i]})
        cost = (p["input_tokens"].sum() * PRICE_IN + p["output_tokens"].sum() * PRICE_OUT) / 1e6
        summary[name] = {
            "cases": len(p), "failed": int(p["error"].astype(str).str.startswith("FAILED").sum()),
            "retried": int((p["attempts"] > 1).sum()), "input_tokens": int(p["input_tokens"].sum()),
            "output_tokens": int(p["output_tokens"].sum()), "cost_usd": round(float(cost), 4),
            "cost_per_parse_usd": round(float(cost) / len(p), 6),
            "median_seconds": round(float(p["seconds"].median()), 3), "p95_seconds": round(float(p["seconds"].quantile(.95)), 3),
        }
    acc = pd.concat(tables)
    acc.to_csv(OUT / "m8_parser_accuracy.csv", index=False)
    pd.DataFrame(errors).to_csv(OUT / "m8_parser_errors.csv", index=False)
    commit = subprocess.run(["git", "log", "-1", "--format=%h", "--", "regimecast/parsing/prompts.py"],
                            capture_output=True, text=True, cwd=ROOT).stdout.strip()
    meta = {"model": claude.MODEL, "prompts_commit": commit, "test_set": "docs/m8/cases_200_final.csv",
            "labels": "two blind AI passes (Claude), not human", "parsers": summary}
    (OUT / "m8_parser_summary.json").write_text(json.dumps(meta, indent=2))
    wide = acc.pivot(index="field", columns="parser", values="accuracy").reindex(FIELDS + ["all_fields", "app_outcome"])
    print((100 * wide).round(1).to_string())
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
