"""Tests for the snowball crawler, using a fake platform API."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instaindex.crawl import (
    CrawlBudget,
    CrawlState,
    prune_to_core,
    snowball,
    state_to_graph,
)
from instaindex.ranking import rank


class FakePlatform:
    """A tiny in-memory social network to crawl."""

    def __init__(self, following_map, profiles=None, fail_on=()):
        self.following_map = following_map
        self.profiles = profiles or {}
        self.fail_on = set(fail_on)
        self.calls = []

    def fetch(self, key):
        self.calls.append(key)
        if key in self.fail_on:
            raise RuntimeError("rate limited")
        return self.following_map.get(key, []), self.profiles.get(key)


class TestSnowball(unittest.TestCase):
    def setUp(self):
        self.net = {
            "a": ["b", "c"],
            "b": ["c", "d"],
            "c": ["d"],
            "d": ["e"],
            "e": ["a"],
        }

    def test_one_hop_expands_only_seeds(self):
        api = FakePlatform(self.net)
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=0))
        self.assertEqual(api.calls, ["a"])
        self.assertEqual(set(st.edges), {("a", "b"), ("a", "c")})

    def test_two_hops(self):
        api = FakePlatform(self.net)
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=1))
        self.assertEqual(set(api.calls), {"a", "b", "c"})
        self.assertIn(("b", "d"), st.edges)

    def test_rim_edges_recorded_without_expansion(self):
        """Accounts at the hop limit are still ranked, just not paged."""
        api = FakePlatform(self.net)
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=1))
        self.assertNotIn("d", api.calls)
        graph = state_to_graph(st)
        self.assertIsNotNone(graph.resolve("d"))

    def test_no_duplicate_fetches(self):
        api = FakePlatform(self.net)
        snowball(["a"], api.fetch, CrawlBudget(max_hops=3))
        self.assertEqual(len(api.calls), len(set(api.calls)))

    def test_cycle_terminates(self):
        api = FakePlatform(self.net)
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=10))
        self.assertEqual(st.visited, {"a", "b", "c", "d", "e"})

    def test_account_budget_respected(self):
        api = FakePlatform(self.net)
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=10, max_accounts=3))
        self.assertLessEqual(len(st.visited), 3)

    def test_failure_is_recorded_not_fatal(self):
        api = FakePlatform(self.net, fail_on=["b"])
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=2))
        self.assertIn("b", st.failed)
        self.assertIn("rate limited", st.failed["b"])
        self.assertIn("c", st.visited)

    def test_skips_accounts_following_too_many(self):
        api = FakePlatform(
            {"a": ["spam"], "spam": [f"x{i}" for i in range(100)]},
            profiles={"spam": {"following": 99_999}},
        )
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=2, skip_if_following_over=25_000))
        self.assertIn("spam", st.profiles)
        self.assertFalse([e for e in st.edges if e[0] == "spam"])

    def test_self_follow_edge_dropped(self):
        api = FakePlatform({"a": ["a", "b"]})
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=0))
        self.assertEqual(st.edges, [("a", "b")])

    def test_handles_are_normalized(self):
        api = FakePlatform({"a": ["@b"]})
        st = snowball(["@a"], api.fetch, CrawlBudget(max_hops=0))
        self.assertEqual(st.edges, [("a", "b")])

    def test_following_page_cap(self):
        api = FakePlatform({"a": [f"u{i}" for i in range(500)]})
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=0, max_following_per_account=10))
        self.assertEqual(len(st.edges), 10)


class TestCheckpointing(unittest.TestCase):
    def test_roundtrip_and_resume(self):
        api = FakePlatform({
            "a": ["b", "c"], "b": ["d"], "c": ["d"], "d": ["e"], "e": [],
        })
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            partial = snowball(["a"], api.fetch, CrawlBudget(max_hops=5, max_accounts=2),
                               checkpoint_path=path)
            self.assertTrue(os.path.exists(path))

            reloaded = CrawlState.load(path)
            self.assertEqual(reloaded.visited, partial.visited)
            self.assertEqual([tuple(e) for e in reloaded.edges],
                             [tuple(e) for e in partial.edges])

            resumed = snowball([], api.fetch, CrawlBudget(max_hops=5, max_accounts=99),
                               state=reloaded)
            self.assertGreater(len(resumed.visited), len(partial.visited))


class TestGraphConversion(unittest.TestCase):
    def test_profiles_become_accounts(self):
        api = FakePlatform(
            {"a": ["b"]},
            profiles={"a": {"handle": "alice", "followers": 1000, "following": 50,
                            "name": "Alice", "verified": True}},
        )
        st = snowball(["a"], api.fetch, CrawlBudget(max_hops=0))
        g = state_to_graph(st, platform="x")
        acct = g.account_of(g.resolve("a"))
        self.assertEqual(acct.handle, "alice")
        self.assertEqual(acct.followers, 1000)
        self.assertTrue(acct.verified)
        self.assertEqual(acct.platform, "x")

    def test_crawled_graph_is_rankable(self):
        net = {f"u{i}": [f"u{j}" for j in range(10) if j != i] for i in range(10)}
        api = FakePlatform(net)
        st = snowball(["u0"], api.fetch, CrawlBudget(max_hops=2))
        g = state_to_graph(st)
        ranking = rank(g, ["u0"])
        self.assertTrue(ranking.topic_result.converged)
        self.assertEqual(len(ranking.rows), len(g))

    def test_prune_to_core(self):
        api = FakePlatform({"a": ["hub", "rim1"], "b": ["hub", "rim2"], "hub": []})
        st = snowball(["a", "b"], api.fetch, CrawlBudget(max_hops=1))
        g = state_to_graph(st)
        keep, dropped = prune_to_core(g, min_in_degree=2)
        self.assertIn("hub", keep)
        self.assertNotIn("rim1", keep)
        self.assertGreater(dropped, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
