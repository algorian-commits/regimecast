# Team guide (updated 28 Oct 2026)

The full plan, status and every decision are in `docs/PLAN.md`; read its "Where we are" table first.
All code is built by Saeed with Claude. Teammates don't need to code: each one presents modules from the
plain-English walkthroughs below.

## Where things stand

- **Done and live** (https://web-production-210f7.up.railway.app): Phases 1-9 and 11, which is everything
  except the final test, Phase 12 and the UI polish.
- **Phase 12, trade verdict for beginners** (not started yet): an A-F grade for a typed trade, "fix my trade", a
  what-if strip, crowd warning, similar past moments and bad-timing warnings. It judges how well a trade is set
  up, never which way the price will go.
- **Phase 10, final test** (after Phase 12): every model is frozen and scored once on the sealed year
  (Oct 2025 to Sep 2026) against pass marks already written in `docs/PLAN.md`. The results go in
  `docs/FINAL_EVALUATION.md`, whatever they are. The sealed year must not be used for anything before then.
- Dropped: Track Record page, automated end-to-end tests, deployment hardening, user/admin guides.

## Who presents what

| Person | Modules | Read |
|---|---|---|
| Saeed Ahmad Jan | M1 data, M4 features and regimes, M7 volatility and liquidation risk, M10 stress and paper agent | `docs/M4_WALKTHROUGH.md`, `docs/M7_WALKTHROUGH.md`, `docs/M10_STRESS_WALKTHROUGH.md`, `docs/M10_AGENT_WALKTHROUGH.md` |
| Ryan Ali | M2 app, M5/M6 dashboard and accounts, M8/M9 trade parser and risk check | `docs/M2_WALKTHROUGH.md`, `docs/M5_M6_WALKTHROUGH.md`, `docs/M8_M9_WALKTHROUGH.md` |
| Aila Jabeen | M3 news sentiment, M12 crash replay, alerts and Whale Watch | `docs/M3_WALKTHROUGH.md`, `docs/M12_WALKTHROUGH.md` |

This split is a suggestion; change it if you agree on another one.

## Open items

- Ask Mr. Awais whether success criteria 1 and 4 may be worded as "we test and report either way".
- Anthropic API key for the trade parser. It's skipped for now; steps are in `docs/PLAN.md` open questions.
  Never paste keys in chat or commit them.
