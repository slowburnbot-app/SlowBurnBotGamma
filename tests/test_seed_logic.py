"""Decision logic of the seed pool (bot-client/burnBot_seeds.py, burnBot_followGroup.py).

Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from tests import _stubs  # noqa: F401  (stubs selenium, sets sys.path)

import burnBot_seeds as seeds
from burnBot_seeds import SeedBook, decide_seed_use, seed_card_verdict

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def seed(**kw):
    base = {"id": "s1", "handle": "x", "last_saturation": None, "last_action": None,
            "strikes": 0, "complete": 0, "followed_back": 0, "rate": None}
    base.update(kw)
    return base


class FakeApi:
    def __init__(self):
        self.updates = []

    def update_seed(self, seed_id, **fields):
        self.updates.append((seed_id, fields))
        return {}


def book(api, action="likers", rate=0.3):
    lines = []
    return SeedBook(api, rate, "acct", "scope", "lbl-", lines.append, action), lines


class DecideSeedUse(unittest.TestCase):
    def test_first_saturated_use_only_records(self):
        fields, reason, cooldown = decide_seed_use(seed(), 90, 0.3, action="likers", now=NOW)
        self.assertIsNone(reason)
        self.assertEqual(fields["last_saturation"], 90)
        self.assertEqual(fields["last_action"], "likers")

    def test_two_saturated_uses_of_the_same_action_start_a_cooldown(self):
        s = seed(last_saturation=85, last_action="likers")
        fields, reason, cooldown = decide_seed_use(s, 90, 0.3, action="likers", now=NOW)
        self.assertTrue(reason.startswith("saturated 85% then 90%"))
        self.assertTrue(cooldown)
        self.assertFalse(fields["active"])
        self.assertEqual(fields["cooldown_until"], NOW + timedelta(days=seeds.COOLDOWN_DAYS))

    def test_two_dry_uses_retire_with_the_real_reason_not_saturated(self):
        s = seed(last_saturation=100, last_action="likers")
        _f, reason, cooldown = decide_seed_use(s, 100, 0.3, action="likers",
                                               dry_label="no likes link (like counts hidden?)", now=NOW)
        self.assertTrue(cooldown)
        self.assertIn("no likes link", reason)
        self.assertNotIn("saturated", reason)

    def test_saturation_of_another_action_does_not_count(self):
        # a "similar" use that found no panel (100%) must not retire a good likers seed
        s = seed(last_saturation=100, last_action="similar")
        _fields, reason, _cd = decide_seed_use(s, 90, 0.3, action="likers", now=NOW)
        self.assertIsNone(reason)

    def test_legacy_seed_without_last_action_needs_one_more_use(self):
        s = seed(last_saturation=100, last_action=None)
        _fields, reason, _cd = decide_seed_use(s, 90, 0.3, action="likers", now=NOW)
        self.assertIsNone(reason)

    def test_one_low_use_between_two_high_ones_resets_the_streak(self):
        s = seed(last_saturation=30, last_action="likers")
        _fields, reason, _cd = decide_seed_use(s, 90, 0.3, action="likers", now=NOW)
        self.assertIsNone(reason)

    def test_certain_reason_is_permanent(self):
        fields, reason, cooldown = decide_seed_use(seed(), None, 0.3, action="likers",
                                                   retire_reason="private profile", now=NOW)
        self.assertEqual(reason, "private profile")
        self.assertFalse(cooldown)
        self.assertNotIn("cooldown_until", fields)

    def test_soft_failure_needs_two_strikes(self):
        f1, r1, _ = decide_seed_use(seed(), None, 0.3, action="likers", soft_reason="no posts", now=NOW)
        self.assertIsNone(r1)
        self.assertEqual(f1["strikes"], 1)
        f2, r2, cd2 = decide_seed_use(seed(strikes=1), None, 0.3, action="likers", soft_reason="no posts", now=NOW)
        self.assertEqual(r2, "no posts x2")
        self.assertTrue(cd2)
        self.assertIn("cooldown_until", f2)

    def test_good_use_ends_a_strike_streak(self):
        fields, reason, _ = decide_seed_use(seed(strikes=1), 20, 0.3, action="likers", good=True, now=NOW)
        self.assertIsNone(reason)
        self.assertEqual(fields["strikes"], 0)

    def test_low_follow_back_after_enough_data_is_permanent(self):
        s = seed(complete=40, followed_back=2, rate=0.05)
        _fields, reason, cooldown = decide_seed_use(s, 10, 0.3, action="likers", now=NOW)
        self.assertTrue(reason.startswith("follow-back"))
        self.assertFalse(cooldown)

    def test_low_follow_back_with_little_data_is_ignored(self):
        s = seed(complete=10, followed_back=0, rate=0.0)
        _fields, reason, _cd = decide_seed_use(s, 10, 0.3, action="likers", now=NOW)
        self.assertIsNone(reason)


class SeedBookRules(unittest.TestCase):
    def test_failures_are_not_recorded_when_no_seed_worked(self):
        api = FakeApi()
        b, lines = book(api)
        for i in range(3):
            b.fail(seed(id=f"s{i}"), soft_reason="no posts")
        self.assertTrue(b.tripped())
        b.flush()
        self.assertEqual(api.updates, [])
        self.assertTrue(any("not recorded" in line for line in lines))

    def test_failures_are_recorded_when_another_seed_worked(self):
        api = FakeApi()
        b, _lines = book(api)
        b.fail(seed(id="bad"), soft_reason="no posts")
        b.ok(seed(id="good"), 20)
        self.assertFalse(b.tripped())
        b.flush()
        ids = [u[0] for u in api.updates]
        self.assertEqual(ids, ["good", "bad"])
        self.assertEqual(api.updates[1][1]["strikes"], 1)

    def test_dry_label_survives_the_flush(self):
        api = FakeApi()
        b, _ = book(api)
        b.ok(seed(id="good"), 20)
        b.fail(seed(id="dry", last_saturation=100, last_action="likers"), saturation_pct=100, dry_label="no candidates")
        b.flush()
        self.assertIn("no candidates", api.updates[1][1]["retire_reason"])

    def test_a_success_resets_the_breaker(self):
        b, _ = book(FakeApi())
        b.fail(seed(), soft_reason="no posts")
        b.fail(seed(), soft_reason="no posts")
        b.ok(seed(), 10)
        b.fail(seed(), soft_reason="no posts")
        self.assertFalse(b.tripped())

    def test_private_profile_retires_at_once_and_is_neutral_for_the_breaker(self):
        api = FakeApi()
        b, _ = book(api)
        for _ in range(5):
            b.certain(seed(), "private profile")
        self.assertFalse(b.tripped())
        self.assertEqual(len(api.updates), 5)
        self.assertTrue(all(u[1]["active"] is False for u in api.updates))

    def test_legacy_seed_without_id_is_a_no_op(self):
        api = FakeApi()
        b, _ = book(api)
        b.ok({"handle": "typed", "id": None}, 50)
        self.assertEqual(api.updates, [])


class DiscoveryCardVerdict(unittest.TestCase):
    def test_private_card_is_rejected(self):
        self.assertEqual(seed_card_verdict({"private": True, "posts": 50}), (False, "private"))

    def test_account_with_few_posts_is_rejected(self):
        self.assertFalse(seed_card_verdict({"private": False, "posts": 1})[0])

    def test_public_account_passes(self):
        self.assertTrue(seed_card_verdict({"private": False, "posts": 120})[0])

    def test_missing_card_passes(self):
        self.assertEqual(seed_card_verdict(None), (True, "no-card"))


class LoadSeedPool(unittest.TestCase):
    class Api:
        def __init__(self, items=None, fail=False):
            self.items, self.fail, self.created = items or [], fail, []

        def get_seeds(self, account_id):
            if self.fail:
                raise RuntimeError("boom\nsecond line")
            return {"items": list(self.items), "account_rate": 0.3}

        def create_seed(self, account_id, handle, origin="manual"):
            self.created.append((handle, origin))
            return {"id": f"new-{handle}", "handle": handle.lower(), "active": True, "origin": origin}

    def test_typed_handles_become_manual_seeds_in_pool_mode(self):
        api = self.Api([{"id": "1", "handle": "known", "active": True}])
        pool, _rate, all_seeds, group = seeds.load_seed_pool(api, "acct", "Known, @Fresh", "pool")
        self.assertEqual(api.created, [("Fresh", "manual")])
        self.assertEqual(sorted(s["handle"] for s in pool), ["fresh", "known"])
        self.assertEqual(len(all_seeds), 2)
        self.assertEqual(group, ["Known", "Fresh"])

    def test_a_removed_seed_is_not_created_again(self):
        # the tombstone comes back from the API as an inactive row
        api = self.Api([{"id": "1", "handle": "gone", "active": False, "retire_reason": "removed by user"}])
        pool, _rate, _all, _group = seeds.load_seed_pool(api, "acct", "gone", "pool")
        self.assertEqual(api.created, [])
        self.assertEqual(pool, [])

    def test_manual_mode_never_touches_the_api(self):
        api = self.Api()
        pool, rate, _all, _group = seeds.load_seed_pool(api, "acct", "a,b", "manual")
        self.assertEqual([s["handle"] for s in pool], ["a", "b"])
        self.assertIsNone(rate)
        self.assertEqual(api.created, [])

    def test_api_failure_falls_back_and_says_so(self):
        lines = []
        pool, _rate, _all, _group = seeds.load_seed_pool(self.Api(fail=True), "acct", "a,b", "pool", log=lines.append)
        self.assertEqual([s["handle"] for s in pool], ["a", "b"])
        self.assertEqual(len(lines), 1)
        self.assertIn("boom", lines[0])
        self.assertNotIn("second line", lines[0])


class SaturationMetric(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        from unittest.mock import MagicMock
        # followGroup imports the whole browser stack; the metric needs none of it.
        for name in ("burnBot_utils", "burnBot_followSuggested", "burnBot_followFilter",
                     "burnBot_status", "burnBot_actionLimit"):
            sys.modules.setdefault(name, MagicMock())
        import burnBot_followGroup as fg
        cls.pct = staticmethod(fg._saturation_pct)

    def test_old_filtered_rows_make_a_dialog_look_exhausted(self):
        # 2026-11-25 review, finding 1: 50 Follow rows, all filtered on an earlier run
        # (the bot now counts those as known), nobody followed -> 100 %
        self.assertEqual(self.pct(50, 0, 0), 100)

    def test_new_rows_keep_saturation_low(self):
        self.assertEqual(self.pct(5, 15, 4), round(5 / 24 * 100))

    def test_small_sample_with_a_follow_is_not_judged(self):
        self.assertIsNone(self.pct(4, 0, 1))

    def test_small_sample_without_a_follow_is_judged(self):
        self.assertEqual(self.pct(4, 0, 0), 100)

    def test_nothing_processed(self):
        self.assertIsNone(self.pct(0, 0, 0))

    def test_pool_mode_never_asks_the_user_to_rotate_targets(self):
        import burnBot_followGroup as fg
        fg._p = lambda *_a, **_k: None
        warn = fg._saturation_warning("a", "s", "l-", "follow[x]", "seed", 90, 0, 5, pool=False)
        self.assertIn("rotate targets", warn)
        self.assertEqual(fg._saturation_warning("a", "s", "l-", "follow[x]", "seed", 90, 0, 5, pool=True), "")


if __name__ == "__main__":
    unittest.main()
