# The ranking algorithm

## The idea in one line

An account is authoritative if authoritative accounts follow it — applied
recursively until it converges.

## Why a follow edge is a vote

When someone follows an account, they are spending something scarce: a slot in
their own feed. That makes a follow a genuine, costly signal in a way that a
like or a view is not. Two properties fall out of this and they are the reason
PageRank is the right tool rather than a hand-tuned score:

1. **Votes are weighted by the voter's own authority.** A follow from a
   respected researcher carries more than a follow from an account created
   yesterday. This is recursive — the researcher's authority is itself derived
   from who follows *them*.
2. **Votes are split.** If you follow 8,000 accounts, each follow expresses
   1/8000 of your endorsement. If you follow 40, each one is a real statement.
   This falls out of out-degree normalization for free, and it is what makes
   "followed by X" meaningful rather than just "followed by a lot of people."

Together these mean follower *count* barely matters. Ten thousand followers who
follow everything and are followed by nobody contribute almost exactly nothing.

## Edge direction

```
u ──follows──▶ v        means        u endorses v
```

Authority flows **from follower to followee**. Getting this backwards produces a
ranking of the most indiscriminate lurkers on the platform, so it is worth
stating twice.

## The equation

```
r  =  (1 − d) · p  +  d · ( Mᵀ r  +  leak(r) · p )
```

| term | meaning |
|---|---|
| `r` | the authority vector we are solving for; sums to 1 |
| `d` | damping — probability the surfer follows an edge rather than teleporting. Default `0.85` |
| `p` | **teleport distribution** — where the surfer restarts. This is the topic definition |
| `M[u][v]` | `trust(u) / outdeg(u)` for each edge `u → v` |
| `leak(r)` | rank that reached a dead end and must be recycled |

Solved by power iteration. Error contracts by a factor of `d` per sweep, so it
converges in ~50–120 sweeps at `d = 0.85`. Cost per sweep is `O(|E|)`.

## The part that makes it work: topic-sensitive teleport

This is the difference between a useful tool and a list of pop stars.

In classic PageRank, `p` is uniform — the surfer restarts at a random account.
That measures *global* fame, and the global follow graph is dominated by
footballers and musicians. Ranking tech creators with uniform teleport returns
noise.

Instead (Haveliwala's topic-sensitive PageRank) we concentrate `p` on a curated
**seed set** of accounts already known to be good in the topic. The surfer now
always restarts inside the tech/AI/science community, so the stationary
distribution measures *authority as seen from that community*. Celebrity with no
connection to the seeds simply never accumulates mass.

Practically: **your seed set is your definition of quality.** Everything else in
this repo is mechanism. See `instaindex/seeds.py` for how to build a good one.

### Why the leak is routed through `p`

Two kinds of rank hit a dead end each sweep:

- **Dangling nodes** — accounts that follow nobody *inside the crawled graph*
  (extremely common at the rim of a snowball crawl).
- **Withheld spam mass** — the fraction not passed on by low-trust accounts.

Standard implementations redistribute this uniformly. That is a real bug for our
purposes: at the rim of a two-hop crawl the dangling mass is large, and dumping
it uniformly re-injects exactly the generic-popularity signal that
personalization exists to remove. Routing it through `p` keeps the ranking
genuinely topical. Total mass stays exactly 1 either way.

## Anti-gaming

The attack is obvious: buy 50,000 followers, or spin up a bot ring.

**Personalized PageRank is structurally resistant to this**, and not because of
any heuristic. Fake accounts have no authority to give — they only have
authority if the seed community reaches them, and it does not. A bot ring can
pass rank around among themselves forever and it stays worthless, because the
`(1−d)·p` term means all mass ultimately originates from the seeds. You cannot
manufacture authority; you can only receive it from someone who already has it.

The demo makes this concrete: 250 bots all following a "gamer" account leaves it
below the *median ordinary creator*. `tests/test_ranking.py` asserts it.

The one attack that does work is **getting a genuinely respected account to
follow you** — which is not an attack, it is the signal working correctly.

### The trust factor (secondary defence)

Out-degree normalization already shrinks each vote from a promiscuous account,
but such an account still functions as a high-throughput conduit that launders
rank from wherever it receives it. So we additionally shrink its total outflow:

```
trust(u) = min(1, (follow_soft_cap / following(u)) ** spam_exponent)
```

Defaults `follow_soft_cap = 2000`, `spam_exponent = 0.5`. The withheld mass goes
back to `p`, not to the node. Set `--spam-exponent 0` for textbook PageRank.

This uses the *profile's* self-reported following count, not the crawled
out-degree, because a partial crawl badly understates how promiscuous an account
really is.

## The five reported metrics

| metric | what it answers |
|---|---|
| `authority` | **the headline.** Topic-sensitive PageRank |
| `endorsement` | the same idea unrolled one hop: summed authority of your followers, each split by their out-degree. Directly interpretable as "followed by great people" |
| `topic_focus` | `authority / baseline`, where baseline is uniform-teleport PageRank. High = big *because of this community*; ~1 = big anyway |
| `underrated` | authority ÷ log(followers). **Usually the most interesting column** — creators their peers rate far above their audience size |
| `curator` | a hub score: how much of *their* following list points at high-authority accounts. Good curators are how you find the next batch of creators |

`endorsement` and `curator` are the two-sided HITS intuition (authorities and
hubs) computed against the personalized authority vector.

## Explainability

For every account we keep the top-k followers by contribution
(`authority(u) × trust(u)/outdeg(u)`), which is exactly the term each follower
adds to the sum. So every score comes with a receipt:

```
authority_0008   0.021162
  ↳ endorsed by: authority_0000 8%, authority_0007 8%, authority_0004 7%, …
```

This matters more than it sounds. It is how you audit a surprising result, and
how you spot a seed set that is quietly returning one clique's friends.

## Tuning

| knob | effect |
|---|---|
| `--damping` | higher = authority travels further from the seeds (broader, noisier). Lower = tighter around the seed community. `0.85` is standard; try `0.7` for a tighter topical read |
| `--background` | uniform mass mixed into `p`. Keep small (default `0.02`). It exists so accounts unreachable from the seeds are not exactly zero — but it is precisely the channel through which global celebrity leaks back in |
| `--follow-soft-cap` / `--spam-exponent` | the trust factor above |
| `--exclude-seeds` | hide the seeds from output. Seeds hold teleport mass by construction, so they float to the top and are not a *finding*. Turn this on for discovery |

## Known limitations

- **Follows are sticky.** People rarely unfollow, so the graph encodes accumulated
  reputation and lags real current quality. An account that was excellent in 2019
  keeps its rank. Re-crawl periodically; consider weighting recent follows higher
  if you can get follow timestamps (X's API does not expose them).
- **It ranks reputation, not content.** Nothing here reads a single post. A
  well-connected account that stopped posting still ranks. Pair the output with a
  content-based filter if that matters.
- **Rich-get-richer.** PageRank rewards existing position. `underrated` is the
  partial antidote; it is not a complete one.
- **Seed bias is total.** A seed set drawn from one sub-community returns that
  sub-community and confidently presents it as "the best". Run several different
  seed sets and trust the names that survive all of them — that intersection is
  a far more honest answer than any single run.
- **Sampling bias.** A two-hop crawl cannot rank anyone it never saw. Absence
  from the output means "not reached", never "not good".

## References

- Page & Brin, *The PageRank Citation Ranking* (1998)
- Haveliwala, *Topic-Sensitive PageRank* (WWW 2002) — the seed-vector method
- Gupta et al., *WTF: The Who to Follow Service at Twitter* (WWW 2013) —
  personalized PageRank / SALSA on a real follow graph at scale
- Kleinberg, *Authoritative Sources in a Hyperlinked Environment* (1999) — HITS
