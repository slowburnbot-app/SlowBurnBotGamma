"""Backend rules for follow seeds: source-label stats and cool-down reactivation.
Run from the repo root:  .venv/bin/python -m unittest discover -s tests -v"""
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from app.models.follow_seed import FollowSeed
from app.services.follow_seeds import _source_prefix, reactivate_if_cooled, remove_seed, reset_seed_state

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def make(**kw):
    s = FollowSeed(id=uuid.uuid4(), handle="x", origin="similar:y", active=True)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class SourcePrefix(unittest.TestCase):
    def test_all_four_labels_map_to_the_handle(self):
        for label in ("followers", "following", "similar", "likers"):
            self.assertEqual(_source_prefix(f"Nike[{label}]"), "nike")

    def test_topic_source_does_not_share_a_bucket_with_the_account(self):
        self.assertEqual(_source_prefix("#nike[likers]"), "#nike")

    def test_empty_source(self):
        self.assertIsNone(_source_prefix(None))
        self.assertIsNone(_source_prefix("[likers]"))


class CooldownReactivation(unittest.TestCase):
    def retired(self, **kw):
        return make(active=False, retire_reason="saturated 90% then 95% (likers)", last_saturation=95,
                    last_action="likers", strikes=2, retired_at=NOW - timedelta(days=31), **kw)

    def test_seed_comes_back_after_its_cooldown(self):
        s = self.retired(cooldown_until=NOW - timedelta(minutes=1))
        self.assertTrue(reactivate_if_cooled(s, NOW))
        self.assertTrue(s.active)
        self.assertIsNone(s.retire_reason)
        self.assertIsNone(s.cooldown_until)
        self.assertIsNone(s.last_saturation)   # not one strike from retirement again
        self.assertEqual(s.strikes, 0)

    def test_seed_stays_down_during_the_cooldown(self):
        s = self.retired(cooldown_until=NOW + timedelta(days=3))
        self.assertFalse(reactivate_if_cooled(s, NOW))
        self.assertFalse(s.active)

    def test_permanent_retirement_never_comes_back(self):
        s = make(active=False, retire_reason="private profile", cooldown_until=None)
        self.assertFalse(reactivate_if_cooled(s, NOW))
        self.assertFalse(s.active)

    def test_active_seed_is_untouched(self):
        s = make(cooldown_until=NOW - timedelta(days=1), last_saturation=40)
        self.assertFalse(reactivate_if_cooled(s, NOW))
        self.assertEqual(s.last_saturation, 40)


class UserRemoval(unittest.TestCase):
    def test_remove_keeps_a_tombstone_that_never_comes_back_by_itself(self):
        s = make(cooldown_until=NOW - timedelta(days=1))
        remove_seed(s, NOW)
        self.assertFalse(s.active)
        self.assertEqual(s.removed_at, NOW)
        self.assertIsNone(s.cooldown_until)
        self.assertFalse(reactivate_if_cooled(s, NOW + timedelta(days=90)))

    def test_re_adding_a_removed_handle_clears_the_tombstone(self):
        s = make()
        remove_seed(s, NOW)
        reset_seed_state(s)
        self.assertTrue(s.active)
        self.assertIsNone(s.removed_at)


class UserReEnable(unittest.TestCase):
    def test_reenable_clears_saturation_strikes_and_cooldown(self):
        s = make(active=False, retire_reason="disabled by user", last_saturation=100, last_action="likers",
                 strikes=1, cooldown_until=NOW + timedelta(days=9), retired_at=NOW)
        reset_seed_state(s)
        self.assertTrue(s.active)
        self.assertEqual((s.last_saturation, s.last_action, s.strikes, s.cooldown_until, s.retire_reason),
                         (None, None, 0, None, None))


if __name__ == "__main__":
    unittest.main()
