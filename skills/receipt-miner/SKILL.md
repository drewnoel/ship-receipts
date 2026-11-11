---
name: receipt-miner
description: Mine the user's own receipts (git commits, files they built, notes, optional calendar) into ranked, de-duplicated, firewall-checked social post IDEAS, each with the receipt, why-now, and exactly 3 questions the user answers in their own words. Ideas only, never post copy. Use when the user asks "what should I post", "more post ideas", "top up the queue", or when ship-loop says next_action = mine.
---

# receipt-miner

Turns real work into post ideas. **It never writes the post.** Each idea is a receipt (something the user actually
built, measured, or learned) plus three questions. The user's answers, in their own words, are the post.

Tool: `python3 "$SHIP_RECEIPTS_REPO/tools/receipts.py"` (below: `rcpt`). Data: `$SHIP_RECEIPTS_HOME` (default
`~/.ship-receipts`). Config: `ship.yaml` there.

## Hard rules
- **Ideas only.** No drafts, no hook sentences, no hashtags, no "here's a version you could post". Titles are working
  labels. If the user asks you to write the post, say this skill doesn't, and offer to sharpen the questions instead.
  The tool rejects ideas with draft/copy fields, multi-paragraph receipts, or hashtags.
- **Receipts are the user's own.** An idea with no receipt from their own work scores receipt ≤ 2 by design. Reading
  someone else's take is fine as context, but the receipt must be something the user did.
- **Read-only, local.** Git log, local files, notes. Calendar or email MCP tools only if the user has connected them,
  and only search/list/read: never send, draft, accept, label, or archive. Never scrape a social platform.
- **Firewall.** Anonymize anything in `firewall_hits` ("a client", "a vendor", "a teammate"). No customer or employer
  names unless the user has said they're public, no people's names without their OK, no figures that identify a
  company. The scan catches obvious slips; whether an idea is safe to share is still your call.
- `source` is an internal pointer (repo@hash, file path). It never goes into the public-facing fields.

## Procedure
1. **Window.** Default last 7 days (`--days 7`); a top-up from ship-loop uses 3-4; a first run uses 30.
2. **Gather:** `rcpt gather --days N --out "$TMPDIR/receipts.json"`, then Read it. Note `posted` and `queued` (dedupe
   lists), `pillars`, `formats`, and each receipt's `firewall_hits`.
3. **Optional calendar (read-only):** if a calendar MCP is connected, list the same window. Talks given, workshops,
   launches and reviews make good milestone ideas. Skip personal, medical, and anything about job processes or deals.
4. **Generate ideas.** Target the shortfall from `rcpt loop check --json` (`fresh_target - fresh_count`), minimum 3.
   Spread across pillars; no more than half from one source. Per idea:
   ```json
   {"title": "...", "pillar": "BUILD", "format": "build-in-public", "effort": "S|M|L",
    "receipt": "1-3 plain sentences: what they did, with the number if there is one",
    "source": "git:repo@hash | file:path | note:file | calendar:date title",
    "why_now": "why this week and not someday",
    "questions": ["three questions", "only they can answer", "each ending in ?"],
    "scores": {"receipt": 1-5, "timeliness": 1-5, "fit": 1-5},
    "risk": "mild|spicy|risky (unpopular-opinion only)", "guard": "optional firewall note"}
   ```
   - **Questions** pull out the story, the surprise, and the opinion: "what broke first", "who pushed back and what did
     they say", "where is this advice wrong". Avoid yes/no questions and questions that smuggle in an answer.
   - **Scores.** receipt 5 = they built or measured it and there is a number; 3 = they did it, no number; 1 = a trend
     with no work behind it. timeliness 5 = this week; 3 = evergreen. fit = how well it serves the pillars they chose.
     The tool caps receipt scores the text can't back up.
5. **Write** the list to `$TMPDIR/ideas-new.json`; run `rcpt add "$TMPDIR/ideas-new.json"`.
6. **Verify.** Read the output: fix and re-add REJECTs that are format errors; drop duplicates; anonymize FLAGs and
   re-add (or leave the FLAG and tell the user: flagged ideas stay out of the picks). VERIFY lines mean a score was
   capped; accept it or strengthen the receipt with a real number from the source.
7. **Render** `rcpt queue render` and show the top 3 (title, receipt, why-now, questions). Ask which one they want to
   answer. That choice is theirs.
