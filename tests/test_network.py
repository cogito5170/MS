import unittest

from ms import APPLIED, ContextPolicy, StateManager, StateQuery, run_query
from ms.context import DROP, KEEP, RETRIEVE, SUMMARIZE
from ms.graph import StateGraph
from ms.network import (Interaction, NetConfig, Observation, PeerMessage, VerifyFlags, activate, contradicting_peers,
                        ingest_peer_message, pi)

CFG = NetConfig(window=10, half_life=100.0)
REQ, EXP = ["a", "b"], ["a", "b"]


def good(t):
    return Interaction(t, transition=True, uncertain_before=4, uncertain_after=1)


def idle(t):
    return Interaction(t, uncertain_before=4, uncertain_after=4)


def P(inter, obs=(), req=REQ, exp=EXP, now=10.0, cfg=CFG):
    return pi(inter, obs, req, exp, now, cfg)


class PiTests(unittest.TestCase):
    def test_prior_defined_at_t0(self):                  # 사전: u=1/2, t=1/2, g=0 -> (1/3)(1/2 + 0 + 1/2)
        self.assertAlmostEqual(P([]), 1 / 3)

    def test_useful_raises(self):
        a = P([good(1)])
        b = P([good(1), good(2), good(3)])
        self.assertGreater(a, P([]))
        self.assertGreater(b, a)

    def test_irrelevant_lowers(self):
        base = P([good(1)])
        self.assertLess(P([good(1)], exp=["x"]), base)
        self.assertEqual(P([good(1)], exp=["x"]), 0.0)   # r 은 곱이다
        self.assertAlmostEqual(P([good(1)], exp=["a"]), base / 2)

    def test_invalidated_or_no_gain_lowers(self):
        base = P([good(1), good(2)])
        self.assertLess(P([good(1), Interaction(2, transition=True, invalidated=True, uncertain_before=4,
                                                uncertain_after=1)]), base)
        self.assertLess(P([idle(1), idle(2)]), base)
        self.assertLess(P([idle(1), idle(2)]), P([]))   # 이득 없는 상호작용을 거듭하면 사전 밑으로

    def test_trust(self):
        bad = [Observation(1, invalid=True)] * 3
        self.assertLess(P([], bad), P([]))
        self.assertGreater(P([], [Observation(1)] * 3), P([]))

    def test_decay(self):
        old, new = [good(0)] * 3, [good(99)] * 3
        self.assertGreater(P(new, now=100.0), P(old, now=100.0))
        self.assertAlmostEqual(P(old, now=1e9, cfg=CFG), (1 / 3) * (0.5 + 0 + 0.5), places=6)

    def test_deterministic(self):
        i = [good(1), idle(2), good(3)]
        o = [Observation(1), Observation(2, True)]
        self.assertEqual(P(i, o), P(list(i), list(o)))

    def test_weights_default_third(self):
        self.assertEqual(NetConfig().weights, (1 / 3, 1 / 3, 1 / 3))
        with self.assertRaises(ValueError):
            NetConfig(weights=(1, 1, 1))


class ActivationTests(unittest.TestCase):
    def test_threshold(self):
        self.assertEqual(activate("i", "j", 0.5, CFG), (True, "pi"))
        self.assertEqual(activate("i", "j", 0.1, CFG), (False, "none"))

    def test_required_only_j(self):
        self.assertEqual(activate("i", "j", 0.0, CFG, missing=["x"], covers={"j": ["x"]}), (True, "required"))
        self.assertEqual(activate("i", "j", 0.0, CFG, missing=["x"], covers={"j": ["x"], "k": ["x"]}), (False, "none"))

    def test_contradiction_opens_one_verify_exchange(self):
        g = StateGraph()
        for n in ("a1", "b1"):
            g.add_node(n, "M")
        g.add_edge("contradicts", "a1", "b1")
        peers = contradicting_peers(g, {"a1": "i", "b1": "j"}, "i")
        self.assertEqual(peers, ["j"])
        f = VerifyFlags()
        self.assertEqual(activate("i", "j", 0.0, CFG, flags=f), (False, "none"))
        f.open("i", "j")
        self.assertEqual(activate("i", "j", 0.0, CFG, flags=f), (True, "verify"))
        f.sent("i", "j")
        self.assertEqual(activate("i", "j", 0.0, CFG, flags=f), (False, "none"))   # 교환은 한 번

    def test_verify_does_not_raise_pi(self):
        base = P([good(1)])
        with_verify = P([good(1), Interaction(2, transition=True, uncertain_before=4, uncertain_after=0, verify=True)])
        self.assertEqual(base, with_verify)
        f = VerifyFlags()
        f.open("i", "j")
        f.resolve("i", "j")
        self.assertIsNone(f.state("i", "j"))


class PeerContextTests(unittest.TestCase):
    def _m(self, n, refs_n=3):
        m = StateManager(clock=lambda: 1000.0)
        for k in range(n):
            res = ingest_peer_message(m, PeerMessage("j", "i", f"m{k:02d}", schema="obs/1",
                                                     refs=tuple(f"r{x}" for x in range(refs_n)), bytes=100 + k))
            self.assertTrue(all(r.status == APPLIED for r in res))
        return m

    def test_rows_in_graph(self):
        m = self._m(1)
        r = run_query(StateQuery("peer", model="PeerMessage"), m).rows[0]
        self.assertEqual(r.props["sender"]["value"], "j")
        self.assertEqual(r.props["refs"]["value"], "r0,r1,r2")

    def test_validation_applies(self):
        m = StateManager(clock=lambda: 1.0)
        res = ingest_peer_message(m, PeerMessage("j", "i", "m", bytes=-5))
        self.assertIn("rejected", [r.status for r in res])

    def test_over_budget_summarized_not_copied(self):
        m = self._m(30)
        res = [run_query(StateQuery("peer", model="PeerMessage"), m)]
        ctx = ContextPolicy(budget_chars=900).build("t", res)
        self.assertLess(len(ctx.kept), 30)
        self.assertTrue(ctx.summaries)
        self.assertLessEqual(ctx.stats()["chars"], 900)
        self.assertEqual(ctx.stats()["keep"] + ctx.stats()["summarize"] + ctx.stats()["retrieve_only"], 30)
        self.assertIn(SUMMARIZE, ctx.decisions.values())

    def test_over_budget_dropped(self):
        m = self._m(30)
        q = StateQuery("peer", model="PeerMessage", droppable=[["bytes", ">=", 0]])
        ctx = ContextPolicy(budget_chars=900, drop=True).build("t", [run_query(q, m)])
        self.assertEqual(ctx.dropped, {"peer": 30})
        self.assertEqual(set(ctx.decisions.values()), {DROP})
        self.assertEqual(ctx.kept, [])


if __name__ == "__main__":
    unittest.main()
