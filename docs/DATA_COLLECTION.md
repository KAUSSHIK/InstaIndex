# Getting the data

The algorithm is the easy part. **Obtaining follow-graph data is the hard part**,
and on one of the two platforms you named it is currently close to impossible
through official channels. Read this before planning any work.

> Platform API terms, pricing and access tiers change frequently, and the
> specifics below may be out of date. Verify against current official docs
> before committing to a plan or a budget. The structural points — which data a
> platform exposes *at all* — are stable; the prices are not.

## What the algorithm actually needs

Very little:

```jsonl
// edges.jsonl   (required)
{"src": "alice", "dst": "bob"}     // alice follows bob

// accounts.jsonl   (optional but recommended)
{"id": "bob", "handle": "bob", "platform": "x", "followers": 51234, "following": 380}
```

`followers` powers the `underrated` metric; `following` powers the spam-trust
factor. Everything else is cosmetic. No post content, no engagement data, no
timestamps.

## The one optimization that matters

**Crawl `following` lists, not `follower` lists.**

Both give you the same edges. But a large account has 5,000,000 followers and
follows 400 people:

| direction | API calls for one big account (1000/page) |
|---|---|
| paging its **followers** | ~5,000 |
| paging its **following** | **1** |

Three orders of magnitude, for identical information. Since PageRank only needs
the edge set and does not care how you discovered it, always walk outward along
`following`. `instaindex/crawl.py` is built around this.

## You do not need the whole platform

A **two-hop snowball crawl from a good seed set** is enough. Start at ~20 seeds,
fetch who they follow, fetch who *those* accounts follow, stop. The people worth
ranking are by construction the people the seed community follows.

Rough scale for a 20-seed, 2-hop crawl:

| hop | accounts paged | cumulative API calls (~1000/page) |
|---|---|---|
| 0 | 20 seeds | ~30 |
| 1 | ~5,000 discovered | ~7,000 |
| 2 | *not paged* — edges recorded only | 0 |

So on the order of **5,000–10,000 API calls** for a graph of ~100k accounts and
several million edges. That is a very tractable target, and it is why
`CrawlBudget.max_hops` defaults to 2.

---

## Platform reality check

### Instagram — the follow graph is not available

This is the blunt truth and it is worth knowing before you invest:

- **The Instagram Graph API does not expose follower or following *lists* for
  any account, including your own.** It is built for businesses to manage their
  own content and read their own aggregate insights. There is no endpoint that
  answers "who does account X follow". This is an architectural decision, not a
  pricing tier — no amount of money unlocks it.
- The Instagram Basic Display API, which was already far more limited, has been
  deprecated.
- Scraping the private/mobile endpoints violates the Terms of Use, and Meta
  actively detects and blocks it — expect IP bans and account bans.

**Conclusion: you cannot legitimately build a follow-graph ranking for
Instagram.** Given the repo is called InstaIndex, this is the single most
important thing in this document. Options, honestly ranked:

1. **Rank on a different platform, present the results as creator discovery.**
   The tech/science creator community overlaps heavily across platforms; many
   accounts you surface will have Instagram presences you can then look up.
2. **Content-based indexing for Instagram instead.** The repo's stated goal is
   "index Instagram by the content they contain" — hashtags, captions and
   embeddings are reachable in ways the social graph is not, and don't require
   the follow graph at all.
3. **Manually curated Instagram graph.** For a few hundred accounts, following
   lists are visible in the UI. Tedious, but legal and possibly sufficient for a
   seed-quality list.

### X / Twitter — possible, but priced as a serious commitment

`GET /2/users/:id/following` exists and returns exactly what we need. The
constraints are access and cost:

- The free tier does not meaningfully support graph reads.
- Paid tiers exist at roughly the low-hundreds-per-month and
  several-thousand-per-month levels, with sharply different read caps; the
  follow-graph endpoints in particular have been restricted to higher tiers at
  various points. **Check current pricing and endpoint availability directly** —
  this has changed repeatedly and any number quoted here will be stale.
- Rate limits on these endpoints are aggressive (historically on the order of
  ~15 requests per 15-minute window, 1,000 users per page). A 5,000-call crawl
  at that rate is measured in **days, not hours**, so checkpointing is
  mandatory. `CrawlState.save()` writes atomically for exactly this reason.

Budget for a slow, resumable, multi-day crawl rather than a script you run once.

### Bluesky — start here

If the goal is to *get the system working and see real results*, this is the
pragmatic recommendation:

- The AT Protocol is **open**. `app.bsky.graph.getFollows` returns following
  lists, public data is readable without a paid tier, and rate limits are
  generous.
- There is a real, dense tech / AI / science / academic community there —
  precisely your target topic.
- A full two-hop crawl is achievable in hours, free.

Prove the ranking works on Bluesky data, tune your seed set against results you
can actually eyeball, *then* decide whether X's pricing is worth it. Bluesky's
handles are domain-like (`user.bsky.social`), which the loader accepts as ids
without modification.

### Mastodon

Also fully open (per-instance REST API, `/api/v1/accounts/:id/following`), with a
strong FOSS/infosec/science community. More fragmented across instances, so the
crawler needs per-instance host routing, but entirely viable.

---

## Wiring up a crawler

`instaindex/crawl.py` deliberately contains no HTTP code. You supply one
callable and it handles frontier management, dedup, budgets, failure isolation
and resume:

```python
from instaindex.crawl import CrawlBudget, CrawlState, snowball, state_to_graph
from instaindex.ranking import rank

def fetch_following(handle):
    """Return (list_of_handles_they_follow, profile_dict_or_None)."""
    profile = api.get_profile(handle)
    following = api.get_all_following(handle)      # paginate internally
    return following, {
        "handle": handle,
        "followers": profile["followers_count"],
        "following": profile["following_count"],   # drives the spam-trust factor
        "name": profile.get("display_name", ""),
        "verified": profile.get("verified", False),
    }

state = snowball(
    seeds=["karpathy", "3blue1brown"],
    fetch_following=fetch_following,
    budget=CrawlBudget(max_hops=2, max_accounts=50_000, sleep_between_calls=1.0),
    checkpoint_path="data/crawl_state.json",   # resume after a rate-limit stop
)

ranking = rank(state_to_graph(state, platform="bsky"), seeds=["karpathy"])
```

To resume an interrupted crawl:

```python
state = snowball([], fetch_following, budget, state=CrawlState.load("data/crawl_state.json"))
```

Exceptions from `fetch_following` are caught per-account and recorded in
`state.failed`, so one suspended account cannot kill a three-day crawl.

## Practical notes

- **Cache aggressively.** Follow lists change slowly. Never re-fetch an account
  within a run; ideally not within a week.
- **Respect `Retry-After`.** Backing off politely is cheaper than getting your
  key revoked.
- **Store raw responses.** You will want to re-derive the graph with different
  filters without re-crawling. That crawl is the expensive asset, not the graph.
- **Prune the rim before eyeballing results.** A two-hop crawl ends with a large
  fringe of accounts seen exactly once. `prune_to_core(graph, min_in_degree=2)`
  identifies them. This is cosmetic, not a correctness issue.
- **Privacy.** You are assembling a social graph of real people. Keep it to
  public accounts, don't republish raw edge lists, and be aware this kind of
  dataset falls under GDPR/CCPA if you handle EU or California residents' data.
- **Check the ToS for the platform you pick**, not just its rate limits.
  Automated collection is often governed by a separate clause from API access.
