"""Snowball crawler for building the follow graph.

The single most important engineering decision in this project:

    CRAWL "FOLLOWING" LISTS, NOT "FOLLOWER" LISTS.

Both directions yield the same edge set, but a big account has 5,000,000
followers and follows 400 people. Paging its follower list costs ~5,000 API
calls; paging its following list costs 2. Since PageRank only needs the edges,
always walk outward along *following*.

The crawl starts at the seed set and expands breadth-first. It does not need
the whole platform -- two hops out from a good seed set already covers the
community you care about, because the people worth ranking are, by definition,
the people the seeds (and the seeds' peers) follow.

This module deliberately contains no HTTP code. You supply a ``fetch_following``
callable for your platform and credentials; the crawler handles the frontier,
deduplication, budgets, checkpointing and resume. That keeps the part that can
be tested separate from the part that needs an API key.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .graph import Account, FollowGraph

# fetch_following(user_key) -> (list_of_followee_keys, optional_profile_dict)
FetchFollowing = Callable[[str], Tuple[Sequence[str], Optional[dict]]]


@dataclass
class CrawlBudget:
    max_hops: int = 2
    max_accounts: int = 5000
    max_following_per_account: int = 5000
    # Skip accounts that follow absurd numbers of people: they cost the most
    # to fetch and contribute the least signal (their votes are already
    # discounted to near-nothing by the trust factor at ranking time).
    skip_if_following_over: int = 25_000
    sleep_between_calls: float = 0.0


@dataclass
class CrawlState:
    """Serializable crawl progress, so a rate-limited run can resume."""

    visited: Set[str] = field(default_factory=set)
    frontier: List[Tuple[str, int]] = field(default_factory=list)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    profiles: Dict[str, dict] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "visited": sorted(self.visited),
            "frontier": [[k, h] for k, h in self.frontier],
            "edges": [[a, b] for a, b in self.edges],
            "profiles": self.profiles,
            "failed": self.failed,
        }

    @classmethod
    def from_json(cls, data: dict) -> "CrawlState":
        return cls(
            visited=set(data.get("visited", [])),
            frontier=[(k, int(h)) for k, h in data.get("frontier", [])],
            edges=[(a, b) for a, b in data.get("edges", [])],
            profiles=data.get("profiles", {}),
            failed=data.get("failed", {}),
        )

    def save(self, path: str) -> None:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_json(), fh)
        os.replace(tmp, path)  # atomic: a killed crawl never corrupts state

    @classmethod
    def load(cls, path: str) -> "CrawlState":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(json.load(fh))


def snowball(
    seeds: Sequence[str],
    fetch_following: FetchFollowing,
    budget: Optional[CrawlBudget] = None,
    state: Optional[CrawlState] = None,
    checkpoint_path: Optional[str] = None,
    checkpoint_every: int = 25,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> CrawlState:
    """Breadth-first crawl outward from ``seeds`` along following edges.

    Hop 0 is the seeds themselves. An account discovered at hop ``h`` is only
    expanded if ``h < budget.max_hops``; otherwise its edges are still recorded
    (it can be *ranked*), it is just not itself paged. This is what keeps the
    crawl finite while leaving the graph's outer rim intact.
    """
    b = budget or CrawlBudget()
    st = state or CrawlState()

    if not st.frontier and not st.visited:
        st.frontier = [(s.strip().lstrip("@"), 0) for s in seeds if s.strip()]

    processed = 0
    while st.frontier:
        if len(st.visited) >= b.max_accounts:
            break

        key, hop = st.frontier.pop(0)
        if key in st.visited:
            continue
        st.visited.add(key)

        try:
            following, profile = fetch_following(key)
        except Exception as exc:  # noqa: BLE001 - one bad account must not kill a long crawl
            st.failed[key] = f"{type(exc).__name__}: {exc}"
            continue

        if profile:
            st.profiles[key] = profile
            declared = profile.get("following")
            if isinstance(declared, int) and declared > b.skip_if_following_over:
                # Record the profile, drop the (low-signal, high-cost) edges.
                continue

        following = list(following)[: b.max_following_per_account]
        for target in following:
            target = str(target).strip().lstrip("@")
            if not target or target == key:
                continue
            st.edges.append((key, target))
            if hop + 1 <= b.max_hops and target not in st.visited:
                st.frontier.append((target, hop + 1))

        processed += 1
        if on_progress:
            on_progress(len(st.visited), len(st.frontier), key)
        if checkpoint_path and processed % checkpoint_every == 0:
            st.save(checkpoint_path)
        if b.sleep_between_calls:
            time.sleep(b.sleep_between_calls)

    if checkpoint_path:
        st.save(checkpoint_path)
    return st


def state_to_graph(st: CrawlState, platform: str = "unknown") -> FollowGraph:
    """Turn crawl output into a rankable :class:`FollowGraph`."""
    graph = FollowGraph()
    for key, profile in st.profiles.items():
        graph.add_account(
            Account(
                id=key,
                platform=profile.get("platform", platform),
                handle=str(profile.get("handle") or key),
                name=str(profile.get("name") or ""),
                followers=profile.get("followers"),
                following=profile.get("following"),
                bio=str(profile.get("bio") or ""),
                verified=bool(profile.get("verified", False)),
            )
        )
    for src, dst in st.edges:
        graph.add_follow(src, dst)
    return graph


def prune_to_core(
    graph: FollowGraph, min_in_degree: int = 2
) -> Tuple[List[str], int]:
    """Identify rim accounts seen too rarely to rank meaningfully.

    A two-hop crawl ends with a huge fringe of accounts followed by exactly one
    crawled account. They cannot be scored reliably -- we have one data point on
    them -- and they inflate the graph. This returns the ids worth keeping plus
    the number dropped, so you can decide whether to filter before ranking.

    Note this is about *reporting*, not correctness: leaving them in does not
    break PageRank, it just adds noise to the long tail.
    """
    keep = [
        graph.ids[i] for i in range(len(graph)) if graph.in_degree(i) >= min_in_degree
    ]
    return keep, len(graph) - len(keep)
