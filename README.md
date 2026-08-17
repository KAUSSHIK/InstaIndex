# InstaIndex

An attempt to index Instagram by the content they contain. For now this is
limited only to the english language.

This repo currently implements the **influencer ranking** half of that goal: a
topic-sensitive PageRank over a social follow graph, for finding the best
content creators in tech / AI / computing / math / science.

## The premise

> If great people follow an account, that account is probably great.

That is PageRank, applied to follows instead of hyperlinks. A follow is a vote,
weighted by the voter's own authority, and split across everyone they follow. It
is recursive, so it beats follower count outright: 100,000 bot followers are
worth almost exactly nothing, while one follow from a selective, respected
account is worth a great deal.

One addition is essential. Plain PageRank on a social graph ranks *global* fame
and returns footballers and pop stars. So the random surfer teleports back into
a curated **seed set** of known-good tech/science accounts instead of jumping
anywhere — Haveliwala's topic-sensitive PageRank. Authority then flows outward
from that community and celebrity is filtered out for free.

**Your seed set is your definition of quality.** Everything else is mechanism.

## Try it

No dependencies, no data, no API keys — pure Python 3.8+:

```bash
python -m instaindex demo
```

This builds a synthetic 918-account ecosystem containing real authorities,
mid-size creators, lurkers, off-topic mega-celebrities, and a 250-account botnet
pointed at a "gamer" account trying to buy rank. Output:

```
sanity check -- mean authority by archetype:
  authority  n=  30  mean authority 0.022378
  creator    n= 120  mean authority 0.002699
  celebrity  n=  15  mean authority 0.000099
  gamer      n=   3  mean authority 0.000024
  botnet     n= 250  mean authority 0.000006
  lurker     n= 500  mean authority 0.000003

top 5 by raw follower count (what a naive ranking would return):
  celebrity_0010       86,239,602 followers  authority 0.000079  (celebrity)
  celebrity_0013       85,757,960 followers  authority 0.000112  (celebrity)
```

The 86M-follower celebrity scores **500× lower** than a 400k-follower topical
authority, and the purchased botnet buys its target nothing.

### Discovery mode

Seeds hold teleport mass by construction, so they float to the top and aren't a
*finding*. Hide them and sort by `underrated` to surface creators their peers
rate far above their audience size:

```bash
python -m instaindex demo --exclude-seeds --metric underrated --explain
```

```
  #  account          authority    focus  underrated   followers      in
------------------------------------------------------------------------
  1  authority_0008    0.021162     1.12    0.004108     141,672     288
                   ↳ endorsed by: authority_0000 8%, authority_0007 8%, …
```

Every score comes with a receipt: which followers actually produced it. In the
demo, all 22 authorities that were *not* seeded are recovered purely from graph
structure.

## Using your own data

Two JSONL files:

```jsonl
// edges.jsonl   (required)     src follows dst  =>  src endorses dst
{"src": "alice", "dst": "bob"}

// accounts.jsonl  (optional)
{"id": "bob", "handle": "bob", "platform": "x", "followers": 51234, "following": 380}
```

```bash
python -m instaindex rank \
  --edges data/edges.jsonl \
  --accounts data/accounts.jsonl \
  --seeds tech_x \
  --exclude-seeds --explain --json out/ranking.json
```

`--seeds` accepts a bundled preset name (`instaindex seeds` lists them), a file
path, or a comma-separated list of handles.

Generate a real on-disk dataset to experiment with: `python -m instaindex synth`

## Metrics

| metric | answers |
|---|---|
| `authority` | **the headline.** Topic-sensitive PageRank |
| `endorsement` | "followed by great people", unrolled one hop |
| `topic_focus` | big *because of this community*, or big anyway? |
| `underrated` | authority vs. audience size. **Often the most interesting column** |
| `curator` | whose *following list* is a good discovery source |

## Where the data comes from

This is the hard part, and it deserves a blunt warning:

**Instagram's official API does not expose follower or following lists for any
account — including your own.** There is no endpoint that answers "who does X
follow", at any price. It's an architectural decision, not a pricing tier. So a
follow-graph ranking cannot be built legitimately for Instagram.

X/Twitter's API does expose `following`, but on paid tiers with aggressive rate
limits — plan for a slow, resumable, multi-day crawl.

**Recommended starting point: Bluesky.** The AT Protocol is open and free, rate
limits are generous, and it has a dense real tech/AI/science community. Prove the
ranking works there before paying anyone.

`instaindex/crawl.py` provides a checkpointing snowball crawler that takes your
platform API as a pluggable callable. It crawls **following** lists rather than
follower lists — same edges, ~1000× fewer API calls.

Full detail, including the crawl-budget math: **[docs/DATA_COLLECTION.md](docs/DATA_COLLECTION.md)**

## Documentation

- **[docs/ALGORITHM.md](docs/ALGORITHM.md)** — the math, the anti-gaming
  analysis, tuning knobs, and known limitations
- **[docs/DATA_COLLECTION.md](docs/DATA_COLLECTION.md)** — platform-by-platform
  reality check and crawl strategy

## Tests

```bash
python -m unittest discover -s tests -v
```

56 tests. The ones that matter assert product behaviour: that follower count
doesn't buy rank, that a botnet doesn't buy rank, and that genuine authorities
are recovered even when they were never seeded.

## Layout

```
instaindex/
  graph.py      follow graph, JSONL loaders, spam-trust weighting
  pagerank.py   personalized PageRank power iteration
  ranking.py    the five metrics + explanations + table rendering
  crawl.py      checkpointing snowball crawler (bring your own API client)
  seeds.py      seed-set loading and guidance
  synth.py      synthetic ecosystem for demos and tests
  cli.py        command line interface
data/seeds/     starting-point seed sets (verify handles before use)
```
