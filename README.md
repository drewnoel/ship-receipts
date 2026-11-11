# ship-receipts

**A Claude Code skill + hook loop that watches the work you're already doing and keeps you posting about it, in
your own words.**

ship-receipts watches the work you already do: git commits, files you build, your notes, and your calendar if you
connect it. It turns that work into *receipts*, meaning real things you built, measured, or learned. Each receipt
becomes a ranked post idea with three questions only you can answer. The tool tracks a weekly posting floor
(say 2-3 per week) and nudges you when that floor is at risk. It also checks everything against a firewall so
nothing confidential leaks.

**It never writes the post, and it never publishes anything.** You answer the questions, and your answers are the
post. Then you post it yourself.

Human-authored, AI-assisted. LinkedIn first; X and Bluesky are optional labels in the config.

---

## Why

Most people who build things have plenty to say and post rarely. Two things usually get in the way:

1. **"What would I even post?"** The material is already in your commit log and notes. It just never gets
   connected to a post.
2. **The week gets away from you.** Without a floor and a reminder, posting slides to "someday".

AI ghostwriting fixes both by writing the post for you. The result can read like
everyone else's. ship-receipts covers the first two steps, finding the material and keeping the cadence, and
leaves the writing to you.

## Where this fits (honestly)

| | AI ghostwriting / post-generation tools | Schedulers and analytics tools | ship-receipts |
|---|---|---|---|
| Finds material in *your* work | Usually from prompts or trends | No | Yes: git, files, notes, optional calendar |
| Writes the post | Yes | Some | **No, by design** |
| Publishes / schedules | Often | Yes | **No.** You post by hand |
| Cadence floor + nudges | Varies | Calendar view | Yes, with quiet hours |
| Confidentiality check | Rarely | No | Yes: your terms + generic patterns |
| Analytics | Often via platform integration | Yes | Only what you paste or export |

**Use it with a scheduler if you like.** Write in your own words, run the firewall check, then post or schedule
wherever you want.

**It's not a fit if** you want finished posts generated for you, multi-account social management, or automatic
analytics sync. Look at the tool categories above instead.

## How it works

```
 your work ──► gather ──► receipt-miner ──► queue (ranked, deduped, firewall-checked)
 (git, files,   (tool)     (skill: receipt +       │
  notes, cal)              why-now + 3 questions)   ▼
                                       ship-loop: floor at risk? ──► nudge with top idea
                                                   │
                                     YOU pick an idea, answer the 3 questions, post it yourself
                                                   │
                                  posted add ──► post-debrief (metrics you paste) ──► re-weights scoring
```

- `tools/receipts.py`: the deterministic half. It gathers receipts, validates, dedupes, firewalls and scores
  ideas, and handles cadence math, quiet hours, nudges, the loop's stop condition, and learned weights.
  Stdlib Python 3.9+, no network.
- `skills/receipt-miner`: turns receipts into ideas (ideas only).
- `skills/ship-loop`: a self-paced `/loop` that checks cadence, tops up the queue, nudges, and records what you
  shipped. It stops when the queue is full and the week's floor is met (the target is a stretch goal) or out of reach.
- `skills/post-debrief`: you paste post metrics; scoring weights adjust by format and pillar.
- `hooks/`: `post_commit.py` logs each `git commit` Claude Code runs as a receipt candidate.
  `cadence_nudge.py` prints one line at session start or stop when the floor is at risk. It respects your
  quiet hours and a throttle.

See [`docs/AGENTIC.md`](docs/AGENTIC.md) for the loop and the human gates, and
[`docs/FIREWALL.md`](docs/FIREWALL.md) for what is checked.

## 10-minute onboarding

**1. Clone and point your shell at it** (2 min)

```sh
git clone https://github.com/drewnoel/ship-receipts ~/ship-receipts
echo 'export SHIP_RECEIPTS_REPO="$HOME/ship-receipts"' >> ~/.zshrc   # or ~/.bashrc
export SHIP_RECEIPTS_REPO="$HOME/ship-receipts"
python3 "$SHIP_RECEIPTS_REPO/tools/receipts.py" init               # creates ~/.ship-receipts + ship.yaml
```

**2. Edit `~/.ship-receipts/ship.yaml`** (3 min)

- `cadence.floor_per_week` / `target_per_week` / `preferred_days`
- `quiet_hours.start` / `end`: no nudges in this window
- `watch.repos`, `watch.dirs`, `watch.notes_dir`: where your receipts live
- `firewall.terms`: client names, codenames, people. Anything that must never appear in a post.

This file holds your confidential terms. It lives outside the repo and is git-ignored.

**3. Install the skills** (1 min)

```sh
mkdir -p ~/.claude/skills
for s in receipt-miner ship-loop post-debrief; do ln -s "$SHIP_RECEIPTS_REPO/skills/$s" ~/.claude/skills/$s; done
```

**4. Wire the hooks** (2 min)

```sh
python3 "$SHIP_RECEIPTS_REPO/tools/receipts.py" hooks-snippet
```

Merge the printed `hooks` block into `~/.claude/settings.json` (or a project's `.claude/settings.json`).
[`hooks/settings.example.json`](hooks/settings.example.json) shows the same block with placeholder paths. Both
hooks always exit 0 and make no network calls. The Stop hook only emits a `systemMessage`, so it can't block a
session.

Optional: set `nudges.desktop: true` for a macOS (`osascript`) or Linux (`notify-send`) notification as well.

**5. First run** (2 min)

In Claude Code:

```
/receipt-miner            # first run: mines the last 30 days
/loop /ship-loop          # self-paced; stops when the week is covered
```

When you post: tell ship-loop, or run `receipts.py posted add --title "..." --idea I00N`.
When metrics come in: `/post-debrief` and paste them.

## Commands

```sh
rcpt() { python3 "$SHIP_RECEIPTS_REPO/tools/receipts.py" "$@"; }
rcpt gather --days 7 --out /tmp/r.json     # receipts in a window, firewall-tagged
rcpt add ideas.json                        # validate, verify, dedupe, firewall, score
rcpt queue list                            # ranked queue, short
rcpt queue render                          # full Markdown queue -> ~/.ship-receipts/QUEUE.md
rcpt queue status I004 picked              # new | picked | posted | parked
rcpt cadence status                        # "cadence at risk: 1 post this week, floor 2 | top idea: ..."
rcpt firewall --file my-post.txt           # pre-publish check; exit 2 on FLAG
rcpt posted add --title "..." --idea I004 --url https://...
rcpt debrief add --post P003 --impressions 1800 --reactions 40 --comments 9 --reposts 2
rcpt debrief import export.csv
rcpt loop check --json                     # next_action, done, fresh_count, cadence
```

Try it on synthetic data first: see [`examples/README.md`](examples/README.md).

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## Limitations

- **The firewall catches obvious slips, not everything.** Regexes and a term list can't tell that "our biggest customer's migration"
  identifies someone. You remain the final check.
- **Receipt strength is a heuristic.** It looks for did-it verbs, numbers, and a source. A strong receipt with
  plain wording can score low, and you can override it.
- **Weights need data.** A format or pillar only moves after 3+ debriefed posts, and the change is clamped to
  0.8-1.25. With fewer than ~8 posts, treat the weights as noise.
- **Hooks see only what Claude Code runs.** Commits you make in your own terminal show up through `gather` (git
  log), not the hook.
- **No platform integration, on purpose.** No posting, scheduling, scraping, or API sync. Analytics come only from
  what you paste or export. Automating most social platforms is against their Terms of Service.
- **macOS and Linux.** Desktop notifications use `osascript` or `notify-send`. Everything else is plain Python.
- The YAML reader is a small stdlib subset (maps, scalar lists, inline lists). A `ship.json` (in `~/.ship-receipts/` or the repo's `config/`) also works.

## Privacy

Everything stays local in `~/.ship-receipts/`. Nothing is sent anywhere. The repo's `.gitignore` excludes data and
config files. If you fork this repo, keep your real `ship.yaml` and queue out of it.

## License

MIT. Built by Drew Noel.
