"""Upper bound: a dispatcher that can replay the future. For every decision it tries each rank, finishes the
episode with "nearest" afterwards, and keeps the rank that gave the best result.  Nothing a learned policy can
see could beat this by much more than it shows, so it measures the room above "send the nearest".

    python -m rl.oracle normal      # also: sparse, heavy, surge
"""

import math
import sys

import rl.env as E
from rl.search import REGIMES, kwargs

SEEDS = range(70_000, 70_020)


def episode(seed, reg, prefix):
    """Follow `prefix` for the first decisions, 'nearest' afterwards. Returns (score, valid ranks per decision)."""
    k, w, fleet = kwargs(reg)
    ms, n = [], [0]

    def choose(s, m):
        i = n[0]; n[0] += 1
        ms.append(m)
        return min(prefix[i], m - 1) if i < len(prefix) else 0

    return E.run_episode(seed, choose=choose, world=w, fleet=fleet, **k)["weighted"], ms


def oracle(seed, reg):
    prefix = []
    best, ms = episode(seed, reg, prefix)
    base, i = best, 0
    while i < len(ms):
        pick = 0
        for a in range(1, min(E.N_ACTIONS, ms[i])):
            sc, ms2 = episode(seed, reg, prefix + [a])
            if sc < best - 1e-9:
                best, pick, ms = sc, a, ms2
        prefix.append(pick)
        i += 1
    return base, best, sum(1 for a in prefix if a)


def main(name):
    reg = REGIMES[name]
    rows = [oracle(sd, reg) for sd in SEEDS]
    d = [b - a for a, b, _ in rows]
    mu = sum(d) / len(d)
    se = math.sqrt(sum((x - mu) ** 2 for x in d) / (len(d) - 1) / len(d))
    g = sum(a for a, _, _ in rows) / len(rows)
    print("[%s] nearest %.3f min | with perfect foresight %.3f min | best possible gain %.3f min = %.1f%%  (95%% CI %.3f..%.3f) | "
          "deviates from nearest on %.0f of ~%d decisions" % (
              name, g, g + mu, -mu, -100 * mu / g, -(mu + 1.96 * se), -(mu - 1.96 * se),
              sum(c for _, _, c in rows) / len(rows), 170), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
