"""Generate a synthetic follow graph for demos and tests.

Real follow data is slow and expensive to collect (see docs/DATA_COLLECTION.md),
so the demo runs on a fabricated ecosystem built from archetypes. The archetypes
exist to exercise the exact failure modes the ranking is supposed to survive:

  authority   genuine topical experts; the accounts we want at the top
  creator     mid-size topical creators; the accounts we want to *discover*
  lurker      ordinary users who follow good accounts but are followed by ~none
  celebrity   enormous follower counts, off-topic; vanilla PageRank loves them
  botnet      a clique of fake accounts that all follow each other and one
              "gamer" account, i.e. a follower-count purchase
  gamer       the beneficiary of the botnet

A correct topic-sensitive ranking puts authorities and creators on top, and
leaves the celebrity and the gamer far down the list despite their follower
counts. tests/test_ranking.py asserts exactly that.
"""

from __future__ import annotations

import random
from typing import Dict, List, Tuple

from .graph import Account, FollowGraph

ARCHETYPES = ("authority", "creator", "lurker", "celebrity", "botnet", "gamer")


def build_synthetic_graph(
    seed: int = 20240817,
    n_authorities: int = 30,
    n_creators: int = 120,
    n_lurkers: int = 500,
    n_celebrities: int = 15,
    n_bots: int = 250,
    n_gamers: int = 3,
) -> Tuple[FollowGraph, List[str], Dict[str, str]]:
    """Return ``(graph, seed_handles, archetype_by_id)``.

    The returned seed list is a *subset* of the authorities: seeding with all of
    them would make the test tautological. We seed 8 and check that the ranking
    recovers the other 22 on its own.
    """
    rng = random.Random(seed)
    graph = FollowGraph()
    archetype: Dict[str, str] = {}

    def mint(prefix: str, i: int, kind: str, followers: int, following: int) -> str:
        handle = f"{prefix}_{i:04d}"
        graph.add_account(
            Account(
                id=handle,
                platform="synthetic",
                handle=handle,
                name=handle.replace("_", " ").title(),
                followers=followers,
                following=following,
                bio=f"synthetic {kind}",
                verified=kind in ("authority", "celebrity"),
            )
        )
        archetype[handle] = kind
        return handle

    authorities = [
        mint("authority", i, "authority",
             followers=rng.randint(120_000, 900_000),
             following=rng.randint(150, 900))
        for i in range(n_authorities)
    ]
    creators = [
        mint("creator", i, "creator",
             followers=rng.randint(4_000, 90_000),
             following=rng.randint(200, 1_500))
        for i in range(n_creators)
    ]
    lurkers = [
        mint("lurker", i, "lurker",
             followers=rng.randint(5, 400),
             following=rng.randint(100, 900))
        for i in range(n_lurkers)
    ]
    celebrities = [
        mint("celebrity", i, "celebrity",
             followers=rng.randint(8_000_000, 90_000_000),
             following=rng.randint(20, 300))
        for i in range(n_celebrities)
    ]
    bots = [
        mint("bot", i, "botnet",
             followers=rng.randint(180, 320),
             following=rng.randint(3_000, 9_000))
        for i in range(n_bots)
    ]
    gamers = [
        mint("gamer", i, "gamer",
             followers=rng.randint(200_000, 400_000),
             following=rng.randint(1_000, 3_000))
        for i in range(n_gamers)
    ]

    def connect(src_pool, dst_pool, lo, hi, weighted=False):
        for src in src_pool:
            k = rng.randint(lo, hi)
            if not dst_pool or k <= 0:
                continue
            if weighted:
                # Preferential attachment: earlier accounts in the pool are the
                # bigger ones, so bias picks toward the head of the list.
                picks = set()
                for _ in range(k * 2):
                    j = min(int(abs(rng.gauss(0, len(dst_pool) / 3.0))), len(dst_pool) - 1)
                    picks.add(dst_pool[j])
                    if len(picks) >= k:
                        break
                chosen = picks
            else:
                chosen = rng.sample(dst_pool, min(k, len(dst_pool)))
            for dst in chosen:
                graph.add_follow(src, dst)

    # Authorities follow each other densely -- experts read experts -- and a
    # handful of the strongest creators.
    connect(authorities, authorities, 8, 18)
    connect(authorities, creators, 3, 10, weighted=True)

    # Creators follow authorities heavily (this is the edge that carries real
    # signal) and each other moderately.
    connect(creators, authorities, 6, 20, weighted=True)
    connect(creators, creators, 4, 15)

    # Lurkers follow both tiers plus some celebrities.
    connect(lurkers, authorities, 3, 12, weighted=True)
    connect(lurkers, creators, 2, 10)
    connect(lurkers, celebrities, 1, 5)

    # Celebrities follow mostly each other; they are not part of the topic.
    connect(celebrities, celebrities, 3, 9)
    connect(celebrities, authorities, 0, 1)

    # The purchased-follower attack: a dense bot clique pointed at the gamers.
    connect(bots, bots, 20, 60)
    for bot in bots:
        for g in gamers:
            graph.add_follow(bot, g)
    # A little camouflage so the botnet is not trivially disconnected.
    connect(bots, celebrities, 1, 3)
    connect(gamers, bots, 40, 90)

    seed_handles = authorities[:8]
    return graph, seed_handles, archetype
