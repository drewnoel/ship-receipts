#!/usr/bin/env python3
"""ship-receipts: the deterministic half of a human-authored content loop.

It mines *receipts* (things you actually built, measured or learned) from local sources, keeps a ranked,
de-duplicated, firewall-checked queue of post IDEAS, tracks a weekly posting floor, and nudges when the floor is
at risk. It never writes post copy and never posts anywhere. Posting is always your action.

Stdlib only, Python 3.9+. Local only: no network calls.

Paths
  data dir   $SHIP_RECEIPTS_HOME            (default ~/.ship-receipts)
  config     $SHIP_RECEIPTS_CONFIG          (default <data dir>/ship.yaml|.json, then <repo>/config/ship.yaml|.json)

Commands
  init                                   create the data dir and copy the example config
  gather --days N [--out FILE]           receipts in a window: git log, modified files, notes, logged candidates
  receipt add TEXT [--source S] [--kind K]   log one receipt candidate (the commit hook uses this)
  add FILE.json [--dry-run]              merge new ideas: validate, verify, dedupe, firewall, score
  firewall TEXT... | --file F            scan text; exit 0 PASS, 2 FLAG
  queue render [--out F]                 write the ranked queue as Markdown
  queue list                             short ranked list
  queue status ID new|picked|posted|parked
  cadence status [--json]                posts this week vs floor
  posted add --title T [--idea ID] [--platform P] [--date ISO] [--url U]
  debrief add --post PID [metrics]       log analytics you pasted; re-weights scoring
  debrief import FILE.csv                same, from an analytics export
  weights show
  nudge [--channel stdout|system-message|desktop] [--force]
  loop check [--json]                    the loop's stop condition and next action
  hooks-snippet                          print a settings.json hooks block with this repo's real paths

Global flags (mostly for tests): --today YYYY-MM-DD, --now YYYY-MM-DDTHH:MM
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
STATUSES = ["new", "picked", "posted", "parked"]
EFFORT = {"S": 1, "M": 2, "L": 3}
DEFAULT_PILLARS = ["BUILD", "MEASURE", "LEARN", "OPINION"]
FORMATS = [
    "build-in-public", "lessons-list", "data-point", "how-i", "pov", "unpopular-opinion",
    "behind-the-scenes", "milestone", "before-after", "question",
]

DEFAULTS: Dict[str, Any] = {
    "cadence": {"floor_per_week": 2, "target_per_week": 3, "preferred_days": ["tue", "wed", "thu"],
                "min_gap_hours": 24, "week_starts": "mon"},
    "quiet_hours": {"start": "21:00", "end": "07:00"},
    "nudges": {"desktop": False, "throttle_hours": 6, "only_when_at_risk": True},
    "platforms": ["linkedin"],
    "watch": {"repos": [], "dirs": [], "notes_dir": "", "author": "",
              "extensions": [".md", ".py", ".ts", ".js", ".sql", ".ipynb", ".yaml", ".json", ".txt"],
              "max_files": 150},
    "queue": {"fresh_target": 8, "fresh_days": 21, "dedupe_threshold": 0.75},
    "pillars": DEFAULT_PILLARS,
    "firewall": {"terms": [], "allow": [], "generic": True},
}


# ---------------------------------------------------------------------------------------------------------------
# Minimal YAML-subset reader (stdlib only): maps by indentation, "- item" lists of scalars, inline [a, b] lists,
# quoted strings, ints, floats, booleans, null, and # comments. Lists of maps are not supported (not needed).
# ---------------------------------------------------------------------------------------------------------------
def _scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return None
    if s[0] in "\"'":
        q = s[0]
        end = s.find(q, 1)
        return s[1:end] if end > 0 else s[1:]
    s = re.sub(r"\s+#.*$", "", s).strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_scalar(x) for x in _split_inline(inner)] if inner else []
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~", "none"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d*", s):
        return float(s)
    return s


def _split_inline(inner: str) -> List[str]:
    parts, buf, q = [], "", ""
    for ch in inner:
        if q:
            buf += ch
            if ch == q:
                q = ""
        elif ch in "\"'":
            q = ch
            buf += ch
        elif ch == ",":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return parts


def parse_yaml(text: str) -> Dict[str, Any]:
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), raw.strip()))
    root: Dict[str, Any] = {}
    stack: List[Any] = [(-1, root)]  # (indent, container)
    for i, (ind, line) in enumerate(lines):
        while stack and ind <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            if isinstance(parent, list):
                parent.append(_scalar(line[2:]))
            continue
        m = re.match(r"^([^:]+):(.*)$", line)
        if not m or not isinstance(parent, dict):
            continue
        key, rest = m.group(1).strip().strip("\"'"), m.group(2)
        if rest.strip() and not rest.strip().startswith("#"):
            parent[key] = _scalar(rest)
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if nxt and nxt[0] > ind:
            child: Any = [] if nxt[1].startswith("- ") else {}
        else:
            child = None
        parent[key] = child
        if child is not None:
            stack.append((ind, child))
    return root


def merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


# ---------------------------------------------------------------------------------------------------------------
# Context: paths, config, clock
# ---------------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, data: Optional[str] = None, config: Optional[str] = None,
                 today: Optional[str] = None, now: Optional[str] = None):
        self.data = Path(os.path.expanduser(data or os.environ.get("SHIP_RECEIPTS_HOME") or "~/.ship-receipts"))
        cfg_path = config or os.environ.get("SHIP_RECEIPTS_CONFIG")
        candidates = [Path(os.path.expanduser(cfg_path))] if cfg_path else [
            self.data / "ship.yaml", self.data / "ship.json", REPO / "config" / "ship.yaml", REPO / "config" / "ship.json"]
        self.config_path = next((p for p in candidates if p.exists()), None)
        loaded: Dict[str, Any] = {}
        if self.config_path:
            txt = self.config_path.read_text()
            loaded = json.loads(txt) if self.config_path.suffix == ".json" else parse_yaml(txt)
        self.cfg = merge(DEFAULTS, loaded)
        if now:
            self.now = dt.datetime.fromisoformat(now)
        elif today:
            self.now = dt.datetime.combine(dt.date.fromisoformat(today), dt.time(12, 0))
        else:
            self.now = dt.datetime.now().replace(second=0, microsecond=0)
        self.today = self.now.date()

    # file locations
    @property
    def queue_file(self) -> Path:
        return self.data / "queue.json"

    @property
    def posted_file(self) -> Path:
        return self.data / "posted.json"

    @property
    def receipts_file(self) -> Path:
        return self.data / "receipts.jsonl"

    @property
    def debrief_file(self) -> Path:
        return self.data / "debriefs.jsonl"

    @property
    def weights_file(self) -> Path:
        return self.data / "weights.json"

    @property
    def nudge_state(self) -> Path:
        return self.data / "nudge-state.json"

    def pillars(self) -> List[str]:
        return [str(p).upper() for p in (self.cfg.get("pillars") or DEFAULT_PILLARS)]


def read_json(p: Path, default: Any) -> Any:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(p)


def append_jsonl(p: Path, obj: Dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(p: Path) -> List[Dict[str, Any]]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------------------------------------------------------
# Firewall: your confidential terms + generic patterns. It catches the obvious slips; you still make the call.
# ---------------------------------------------------------------------------------------------------------------
GENERIC_PATTERNS = {
    "email address": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    "phone number": r"(?<![\w$])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
                    r"|(?<![\w+])\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?!\d)",
    "secret-like token": r"\b(?:sk|pk|rk|ghp|gho|xox[abprs])[-_][A-Za-z0-9_-]{10,}|\bAKIA[0-9A-Z]{16}\b",
    "credential assignment": r"(?i)\b\w*(?:api[_ -]?key|secret|password|passwd|token)\w*\s*[:=]\s*\S+",
    "private key block": r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----",
    "bearer token": r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*",
    "JWT": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    "credentials in URL": r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/:@]+:[^\s/@]+@",
    "Slack webhook": r"https://hooks\.slack\.com/(?:services|workflows|triggers)/\S+",
    "Discord webhook": r"https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/\S+",
    "SSN-style ID": r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])",
    "internal hostname": r"\b[\w-]+\.(?:internal|corp|intranet|lan)\b",
    "private IP address": r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
}
MONEY = r"(?:\$|USD\s?|EUR\s?|£|€)\s?\d[\d,]*(?:\.\d+)?\s?(?:[kKmMbB]\b|million|billion|thousand)?"
COMPANY_NEAR = (r"\b(?:at|from|with|for|by|of|to)\s+(?:[A-Z][\w&.-]*)(?:\s+[A-Z][\w&.-]*)*"
                r"|\b[A-Z][\w&-]*(?:\s+[A-Z][\w&-]*)*\s+(?:Inc|Corp|Corporation|LLC|Ltd|GmbH|Co|Labs|Group)\b\.?")


def firewall_scan(text: str, cfg: Dict[str, Any]) -> List[str]:
    fw = cfg.get("firewall") or {}
    allow = [str(a).lower() for a in (fw.get("allow") or [])]
    hits: List[str] = []

    def add(cat: str, match: str) -> None:
        m = match.strip()
        if any(a and a in m.lower() for a in allow):
            return
        tag = f"{cat}: {m[:60]}"
        if tag not in hits:
            hits.append(tag)

    for term in fw.get("terms") or []:
        t = str(term).strip()
        if not t:
            continue
        if t.startswith("re:"):
            for m in re.finditer(t[3:], text, re.I):
                add("confidential term", m.group(0))
        else:
            for m in re.finditer(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, re.I):
                add("confidential term", m.group(0))
    if fw.get("generic", True):
        for cat, pat in GENERIC_PATTERNS.items():
            for m in re.finditer(pat, text):
                add(cat, m.group(0))
        for m in re.finditer(MONEY, text):
            lo, hi = max(0, m.start() - 60), min(len(text), m.end() + 60)
            window = text[lo:hi]
            near = re.search(COMPANY_NEAR, window)
            if near:
                add("money tied to a named org", f"{m.group(0).strip()} ~ {near.group(0).strip()}")
    return hits


# ---------------------------------------------------------------------------------------------------------------
# Ideas: validate, verify, dedupe, score
# ---------------------------------------------------------------------------------------------------------------
SCAN_FIELDS = ["title", "receipt", "why_now", "questions", "angle"]  # public-facing; `source` stays internal
COPY_FIELDS = ["draft", "post", "copy", "body", "hook_line", "text"]
STOP = set("a an the of to and or for in on at is it my your you i we our with as by vs not from this that be "
           "was were are how what why when".split())


def idea_text(idea: Dict[str, Any]) -> str:
    parts: List[str] = []
    for f in SCAN_FIELDS:
        v = idea.get(f)
        if isinstance(v, dict):
            parts += [str(x) for x in v.values()]
        elif isinstance(v, list):
            parts += [str(x) for x in v]
        elif v:
            parts.append(str(v))
    return "\n".join(parts)


def toks(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9%$]+", s.lower()) if w not in STOP and len(w) > 2}


def sim(a: str, b: str) -> float:
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def looks_like_post_copy(idea: Dict[str, Any]) -> Optional[str]:
    for f in COPY_FIELDS:
        if idea.get(f):
            return f"field '{f}' looks like post copy (ideas only)"
    r = str(idea.get("receipt", ""))
    if len(r) > 500 or r.count("\n\n") >= 1:
        return "receipt reads like post copy: keep it to 1-3 plain sentences"
    if re.search(r"#\w+\s+#\w+", idea_text(idea)):
        return "hashtags found: that is post copy, not an idea"
    return None


def validate(idea: Dict[str, Any], pillars: List[str]) -> List[str]:
    errs = []
    for k in ("title", "pillar", "format", "receipt", "source", "why_now", "questions", "effort"):
        if not idea.get(k):
            errs.append(f"missing {k}")
    if idea.get("pillar") and str(idea["pillar"]).upper() not in pillars:
        errs.append(f"pillar must be one of {pillars}")
    if idea.get("format") and idea["format"] not in FORMATS:
        errs.append(f"format must be one of {FORMATS}")
    if idea.get("effort") and idea["effort"] not in EFFORT:
        errs.append("effort must be S, M or L")
    qs = idea.get("questions") or []
    if not isinstance(qs, list) or len(qs) != 3:
        errs.append("need exactly 3 questions")
    elif not all(str(q).strip().endswith("?") for q in qs):
        errs.append("each question must be a question (end with '?')")
    if len(str(idea.get("title", ""))) > 90:
        errs.append("title is a working label, keep it under 90 chars")
    if idea.get("format") == "unpopular-opinion" and idea.get("risk") not in ("mild", "spicy", "risky"):
        errs.append("unpopular-opinion needs risk: mild|spicy|risky")
    copy = looks_like_post_copy(idea)
    if copy:
        errs.append(copy)
    return errs


NUMBER = re.compile(r"\d")
DID_VERBS = re.compile(r"\b(?:built|shipped|wrote|cut|measured|migrated|fixed|launched|ran|tested|replaced|automated|"
                       r"rebuilt|refactored|deleted|benchmarked|interviewed|learned|found|moved|reduced|doubled|"
                       r"halved|saved|tracked|deployed|prototyped)\b", re.I)


def receipt_strength(idea: Dict[str, Any]) -> int:
    """Independent 1-5 estimate from the receipt text: did you do it, is there a number, is there a source."""
    r = str(idea.get("receipt", ""))
    s = 1
    if DID_VERBS.search(r):
        s += 2
    if NUMBER.search(r):
        s += 1
    src = str(idea.get("source", ""))
    if src.startswith(("git:", "file:", "note:", "receipt:", "calendar:", "debrief:")):
        s += 1
    return max(1, min(5, s))


def verify_scores(idea: Dict[str, Any]) -> List[str]:
    """Cap self-reported receipt scores the text can't back up. Returns notes."""
    notes = []
    sc = idea.setdefault("scores", {})
    claimed = int(sc.get("receipt", 3))
    est = receipt_strength(idea)
    if claimed > est + 1:
        sc["receipt"] = est + 1
        notes.append(f"receipt score {claimed} capped to {est + 1} (text shows strength ~{est})")
    idea["receipt_strength"] = est
    return notes


def load_weights(ctx: Ctx) -> Dict[str, Dict[str, float]]:
    return read_json(ctx.weights_file, {"format": {}, "pillar": {}})


def score(idea: Dict[str, Any], weights: Optional[Dict[str, Dict[str, float]]] = None) -> float:
    """receipt x timeliness x fit (each 1-5) -> 0-100, times learned format/pillar weights; FLAG halves it."""
    s = idea.get("scores", {})
    vals = [max(1, min(5, int(s.get(k, 3)))) for k in ("receipt", "timeliness", "fit")]
    raw = vals[0] * vals[1] * vals[2] / 125 * 100
    w = weights or {}
    raw *= float((w.get("format") or {}).get(idea.get("format", ""), 1.0))
    raw *= float((w.get("pillar") or {}).get(str(idea.get("pillar", "")).upper(), 1.0))
    if idea.get("firewall", "PASS") != "PASS":
        raw *= 0.5
    return round(min(raw, 100.0), 1)


def next_id(items: List[Dict[str, Any]], prefix: str) -> str:
    n = max([int(i["id"][1:]) for i in items if re.fullmatch(prefix + r"\d+", str(i.get("id", "")))] or [0])
    return f"{prefix}{n + 1:03d}"


def add_ideas(ctx: Ctx, new: List[Dict[str, Any]], dry: bool = False) -> Dict[str, Any]:
    ideas = read_json(ctx.queue_file, [])
    posted = read_json(ctx.posted_file, [])
    weights = load_weights(ctx)
    pillars = ctx.pillars()
    report: Dict[str, Any] = {"added": [], "rejected": [], "flagged": [], "notes": []}
    th = float(ctx.cfg["queue"].get("dedupe_threshold", 0.75))
    th_body = min(1.0, th + 0.05)  # title+receipt comparison is a little stricter than title alone
    for idea in new:
        errs = validate(idea, pillars)
        if errs:
            report["rejected"].append({"title": idea.get("title", "?"), "why": "; ".join(errs)})
            continue
        idea["pillar"] = str(idea["pillar"]).upper()
        key = f"{idea['title']} {idea['receipt']}"
        dup = next((i for i in ideas if i.get("status") != "parked"
                    and (sim(i["title"], idea["title"]) >= th or sim(f"{i['title']} {i['receipt']}", key) >= th_body)),
                   None)
        if dup:
            report["rejected"].append({"title": idea["title"], "why": f"duplicate of {dup['id']} '{dup['title']}'"})
            continue
        pdup = next((p for p in posted if sim(p.get("title", ""), idea["title"]) >= th), None)
        if pdup and not idea.get("sequel_of"):
            report["rejected"].append({"title": idea["title"],
                                       "why": f"already posted as {pdup['id']} '{pdup['title']}' (set sequel_of)"})
            continue
        for n in verify_scores(idea):
            report["notes"].append(f"{idea['title']}: {n}")
        hits = firewall_scan(idea_text(idea), ctx.cfg)
        idea["firewall"] = "PASS" if not hits else "FLAG"
        idea["firewall_hits"] = hits
        idea.setdefault("status", "new")
        idea.setdefault("added", ctx.today.isoformat())
        idea["id"] = next_id(ideas, "I")
        idea["score"] = score(idea, weights)
        ideas.append(idea)
        report["added"].append(idea["id"])
        if hits:
            report["flagged"].append({"id": idea["id"], "title": idea["title"], "hits": hits})
    if not dry:
        write_json(ctx.queue_file, ideas)
    report["store"] = len(ideas)
    return report


def active_ranked(ctx: Ctx, pass_only: bool = False) -> List[Dict[str, Any]]:
    weights = load_weights(ctx)
    ideas = [i for i in read_json(ctx.queue_file, []) if i.get("status") in ("new", "picked")]
    for i in ideas:
        hits = firewall_scan(idea_text(i), ctx.cfg)  # re-scan: terms added to the config apply to queued ideas too
        i["firewall"], i["firewall_hits"] = ("PASS" if not hits else "FLAG"), hits
        i["score"] = score(i, weights)
    if pass_only:
        ideas = [i for i in ideas if i.get("firewall") == "PASS"]
    ideas.sort(key=lambda i: (i.get("status") != "picked", -i["score"], EFFORT.get(i.get("effort", "M"), 2)))
    return ideas


def fresh_ideas(ctx: Ctx) -> List[Dict[str, Any]]:
    days = int(ctx.cfg["queue"].get("fresh_days", 21))
    cutoff = (ctx.today - dt.timedelta(days=days)).isoformat()
    return [i for i in active_ranked(ctx, pass_only=True) if i.get("status") == "new" and i.get("added", "") >= cutoff]


# ---------------------------------------------------------------------------------------------------------------
# Cadence + quiet hours
# ---------------------------------------------------------------------------------------------------------------
def _hm(s: str) -> dt.time:
    h, m = (str(s).split(":") + ["0"])[:2]
    return dt.time(int(h) % 24, int(m))


def in_quiet_hours(now: dt.datetime, cfg: Dict[str, Any]) -> bool:
    q = cfg.get("quiet_hours") or {}
    if not q.get("start") or not q.get("end"):
        return False
    start, end, t = _hm(q["start"]), _hm(q["end"]), now.time()
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end  # wraps midnight, e.g. 21:00-07:00


def week_bounds(day: dt.date, week_starts: str) -> (dt.date, dt.date):
    ws = DAY_NAMES.index(str(week_starts)[:3].lower()) if str(week_starts)[:3].lower() in DAY_NAMES else 0
    start = day - dt.timedelta(days=(day.weekday() - ws) % 7)
    return start, start + dt.timedelta(days=7)


def parse_when(s: str) -> dt.datetime:
    s = str(s)
    return dt.datetime.fromisoformat(s) if "T" in s or " " in s else dt.datetime.combine(dt.date.fromisoformat(s),
                                                                                          dt.time(9, 0))


def cadence_status(ctx: Ctx) -> Dict[str, Any]:
    c = ctx.cfg["cadence"]
    floor, target = int(c.get("floor_per_week", 2)), int(c.get("target_per_week", 3))
    start, end = week_bounds(ctx.today, c.get("week_starts", "mon"))
    posts = read_json(ctx.posted_file, [])
    this_week = [p for p in posts if start <= parse_when(p["date"]).date() < end]
    count = len(this_week)
    needed = max(0, floor - count)
    preferred = [str(d)[:3].lower() for d in (c.get("preferred_days") or DAY_NAMES)]
    posted_today = any(parse_when(p["date"]).date() == ctx.today for p in posts)
    days_left = [start + dt.timedelta(days=n) for n in range(7) if start + dt.timedelta(days=n) >= ctx.today]
    if posted_today:
        days_left = [d for d in days_left if d > ctx.today]
    pref_left = [d for d in days_left if DAY_NAMES[d.weekday()] in preferred]
    last = max((parse_when(p["date"]) for p in posts), default=None)
    gap_h = int(c.get("min_gap_hours", 24))
    next_ok = (last + dt.timedelta(hours=gap_h)) if last else None
    if count >= target:
        state = "target-met"
    elif count >= floor:
        state = "floor-met"
    elif needed > len(days_left):
        state = "missed"
    elif needed >= len(pref_left):
        state = "at-risk"
    else:
        state = "on-track"
    return {
        "week_of": start.isoformat(), "posts_this_week": count, "floor": floor, "target": target,
        "needed_for_floor": needed, "days_left": len(days_left), "preferred_days_left": len(pref_left),
        "state": state, "last_post": last.isoformat(timespec="minutes") if last else None,
        "next_eligible": (max(next_ok, ctx.now) if next_ok else ctx.now).isoformat(timespec="minutes"),
        "posted_titles_this_week": [p.get("title", "") for p in this_week],
    }


def cadence_line(st: Dict[str, Any], top: Optional[Dict[str, Any]]) -> str:
    n, f = st["posts_this_week"], st["floor"]
    word = "post" if n == 1 else "posts"
    lead = {
        "at-risk": f"cadence at risk: {n} {word} this week, floor {f}",
        "missed": f"floor out of reach this week: {n} {word}, floor {f}; reset next week",
        "on-track": f"cadence on track: {n} {word} this week, floor {f}",
        "floor-met": f"floor met: {n} {word} this week (target {st['target']})",
        "target-met": f"target met: {n} {word} this week",
    }[st["state"]]
    if top and st["state"] in ("at-risk", "on-track", "floor-met"):
        lead += f" | top idea: {top['id']} {top['title']}"
    return lead


# ---------------------------------------------------------------------------------------------------------------
# Gather receipts
# ---------------------------------------------------------------------------------------------------------------
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache", ".idea"}


def git_receipts(repo: Path, since: str, author: str = "") -> List[Dict[str, Any]]:
    cmd = ["git", "-C", str(repo), "log", f"--since={since}", "--no-merges",
           "--pretty=%h%x1f%ad%x1f%s", "--date=short", "--shortstat"]
    if author:
        cmd.append(f"--author={author}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rec: List[Dict[str, Any]] = []
    for line in out.splitlines():
        if "\x1f" in line:
            h, d, s = line.split("\x1f", 2)
            rec.append({"date": d, "kind": "commit", "text": s, "source": f"git:{repo.name}@{h}"})
        elif "changed" in line and rec:
            rec[-1]["stat"] = line.strip()
    return rec


def file_receipts(root: Path, since_ts: float, exts: List[str], limit: int) -> List[Dict[str, Any]]:
    rec: List[Dict[str, Any]] = []
    if not root.is_dir():
        return rec
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if exts and p.suffix.lower() not in exts:
                continue
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt < since_ts:
                continue
            text = f"modified {p.relative_to(root)}"
            if p.suffix.lower() == ".md":
                head = _first_heading(p)
                if head:
                    text += f" ('{head}')"
            rec.append({"date": dt.date.fromtimestamp(mt).isoformat(), "kind": "file", "text": text,
                        "source": f"file:{root.name}/{p.relative_to(root)}", "_mt": mt})
    rec.sort(key=lambda r: -r["_mt"])
    for r in rec:
        r.pop("_mt", None)
    return rec[:limit]


def _first_heading(p: Path) -> str:
    try:
        with p.open(errors="ignore") as fh:
            for _ in range(20):
                line = fh.readline()
                if line.startswith("#"):
                    return line.lstrip("# ").strip()[:100]
    except OSError:
        pass
    return ""


def note_receipts(root: Path, since_ts: float) -> List[Dict[str, Any]]:
    rec: List[Dict[str, Any]] = []
    if not root.is_dir():
        return rec
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".md", ".txt") or not p.is_file():
            continue
        if p.stat().st_mtime < since_ts:
            continue
        body = re.sub(r"\s+", " ", p.read_text(errors="ignore")).strip()[:600]
        rec.append({"date": dt.date.fromtimestamp(p.stat().st_mtime).isoformat(), "kind": "note", "text": body,
                    "source": f"note:{p.name}"})
    return rec


def gather(ctx: Ctx, days: int) -> Dict[str, Any]:
    since_d = ctx.today - dt.timedelta(days=days)
    since, since_ts = since_d.isoformat(), dt.datetime.combine(since_d, dt.time()).timestamp()
    w = ctx.cfg["watch"]
    exts = [str(e).lower() for e in (w.get("extensions") or [])]
    rec: List[Dict[str, Any]] = []
    for r in w.get("repos") or []:
        rec += git_receipts(Path(os.path.expanduser(str(r))), since, str(w.get("author") or ""))
    for d in w.get("dirs") or []:
        rec += file_receipts(Path(os.path.expanduser(str(d))), since_ts, exts, int(w.get("max_files", 150)))
    notes = w.get("notes_dir") or str(ctx.data / "notes")
    rec += note_receipts(Path(os.path.expanduser(str(notes))), since_ts)
    seen = {r["source"] for r in rec}
    for r in read_jsonl(ctx.receipts_file):
        if r.get("date", "") >= since and r.get("source") not in seen:
            rec.append(r)
            seen.add(r.get("source"))
    for r in rec:
        r["firewall_hits"] = firewall_scan(r["text"], ctx.cfg)
    return {
        "since": since, "count": len(rec), "flagged": sum(1 for r in rec if r["firewall_hits"]),
        "receipts": rec,
        "posted": [p.get("title", "") for p in read_json(ctx.posted_file, [])],
        "queued": [i["title"] for i in read_json(ctx.queue_file, []) if i.get("status") != "parked"],
        "pillars": ctx.pillars(), "formats": FORMATS,
    }


def log_receipt(ctx: Ctx, text: str, source: str, kind: str = "manual", date: Optional[str] = None) -> bool:
    """Append one receipt candidate; skips exact-source duplicates. Returns True when written."""
    existing = {r.get("source") for r in read_jsonl(ctx.receipts_file)}
    if source in existing:
        return False
    append_jsonl(ctx.receipts_file, {"date": date or ctx.today.isoformat(), "kind": kind, "text": text[:500],
                                     "source": source, "logged": ctx.now.isoformat(timespec="minutes")})
    return True


def receipt_from_commit(ctx: Ctx, cwd: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", cwd, "log", "-1", "--pretty=%h%x1f%ad%x1f%s", "--date=short"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if "\x1f" not in out:
        return None
    h, d, s = out.split("\x1f", 2)
    src = f"git:{Path(top or cwd).name}@{h}"
    return src if log_receipt(ctx, s, src, kind="commit", date=d) else None


# ---------------------------------------------------------------------------------------------------------------
# Posted log + debriefs + learned weights
# ---------------------------------------------------------------------------------------------------------------
def set_status(ctx: Ctx, iid: str, status: str) -> bool:
    ideas = read_json(ctx.queue_file, [])
    for i in ideas:
        if i["id"] == iid:
            i["status"] = status
            i[f"{status}_on"] = ctx.today.isoformat()
            write_json(ctx.queue_file, ideas)
            return True
    return False


def add_posted(ctx: Ctx, title: str, idea: Optional[str], platform: str, date: Optional[str],
               url: Optional[str]) -> Dict[str, Any]:
    posts = read_json(ctx.posted_file, [])
    entry: Dict[str, Any] = {"id": next_id(posts, "P"), "title": title, "platform": platform,
                             "date": date or ctx.now.isoformat(timespec="minutes")}
    if url:
        entry["url"] = url
    if idea:
        entry["idea"] = idea
        src = next((i for i in read_json(ctx.queue_file, []) if i["id"] == idea), None)
        if src:
            entry["pillar"], entry["format"] = src.get("pillar"), src.get("format")
            set_status(ctx, idea, "posted")
    posts.append(entry)
    write_json(ctx.posted_file, posts)
    return entry


def engagement(m: Dict[str, Any]) -> Optional[float]:
    imp = float(m.get("impressions") or 0)
    if imp <= 0:
        return None
    return (float(m.get("reactions") or 0) + 2 * float(m.get("comments") or 0)
            + 3 * float(m.get("reposts") or 0)) / imp


def recompute_weights(ctx: Ctx, min_n: int = 3, prior: int = 5) -> Dict[str, Dict[str, float]]:
    """Per format/pillar multiplier = shrunk ratio of its mean engagement to the overall mean, clamped 0.8-1.25."""
    posts = {p["id"]: p for p in read_json(ctx.posted_file, [])}
    latest: Dict[str, Dict[str, Any]] = {}
    for d in read_jsonl(ctx.debrief_file):
        latest[d["post"]] = d  # last debrief per post wins (e.g. a 7-day read replaces the 24h read)
    rows = []
    for pid, d in latest.items():
        e = engagement(d)
        if e is not None and pid in posts:
            rows.append((posts[pid], e))
    out: Dict[str, Dict[str, float]] = {"format": {}, "pillar": {}}
    if not rows:
        write_json(ctx.weights_file, out)
        return out
    overall = sum(e for _, e in rows) / len(rows)
    for dim in ("format", "pillar"):
        groups: Dict[str, List[float]] = {}
        for p, e in rows:
            if p.get(dim):
                groups.setdefault(str(p[dim]), []).append(e)
        for k, es in groups.items():
            if len(es) < min_n or overall <= 0:
                continue
            ratio = (sum(es) / len(es)) / overall
            shrunk = 1 + (ratio - 1) * len(es) / (len(es) + prior)
            out[dim][k] = round(max(0.8, min(1.25, shrunk)), 3)
    out["_basis"] = {"posts_with_metrics": len(rows), "overall_engagement": round(overall, 5)}  # type: ignore
    write_json(ctx.weights_file, out)
    return out


def add_debrief(ctx: Ctx, post: str, metrics: Dict[str, Any], notes: str = "") -> Dict[str, Any]:
    posts = read_json(ctx.posted_file, [])
    if not any(p["id"] == post for p in posts):
        raise SystemExit(f"no posted entry {post} (log it first with: posted add)")
    row = {"post": post, "date": ctx.today.isoformat(), **{k: v for k, v in metrics.items() if v is not None}}
    if notes:
        row["notes"] = notes[:500]
    append_jsonl(ctx.debrief_file, row)
    return recompute_weights(ctx)


METRIC_ALIASES = {
    "impressions": ["impressions", "views", "impr"], "reactions": ["reactions", "likes", "reaction"],
    "comments": ["comments", "replies"], "reposts": ["reposts", "shares", "retweets", "boosts"],
    "followers": ["followers", "followers_gained", "follows"],
}


def import_debriefs(ctx: Ctx, path: str) -> int:
    posts = read_json(ctx.posted_file, [])
    by_url = {p.get("url"): p["id"] for p in posts if p.get("url")}
    n = 0
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            pid = low.get("post") or low.get("post_id") or by_url.get(low.get("url", ""))
            if not pid:
                continue
            metrics: Dict[str, Any] = {}
            for key, names in METRIC_ALIASES.items():
                for nm in names:
                    if low.get(nm):
                        metrics[key] = int(float(low[nm].replace(",", "")))
                        break
            add_debrief(ctx, pid, metrics)
            n += 1
    return n


# ---------------------------------------------------------------------------------------------------------------
# Nudge + loop check
# ---------------------------------------------------------------------------------------------------------------
def nudge_message(ctx: Ctx, force: bool = False) -> Optional[str]:
    """Return the nudge line, or None when quiet hours, throttle or a healthy cadence say stay silent."""
    if not force and in_quiet_hours(ctx.now, ctx.cfg):
        return None
    st = cadence_status(ctx)
    ranked = active_ranked(ctx, pass_only=True)
    fresh = fresh_ideas(ctx)
    target = int(ctx.cfg["queue"].get("fresh_target", 8))
    n_cfg = ctx.cfg.get("nudges") or {}
    worth = st["state"] in ("at-risk", "missed") or len(fresh) < max(1, target // 2)
    if not force and n_cfg.get("only_when_at_risk", True) and not worth:
        return None
    if not force:
        state = read_json(ctx.nudge_state, {})
        last = state.get("last")
        if last and ctx.now - dt.datetime.fromisoformat(last) < dt.timedelta(hours=float(n_cfg.get("throttle_hours", 6))):
            return None
    msg = "[ship-receipts] " + cadence_line(st, ranked[0] if ranked else None)
    if len(fresh) < target:
        msg += f" | queue low: {len(fresh)} fresh ideas (target {target}), run receipt-miner"
    write_json(ctx.nudge_state, {"last": ctx.now.isoformat(timespec="minutes")})
    return msg


def desktop_notify(msg: str) -> bool:
    title = "ship-receipts"
    body = msg.replace("[ship-receipts] ", "")[:220]
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            safe = body.replace("\\", "").replace('"', "'")
            subprocess.run(["osascript", "-e", f'display notification "{safe}" with title "{title}"'],
                           timeout=5, capture_output=True)
            return True
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", title, body], timeout=5, capture_output=True)
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def loop_check(ctx: Ctx) -> Dict[str, Any]:
    st = cadence_status(ctx)
    fresh = fresh_ideas(ctx)
    target = int(ctx.cfg["queue"].get("fresh_target", 8))
    cadence_met = st["state"] in ("floor-met", "target-met")
    queue_ok = len(fresh) >= target
    quiet = in_quiet_hours(ctx.now, ctx.cfg)
    if quiet:
        action = "sleep"
    elif not queue_ok:
        action = "mine"
    elif not cadence_met and st["state"] != "missed":
        action = "write"
    else:
        action = "idle"
    done = queue_ok and (cadence_met or st["state"] == "missed")
    ranked = active_ranked(ctx, pass_only=True)
    return {
        "now": ctx.now.isoformat(timespec="minutes"), "quiet_hours": quiet, "cadence": st,
        "cadence_met": cadence_met, "fresh_count": len(fresh), "fresh_target": target, "queue_ok": queue_ok,
        "done": done, "next_action": action,
        "top_ideas": [{"id": i["id"], "title": i["title"], "score": i["score"]} for i in ranked[:3]],
        "suggested_recheck_minutes": 720 if done or quiet else (240 if action == "idle" else 90),
    }


# ---------------------------------------------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------------------------------------------
def render_queue(ctx: Ctx) -> str:
    ranked = active_ranked(ctx)
    st = cadence_status(ctx)
    allq = read_json(ctx.queue_file, [])
    L = [
        "# Ship queue",
        "",
        "> Generated by `receipts.py queue render`. Ideas only: you answer the questions in your own words, and",
        "> that answer is the post. Source pointers are internal; never paste them into a post.",
        "",
        f"_Rendered {ctx.now:%Y-%m-%d %H:%M} · {len(ranked)} active / {len(allq)} total · "
        f"{cadence_line(st, None)}_",
        "",
        "| # | ID | Score | Title | Pillar | Format | Effort | FW | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, i in enumerate(ranked, 1):
        L.append(f"| {n} | {i['id']} | {i['score']} | {i['title']} | {i['pillar']} | {i['format']} | "
                 f"{i['effort']} | {i.get('firewall', '?')} | {i['status']} |")
    L += ["", "## Idea cards", ""]
    for i in ranked:
        fw = i.get("firewall", "?")
        if i.get("firewall_hits"):
            fw += " (" + "; ".join(i["firewall_hits"]) + ")"
        L += [
            f"### {i['id']} · {i['title']} · {i['score']}",
            f"- **Pillar / format / effort:** {i['pillar']} · {i['format']} · {i['effort']}"
            + (f" · risk {i['risk']}" if i.get("risk") else ""),
            f"- **Receipt:** {i['receipt']}",
            f"- **Why now:** {i.get('why_now', '')}",
            "- **Answer these in your own words (that's the post):**",
        ]
        L += [f"  {k}. {q}" for k, q in enumerate(i.get("questions", []), 1)]
        L += [f"- **Source (internal):** {i.get('source', '')}", f"- **Firewall:** {fw}", ""]
    return "\n".join(L) + "\n"


def hooks_snippet() -> Dict[str, Any]:
    hk = REPO / "hooks"
    return {"hooks": {
        "PostToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": f'python3 "{hk / "post_commit.py"}"', "timeout": 10}]}],
        "SessionStart": [{"hooks": [
            {"type": "command", "command": f'python3 "{hk / "cadence_nudge.py"}" --event session-start',
             "timeout": 10}]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": f'python3 "{hk / "cadence_nudge.py"}" --event stop', "timeout": 10}]}],
    }}


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="receipts.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data")
    ap.add_argument("--config")
    ap.add_argument("--today")
    ap.add_argument("--now")
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    g = sp.add_parser("gather")
    g.add_argument("--days", type=int, default=7)
    g.add_argument("--out")
    r = sp.add_parser("receipt")
    rs = r.add_subparsers(dest="sub", required=True)
    ra = rs.add_parser("add")
    ra.add_argument("text", nargs="+")
    ra.add_argument("--source")
    ra.add_argument("--kind", default="manual")
    a = sp.add_parser("add")
    a.add_argument("file")
    a.add_argument("--dry-run", action="store_true")
    f = sp.add_parser("firewall")
    f.add_argument("text", nargs="*")
    f.add_argument("--file")
    q = sp.add_parser("queue")
    qs = q.add_subparsers(dest="sub", required=True)
    qr = qs.add_parser("render")
    qr.add_argument("--out")
    qs.add_parser("list")
    qst = qs.add_parser("status")
    qst.add_argument("id")
    qst.add_argument("status", choices=STATUSES)
    c = sp.add_parser("cadence")
    cs = c.add_subparsers(dest="sub", required=True)
    cst = cs.add_parser("status")
    cst.add_argument("--json", action="store_true")
    p = sp.add_parser("posted")
    ps = p.add_subparsers(dest="sub", required=True)
    pa = ps.add_parser("add")
    pa.add_argument("--title", required=True)
    pa.add_argument("--idea")
    pa.add_argument("--platform", default="linkedin")
    pa.add_argument("--date")
    pa.add_argument("--url")
    d = sp.add_parser("debrief")
    ds = d.add_subparsers(dest="sub", required=True)
    da = ds.add_parser("add")
    da.add_argument("--post", required=True)
    for m in ("impressions", "reactions", "comments", "reposts", "followers"):
        da.add_argument(f"--{m}", type=int)
    da.add_argument("--notes", default="")
    di = ds.add_parser("import")
    di.add_argument("file")
    w = sp.add_parser("weights")
    ws = w.add_subparsers(dest="sub", required=True)
    ws.add_parser("show")
    n = sp.add_parser("nudge")
    n.add_argument("--channel", choices=["stdout", "system-message", "desktop"], default="stdout")
    n.add_argument("--force", action="store_true")
    lp = sp.add_parser("loop")
    ls = lp.add_subparsers(dest="sub", required=True)
    lc = ls.add_parser("check")
    lc.add_argument("--json", action="store_true")
    sp.add_parser("hooks-snippet")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    ns = build_parser().parse_args(argv)
    ctx = Ctx(ns.data, ns.config, ns.today, ns.now)
    cmd, sub = ns.cmd, getattr(ns, "sub", None)
    if cmd == "init":
        ctx.data.mkdir(parents=True, exist_ok=True)
        (ctx.data / "notes").mkdir(exist_ok=True)
        dest = ctx.data / "ship.yaml"
        if not dest.exists():
            shutil.copy(REPO / "config" / "ship.example.yaml", dest)
        print(f"data dir {ctx.data} · config {dest} (edit it: cadence, quiet hours, repos, firewall terms)")
        return 0
    if cmd == "gather":
        payload = gather(ctx, ns.days)
        s = json.dumps(payload, indent=1, ensure_ascii=False)
        if ns.out:
            Path(ns.out).write_text(s)
            print(f"{payload['count']} receipts since {payload['since']} ({payload['flagged']} firewall-tagged) -> {ns.out}")
        else:
            print(s)
        return 0
    if cmd == "receipt":
        text = " ".join(ns.text)
        src = ns.source or f"receipt:{ctx.now:%Y%m%d%H%M}:{abs(hash(text)) % 10000:04d}"
        print("logged" if log_receipt(ctx, text, src, ns.kind) else "duplicate source, skipped")
        return 0
    if cmd == "add":
        rep = add_ideas(ctx, json.loads(Path(ns.file).read_text()), ns.dry_run)
        print(f"added {len(rep['added'])} · rejected {len(rep['rejected'])} · flagged {len(rep['flagged'])} · "
              f"store {rep['store']}" + (" (dry run)" if ns.dry_run else ""))
        for r in rep["rejected"]:
            print(f"  REJECT {r['title']!r}: {r['why']}")
        for r in rep["flagged"]:
            print(f"  FLAG {r['id']} {r['title']!r}: {'; '.join(r['hits'])}")
        for note in rep["notes"]:
            print(f"  VERIFY {note}")
        return 0
    if cmd == "firewall":
        text = Path(ns.file).read_text() if ns.file else " ".join(ns.text)
        if not text and not sys.stdin.isatty():
            text = sys.stdin.read()
        hits = firewall_scan(text, ctx.cfg)
        print("PASS" if not hits else "FLAG\n  " + "\n  ".join(hits))
        return 0 if not hits else 2
    if cmd == "queue":
        if sub == "render":
            md = render_queue(ctx)
            out = Path(ns.out) if ns.out else ctx.data / "QUEUE.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md)
            print(f"rendered {out}")
        elif sub == "list":
            for k, i in enumerate(active_ranked(ctx), 1):
                print(f"{k:>2}. {i['id']} {i['score']:>5} {i.get('firewall', '?'):<4} {i['status']:<6} {i['title']}")
        else:
            ok = set_status(ctx, ns.id, ns.status)
            print(f"{ns.id} -> {ns.status}" if ok else f"no idea {ns.id}")
            return 0 if ok else 1
        return 0
    if cmd == "cadence":
        st = cadence_status(ctx)
        ranked = active_ranked(ctx, pass_only=True)
        print(json.dumps(st, indent=1) if ns.json else cadence_line(st, ranked[0] if ranked else None))
        return 0
    if cmd == "posted":
        e = add_posted(ctx, ns.title, ns.idea, ns.platform, ns.date, ns.url)
        print(f"logged {e['id']} '{e['title']}' on {e['platform']} at {e['date']}")
        print(cadence_line(cadence_status(ctx), None))
        return 0
    if cmd == "debrief":
        if sub == "add":
            m = {k: getattr(ns, k) for k in ("impressions", "reactions", "comments", "reposts", "followers")}
            wts = add_debrief(ctx, ns.post, m, ns.notes)
            print(f"debrief logged for {ns.post} · weights {json.dumps(wts)}")
        else:
            print(f"imported {import_debriefs(ctx, ns.file)} rows · weights {json.dumps(load_weights(ctx))}")
        return 0
    if cmd == "weights":
        print(json.dumps(load_weights(ctx), indent=1))
        return 0
    if cmd == "nudge":
        msg = nudge_message(ctx, ns.force)
        if not msg:
            return 0
        if ns.channel == "system-message":
            print(json.dumps({"systemMessage": msg}))
        elif ns.channel == "desktop":
            if not desktop_notify(msg):
                print(msg)
        else:
            print(msg)
        return 0
    if cmd == "loop":
        res = loop_check(ctx)
        if ns.json:
            print(json.dumps(res, indent=1))
        else:
            print(f"next: {res['next_action']} · done={res['done']} · fresh {res['fresh_count']}/{res['fresh_target']}"
                  f" · {cadence_line(res['cadence'], None)} · recheck in ~{res['suggested_recheck_minutes']}m")
        return 0
    if cmd == "hooks-snippet":
        print(json.dumps(hooks_snippet(), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
