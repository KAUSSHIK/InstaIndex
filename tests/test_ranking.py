"""End-to-end tests: does the ranking actually pick good creators?

These are the tests that matter. They assert the product behaviour -- that
follower count does not buy rank, that a botnet does not buy rank, and that
genuine topical authorities are recovered even when they were not seeded.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instaindex.graph import Account, FollowGraph
from instaindex.ranking import RankingConfig, format_table, rank
from instaindex.seeds import load_seed_file, resolve_seed_argument
from instaindex.synth import build_synthetic_graph


class TestSyntheticEcosystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph, cls.seeds, cls.archetype = build_synthetic_graph()
        cls.ranking = rank(cls.graph, cls.seeds)
        cls.by_kind = {}
        for row in cls.ranking.rows:
            kind = cls.archetype[row.account.id]
            cls.by_kind.setdefault(kind, []).append(row)

    def mean_authority(self, kind):
        rows = self.by_kind[kind]
        return sum(r.authority for r in rows) / len(rows)

    def test_all_seeds_resolved(self):
        self.assertEqual(len(self.ranking.seeds_resolved), len(self.seeds))
        self.assertEqual(self.ranking.seeds_missing, [])

    def test_converged(self):
        self.assertTrue(self.ranking.topic_result.converged)
        self.assertTrue(self.ranking.baseline_result.converged)

    def test_authorities_outrank_every_other_archetype(self):
        top = self.mean_authority("authority")
        for kind in ("creator", "celebrity", "botnet", "lurker", "gamer"):
            self.assertGreater(top, self.mean_authority(kind),
                               f"authorities should outrank {kind}")

    def test_creators_outrank_celebrities(self):
        """A 50k-follower topical creator must beat a 50M-follower pop star."""
        self.assertGreater(self.mean_authority("creator"),
                           self.mean_authority("celebrity") * 10)

    def test_botnet_does_not_buy_rank(self):
        """250 fake followers each pointing at the gamer must not work."""
        gamers = self.by_kind["gamer"]
        creators = self.by_kind["creator"]
        best_gamer = max(r.authority for r in gamers)
        median_creator = sorted(r.authority for r in creators)[len(creators) // 2]
        self.assertLess(best_gamer, median_creator,
                        "purchased followers should not lift an account above "
                        "ordinary genuine creators")

    def test_unseeded_authorities_are_recovered(self):
        """The algorithm must find good accounts it was never told about."""
        seed_set = set(self.seeds)
        ranked = self.ranking.sorted_by("authority", exclude_seeds=True)
        top20 = ranked[:20]
        found = [r for r in top20 if self.archetype[r.account.id] == "authority"]
        self.assertGreaterEqual(len(found), 15,
                                "most of the top 20 non-seed accounts should be "
                                "genuine authorities discovered from structure")
        for r in found:
            self.assertNotIn(r.account.id, seed_set)

    def test_follower_count_does_not_determine_rank(self):
        """The whole premise: the biggest accounts are not the best accounts."""
        biggest = max(self.ranking.rows, key=lambda r: r.account.followers or 0)
        self.assertEqual(self.archetype[biggest.account.id], "celebrity")
        best = max(self.ranking.rows, key=lambda r: r.authority)
        self.assertEqual(self.archetype[best.account.id], "authority")
        self.assertGreater(best.authority, biggest.authority * 50)

    def test_topic_focus_separates_topical_from_generic(self):
        self.assertGreater(self.mean_topic_focus("authority"),
                           self.mean_topic_focus("celebrity"))

    def mean_topic_focus(self, kind):
        rows = self.by_kind[kind]
        return sum(r.topic_focus for r in rows) / len(rows)

    def test_endorsement_and_authority_agree_at_the_top(self):
        """The one-hop metric should broadly track the recursive one."""
        by_auth = {r.account.id for r in self.ranking.sorted_by("authority")[:25]}
        by_end = {r.account.id for r in self.ranking.sorted_by("endorsement")[:25]}
        self.assertGreaterEqual(len(by_auth & by_end), 15)

    def test_explanations_are_populated_and_normalized(self):
        top = self.ranking.sorted_by("authority")[0]
        self.assertTrue(top.top_endorsers)
        for _handle, share in top.top_endorsers:
            self.assertGreater(share, 0.0)
            self.assertLessEqual(share, 1.0)

    def test_lurkers_score_near_zero(self):
        """Accounts nobody follows should have essentially no authority."""
        self.assertLess(self.mean_authority("lurker"),
                        self.mean_authority("creator") / 100)


class TestRankingMechanics(unittest.TestCase):
    def test_empty_graph_is_handled(self):
        r = rank(FollowGraph(), ["nobody"])
        self.assertEqual(r.rows, [])

    def test_exclude_seeds(self):
        g, seeds, _ = build_synthetic_graph()
        r = rank(g, seeds)
        ids = {row.account.id for row in r.sorted_by("authority", exclude_seeds=True)}
        self.assertTrue(set(seeds).isdisjoint(ids))

    def test_missing_seeds_are_reported_not_fatal(self):
        g, seeds, _ = build_synthetic_graph()
        r = rank(g, list(seeds) + ["ghost_account_xyz"])
        self.assertIn("ghost_account_xyz", r.seeds_missing)
        self.assertEqual(len(r.seeds_resolved), len(seeds))

    def test_underrated_favours_smaller_accounts_at_equal_authority(self):
        g = FollowGraph()
        g.add_account(Account(id="big", followers=5_000_000))
        g.add_account(Account(id="small", followers=5_000))
        for i in range(20):
            g.add_account(Account(id=f"peer{i}", following=100))
            g.add_follow(f"peer{i}", "big")
            g.add_follow(f"peer{i}", "small")
        r = rank(g, [f"peer{i}" for i in range(20)])
        rows = {row.account.id: row for row in r.rows}
        self.assertAlmostEqual(rows["big"].authority, rows["small"].authority, places=9)
        self.assertGreater(rows["small"].underrated, rows["big"].underrated)

    def test_curator_score_rewards_pointing_at_good_accounts(self):
        g, seeds, archetype = build_synthetic_graph()
        r = rank(g, seeds)
        by_kind = {}
        for row in r.rows:
            by_kind.setdefault(archetype[row.account.id], []).append(row.curator)
        mean = lambda k: sum(by_kind[k]) / len(by_kind[k])
        self.assertGreater(mean("creator"), mean("botnet"))

    def test_config_is_honoured(self):
        g, seeds, _ = build_synthetic_graph()
        r = rank(g, seeds, config=RankingConfig(damping=0.5, spam_exponent=0.0))
        self.assertEqual(r.config.damping, 0.5)
        self.assertTrue(r.topic_result.converged)

    def test_format_table_runs(self):
        g, seeds, _ = build_synthetic_graph()
        r = rank(g, seeds)
        text = format_table(r.sorted_by("authority"), limit=5, show_explanations=True)
        self.assertIn("authority", text)
        self.assertEqual(text.count("↳"), 5)


class TestSeedLoading(unittest.TestCase):
    def test_comma_separated(self):
        keys, weights = resolve_seed_argument("alice,@bob, carol")
        self.assertEqual(keys, ["alice", "bob", "carol"])
        self.assertEqual(weights["alice"], 1.0)

    def test_json_list_and_weights(self):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"seeds": [{"handle": "a", "weight": 2.5}, "b"]}, fh)
            path = fh.name
        try:
            keys, weights = load_seed_file(path)
            self.assertEqual(keys, ["a", "b"])
            self.assertEqual(weights["a"], 2.5)
            self.assertEqual(weights["b"], 1.0)
        finally:
            os.unlink(path)

    def test_plain_text_with_comments(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("@alice  # a comment\n\nbob\n# whole line comment\n")
            path = fh.name
        try:
            keys, _ = load_seed_file(path)
            self.assertEqual(keys, ["alice", "bob"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
