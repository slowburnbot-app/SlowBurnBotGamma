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

    def test_clearly_low_follow_back_starts_a_cooldown(self):
        # 3 of 60 (5 %): even the upper 95 % bound (about 13 %) is under half of 0.35
        s = seed(complete=60, followed_back=3)
        _fields, reason, cooldown = decide_seed_use(s, 10, 0.35, action="likers", now=NOW)
        self.assertTrue(reason.startswith("follow-back 3/60 in likers"))
        self.assertTrue(cooldown)

    def test_a_noisy_borderline_rate_does_not_retire(self):
        # 11 of 65 (17 %) was retired against 0.35 on 2026-11-25; its upper bound is about 26 %
        s = seed(complete=65, followed_back=11)
        _fields, reason, _cd = decide_seed_use(s, 10, 0.35, action="likers", now=NOW)
        self.assertIsNone(reason)

    def test_low_follow_back_with_little_data_is_ignored(self):
        s = seed(complete=10, followed_back=0)
        _fields, reason, _cd = decide_seed_use(s, 10, 0.3, action="likers", now=NOW)
        self.assertIsNone(reason)

    def test_no_baseline_means_no_rate_retirement(self):
        s = seed(complete=100, followed_back=0)
        _fields, reason, _cd = decide_seed_use(s, 10, None, action="likers", now=NOW)
        self.assertIsNone(reason)


class WilsonUpper(unittest.TestCase):
    def test_known_values(self):
        self.assertAlmostEqual(seeds.wilson_upper(0, 0), 1.0)
        self.assertAlmostEqual(seeds.wilson_upper(3, 60), 0.123, places=2)
        self.assertAlmostEqual(seeds.wilson_upper(11, 65), 0.259, places=2)

    def test_more_data_narrows_the_bound(self):
        self.assertLess(seeds.wilson_upper(50, 500), seeds.wilson_upper(5, 50))

    def test_never_above_one(self):
        self.assertLessEqual(seeds.wilson_upper(10, 10), 1.0)


class SeedWeight(unittest.TestCase):
    def test_new_seed_starts_at_the_pool_average(self):
        self.assertAlmostEqual(seeds.seed_weight(seed(base_rate=0.2)), 0.2)

    def test_new_seed_does_not_beat_a_proven_seed_at_the_average(self):
        new = seeds.seed_weight(seed(base_rate=0.2))
        proven = seeds.seed_weight(seed(base_rate=0.2, complete=100, followed_back=30))
        self.assertGreater(proven, new)

    def test_proven_bad_seed_weighs_less_than_a_new_one(self):
        bad = seeds.seed_weight(seed(base_rate=0.2, complete=100, followed_back=2))
        self.assertLess(bad, seeds.seed_weight(seed(base_rate=0.2)))

    def test_default_prior_when_the_backend_has_no_average(self):
        self.assertAlmostEqual(seeds.seed_weight(seed()), seeds.DEFAULT_PRIOR_RATE)

    def test_saturated_seed_is_damped(self):
        self.assertAlmostEqual(seeds.seed_weight(seed(base_rate=0.4, last_saturation=90)), 0.1)


class DiscoveredSeedWeight(unittest.TestCase):
    def test_discovered_seed_weighs_like_a_seed_at_the_pool_average(self):
        # a discovered seed used to fall back to the 0.5 prior and take most of the picks
        class Api:
            def get_seeds(self, account_id):
                return {"action_rates": {"likers": 0.2}, "items": [
                    {"id": "1", "handle": "old", "active": True,
                     "by_action": {"likers": {"complete": 100, "followed_back": 20}}}]}

            def create_seed(self, account_id, handle, origin="manual"):
                return {"id": "n", "handle": handle, "active": True, "by_action": {}}

        import sys
        from unittest.mock import MagicMock, patch
        pool, _base, all_seeds, group = seeds.load_seed_pool(Api(), "acct", "", "pool", action="likers")
        real, seeds._discover_from = seeds._discover_from, lambda *a, **k: ["fresh"]
        try:
            # maybe_discover_seeds imports the browser modules lazily: stub them for this test only
            with patch.dict(sys.modules, {"burnBot_followGroup": MagicMock(), "burnBot_followSuggested": MagicMock()}):
                added = seeds.maybe_discover_seeds(None, Api(), "acct", "me", pool, all_seeds, group, "s", "l-",
                                                   lambda m: None)
        finally:
            seeds._discover_from = real
        self.assertEqual(added, ["fresh"])
        # the old seed has 20/100 = the average, so both weigh the same
        self.assertAlmostEqual(seeds.seed_weight(pool[1]), seeds.seed_weight(pool[0]))
        self.assertAlmostEqual(seeds.seed_weight(pool[1]), 0.2)


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

    def test_numbers_and_baseline_are_those_of_the_action(self):
        class Api(self.Api):
            def get_seeds(self, account_id):
                return {"account_rate": 0.5, "action_rates": {"likers": 0.2}, "items": [
                    {"id": "1", "handle": "a", "active": True, "complete": 99, "followed_back": 50, "rate": 0.5,
                     "by_action": {"likers": {"complete": 40, "followed_back": 6}, "followers": {"complete": 59, "followed_back": 44}}},
                    {"id": "2", "handle": "b", "active": True, "complete": 10, "followed_back": 5, "rate": 0.5,
                     "by_action": {"followers": {"complete": 10, "followed_back": 5}}},
                ]}
        pool, base, _all, _g = seeds.load_seed_pool(Api(), "acct", "a,b", "pool", action="likers")
        self.assertEqual(base, 0.2)
        a, b = pool
        self.assertEqual((a["complete"], a["followed_back"], a["base_rate"]), (40, 6, 0.2))
        self.assertEqual((b["complete"], b["followed_back"], b["rate"]), (0, 0, None))

    def test_baseline_falls_back_to_the_account_rate(self):
        class Api(self.Api):
            def get_seeds(self, account_id):
                return {"account_rate": 0.4, "items": [{"id": "1", "handle": "a", "active": True, "by_action": {}}]}
        _pool, base, _all, _g = seeds.load_seed_pool(Api(), "acct", "a", "pool", action="similar")
        self.assertEqual(base, 0.4)

    def test_an_old_backend_without_by_action_keeps_the_all_time_numbers(self):
        class Api(self.Api):
            def get_seeds(self, account_id):
                return {"account_rate": 0.4, "items": [{"id": "1", "handle": "a", "active": True, "complete": 50, "followed_back": 9}]}
        pool, _base, _all, _g = seeds.load_seed_pool(Api(), "acct", "a", "pool", action="likers")
        self.assertEqual((pool[0]["complete"], pool[0]["followed_back"]), (50, 9))

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
