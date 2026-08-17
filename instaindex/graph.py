"""Follow-graph model.

Edge direction is the whole ballgame, so state it once, loudly:

    an edge  u -> v  means "u follows v", i.e. u ENDORSES v.

PageRank flows authority along edges, so authority flows from the follower to
the followed. That is exactly the intuition "if great people follow this
account, this account is great."
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


@dataclass
class Account:
    """Profile metadata for one node. Everything except ``id`` is optional."""

    id: str
    platform: str = "unknown"
    handle: str = ""
    name: str = ""
    followers: Optional[int] = None
    following: Optional[int] = None
    bio: str = ""
    verified: bool = False

    @property
    def label(self) -> str:
        return self.handle or self.name or self.id


@dataclass
class FollowGraph:
    """Sparse directed graph held as parallel adjacency lists.

    We keep both directions. Out-edges drive the vote-splitting; in-edges drive
    the power iteration and, just as importantly, the explanations ("which
    followers actually produced this score?").
    """

    ids: List[str] = field(default_factory=list)
    index: Dict[str, int] = field(default_factory=dict)
    accounts: Dict[str, Account] = field(default_factory=dict)
    out_adj: List[List[int]] = field(default_factory=list)
    in_adj: List[List[int]] = field(default_factory=list)
    _edges: set = field(default_factory=set, repr=False)

    # -- construction ----------------------------------------------------

    def node(self, node_id: str) -> int:
        """Return the dense index for ``node_id``, creating the node if new."""
        idx = self.index.get(node_id)
        if idx is None:
            idx = len(self.ids)
            self.index[node_id] = idx
            self.ids.append(node_id)
            self.out_adj.append([])
            self.in_adj.append([])
        return idx

    def add_account(self, account: Account) -> int:
        idx = self.node(account.id)
        self.accounts[account.id] = account
        return idx

    def add_follow(self, follower_id: str, followee_id: str) -> bool:
        """Record "follower_id follows followee_id". Returns False if dropped.

        Self-follows and duplicates are dropped: both are pure noise, and a
        duplicated edge would silently double someone's vote.
        """
        if follower_id == followee_id:
            return False
        u = self.node(follower_id)
        v = self.node(followee_id)
        key = (u, v)
        if key in self._edges:
            return False
        self._edges.add(key)
        self.out_adj[u].append(v)
        self.in_adj[v].append(u)
        return True

    # -- accessors -------------------------------------------------------

    def __len__(self) -> int:
        return len(self.ids)

    @property
    def n_edges(self) -> int:
        return len(self._edges)

    def account_of(self, idx: int) -> Account:
        node_id = self.ids[idx]
        acct = self.accounts.get(node_id)
        if acct is None:
            acct = Account(id=node_id)
        return acct

    def label(self, idx: int) -> str:
        return self.account_of(idx).label

    def out_degree(self, idx: int) -> int:
        return len(self.out_adj[idx])

    def in_degree(self, idx: int) -> int:
        return len(self.in_adj[idx])

    def resolve(self, key: str) -> Optional[int]:
        """Look a node up by id or by (case-insensitive, @-tolerant) handle."""
        if key in self.index:
            return self.index[key]
        probe = key.lstrip("@").lower()
        if probe in self.index:
            return self.index[probe]
        for node_id, idx in self.index.items():
            if node_id.lstrip("@").lower() == probe:
                return idx
        for node_id, acct in self.accounts.items():
            if acct.handle.lstrip("@").lower() == probe:
                return self.index.get(node_id)
        return None

    # -- vote weights ----------------------------------------------------

    def trust_vector(
        self,
        follow_soft_cap: int = 2000,
        spam_exponent: float = 0.5,
    ) -> List[float]:
        """Per-node multiplier on how much of its rank a node gets to pass on.

        An account following 50,000 others is a follow-spammer, a growth-hack
        bot, or an indiscriminate lurker. Out-degree normalization already
        makes each of its individual votes tiny, but it does not stop the
        account from acting as a high-throughput conduit that launders rank
        into whatever it follows. So we additionally shrink its total outflow;
        the withheld mass is not kept by the node, it is returned to the
        teleport distribution (see :mod:`instaindex.pagerank`).

        ``trust = min(1, (soft_cap / following) ** spam_exponent)``

        Set ``spam_exponent=0`` to disable and get textbook PageRank.
        """
        if spam_exponent <= 0.0:
            return [1.0] * len(self.ids)

        trust: List[float] = []
        for idx, node_id in enumerate(self.ids):
            acct = self.accounts.get(node_id)
            # Prefer the profile's self-reported following count: the crawled
            # out-degree only sees the slice of the graph we collected, so it
            # understates how promiscuous an account really is.
            following = None
            if acct is not None and acct.following is not None:
                following = acct.following
            if following is None:
                following = self.out_degree(idx)
            if following <= follow_soft_cap:
                trust.append(1.0)
            else:
                trust.append((follow_soft_cap / float(following)) ** spam_exponent)
        return trust


# -- loaders -------------------------------------------------------------


def _iter_jsonl(path: str) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: bad JSON: {exc}") from exc


def _as_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_accounts(graph: FollowGraph, path: str) -> int:
    """Load ``accounts.jsonl``: one profile object per line."""
    count = 0
    for row in _iter_jsonl(path):
        node_id = row.get("id") or row.get("handle") or row.get("username")
        if not node_id:
            continue
        graph.add_account(
            Account(
                id=str(node_id),
                platform=row.get("platform", "unknown"),
                handle=str(row.get("handle") or row.get("username") or ""),
                name=str(row.get("name") or ""),
                followers=_as_int(row.get("followers")),
                following=_as_int(row.get("following")),
                bio=str(row.get("bio") or ""),
                verified=bool(row.get("verified", False)),
            )
        )
        count += 1
    return count


def load_edges(graph: FollowGraph, path: str) -> int:
    """Load ``edges.jsonl``: ``{"src": follower, "dst": followee}`` per line.

    Also accepts ``{"follower": ..., "followee": ...}``.
    """
    count = 0
    for row in _iter_jsonl(path):
        src = row.get("src") or row.get("follower") or row.get("from")
        dst = row.get("dst") or row.get("followee") or row.get("to")
        if not src or not dst:
            continue
        if graph.add_follow(str(src), str(dst)):
            count += 1
    return count


def load_graph(accounts_path: Optional[str], edges_path: str) -> FollowGraph:
    graph = FollowGraph()
    if accounts_path and os.path.exists(accounts_path):
        load_accounts(graph, accounts_path)
    load_edges(graph, edges_path)
    return graph


def write_jsonl(path: str, rows: Iterable[dict]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
