"""Correctness tests for the PageRank core."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from instaindex.graph import Account, FollowGraph
from instaindex.pagerank import pagerank, seed_teleport, uniform_teleport


def chain(n):
    g = FollowGraph()
    for i in range(n - 1):
        g.add_follow(f"n{i}", f"n{i+1}")
    g.node(f"n{n-1}")
    return g


class TestInvariants(unittest.TestCase):
    def test_scores_sum_to_one(self):
        g = chain(20)
        r = pagerank(g)
        self.assertAlmostEqual(sum(r.scores), 1.0, places=10)

    def test_dangling_node_conserves_mass(self):
        """n19 follows nobody; its rank must be recycled, not destroyed."""
        g = chain(20)
        r = pagerank(g)
        self.assertTrue(r.converged)
        self.assertAlmostEqual(sum(r.scores), 1.0, places=10)
        self.assertGreater(r.scores[19], 0.0)

    def test_empty_graph(self):
        r = pagerank(FollowGraph())
        self.assertEqual(r.scores, [])
        self.assertTrue(r.converged)

    def test_isolated_nodes_get_teleport_mass_only(self):
        g = FollowGraph()
        g.add_follow("a", "b")
        g.node("island")
        r = pagerank(g)
        self.assertGreater(r.scores[g.index["island"]], 0.0)

    def test_rejects_bad_damping(self):
        with self.assertRaises(ValueError):
            pagerank(chain(3), damping=1.0)

    def test_rejects_zero_teleport(self):
        with self.assertRaises(ValueError):
            pagerank(chain(3), teleport=[0.0, 0.0, 0.0])


class TestSemantics(unittest.TestCase):
    def test_followed_by_authority_beats_followed_by_nobodies(self):
        """The core claim: one great follower > many worthless followers."""
        g = FollowGraph()
        # `hub` is an established authority.
        for i in range(30):
            g.add_follow(f"fan{i}", "hub")
        # `quality` is followed only by hub. `noise` is followed by 25 accounts
        # that nobody follows at all.
        g.add_follow("hub", "quality")
        for i in range(25):
            g.add_follow(f"rando{i}", "noise")

        r = pagerank(g)
        self.assertGreater(
            r.scores[g.index["quality"]],
            r.scores[g.index["noise"]],
            "an endorsement from a high-authority account should outweigh "
            "many endorsements from zero-authority accounts",
        )

    def test_vote_is_split_across_everyone_you_follow(self):
        """Being followed by a selective account is worth more."""
        g = FollowGraph()
        for i in range(20):
            g.add_follow(f"fan{i}", "selective")
            g.add_follow(f"fan{i}", "promiscuous")
        g.add_follow("selective", "alpha")
        g.add_follow("promiscuous", "beta")
        for i in range(50):
            g.add_follow("promiscuous", f"other{i}")

        r = pagerank(g)
        self.assertGreater(r.scores[g.index["alpha"]], r.scores[g.index["beta"]])

    def test_reachability_direction(self):
        """Edges point follower -> followee, so authority flows downstream."""
        g = FollowGraph()
        g.add_follow("follower", "star")
        r = pagerank(g)
        self.assertGreater(r.scores[g.index["star"]], r.scores[g.index["follower"]])


class TestPersonalization(unittest.TestCase):
    def test_seed_teleport_sums_to_one(self):
        g = chain(10)
        p = seed_teleport(g, ["n0", "n5"], background=0.05)
        self.assertAlmostEqual(sum(p), 1.0, places=12)

    def test_seed_vector_concentrates_on_seeds(self):
        g = chain(10)
        p = seed_teleport(g, ["n0"], background=0.02)
        self.assertGreater(p[0], 0.9)

    def test_missing_seeds_fall_back_to_uniform(self):
        g = chain(5)
        p = seed_teleport(g, ["nonexistent"])
        for x in p:
            self.assertAlmostEqual(x, 0.2, places=12)

    def test_seed_weights_are_respected(self):
        g = chain(6)
        p = seed_teleport(g, ["n0", "n1"], weights={"n0": 3.0, "n1": 1.0}, background=0.0)
        self.assertAlmostEqual(p[0] / p[1], 3.0, places=9)

    def test_personalization_changes_the_answer(self):
        """Two disconnected communities: seeding one must not rank the other."""
        g = FollowGraph()
        for i in range(10):
            for j in range(10):
                if i != j:
                    g.add_follow(f"tech{i}", f"tech{j}")
                    g.add_follow(f"sport{i}", f"sport{j}")
        # Make the sports community structurally far more popular.
        for i in range(300):
            g.add_follow(f"public{i}", "sport0")

        seeded = pagerank(g, teleport=seed_teleport(g, ["tech0"], background=0.0))
        generic = pagerank(g, teleport=uniform_teleport(len(g)))

        self.assertGreater(generic.scores[g.index["sport0"]], generic.scores[g.index["tech1"]],
                           "uniform PageRank should favour the globally popular account")
        self.assertGreater(seeded.scores[g.index["tech1"]], seeded.scores[g.index["sport0"]],
                           "seeded PageRank should favour the topical community")


class TestSpamResistance(unittest.TestCase):
    def test_trust_discounts_follow_spammers(self):
        g = FollowGraph()
        g.add_account(Account(id="spammer", following=100_000))
        g.add_account(Account(id="curator", following=120))
        g.add_follow("spammer", "target_a")
        g.add_follow("curator", "target_b")
        trust = g.trust_vector(follow_soft_cap=2000, spam_exponent=0.5)
        self.assertLess(trust[g.index["spammer"]], 0.2)
        self.assertEqual(trust[g.index["curator"]], 1.0)

    def test_trust_disabled_by_zero_exponent(self):
        g = FollowGraph()
        g.add_account(Account(id="spammer", following=100_000))
        trust = g.trust_vector(spam_exponent=0.0)
        self.assertEqual(set(trust), {1.0})

    def test_mass_conserved_with_trust_applied(self):
        g = FollowGraph()
        g.add_account(Account(id="spammer", following=500_000))
        for i in range(50):
            g.add_follow("spammer", f"t{i}")
        r = pagerank(g, trust=g.trust_vector())
        self.assertAlmostEqual(sum(r.scores), 1.0, places=10)


class TestGraphHygiene(unittest.TestCase):
    def test_self_follow_dropped(self):
        g = FollowGraph()
        self.assertFalse(g.add_follow("a", "a"))
        self.assertEqual(g.n_edges, 0)

    def test_duplicate_edge_dropped(self):
        g = FollowGraph()
        self.assertTrue(g.add_follow("a", "b"))
        self.assertFalse(g.add_follow("a", "b"))
        self.assertEqual(g.n_edges, 1)

    def test_resolve_by_handle_case_insensitively(self):
        g = FollowGraph()
        g.add_account(Account(id="u1", handle="ThreeBlueOneBrown"))
        self.assertEqual(g.resolve("@threeblueonebrown"), g.index["u1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
