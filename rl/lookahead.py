"""One-step lookahead rule with ONE parameter: pick the ambulance that minimises
   severity_weight * (its ETA)  +  beta * (expected response time to the NEXT call if it leaves).
beta = 0 is "nearest".  Tuned on training streams, judged on unseen ones.   python -m rl.lookahead normal
"""

import math
import sys

import rl.env as E
from app.policy import features
from app.policy.lookahead import LookaheadPolicy
from rl.search import REGIMES, kwargs

TRAIN, VALID = range(50_000, 50_030), range(70_000, 70_100)
BETAS = (0.0, 0.25, 0.5, 1.0, 1.5, 2.5, 4.0)


class _Incident:
    """The only thing the policy reads from an incident."""

    def __init__(self, severity):
        self.severity = severity


def make_rule(beta):
    """The real LookaheadPolicy, driven from the simulator's state."""
    policy = LookaheadPolicy(features.DEMAND_MAP, beta=beta, speed_kmph=features.SPEED_KMPH, pool_size=E.N_ACTIONS)

    def rank_fn(inc, ranked, m, d, where, world, info=None):
        candidates = [(a, d[a]) for a in ranked]
        positions = {a: world.locs[where[a]] for a in ranked}
        return ranked.index(policy.order(_Incident(inc[3]), candidates, positions)[0][0])

    return rank_fn


def scores(rule, seeds, reg):
    k, w, fleet = kwargs(reg)
    return [E.run_episode(sd, rank_fn=rule, world=w, fleet=fleet, **k)["weighted"] for sd in seeds]


def main(name):
    reg = REGIMES[name]
    g_tr, g_va = scores(None, TRAIN, reg), scores(None, VALID, reg)
    table, best = [], (0.0, 0.0)
    for b in BETAS[1:]:
        tr = scores(make_rule(b), TRAIN, reg)
        gain = (sum(g_tr) - sum(tr)) / len(tr)
        table.append((b, gain))
        if gain > best[1]:
            best = (b, gain)
    print("[%s] gain on training streams by beta: %s" % (name, "  ".join("%.2f:%+.3f" % t for t in table)), flush=True)
    b = best[0]
    if b == 0.0:
        print("[%s] no beta beats nearest on training streams -> keep nearest" % name, flush=True)
        return
    va = scores(make_rule(b), VALID, reg)                     # unseen, paired
    dlt = [x - y for x, y in zip(va, g_va)]
    mu = sum(dlt) / len(dlt)
    se = math.sqrt(sum((x - mu) ** 2 for x in dlt) / (len(dlt) - 1) / len(dlt))
    print("[%s] best beta %.2f | UNSEEN: nearest %.3f  lookahead %.3f  diff %+.3f min  (95%% CI %+.3f..%+.3f)  -> %s" % (
        name, b, sum(g_va) / len(g_va), sum(va) / len(va), mu, mu - 1.96 * se, mu + 1.96 * se,
        "BETTER" if mu + 1.96 * se < 0 else "worse" if mu - 1.96 * se > 0 else "no clear difference"), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
