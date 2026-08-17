"""Derived metrics on top of personalized PageRank.

The raw PPR vector is the answer to "who is authoritative in this topic?", but
on its own it is a black box and it is biased toward the already-huge. This
module adds four things that make it useful for actually *finding creators*:

  authority        topic-sensitive PageRank. The headline number.
  endorsement      the one-hop version of the same idea: sum of the authority
                   of your followers, each split across everyone they follow.
                   This is literally "followed by great people", unrolled one
                   step, and it is the term that sits inside PageRank's own
                   recursion. Reporting it separately makes the score legible.
  topic_focus      authority relative to generic global popularity. High means
                   "big *because* of this community", low means "big anyway".
  underrated       authority relative to follower count. Surfaces creators
                   whose peers rate them far above their audience size --
                   usually the most interesting names in the whole list.
  curator          a hub score: how much of this account's own following list
                   points at high-authority accounts. Good curators are how
                   you discover the next batch of creators.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .graph import Account, FollowGraph
from .pagerank import (
    PageRankResult,
    pagerank,
    resolve_seeds,
    seed_teleport,
    uniform_teleport,
)


@dataclass
class RankedAccount:
    index: int
    account: Account
    authority: float
    endorsement: float
    baseline: float
    topic_focus: float
    underrated: float
    curator: float
    in_degree: int
    out_degree: int
    is_seed: bool
    top_endorsers: List[Tuple[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "handle": self.account.label,
            "id": self.account.id,
            "platform": self.account.platform,
            "name": self.account.name,
            "followers": self.account.followers,
            "authority": self.authority,
            "endorsement": self.endorsement,
            "topic_focus": self.topic_focus,
            "underrated": self.underrated,
            "curator": self.curator,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
            "is_seed": self.is_seed,
            "top_endorsers": [
                {"handle": h, "share": s} for h, s in self.top_endorsers
            ],
        }


@dataclass
class RankingConfig:
    damping: float = 0.85
    background: float = 0.02
    follow_soft_cap: int = 2000
    spam_exponent: float = 0.5
    tol: float = 1e-10
    max_iter: int = 200
    explain_top_k: int = 5


@dataclass
class Ranking:
    rows: List[RankedAccount]
    seeds_resolved: List[str]
    seeds_missing: List[str]
    topic_result: PageRankResult
    baseline_result: PageRankResult
    config: RankingConfig

    def sorted_by(self, metric: str = "authority", exclude_seeds: bool = False):
        rows = [r for r in self.rows if not (exclude_seeds and r.is_seed)]
        return sorted(rows, key=lambda r: getattr(r, metric), reverse=True)


def rank(
    graph: FollowGraph,
    seeds: Sequence[str],
    seed_weights: Optional[Dict[str, float]] = None,
    config: Optional[RankingConfig] = None,
) -> Ranking:
    """Score every account in ``graph`` relative to the ``seeds`` topic."""
    cfg = config or RankingConfig()
    n = len(graph)
    if n == 0:
        empty = PageRankResult([], 0, 0.0, True)
        return Ranking([], [], list(seeds), empty, empty, cfg)

    trust = graph.trust_vector(
        follow_soft_cap=cfg.follow_soft_cap,
        spam_exponent=cfg.spam_exponent,
    )

    resolved, missing = resolve_seeds(graph, seeds)
    seed_indices = set(resolved)

    p_topic = seed_teleport(graph, seeds, seed_weights, background=cfg.background)
    topic = pagerank(
        graph,
        teleport=p_topic,
        damping=cfg.damping,
        tol=cfg.tol,
        max_iter=cfg.max_iter,
        trust=trust,
    )
    # The comparison run: same graph, same trust, uniform teleport. Its only
    # job is to tell us what "famous regardless of topic" looks like here.
    baseline = pagerank(
        graph,
        teleport=uniform_teleport(n),
        damping=cfg.damping,
        tol=cfg.tol,
        max_iter=cfg.max_iter,
        trust=trust,
    )

    a = topic.scores
    b = baseline.scores

    # Per-edge vote weight, recomputed here for the one-hop metrics.
    out_w = [0.0] * n
    for u in range(n):
        deg = graph.out_degree(u)
        if deg:
            out_w[u] = trust[u] / deg

    eps = 1.0 / (n * 1000.0)
    rows: List[RankedAccount] = []

    for v in range(n):
        contributions: List[Tuple[int, float]] = []
        endorsement = 0.0
        for u in graph.in_adj[v]:
            c = a[u] * out_w[u]
            if c > 0.0:
                endorsement += c
                contributions.append((u, c))

        curator = 0.0
        w = out_w[v]
        if w:
            for x in graph.out_adj[v]:
                curator += a[x] * w

        followers = graph.account_of(v).followers
        if followers is None:
            followers = graph.in_degree(v)
        # log-damped: raw authority/followers is dominated by tiny accounts
        # with one lucky follower, which is noise, not a discovery.
        underrated = a[v] / math.log10(max(followers, 0) + 10.0)

        contributions.sort(key=lambda t: t[1], reverse=True)
        top = [
            (graph.label(u), (c / endorsement) if endorsement > 0 else 0.0)
            for u, c in contributions[: cfg.explain_top_k]
        ]

        rows.append(
            RankedAccount(
                index=v,
                account=graph.account_of(v),
                authority=a[v],
                endorsement=endorsement,
                baseline=b[v],
                topic_focus=a[v] / (b[v] + eps),
                underrated=underrated,
                curator=curator,
                in_degree=graph.in_degree(v),
                out_degree=graph.out_degree(v),
                is_seed=v in seed_indices,
                top_endorsers=top,
            )
        )

    return Ranking(
        rows=rows,
        seeds_resolved=[resolved[i] for i in sorted(resolved)],
        seeds_missing=missing,
        topic_result=topic,
        baseline_result=baseline,
        config=cfg,
    )


def format_table(
    rows: Sequence[RankedAccount],
    metric: str = "authority",
    limit: int = 25,
    show_explanations: bool = False,
) -> str:
    """Render a ranked list as a fixed-width table."""
    rows = list(rows)[:limit]
    if not rows:
        return "(no accounts)"

    width = max(len(r.account.label) for r in rows)
    width = max(min(width, 28), 8)

    out = [
        f"{'#':>3}  {'account':<{width}}  {'authority':>10}  {'focus':>7}  "
        f"{'underrated':>10}  {'followers':>10}  {'in':>6}"
    ]
    out.append("-" * len(out[0]))

    for i, r in enumerate(rows, 1):
        label = r.account.label
        if len(label) > width:
            label = label[: width - 1] + "…"
        followers = "-" if r.account.followers is None else f"{r.account.followers:,}"
        seed_mark = "*" if r.is_seed else " "
        out.append(
            f"{i:>3}{seed_mark} {label:<{width}}  {r.authority:>10.6f}  "
            f"{r.topic_focus:>7.2f}  {r.underrated:>10.6f}  {followers:>10}  "
            f"{r.in_degree:>6}"
        )
        if show_explanations and r.top_endorsers:
            parts = ", ".join(f"{h} {s:.0%}" for h, s in r.top_endorsers)
            out.append(f"{'':>{width + 5}}↳ endorsed by: {parts}")

    out.append("")
    out.append("* = seed account   focus = topical vs. generic popularity")
    return "\n".join(out)
