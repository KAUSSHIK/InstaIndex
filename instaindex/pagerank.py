"""Personalized (topic-sensitive) PageRank over a follow graph.

Plain PageRank on a social graph answers "who is globally famous?" and the
answer is always a footballer or a pop star. We want "who is authoritative in
tech / AI / math / science?", so we use Haveliwala's topic-sensitive variant:
the random surfer, when it teleports, does not jump to a uniformly random
account -- it jumps back into a curated seed set of accounts already known to
be good in the topic.

The fixed point is:

    r  =  (1 - d) * p  +  d * ( M^T r  +  leak(r) * p )

where
    p          teleport distribution (the seed vector; sums to 1)
    d          damping factor (probability of following an edge)
    M[u][v]    trust(u) / outdeg(u)   for each edge u -> v
    leak(r)    rank held by nodes that could not pass all of it on --
               dangling nodes (follow nobody) plus the fraction withheld
               from low-trust follow-spammers.

Routing the leak through ``p`` rather than uniformly is what keeps the result
genuinely personalized; sending it uniformly would quietly re-inject global
popularity into the topic ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .graph import FollowGraph


@dataclass
class PageRankResult:
    scores: List[float]
    iterations: int
    residual: float
    converged: bool


def uniform_teleport(n: int) -> List[float]:
    if n == 0:
        return []
    return [1.0 / n] * n


def seed_teleport(
    graph: FollowGraph,
    seeds: Sequence[str],
    weights: Optional[Dict[str, float]] = None,
    background: float = 0.0,
) -> List[float]:
    """Build the teleport vector ``p`` from a seed set.

    ``background`` mixes in a little uniform mass (0.0-1.0). A small value like
    0.02 is a useful escape valve: with a pure seed vector, any account in a
    region of the graph the seeds cannot reach scores exactly zero. A touch of
    background keeps the ranking defined everywhere while leaving the topical
    signal dominant. Keep it small -- background is precisely the channel
    through which global celebrity leaks back in.

    Unresolvable seeds are ignored (they are reported by
    :func:`resolve_seeds`), so a stale handle in your seed file degrades the
    ranking instead of crashing it.
    """
    n = len(graph)
    if n == 0:
        return []
    if not 0.0 <= background <= 1.0:
        raise ValueError("background must be in [0, 1]")

    resolved, _missing = resolve_seeds(graph, seeds)
    vec = [0.0] * n

    if resolved:
        total = 0.0
        for idx, key in resolved.items():
            w = 1.0 if weights is None else float(weights.get(key, 1.0))
            if w <= 0.0:
                continue
            vec[idx] += w
            total += w
        if total > 0.0:
            scale = (1.0 - background) / total
            for i in range(n):
                if vec[i]:
                    vec[i] *= scale
        else:
            background = 1.0
    else:
        # No usable seeds at all: fall back to uniform rather than returning an
        # all-zero teleport vector, which would make the iteration meaningless.
        background = 1.0

    if background > 0.0:
        share = background / n
        for i in range(n):
            vec[i] += share
    return vec


def resolve_seeds(graph: FollowGraph, seeds: Sequence[str]):
    """Map seed keys (ids or handles) to node indices.

    Returns ``(resolved, missing)`` where ``resolved`` is ``{index: key}`` and
    ``missing`` is the list of keys not present in the graph.
    """
    resolved: Dict[int, str] = {}
    missing: List[str] = []
    for key in seeds:
        idx = graph.resolve(key)
        if idx is None:
            missing.append(key)
        else:
            resolved[idx] = key
    return resolved, missing


def pagerank(
    graph: FollowGraph,
    teleport: Optional[Sequence[float]] = None,
    damping: float = 0.85,
    tol: float = 1e-10,
    max_iter: int = 200,
    trust: Optional[Sequence[float]] = None,
) -> PageRankResult:
    """Power-iterate to the personalized PageRank fixed point.

    ``tol`` is on the L1 distance between successive iterates. With d=0.85 the
    error contracts by ~0.85 per sweep, so this converges in well under 200
    sweeps for any sane tolerance; ``max_iter`` is a guard, not a target.
    """
    n = len(graph)
    if n == 0:
        return PageRankResult(scores=[], iterations=0, residual=0.0, converged=True)
    if not 0.0 <= damping < 1.0:
        raise ValueError("damping must be in [0, 1)")

    p = list(teleport) if teleport is not None else uniform_teleport(n)
    if len(p) != n:
        raise ValueError("teleport vector length does not match graph size")
    total_p = sum(p)
    if total_p <= 0.0:
        raise ValueError("teleport vector must have positive mass")
    if abs(total_p - 1.0) > 1e-12:
        p = [x / total_p for x in p]

    t = list(trust) if trust is not None else [1.0] * n
    if len(t) != n:
        raise ValueError("trust vector length does not match graph size")

    # Per-edge weight, precomputed: an account splits its vote evenly across
    # everyone it follows, scaled by how much we trust it to vote at all.
    out_w = [0.0] * n
    for u in range(n):
        deg = graph.out_degree(u)
        if deg:
            out_w[u] = t[u] / deg

    # Fraction of a node's rank that never reaches any neighbour, and so must
    # be recycled through the teleport vector to conserve total mass.
    retained = [1.0 - out_w[u] * graph.out_degree(u) for u in range(n)]

    r = list(p)
    in_adj = graph.in_adj
    residual = 0.0
    converged = False
    it = 0

    for it in range(1, max_iter + 1):
        leak = 0.0
        for u in range(n):
            if retained[u]:
                leak += r[u] * retained[u]

        base = (1.0 - damping) + damping * leak  # multiplier on p
        nxt = [base * p_i for p_i in p]

        for v in range(n):
            acc = 0.0
            for u in in_adj[v]:
                w = out_w[u]
                if w:
                    acc += r[u] * w
            if acc:
                nxt[v] += damping * acc

        residual = 0.0
        for i in range(n):
            residual += abs(nxt[i] - r[i])
        r = nxt

        if residual < tol:
            converged = True
            break

    # Renormalize to kill accumulated floating-point drift.
    total = sum(r)
    if total > 0.0:
        r = [x / total for x in r]

    return PageRankResult(scores=r, iterations=it, residual=residual, converged=converged)
