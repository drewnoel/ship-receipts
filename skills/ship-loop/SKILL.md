---
name: ship-loop
description: A self-paced, /loop-compatible content cadence loop. Each pass checks the weekly posting floor, tops up the idea queue from the user's own receipts when it runs low, nudges with the top idea when the floor is at risk, and records what the user shipped. Stops when the weekly floor is met (the target is a stretch goal) and the queue is full. Never writes or publishes posts. Use with "/loop /ship-loop", or when the user says "run the ship loop", "am I on track to post this week", "I posted it".
---

# ship-loop

The loop around receipt-miner. The tool decides the state; this skill acts on it. **The user writes and publishes
every post.** This skill never drafts copy, never opens a social site, never schedules or posts anything.

Tool: `python3 "$SHIP_RECEIPTS_REPO/tools/receipts.py"` (below: `rcpt`).

## Each pass
1. **Perceive:** `rcpt loop check --json`. Fields: `next_action`, `done`, `cadence.state`, `fresh_count`,
   `fresh_target`, `top_ideas`, `quiet_hours`, `suggested_recheck_minutes`. Don't re-derive this logic yourself.
2. **Act on `next_action`:**
   - `sleep` (quiet hours): say nothing beyond one line; schedule the next pass after quiet hours end.
   - `mine`: run the receipt-miner procedure for the shortfall (window 3-4 days, or 7 if the queue is empty).
   - `write`: the floor needs a post. Show the top idea's receipt, why-now and 3 questions. Ask: "Want to answer these
     now? Five minutes, rough is fine." **Gate:** the user picks the idea (`rcpt queue status ID picked`). If they
     answer, you may tidy only spelling and line breaks, keeping their words, order, and voice; flag anything that
     reads as confidential. Before they publish, run `rcpt firewall --file <their post text>` and show the result.
   - `idle`: floor met, queue healthy. One-line status, nothing else.
3. **Record what shipped (only when the user says they posted):** `rcpt posted add --title "<short label>" --idea ID
   --platform linkedin [--url URL]`. Offer post-debrief once metrics exist (24h and 7 days are good checkpoints).
4. **Verify:** re-run `rcpt loop check` and report the one-line state.
5. **Stop or pace:** if `done` is true or `cadence.state` is `missed`, state next week's floor and **end the loop**. Otherwise wait about
   `suggested_recheck_minutes` before the next pass (self-paced `/loop`), never sooner than 30 minutes, and never
   during quiet hours.

## Human gates (never skip)
- Which idea to write: the user's choice.
- The words: the user's. No drafting, no "improved version".
- Publishing: the user's action, on the platform, by hand. This tool never automates a social platform (that is also
  a Terms of Service matter on most of them).
- Any outbound message, calendar change or email: not part of this loop. Don't.

## Tone
Short and factual: cadence state, the receipt, the questions. No hype, no scolding. A missed week is just a reset; say
the floor for next week and move on.
