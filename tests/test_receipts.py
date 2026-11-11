"""Unit tests for tools/receipts.py and the hook scripts. Run: python3 -m unittest discover -s tests -v"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "hooks"))

import receipts as R  # noqa: E402
import post_commit  # noqa: E402

SAMPLE = json.loads((ROOT / "examples" / "ideas.sample.json").read_text())


def idea(**kw):
    base = {
        "title": "Swapped polling for webhooks", "pillar": "BUILD", "format": "build-in-public", "effort": "S",
        "receipt": "Replaced a 5-minute polling loop with webhooks; API calls fell from 12k to 900 a day.",
        "source": "git:sample@abc1234", "why_now": "Shipped this week.",
        "questions": ["What made polling painful?", "What broke during the switch?", "Would you do it again?"],
        "scores": {"receipt": 5, "timeliness": 4, "fit": 4},
    }
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ship-receipts-test-"))
        self.cfg = self.tmp / "ship.yaml"
        self.cfg.write_text((ROOT / "config" / "ship.example.yaml").read_text())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, now="2026-03-11T10:00"):  # 2026-03-11 is a Wednesday
        return R.Ctx(data=str(self.tmp), config=str(self.cfg), now=now)

    def cli(self, *args, now="2026-03-11T10:00"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = R.main(["--data", str(self.tmp), "--config", str(self.cfg), "--now", now, *args])
        return code, buf.getvalue()


class TestYaml(Base):
    def test_example_config_parses(self):
        c = R.parse_yaml(self.cfg.read_text())
        self.assertEqual(c["cadence"]["floor_per_week"], 2)
        self.assertEqual(c["cadence"]["preferred_days"], ["tue", "wed", "thu"])
        self.assertEqual(c["quiet_hours"]["start"], "21:00")
        self.assertFalse(c["nudges"]["desktop"])
        self.assertIn("Project Bluebird", c["firewall"]["terms"])
        self.assertIn(r"re:\bCUST-\d+\b", c["firewall"]["terms"])
        self.assertEqual(c["watch"]["repos"], ["~/code/my-service", "~/code/side-project"])
        self.assertEqual(c["firewall"]["allow"], [])

    def test_config_resolves_against_repo_and_json(self):
        d = self.tmp / "data"
        d.mkdir()
        (d / "ship.json").write_text(json.dumps({"cadence": {"floor_per_week": 5}}))
        old = os.environ.pop("SHIP_RECEIPTS_CONFIG", None)
        cwd = os.getcwd()
        try:
            os.chdir(str(self.tmp))
            ctx = R.Ctx(data=str(d))
            self.assertEqual(ctx.config_path, d / "ship.json")
            self.assertEqual(ctx.cfg["cadence"]["floor_per_week"], 5)
        finally:
            os.chdir(cwd)
            if old is not None:
                os.environ["SHIP_RECEIPTS_CONFIG"] = old

    def test_defaults_fill_missing(self):
        self.cfg.write_text("cadence:\n  floor_per_week: 4\n")
        ctx = self.ctx()
        self.assertEqual(ctx.cfg["cadence"]["floor_per_week"], 4)
        self.assertEqual(ctx.cfg["cadence"]["target_per_week"], 3)
        self.assertEqual(ctx.cfg["queue"]["fresh_target"], 8)


class TestFirewall(Base):
    def scan(self, text):
        return R.firewall_scan(text, self.ctx().cfg)

    def test_plain_receipt_passes(self):
        self.assertEqual(self.scan("Migrated a cron job to a queue, cut failures 40%."), [])
        self.assertEqual(self.scan("Spent $40 on a cheaper instance size."), [])

    def test_money_tied_to_named_org_flags(self):
        hits = self.scan("Closed a $2M renewal from Acme Corp last week.")
        self.assertTrue(any(h.startswith("money tied to a named org") for h in hits), hits)

    def test_generic_patterns(self):
        self.assertTrue(self.scan("ping me at someone@example.com"))
        self.assertTrue(self.scan("call 555-867-5309"))
        self.assertTrue(self.scan("token sk-abcdefghijklmnop1234"))
        self.assertTrue(self.scan("deployed to build01.corp"))
        self.assertTrue(self.scan("db at 10.0.3.12"))

    def test_widened_and_new_patterns(self):
        def has(text, cat):
            hits = self.scan(text)
            self.assertTrue(any(h.startswith(cat + ":") for h in hits), (text, hits))
        has("call +44 20 7946 0958 tomorrow", "phone number")
        has("reach me on +49 30 123456", "phone number")
        has("DB_PASSWORD: hunter2", "credential assignment")
        has("github_token=abc", "credential assignment")
        has("-----BEGIN OPENSSH PRIVATE KEY-----", "private key block")
        has("Authorization: Bearer abcdefghijklmnop0123", "bearer token")
        has("eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2Q", "JWT")
        has("postgres://admin:example@db.example.test/app", "credentials in URL")
        has("https://hooks.slack.com/services/TEST/TEST/placeholder", "Slack webhook")
        has("https://discord.com/api/webhooks/0000/placeholder", "Discord webhook")
        has("employee id 123-45-6789", "SSN-style ID")
        self.assertEqual(self.scan("released on 2026-03-11, version 1.2.3, up 12% in 3 weeks"), [])

    def test_confidential_terms_and_regex(self):
        self.assertTrue(self.scan("we shipped project bluebird today"))
        self.assertTrue(self.scan("fixed CUST-1234 for them"))
        self.assertEqual(self.scan("a bluebird landed on the deck"), [])

    def test_allow_list(self):
        self.cfg.write_text(self.cfg.read_text().replace("allow: []", "allow: [example.com]"))
        self.assertEqual(self.scan("write to hello@example.com"), [])


class TestIdeas(Base):
    def test_add_sample_queue(self):
        rep = R.add_ideas(self.ctx(), json.loads(json.dumps(SAMPLE)))
        self.assertEqual(len(rep["added"]), 5, rep)
        self.assertEqual(rep["rejected"], [])
        self.assertEqual(rep["flagged"], [])

    def test_rejects_post_copy(self):
        rep = R.add_ideas(self.ctx(), [idea(draft="Here's what nobody tells you about webhooks...")])
        self.assertEqual(len(rep["rejected"]), 1)
        self.assertIn("post copy", rep["rejected"][0]["why"])
        long = idea(receipt="Line one.\n\nA second paragraph that reads like a finished post.")
        self.assertIn("post copy", R.add_ideas(self.ctx(), [long])["rejected"][0]["why"])
        tags = idea(why_now="#devops #webhooks trending")
        self.assertIn("hashtags", R.add_ideas(self.ctx(), [tags])["rejected"][0]["why"])

    def test_requires_exactly_three_questions(self):
        rep = R.add_ideas(self.ctx(), [idea(questions=["Why?", "How?"])])
        self.assertIn("exactly 3 questions", rep["rejected"][0]["why"])
        rep = R.add_ideas(self.ctx(), [idea(questions=["Why?", "How?", "Tell the story."])])
        self.assertIn("end with '?'", rep["rejected"][0]["why"])

    def test_dedupe_against_queue(self):
        ctx = self.ctx()
        R.add_ideas(ctx, [idea()])
        rep = R.add_ideas(ctx, [idea(title="Swapped polling for webhooks, part two?")])
        self.assertIn("duplicate", rep["rejected"][0]["why"])

    def test_dedupe_against_posted(self):
        ctx = self.ctx()
        R.add_posted(ctx, "Swapped polling for webhooks", None, "linkedin", "2026-03-02T08:30", None)
        rep = R.add_ideas(ctx, [idea()])
        self.assertIn("already posted", rep["rejected"][0]["why"])
        rep = R.add_ideas(ctx, [idea(sequel_of="P001")])
        self.assertEqual(len(rep["added"]), 1)

    def test_firewall_flag_halves_score(self):
        ctx = self.ctx()
        rep = R.add_ideas(ctx, [idea(receipt="Shipped webhooks for Project Bluebird; calls fell 90%.")])
        self.assertEqual(len(rep["flagged"]), 1)
        q = R.read_json(ctx.queue_file, [])
        self.assertEqual(q[0]["firewall"], "FLAG")
        self.assertEqual(q[0]["score"], R.score(dict(q[0], firewall="PASS")) / 2)
        self.assertEqual(R.active_ranked(ctx, pass_only=True), [])

    def test_new_config_term_applies_to_queued_ideas(self):
        ctx = self.ctx()
        R.add_ideas(ctx, [idea(receipt="Replaced polling with webhooks for Northwind; calls fell 90%.")])
        self.assertEqual(len(R.active_ranked(ctx, pass_only=True)), 1)
        self.cfg.write_text(self.cfg.read_text().replace("    - acme-internal", "    - acme-internal\n    - Northwind"))
        ctx = self.ctx()
        self.assertEqual(R.active_ranked(ctx, pass_only=True), [])
        self.assertNotIn("webhooks", R.nudge_message(ctx, force=True).lower())

    def test_dedupe_threshold_configurable(self):
        near = dict(title="Webhooks for billing events", receipt="Moved billing events to a bus.", source="git:x@1")
        ctx = self.ctx()
        R.add_ideas(ctx, [idea()])
        self.assertEqual(len(R.add_ideas(ctx, [idea(**near)], dry=True)["added"]), 1)  # default 0.75: distinct
        self.cfg.write_text(self.cfg.read_text().replace("dedupe_threshold: 0.75", "dedupe_threshold: 0.3"))
        ctx = self.ctx()
        self.assertEqual(ctx.cfg["queue"]["dedupe_threshold"], 0.3)
        self.assertIn("duplicate", R.add_ideas(ctx, [idea(**near)])["rejected"][0]["why"])

    def test_verify_caps_unbacked_receipt_score(self):
        ctx = self.ctx()
        rep = R.add_ideas(ctx, [idea(receipt="Webhooks are the future, everyone says so.", source="trend",
                                     scores={"receipt": 5, "timeliness": 3, "fit": 3})])
        self.assertTrue(rep["notes"], rep)
        self.assertLessEqual(R.read_json(ctx.queue_file, [])[0]["scores"]["receipt"], 2)

    def test_ranking_and_status(self):
        ctx = self.ctx()
        R.add_ideas(ctx, json.loads(json.dumps(SAMPLE)))
        ranked = R.active_ranked(ctx)
        self.assertGreaterEqual(ranked[0]["score"], ranked[-1]["score"])
        self.assertTrue(R.set_status(ctx, ranked[-1]["id"], "picked"))
        self.assertEqual(R.active_ranked(ctx)[0]["id"], ranked[-1]["id"])  # picked ideas lead


class TestCadence(Base):
    def post(self, ctx, when, title="t"):
        R.add_posted(ctx, title + when, None, "linkedin", when, None)

    def test_quiet_hours_wraparound(self):
        cfg = {"quiet_hours": {"start": "22:00", "end": "06:00"}}
        q = lambda h, m=0: R.in_quiet_hours(dt.datetime(2026, 3, 11, h, m), cfg)  # noqa: E731
        self.assertTrue(q(23))
        self.assertTrue(q(2))
        self.assertTrue(q(22))
        self.assertFalse(q(6))
        self.assertFalse(q(12))
        day = {"quiet_hours": {"start": "12:00", "end": "13:00"}}
        self.assertTrue(R.in_quiet_hours(dt.datetime(2026, 3, 11, 12, 30), day))
        self.assertFalse(R.in_quiet_hours(dt.datetime(2026, 3, 11, 13, 0), day))

    def test_states_through_the_week(self):
        # Mon, nothing posted, three preferred days ahead -> on track
        self.assertEqual(R.cadence_status(self.ctx("2026-03-09T10:00"))["state"], "on-track")
        # last day of the week, 0 posts, need 2 with 1 day left -> missed
        self.assertEqual(R.cadence_status(self.ctx("2026-03-15T10:00"))["state"], "missed")
        # Wed, nothing posted, need 2 with wed+thu left -> at risk
        st = R.cadence_status(self.ctx("2026-03-11T10:00"))
        self.assertEqual((st["state"], st["preferred_days_left"]), ("at-risk", 2))
        ctx = self.ctx("2026-03-11T10:00")
        self.post(ctx, "2026-03-10T08:30")
        self.assertEqual(R.cadence_status(ctx)["state"], "on-track")  # 1 of 2, needs 1 with wed+thu left
        # Thu with that 1 post: needs 1, only thu left among preferred days -> at risk
        self.assertEqual(R.cadence_status(self.ctx("2026-03-12T10:00"))["state"], "at-risk")

    def test_floor_and_target(self):
        ctx = self.ctx("2026-03-12T10:00")
        self.post(ctx, "2026-03-10T08:30")
        self.post(ctx, "2026-03-11T08:30")
        self.assertEqual(R.cadence_status(ctx)["state"], "floor-met")
        self.post(ctx, "2026-03-12T08:30")
        st = R.cadence_status(ctx)
        self.assertEqual(st["state"], "target-met")
        self.assertEqual(st["posts_this_week"], 3)

    def test_last_week_does_not_count(self):
        ctx = self.ctx("2026-03-11T10:00")
        self.post(ctx, "2026-03-06T08:30")
        self.assertEqual(R.cadence_status(ctx)["posts_this_week"], 0)

    def test_cadence_line_matches_hook_wording(self):
        ctx = self.ctx("2026-03-12T10:00")
        self.post(ctx, "2026-03-10T08:30")
        R.add_ideas(ctx, json.loads(json.dumps(SAMPLE)))
        line = R.cadence_line(R.cadence_status(ctx), R.active_ranked(ctx, pass_only=True)[0])
        self.assertTrue(line.startswith("cadence at risk: 1 post this week, floor 2 | top idea: I"), line)


class TestNudgeAndLoop(Base):
    def test_nudge_quiet_hours_and_throttle(self):
        self.assertIsNone(R.nudge_message(self.ctx("2026-03-11T23:00")))  # quiet
        msg = R.nudge_message(self.ctx("2026-03-11T10:00"))
        self.assertIn("cadence at risk", msg)
        self.assertIn("queue low", msg)
        self.assertIsNone(R.nudge_message(self.ctx("2026-03-11T11:00")))  # throttled (6h)
        self.assertIsNotNone(R.nudge_message(self.ctx("2026-03-11T16:30")))

    def test_nudge_silent_when_healthy(self):
        ctx = self.ctx("2026-03-09T10:00")
        topics = [("Webhook retries", "webhook retry handler"), ("Schema migration", "zero-downtime schema change"),
                  ("Cache warmup", "cache warmup script"), ("Log sampling", "log sampling filter"),
                  ("Flaky test triage", "flaky test quarantine list"), ("Cost report", "monthly cloud cost report"),
                  ("Feature flags", "feature flag cleanup"), ("Alert tuning", "pager alert thresholds")]
        many = [idea(title=t, receipt=f"Rebuilt the {r}; saved {n + 1} hours a week.", source=f"git:s@{n:07d}")
                for n, (t, r) in enumerate(topics)]
        rep = R.add_ideas(ctx, many)
        self.assertEqual(len(rep["added"]), 8, rep)
        self.assertIsNone(R.nudge_message(ctx))

    def test_loop_check_actions(self):
        ctx = self.ctx("2026-03-11T10:00")
        res = R.loop_check(ctx)
        self.assertEqual(res["next_action"], "mine")
        self.assertFalse(res["done"])
        self.assertEqual(R.loop_check(self.ctx("2026-03-11T23:30"))["next_action"], "sleep")
        self.cfg.write_text(self.cfg.read_text().replace("fresh_target: 8", "fresh_target: 3"))
        ctx = self.ctx("2026-03-11T10:00")
        R.add_ideas(ctx, json.loads(json.dumps(SAMPLE)))
        self.assertEqual(R.loop_check(ctx)["next_action"], "write")
        R.add_posted(ctx, "one", None, "linkedin", "2026-03-10T08:30", None)
        R.add_posted(ctx, "two", None, "linkedin", "2026-03-11T08:30", None)
        res = R.loop_check(ctx)
        self.assertTrue(res["done"])
        self.assertEqual(res["next_action"], "idle")

    def test_loop_done_on_missed_week_when_queue_full(self):
        self.cfg.write_text(self.cfg.read_text().replace("fresh_target: 8", "fresh_target: 3"))
        ctx = self.ctx("2026-03-15T10:00")  # last day of the week, 0 posts, floor 2 -> missed
        self.assertFalse(R.loop_check(ctx)["done"])  # queue still empty
        R.add_ideas(ctx, json.loads(json.dumps(SAMPLE)))
        res = R.loop_check(ctx)
        self.assertEqual(res["cadence"]["state"], "missed")
        self.assertTrue(res["done"])

    def test_cli_cadence_and_nudge_channels(self):
        code, out = self.cli("cadence", "status")
        self.assertEqual(code, 0)
        self.assertIn("cadence at risk: 0 posts this week, floor 2", out)
        code, out = self.cli("nudge", "--channel", "system-message")
        self.assertIn("systemMessage", json.loads(out))


class TestDebriefWeights(Base):
    def test_weights_learn_from_metrics(self):
        ctx = self.ctx()
        for n in range(8):
            fmt = "data-point" if n < 4 else "pov"
            e = R.add_posted(ctx, f"post {n}", None, "linkedin", f"2026-02-{n + 1:02d}T08:30", None)
            posts = R.read_json(ctx.posted_file, [])
            posts[-1]["format"], posts[-1]["pillar"] = fmt, "BUILD"
            R.write_json(ctx.posted_file, posts)
            R.add_debrief(ctx, e["id"], {"impressions": 1000, "reactions": 60 if fmt == "data-point" else 10,
                                         "comments": 5, "reposts": 1})
        w = R.load_weights(ctx)
        self.assertGreater(w["format"]["data-point"], 1.0)
        self.assertLess(w["format"]["pov"], 1.0)
        self.assertTrue(0.8 <= w["format"]["pov"] <= 1.25)
        a = R.score({"format": "data-point", "pillar": "BUILD", "scores": {"receipt": 4, "timeliness": 4, "fit": 4}}, w)
        b = R.score({"format": "pov", "pillar": "BUILD", "scores": {"receipt": 4, "timeliness": 4, "fit": 4}}, w)
        self.assertGreater(a, b)

    def test_too_few_posts_no_weights(self):
        ctx = self.ctx()
        e = R.add_posted(ctx, "solo", None, "linkedin", "2026-03-02T08:30", None)
        w = R.add_debrief(ctx, e["id"], {"impressions": 500, "reactions": 10})
        self.assertEqual(w["format"], {})

    def test_csv_import(self):
        ctx = self.ctx()
        R.add_posted(ctx, "first", None, "linkedin", "2026-03-03T08:30", None)
        self.assertEqual(R.import_debriefs(ctx, str(ROOT / "examples" / "analytics.sample.csv")), 1)
        self.assertEqual(R.read_jsonl(ctx.debrief_file)[0]["impressions"], 1840)

    def test_posted_marks_idea(self):
        ctx = self.ctx()
        R.add_ideas(ctx, json.loads(json.dumps(SAMPLE)))
        e = R.add_posted(ctx, "Cron job post", "I001", "linkedin", None, None)
        self.assertEqual(e["format"], "build-in-public")
        self.assertEqual(R.read_json(ctx.queue_file, [])[0]["status"], "posted")


class TestGatherAndHooks(Base):
    def make_repo(self):
        repo = self.tmp / "demo-repo"
        repo.mkdir()
        env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                   GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, env=env)  # noqa
        run("init", "-q")
        (repo / "queue.md").write_text("# Queue migration notes\n")
        run("add", ".")
        run("commit", "-q", "-m", "Move nightly cron job onto a retrying queue")
        return repo

    def test_gather_git_files_notes(self):
        repo = self.make_repo()
        notes = self.tmp / "notes"
        notes.mkdir()
        (notes / "today.md").write_text("Learned that retries without jitter just move the spike.")
        self.cfg.write_text(self.cfg.read_text()
                            .replace("    - ~/code/my-service\n    - ~/code/side-project", f"    - {repo}")
                            .replace("    - ~/work/docs", f"    - {repo}")
                            .replace("notes_dir: ~/.ship-receipts/notes", f"notes_dir: {notes}"))
        today = dt.date.today().isoformat()
        payload = R.gather(R.Ctx(data=str(self.tmp), config=str(self.cfg), today=today), 2)
        kinds = {r["kind"] for r in payload["receipts"]}
        self.assertEqual(kinds, {"commit", "file", "note"}, payload["receipts"])
        commit = next(r for r in payload["receipts"] if r["kind"] == "commit")
        self.assertIn("retrying queue", commit["text"])
        f = next(r for r in payload["receipts"] if r["kind"] == "file")
        self.assertIn("Queue migration notes", f["text"])

    def test_hook_detects_commit(self):
        yes = {"tool_name": "Bash", "tool_input": {"command": "git add -A && git commit -m 'x'"}}
        self.assertTrue(post_commit.is_commit(yes))
        self.assertTrue(post_commit.is_commit({"tool_name": "Bash", "tool_input": {"command": "git -C /r commit -q"}}))
        self.assertFalse(post_commit.is_commit({"tool_name": "Bash", "tool_input": {"command": "git status"}}))
        self.assertFalse(post_commit.is_commit({"tool_name": "Bash", "tool_input": {"command": "echo commit"}}))
        for cmd in ("echo git commit", "grep 'git commit' notes.md", "man git commit"):
            self.assertFalse(post_commit.is_commit({"tool_name": "Bash", "tool_input": {"command": cmd}}), cmd)
        for cmd in ("cd r && git commit -q", "FOO=1 git -c a=b commit", "(cd r; git commit)"):
            self.assertTrue(post_commit.is_commit({"tool_name": "Bash", "tool_input": {"command": cmd}}), cmd)
        self.assertFalse(post_commit.is_commit({"tool_name": "Write", "tool_input": {"command": "git commit"}}))

    def test_hook_end_to_end_logs_receipt(self):
        repo = self.make_repo()
        payload = json.dumps({"tool_name": "Bash", "cwd": str(repo),
                              "tool_input": {"command": "git commit -m 'Move nightly cron job'"}})
        env = dict(os.environ, SHIP_RECEIPTS_HOME=str(self.tmp), SHIP_RECEIPTS_CONFIG=str(self.cfg))
        for _ in range(2):  # second run must dedupe
            p = subprocess.run([sys.executable, str(ROOT / "hooks" / "post_commit.py")], input=payload,
                               capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0)
            self.assertEqual(p.stdout, "")
        rows = R.read_jsonl(self.tmp / "receipts.jsonl")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "commit")
        self.assertTrue(rows[0]["source"].startswith("git:demo-repo@"))

    def test_hooks_never_fail(self):
        env = dict(os.environ, SHIP_RECEIPTS_HOME=str(self.tmp), SHIP_RECEIPTS_CONFIG=str(self.cfg))
        for script, inp in (("post_commit.py", "not json"), ("cadence_nudge.py", "")):
            p = subprocess.run([sys.executable, str(ROOT / "hooks" / script), "--event", "stop"], input=inp,
                               capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
        p = subprocess.run([sys.executable, str(ROOT / "hooks" / "cadence_nudge.py"), "--event", "stop"], input="{}",
                           capture_output=True, text=True, env=env)
        if p.stdout.strip():
            self.assertIn("systemMessage", json.loads(p.stdout))

    def test_hooks_snippet_shape(self):
        s = R.hooks_snippet()["hooks"]
        self.assertEqual(s["PostToolUse"][0]["matcher"], "Bash")
        self.assertEqual(s["Stop"][0]["hooks"][0]["type"], "command")
        json.loads((ROOT / "hooks" / "settings.example.json").read_text())


if __name__ == "__main__":
    unittest.main()
