"""Command-line interface for InstaIndex."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .graph import load_graph, write_jsonl
from .ranking import RankingConfig, format_table, rank
from .seeds import list_presets, resolve_seed_argument
from .synth import build_synthetic_graph

METRICS = ("authority", "endorsement", "topic_focus", "underrated", "curator")


def _add_ranking_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--damping", type=float, default=0.85,
                   help="probability the random surfer follows an edge (default: 0.85)")
    p.add_argument("--background", type=float, default=0.02,
                   help="uniform mass mixed into the teleport vector; keep small (default: 0.02)")
    p.add_argument("--follow-soft-cap", type=int, default=2000,
                   help="following count above which an account's votes are discounted")
    p.add_argument("--spam-exponent", type=float, default=0.5,
                   help="strength of the follow-spam discount; 0 disables it")
    p.add_argument("--metric", choices=METRICS, default="authority",
                   help="metric to sort by (default: authority)")
    p.add_argument("--limit", type=int, default=25, help="rows to show (default: 25)")
    p.add_argument("--exclude-seeds", action="store_true",
                   help="hide seed accounts from the output (useful for discovery)")
    p.add_argument("--explain", action="store_true",
                   help="show which followers produced each score")
    p.add_argument("--json", dest="json_out", metavar="PATH",
                   help="also write full results to this JSON file")


def _report(graph, ranking, args) -> None:
    print(f"graph: {len(graph):,} accounts, {graph.n_edges:,} follow edges")
    print(
        f"seeds: {len(ranking.seeds_resolved)} resolved"
        + (f", {len(ranking.seeds_missing)} not found in graph" if ranking.seeds_missing else "")
    )
    if ranking.seeds_missing:
        shown = ", ".join(ranking.seeds_missing[:8])
        more = "" if len(ranking.seeds_missing) <= 8 else f" (+{len(ranking.seeds_missing) - 8} more)"
        print(f"  missing: {shown}{more}")
    tr = ranking.topic_result
    status = "converged" if tr.converged else "HIT ITERATION CAP"
    print(f"pagerank: {status} in {tr.iterations} iterations (residual {tr.residual:.2e})")
    print()

    rows = ranking.sorted_by(args.metric, exclude_seeds=args.exclude_seeds)
    print(f"ranked by {args.metric}:")
    print()
    print(format_table(rows, metric=args.metric, limit=args.limit,
                       show_explanations=args.explain))

    if args.json_out:
        payload = {
            "metric": args.metric,
            "seeds_resolved": ranking.seeds_resolved,
            "seeds_missing": ranking.seeds_missing,
            "accounts": len(graph),
            "edges": graph.n_edges,
            "converged": tr.converged,
            "iterations": tr.iterations,
            "results": [r.as_dict() for r in rows],
        }
        parent = os.path.dirname(os.path.abspath(args.json_out))
        os.makedirs(parent, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        print(f"\nwrote {args.json_out}")


def cmd_rank(args) -> int:
    if not os.path.exists(args.edges):
        print(f"error: edges file not found: {args.edges}", file=sys.stderr)
        return 2

    graph = load_graph(args.accounts, args.edges)
    if len(graph) == 0:
        print("error: graph is empty", file=sys.stderr)
        return 2

    seed_keys, seed_weights = resolve_seed_argument(args.seeds)
    if not seed_keys:
        print(f"error: no seeds parsed from {args.seeds!r}", file=sys.stderr)
        return 2

    cfg = RankingConfig(
        damping=args.damping,
        background=args.background,
        follow_soft_cap=args.follow_soft_cap,
        spam_exponent=args.spam_exponent,
    )
    ranking = rank(graph, seed_keys, seed_weights, cfg)

    if not ranking.seeds_resolved:
        print("error: none of the seeds appear in the graph -- check that seed "
              "handles match the ids/handles in your data", file=sys.stderr)
        return 2

    _report(graph, ranking, args)
    return 0


def cmd_demo(args) -> int:
    graph, seed_handles, archetype = build_synthetic_graph()
    cfg = RankingConfig(
        damping=args.damping,
        background=args.background,
        follow_soft_cap=args.follow_soft_cap,
        spam_exponent=args.spam_exponent,
    )
    ranking = rank(graph, seed_handles, None, cfg)
    _report(graph, ranking, args)

    # The point of the demo: show that follower count is not the ranking.
    print()
    print("sanity check -- mean authority by archetype:")
    buckets = {}
    for row in ranking.rows:
        buckets.setdefault(archetype.get(row.account.id, "?"), []).append(row.authority)
    for kind, vals in sorted(buckets.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
        mean = sum(vals) / len(vals)
        print(f"  {kind:<10} n={len(vals):>4}  mean authority {mean:.6f}")

    by_followers = sorted(ranking.rows, key=lambda r: -(r.account.followers or 0))[:5]
    print()
    print("top 5 by raw follower count (what a naive ranking would return):")
    for r in by_followers:
        print(f"  {r.account.label:<18} {r.account.followers:>12,} followers  "
              f"authority {r.authority:.6f}  ({archetype.get(r.account.id, '?')})")
    return 0


def cmd_synth(args) -> int:
    graph, seed_handles, _ = build_synthetic_graph()
    acc_path = os.path.join(args.out, "accounts.jsonl")
    edge_path = os.path.join(args.out, "edges.jsonl")
    seed_path = os.path.join(args.out, "seeds.json")

    write_jsonl(acc_path, (
        {
            "id": a.id, "platform": a.platform, "handle": a.handle, "name": a.name,
            "followers": a.followers, "following": a.following,
            "bio": a.bio, "verified": a.verified,
        }
        for a in (graph.account_of(i) for i in range(len(graph)))
    ))
    write_jsonl(edge_path, (
        {"src": graph.ids[u], "dst": graph.ids[v]}
        for u in range(len(graph)) for v in graph.out_adj[u]
    ))
    os.makedirs(args.out, exist_ok=True)
    with open(seed_path, "w", encoding="utf-8") as fh:
        json.dump({"seeds": seed_handles}, fh, indent=2)

    print(f"wrote {acc_path} ({len(graph):,} accounts)")
    print(f"wrote {edge_path} ({graph.n_edges:,} edges)")
    print(f"wrote {seed_path} ({len(seed_handles)} seeds)")
    print()
    print("now try:")
    print(f"  python -m instaindex rank --accounts {acc_path} "
          f"--edges {edge_path} --seeds {seed_path} --explain")
    return 0


def cmd_seeds(args) -> int:
    presets = list_presets()
    if not presets:
        print("no seed presets found in data/seeds/")
        return 0
    print("available seed presets (use with --seeds <name>):")
    for name in presets:
        keys, _ = resolve_seed_argument(name)
        print(f"  {name:<24} {len(keys)} accounts")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instaindex",
        description="Topic-sensitive PageRank over a social follow graph: "
                    "rank creators by who follows them, not how many.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_rank = sub.add_parser("rank", help="rank accounts in a follow graph")
    p_rank.add_argument("--edges", required=True, help="edges.jsonl ({src: follower, dst: followee})")
    p_rank.add_argument("--accounts", help="accounts.jsonl with profile metadata (optional)")
    p_rank.add_argument("--seeds", required=True,
                        help="seed file path, preset name, or comma-separated handles")
    _add_ranking_flags(p_rank)
    p_rank.set_defaults(func=cmd_rank)

    p_demo = sub.add_parser("demo", help="run on a synthetic graph, no data needed")
    _add_ranking_flags(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_synth = sub.add_parser("synth", help="write the synthetic graph to disk")
    p_synth.add_argument("--out", default="data/synthetic", help="output directory")
    p_synth.set_defaults(func=cmd_synth)

    p_seeds = sub.add_parser("seeds", help="list bundled seed presets")
    p_seeds.set_defaults(func=cmd_seeds)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
