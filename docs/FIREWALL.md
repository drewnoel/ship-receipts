# Firewall

Everything the tool stores or shows as public-facing (idea `title`, `receipt`, `why_now`, `questions`) is scanned
before it enters the queue, and again whenever the queue is ranked. Receipts are scanned at gather time so the
agent knows what to anonymize. `source` pointers are internal and not scanned; they never go into a post.

## What it checks

**Your terms** (`firewall.terms` in `ship.yaml`): employer and client names you haven't made public, internal
codenames, product names under NDA, people's names. Case-insensitive, whole-word. Prefix `re:` for a regex,
for example `'re:\bCUST-\d+\b'` for ticket ids.

**Generic patterns** (`firewall.generic: true`):

| Category | Example that flags |
|---|---|
| Email address | `name@company.com` |
| Phone number | `555-867-5309`, `+44 20 7946 0958` |
| Secret-like token | `sk-...`, `ghp_...`, `AKIA...` |
| Credential assignment | `api_key = ...`, `DB_PASSWORD: ...`, `github_token=...` |
| Internal hostname | `build01.corp`, `db.internal` |
| Private IP address | `10.0.3.12`, `192.168.1.4` |
| Private key block | `-----BEGIN RSA PRIVATE KEY-----` |
| Bearer token / JWT | `Bearer abc123...`, `eyJ...`.`...`.`...` |
| Credentials in a URL | `postgres://user:pass@host` |
| Slack / Discord webhook | `https://hooks.slack.com/services/...`, `https://discord.com/api/webhooks/...` |
| SSN-style ID | `123-45-6789` |
| Money tied to a named org | `$2M from Acme Corp` (a currency amount within ~60 characters of a capitalized name after at/from/with/for, or a name ending in Inc/Corp/LLC/Ltd) |

Percentages and amounts with no organization nearby pass: "cut failures 40%", "spent $40 on a smaller instance".

**Allow list** (`firewall.allow`): substrings that never flag, such as your own public company domain.

## What a FLAG does

- The idea is still stored, its score is halved, and it is excluded from nudges and the top picks.
- The receipt-miner skill should anonymize and re-add ("a client", "a vendor", "a teammate", "a mid-size team").
- `receipts.py firewall --file post.txt` exits 2 on FLAG, so you can use it as a pre-publish check in a script.

## Limits (read these)

- Pattern matching misses context. "The migration for our biggest customer" can identify a customer to anyone who
  knows the account, and no regex will catch that. Judgment stays with you.
- The money rule produces false positives ("$40 a month for Postgres" flags). That is the safe direction.
- It does not check images, screenshots, or links you attach.
- Your `ship.yaml` contains the very terms you want hidden. It is git-ignored; keep it out of any repo.

## Dedupe threshold

Not a privacy check, but it runs in the same `add` step. A new idea is rejected as a duplicate when its title
overlaps an existing queued idea, or an already-posted title, by at least `queue.dedupe_threshold` (default `0.75`;
overlap = shared meaningful words / words in the shorter title). Title plus receipt is compared at the threshold
+ 0.05. Lower it to be stricter about near-repeats; raise it if distinct ideas get rejected. Set `sequel_of` on an
idea to post a deliberate follow-up to something already posted.
