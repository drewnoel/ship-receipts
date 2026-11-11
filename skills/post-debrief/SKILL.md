---
name: post-debrief
description: Log how a published post performed from metrics the user pastes or exports (impressions, reactions, comments, reposts, followers), then let the tool re-weight idea scoring by format and pillar. Use when the user says "debrief my post", pastes post analytics or a CSV export, or ship-loop offers a debrief.
---

# post-debrief

Closes the loop: what shipped and how it did feeds the ranking. **Analytics come only from what the user pastes or
exports.** Never scrape, log into, or call a social platform's API on the user's behalf.

Tool: `python3 "$SHIP_RECEIPTS_REPO/tools/receipts.py"` (below: `rcpt`).

## Procedure
1. **Find the post.** Read `$SHIP_RECEIPTS_HOME/posted.json`. If the post isn't logged, log it first:
   `rcpt posted add --title "<label>" --idea ID --platform <p> --date <ISO> [--url URL]`.
   If the user pasted the post text, don't store it; a short label is enough.
2. **Read the metrics** from the paste or screenshot: impressions (views), reactions (likes), comments (replies),
   reposts (shares), followers gained. Missing numbers stay missing; don't estimate.
3. **Log:** `rcpt debrief add --post P00N --impressions N --reactions N --comments N --reposts N [--followers N]
   [--notes "..."]`. For an export: `rcpt debrief import file.csv` (columns `post` or `url`, plus metric columns;
   see `examples/analytics.sample.csv`). A later debrief of the same post replaces the earlier one in the weights.
4. **Read the weights** it prints. A format or pillar only moves after 3+ posts with metrics, shrunk toward 1.0 and
   clamped to 0.8-1.25, so one outlier can't take over the queue.
5. **Report in 3 lines:** how this post did against their running average (`_basis.overall_engagement` in
   `rcpt weights show`), what moved in the weights, and one question for them: "What do you think made the
   difference?" Their answer can go in `--notes` or become a receipt (`rcpt receipt add "..."`).

## Rules
- Small numbers are noisy. Say so when there are fewer than ~8 debriefed posts.
- No vanity framing. Comments and reposts are weighted above reactions because they mean someone engaged.
- Nothing here gets posted or sent anywhere.
