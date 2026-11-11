# How ship-receipts is agentic (and where it deliberately stops)

The working definition used here: an agent has a **goal**, **perceives real data through tools**, **plans**,
**acts**, **verifies its own output**, and **loops until a stop condition**, with humans brought in only at
taste and judgment points and before anything outbound.

## The loop, mapped to commands

| Step | What happens | Concrete command / file | Who |
|---|---|---|---|
| **Goal** | Meet a weekly posting floor (e.g. 2) with posts grounded in real work, while keeping a queue of fresh ideas | `cadence.floor_per_week`, `cadence.target_per_week`, `queue.fresh_target` in `ship.yaml` | User sets it |
| **Perceive** | Read commits, recently modified files, notes, logged receipt candidates, the posted log; optional calendar via a read-only MCP; analytics only from user paste/export | `receipts.py gather --days N`, `hooks/post_commit.py`, `debrief add/import` | Tool |
| **Plan** | Decide the next action from state: mine, write, idle, or sleep | `receipts.py loop check --json` → `next_action` | Tool |
| **Act** | Generate ideas (receipt + why-now + 3 questions); nudge when the floor is at risk; record what shipped | `receipt-miner` skill → `receipts.py add`; `hooks/cadence_nudge.py`; `receipts.py posted add` | Agent + tool |
| **Verify** | Schema check (exactly 3 questions, no post copy), dedupe against queue and posted log, firewall scan, receipt-strength check that caps self-scores the text can't back | `receipts.py add` (REJECT / FLAG / VERIFY lines) | Tool |
| **Loop / stop** | Repeat at a self-chosen interval until fresh queue ≥ target **and** the weekly floor is met or out of reach (`missed`); silent in quiet hours | `ship-loop` skill under `/loop`; `done`, `suggested_recheck_minutes` | Agent |

## Human-in-the-loop gates

1. **Which idea to write.** The agent ranks; the person picks (`queue status ID picked`).
2. **The words.** The person answers the three questions. The agent may fix spelling and line breaks only.
3. **Approving anything public.** The firewall check runs on the person's text before they publish
   (`receipts.py firewall --file post.txt`); the person decides.
4. **Publishing.** Always a human action on the platform. The tool has no posting code, no platform credentials,
   no browser automation. Automating most social platforms is also against their Terms of Service.
5. **Anything outbound** (email, calendar changes, messages): out of scope. Calendar and email MCPs, if connected,
   are used read-only.

## What is deterministic vs model-driven

- **Deterministic (the Python tool):** gathering, dedupe, firewall, scoring, cadence math, quiet hours, throttle,
  the stop condition, learned weights. Tested in `tests/`.
- **Model-driven (the skills):** reading receipts and turning them into a receipt sentence, a why-now, and three
  good questions; deciding when a FLAG needs the person.

The split is intentional: the parts that must be right every time (privacy, cadence, "is the loop done") are not
left to a model.

## Stop conditions

- `done = queue_ok and (cadence_met or state == "missed")`: the queue holds at least `fresh_target` fresh,
  firewall-clean ideas, and either the floor is met this week (the target is a stretch goal) or the floor is out of
  reach (`missed`).
- On `done` or `missed`, ship-loop states next week's floor and ends the loop. A missed week resets; it never escalates.
- Quiet hours: `next_action = sleep`.
