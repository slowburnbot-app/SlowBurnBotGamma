# burnBot_seeds.py
#
# The self-tuning seed pool behind follow[account list …] and
# follow[likers-accounts]. A seed is a target account whose followers / similar
# accounts / post likers get mined. Instead of `random.choice` over the old
# comma-separated text, seeds are:
#   - picked in proportion to their smoothed follow-back rate (new seeds start
#     at 0.5 so they get explored; saturated seeds are damped),
#   - retired when they stop paying (two saturated uses in a row, or a rate
#     well under the account's own average once there is enough data),
#   - replenished from the best seed's "Similar accounts" carousel whenever the
#     active pool drops below the floor.
# Follow-back numbers come from the backend (derived from follow_targets.source),
# so the lag is unfollow_days — a seed's rate is known ~a month after first use.

import random
from datetime import datetime, timedelta, timezone

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from burnBot_human import hsleep
from burnBot_client_log import client_log_line
from burnBot_run_log import debug_line

POOL_FLOOR = 10            # discover more seeds when fewer than this are active
POOL_CEILING = 30          # never grow past this
DISCOVERY_MAX_PER_PASS = 5 # new seeds per discovery pass — keeps unproven seeds from swamping proven ones
SATURATION_RETIRE = 80     # % of the pool already known
MIN_COMPLETE_RETIRE = 30   # completed targets before a low rate can retire a seed
_DISCOVERY_MAX_PARENTS = 3 # parents to try per pass before giving up (some profiles have no Similar panel)
COOLDOWN_DAYS = 30         # an exhausted or soft-failed seed rests this long, then the server brings it back
STRIKES_TO_RETIRE = 2      # soft failures (no posts, ...) in a row before a seed rests
CIRCUIT_BREAKER = 3        # seeds in a row that fail with no success in the run = a session problem, not a seed problem
SEED_MIN_POSTS = 3         # discovery: a candidate seed needs at least this many posts


def parse_group(group_csv):
    return [h.strip().lstrip("@") for h in (group_csv or "").split(",") if h.strip()]


def load_seed_pool(apiClient, account_id, group_csv, group_mode="manual", log=None):
    """(pool, account_rate, all_seeds, group_handles).

    group_mode "manual": the pool IS the account-group text — plain entries
    (no 'id'), equal weights, so pick_seed == the original random.choice.
    group_mode "pool":   the follow_seeds table — active seeds carry 'id' and
    stats; all_seeds includes retired ones (discovery must not re-add them and
    may use the best of them as a parent). A typed account-group handle that is not
    a seed yet becomes a "manual" seed (a removed seed stays a tombstone, so it does
    not come back). An API failure falls back to the manual list so the run still
    does something; `log(text)` says so."""
    group_handles = parse_group(group_csv)
    manual_pool = [{"handle": h, "id": None} for h in group_handles]
    if group_mode != "pool":
        return manual_pool, None, list(manual_pool), group_handles
    try:
        data = apiClient.get_seeds(account_id) or {}
        items = data.get("items")
        if items is None:
            raise ValueError("no items in seeds response")
        have = {str(s.get("handle", "")).lower() for s in items}
        for h in group_handles:
            if h.lower() in have:
                continue
            created = apiClient.create_seed(account_id, h, origin="manual")
            if created:
                items.append(created)
                have.add(h.lower())
        active = [s for s in items if s.get("active", True)]
        return active, data.get("account_rate"), list(items), group_handles
    except Exception as e:
        if log:
            log(f"seed pool unavailable ({str(e).splitlines()[0][:80] if str(e) else type(e).__name__}) - using the account group text")
        return manual_pool, None, list(manual_pool), group_handles


def seed_weight(seed):
    complete = int(seed.get("complete") or 0)
    fb = int(seed.get("followed_back") or 0)
    weight = (fb + 1) / (complete + 2)          # Laplace-smoothed follow-back rate
    if int(seed.get("last_saturation") or 0) >= SATURATION_RETIRE:
        weight *= 0.25
    return max(weight, 0.01)


def order_seeds(pool):
    """Weighted random order without replacement."""
    remaining = list(pool)
    ordered = []
    while remaining:
        weights = [seed_weight(s) for s in remaining]
        pick = random.choices(remaining, weights=weights, k=1)[0]
        ordered.append(pick)
        remaining.remove(pick)
    return ordered


def pick_seed(pool):
    return order_seeds(pool)[0] if pool else None


def decide_seed_use(seed, saturation_pct, account_rate, action=None, retire_reason=None, soft_reason=None, good=False,
                    dry_label=None, now=None):
    """Pure decision for one use of a seed. Returns (fields, reason, cooldown):
    the fields to PATCH, the retirement reason (None = keep the seed), and whether
    the retirement is a cool-down (the server brings the seed back) or permanent.

    retire_reason: the caller has positive evidence, so the retirement is permanent
                   ("not found", "private profile").
    soft_reason:   a failure that could be transient ("no posts"). One strike; the
                   second strike in a row starts a cool-down.
    good:          the use opened the seed and read rows; it ends a strike streak.
    dry_label:     the use had no rows to judge (no likes link, no candidates) and was
                   marked 100 % so the pool damps it. Two such uses in a row retire the
                   seed with this label, not with a "saturated 100%" that never happened.
    saturation_pct: % of the rows of this use that could not produce a follow. Two
                   uses of the SAME action at >= SATURATION_RETIRE start a cool-down.
    """
    now = now or datetime.now(timezone.utc)
    fields = {"last_used_at": now}
    if saturation_pct is not None:
        fields["last_saturation"] = int(saturation_pct)
        if action:
            fields["last_action"] = action
    strikes = int(seed.get("strikes") or 0)

    reason = retire_reason
    cooldown = False
    if reason is None and soft_reason:
        strikes += 1
        fields["strikes"] = strikes
        if strikes >= STRIKES_TO_RETIRE:
            reason = f"{soft_reason} x{strikes}"
            cooldown = True
    elif reason is None:
        if strikes and good:
            fields["strikes"] = 0   # a use that opened and read rows ends the failure streak
        prev_sat = int(seed.get("last_saturation") or 0)
        same_action = bool(action) and seed.get("last_action") == action
        if saturation_pct is not None and saturation_pct >= SATURATION_RETIRE and prev_sat >= SATURATION_RETIRE and same_action:
            if dry_label:
                reason = f"{dry_label}, 2 uses in a row ({action})"
            else:
                reason = f"saturated {prev_sat}% then {int(saturation_pct)}% ({action})"
            cooldown = True
        else:
            complete = int(seed.get("complete") or 0)
            rate = seed.get("rate")
            if complete >= MIN_COMPLETE_RETIRE and account_rate and rate is not None and rate < account_rate / 2:
                reason = f"follow-back {rate:.0%} vs account {account_rate:.0%}"
    if reason:
        fields.update(active=False, retire_reason=reason)
        if cooldown:
            fields["cooldown_until"] = now + timedelta(days=COOLDOWN_DAYS)
    return fields, reason, cooldown


def finish_seed_use(apiClient, seed, saturation_pct, account_rate, account, scope, lbl, _p,
                    retire_reason=None, action=None, soft_reason=None, good=False, dry_label=None):
    """Record the use and retire the seed when the numbers say so (or when the
    caller already knows why, e.g. "private profile"). No-op for legacy (non-API) seeds."""
    if not seed or not seed.get("id"):
        return
    handle = seed.get("handle")
    fields, reason, cooldown = decide_seed_use(
        seed, saturation_pct, account_rate, action=action, retire_reason=retire_reason, soft_reason=soft_reason, good=good,
        dry_label=dry_label,
    )
    if reason:
        # A cool-down is routine housekeeping, not an alarm: only a permanent retirement warns.
        if cooldown:
            _p(client_log_line(account, scope, f"{lbl}seed [{handle}] resting {COOLDOWN_DAYS} days - {reason}"))
        else:
            _p(client_log_line(account, scope, f"{lbl}Warning: retiring seed [{handle}] - {reason}"))
    else:
        debug_line(client_log_line(account, scope, f"{lbl}seed [{handle}] used, saturation={saturation_pct}"))
    apiClient.update_seed(seed["id"], **fields)


class SeedBook:
    """The seed outcomes of ONE action run.

    A good use is recorded at once. A bad outcome ("not found", "no posts", zero
    candidates) is held back and applied at the end of the run ONLY when at least
    one seed worked in the same run: a soft block, a logout or a changed page makes
    every seed look dead, and that must not retire the whole pool (seen as a risk
    in the 2026-11-25 review). Positive evidence (a private profile) is applied
    at once. After CIRCUIT_BREAKER failures in a row with no success, tripped()
    is True and the caller stops the run."""

    def __init__(self, apiClient, account_rate, account, scope, lbl, _p, action):
        self.apiClient, self.account_rate = apiClient, account_rate
        self.account, self.scope, self.lbl, self._p, self.action = account, scope, lbl, _p, action
        self.pending = []
        self.successes = 0
        self.consecutive_failures = 0

    def _finish(self, seed, saturation_pct, **kw):
        finish_seed_use(self.apiClient, seed, saturation_pct, self.account_rate, self.account,
                        self.scope, self.lbl, self._p, action=self.action, **kw)

    def ok(self, seed, saturation_pct):
        self.successes += 1
        self.consecutive_failures = 0
        self._finish(seed, saturation_pct, good=True)

    def certain(self, seed, retire_reason):
        """Positive evidence (private profile): retire now. It says nothing about the
        session (a logged-out page shows the same text), so it moves neither counter."""
        self._finish(seed, None, retire_reason=retire_reason)

    def fail(self, seed, saturation_pct=None, retire_reason=None, soft_reason=None, dry_label=None):
        self.consecutive_failures += 1
        self.pending.append((seed, saturation_pct, retire_reason, soft_reason, dry_label))

    def tripped(self):
        return self.successes == 0 and self.consecutive_failures >= CIRCUIT_BREAKER

    def flush(self):
        if not self.pending:
            return
        if self.successes == 0:
            self._p(client_log_line(self.account, self.scope,
                    f"{self.lbl}Warning: {len(self.pending)} seed failure(s) not recorded - no seed worked in this run (session problem?)"))
            self.pending = []
            return
        for seed, sat, retire_reason, soft_reason, dry_label in self.pending:
            self._finish(seed, sat, retire_reason=retire_reason, soft_reason=soft_reason, dry_label=dry_label)
        self.pending = []


def seed_card_verdict(card):
    """(ok, why) for a discovery candidate's hover card. A seed must be a public
    account with a post grid: 5 of 10 discovered seeds on 2026-11-20 were private
    and never produced a post. A missing card passes (the card is a lever, not a gate)."""
    if not card:
        return True, "no-card"
    if card.get("private"):
        return False, "private"
    posts = card.get("posts")
    if posts is not None and posts < SEED_MIN_POSTS:
        return False, f"{posts} posts"
    return True, ""


def _discover_from(driver, parent_handle, existing, want, fg, find_candidates, account, scope, lbl, _p):
    """Read up to `want` new, vetted handles from one parent's "Suggested for you"
    dialog (the carousel's "See all" list: the hover cards render there, the
    carousel itself shows none). Each candidate is hovered and kept only when its
    card shows a public account with a post grid (seed_card_verdict).
    Returns the list (possibly empty) when the list opened, None when the
    profile is missing, has no Similar panel, or the read raised — the caller
    moves on to the next parent in both cases."""
    from burnBot_followFilter import _hover_and_read   # lazy: keeps this module light to import
    found = []
    try:
        driver.get(f"https://www.instagram.com/{parent_handle}/")
        WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        hsleep(3, 5)
        if driver.find_elements(By.XPATH, "//*[contains(text(), \"Sorry, this page isn't available.\")]"):
            _p(client_log_line(account, scope, f"{lbl}Warning: seed [{parent_handle}] not found - skipping discovery"))
            return None
        fg._p = _p
        opened, _warn = fg._open_similar_panel(driver, account, parent_handle, scope, lbl)
        if not opened:
            return None
        opened, _warn = fg._open_similar_list(driver, account, parent_handle, scope, lbl)
        if not opened:
            return None

        seen = set()
        rejected = 0
        root = fg._dialog_root(driver)
        for handle, _btn, anchor in find_candidates(driver, max_candidates=60, root=root):
            h = handle.lower()
            if h in seen or h in existing:
                continue
            seen.add(h)
            card, _tries = _hover_and_read(driver, handle, anchor)
            ok, why = seed_card_verdict(card)
            if not ok:
                rejected += 1
                debug_line(client_log_line(account, scope, f"{lbl}discovery skips [{h}] - {why}"))
                continue
            found.append(h)
            if len(found) >= want:
                break
        if rejected:
            debug_line(client_log_line(account, scope, f"{lbl}discovery from [{parent_handle}]: {len(found)} kept, {rejected} rejected"))
    except Exception as e:
        _p(client_log_line(account, scope, f"{lbl}Warning: seed discovery failed: {str(e).splitlines()[0][:80]}"))
        return None
    return found


def maybe_discover_seeds(driver, apiClient, account_id, account, pool, all_seeds, group_handles, scope, lbl, _p):
    """Pool mode only. When the active pool is under the floor, read a parent's
    Similar accounts carousel (no following) and add up to DISCOVERY_MAX_PER_PASS
    new handles as seeds. Parents are tried in weighted random order (active
    seeds, else retired ones, up to _DISCOVERY_MAX_PARENTS), else a random
    account-group handle (that's how an empty pool bootstraps).
    Mutates `pool` (appends the new active seeds). Returns the handles added."""
    if any(not s.get("id") for s in pool):
        return []   # manual list / API fallback — nothing to grow
    api_seeds = list(pool)
    api_all = [s for s in (all_seeds or []) if s.get("id")]
    if len(api_seeds) >= POOL_FLOOR:
        return []

    # Lazy imports: followGroup imports this module.
    import burnBot_followGroup as fg
    from burnBot_followSuggested import _find_home_follow_candidates

    # Candidate parents in weighted random order — a plain max() over a pool of new seeds
    # (all tied at the 0.5 prior) returned the same first row every run, and one parent
    # without a Similar accounts button stalled discovery indefinitely. Try a few.
    parents = [s["handle"] for s in order_seeds(api_seeds or api_all)[:_DISCOVERY_MAX_PARENTS]]
    # The typed account group is the last resort: when every seed-born parent is a dead
    # end (no panel, nothing new) discovery falls back to it instead of stalling.
    fallback = random.sample(group_handles, min(2, len(group_handles)))
    parents += [h for h in fallback if h.lower() not in {p.lower() for p in parents}]
    if not parents:
        _p(client_log_line(account, scope, f"{lbl}Warning: seed pool is empty and account group is blank - nothing to discover from"))
        return []
    existing = {s["handle"].lower() for s in api_all} | {h.lower() for h in group_handles} | {account.lower()}
    want = min(DISCOVERY_MAX_PER_PASS, POOL_CEILING - len(api_seeds))
    if want <= 0:
        return []

    found = None
    parent_handle = None
    for parent_handle in parents:
        _p(client_log_line(account, scope, f"{lbl}seed pool low ({len(api_seeds)}/{POOL_FLOOR}) - discovering from [{parent_handle}]"))
        found = _discover_from(driver, parent_handle, existing, want, fg, _find_home_follow_candidates, account, scope, lbl, _p)
        if found:
            break   # an empty list is a dead end like a missing panel: try the next parent
    if not found:
        _p(client_log_line(account, scope, f"{lbl}Warning: seed discovery found no new seeds from {len(parents)} parent(s)"))
        return []

    added = []
    for h in found:
        created = apiClient.create_seed(account_id, h, origin=f"similar:{parent_handle}")
        # The API hands back an existing row as-is; a retired one must not re-enter the pool.
        if created and created.get("active", True):
            added.append(h)
            pool.append(created)
            all_seeds.append(created)
    _p(client_log_line(account, scope, f"{lbl}discovered {len(added)} seed(s) from {parent_handle}[similar]"))
    if not pool:
        _p(client_log_line(account, scope, f"{lbl}Warning: seed pool still empty - add a seed on the dashboard or fill the account group"))
    return added
