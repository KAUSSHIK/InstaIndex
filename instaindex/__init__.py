"""InstaIndex: rank content creators by who follows them, not how many.

    from instaindex import FollowGraph, rank

    g = FollowGraph()
    g.add_follow("alice", "bob")      # alice follows bob => alice endorses bob
    ranking = rank(g, seeds=["alice"])
"""

from .graph import Account, FollowGraph, load_graph
from .pagerank import pagerank, seed_teleport, uniform_teleport
from .ranking import Ranking, RankedAccount, RankingConfig, format_table, rank

__version__ = "0.1.0"

__all__ = [
    "Account",
    "FollowGraph",
    "load_graph",
    "pagerank",
    "seed_teleport",
    "uniform_teleport",
    "Ranking",
    "RankedAccount",
    "RankingConfig",
    "format_table",
    "rank",
]
