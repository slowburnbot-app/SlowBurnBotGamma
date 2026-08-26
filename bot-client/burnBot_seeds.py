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
from datetime import datetime, timezone

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
_DISCOVERY_PAGES = 8       # Similar-accounts carousel pages to read


def parse_group(group_csv):
    return [h.strip().lstrip("@") for h in (group_csv or "").split(",") if h.strip()]


def load_seed_pool(apiClient, account_id, group_csv, group_mode="manual"):
    """(pool, account_rate, all_seeds, group_handles).

    group_mode "manual": the pool IS the account-group text — plain entries
    (no 'id'), equal weights, so pick_seed == the original random.choice.
    group_mode "pool":   the follow_seeds table — active seeds carry 'id' and
    stats; all_seeds includes retired ones (discovery must not re-add them and
    may use the best of them as a parent). An API failure falls back to the
    manual list so the run still does something."""
    group_handles = parse_group(group_csv)
    manual_pool = [{"handle": h, "id": None} for h in group_handles]
    if group_mode != "pool":
        return manual_pool, None, list(manual_pool), group_handles
    try:
        data = apiClient.get_seeds(account_id) or {}
        items = data.get("items")
        if items is None:
            raise ValueError("no items in seeds response")
        active = [s for s in items if s.get("active", True)]
        return active, data.get("account_rate"), list(items), group_handles
    except Exception:
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


def finish_seed_use(apiClient, seed, saturation_pct, account_rate, account, scope, lbl, _p, retire_reason=None):
    """Record the use and retire the seed when the numbers say so (or when the
    caller already knows why, e.g. "not found"). No-op for legacy (non-API) seeds."""
    if not seed or not seed.get("id"):
        return
    handle = seed.get("handle")
    fields = {"last_used_at": datetime.now(timezone.utc)}
    if saturation_pct is not None:
        fields["last_saturation"] = int(saturation_pct)

    reason = retire_reason
    if reason is None:
        prev_sat = int(seed.get("last_saturation") or 0)
        if saturation_pct is not None and saturation_pct >= SATURATION_RETIRE and prev_sat >= SATURATION_RETIRE:
            reason = f"saturated {prev_sat}% then {int(saturation_pct)}%"
        else:
            complete = int(seed.get("complete") or 0)
            rate = seed.get("rate")
            if complete >= MIN_COMPLETE_RETIRE and account_rate and rate is not None and rate < account_rate / 2:
                reason = f"follow-back {rate:.0%} vs account {account_rate:.0%}"

    if reason:
        fields.update(active=False, retire_reason=reason)
        _p(client_log_line(account, scope, f"{lbl}Warning: retiring seed [{handle}] - {reason}"))
    else:
        debug_line(client_log_line(account, scope, f"{lbl}seed [{handle}] used, saturation={saturation_pct}"))
    apiClient.update_seed(seed["id"], **fields)


def maybe_discover_seeds(driver, apiClient, account_id, account, pool, all_seeds, group_handles, scope, lbl, _p):
    """Pool mode only. When the active pool is under the floor, read a parent's
    Similar accounts carousel (no following) and add up to DISCOVERY_MAX_PER_PASS
    new handles as seeds. Parent = best active seed, else best retired seed, else
    a random account-group handle (that's how an empty pool bootstraps).
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

    if api_seeds or api_all:
        parent_handle = max(api_seeds or api_all, key=seed_weight)["handle"]
    elif group_handles:
        parent_handle = random.choice(group_handles)
    else:
        _p(client_log_line(account, scope, f"{lbl}Warning: seed pool is empty and account group is blank - nothing to discover from"))
        return []
    existing = {s["handle"].lower() for s in api_all} | {h.lower() for h in group_handles} | {account.lower()}
    want = min(DISCOVERY_MAX_PER_PASS, POOL_CEILING - len(api_seeds))
    if want <= 0:
        return []

    _p(client_log_line(account, scope, f"{lbl}seed pool low ({len(api_seeds)}/{POOL_FLOOR}) - discovering from [{parent_handle}]"))
    found = []
    try:
        driver.get(f"https://www.instagram.com/{parent_handle}/")
        WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
        hsleep(3, 5)
        if driver.find_elements(By.XPATH, "//*[contains(text(), \"Sorry, this page isn't available.\")]"):
            _p(client_log_line(account, scope, f"{lbl}Warning: seed [{parent_handle}] not found - skipping discovery"))
            return []
        fg._p = _p
        opened, _warn = fg._open_similar_panel(driver, account, parent_handle, scope, lbl)
        if not opened:
            return []

        seen = set()
        for _page in range(_DISCOVERY_PAGES):
            for handle, _btn, _anchor in _find_home_follow_candidates(driver, max_candidates=60):
                h = handle.lower()
                if h in seen or h in existing:
                    continue
                seen.add(h)
                found.append(h)
                if len(found) >= want:
                    break
            if len(found) >= want:
                break
            try:
                nxt = driver.find_element(By.XPATH, fg._SIMILAR_NEXT_XPATH)
                driver.execute_script("arguments[0].click();", nxt)
                hsleep(2, 3)
            except Exception:
                break
    except Exception as e:
        _p(client_log_line(account, scope, f"{lbl}Warning: seed discovery failed: {str(e).splitlines()[0][:80]}"))
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
