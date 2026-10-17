"""How much room is there above "send the nearest"?  Policy search with common random numbers.

Every candidate policy is scored on the SAME incident streams, so differences between policies are exact
instead of buried in noise. Coordinate ascent over the 324 states, then a paired test on unseen seeds.

    python -m rl.search normal      # also: sparse, heavy, surge
"""

import math
import random
import sys
import time

import rl.env as E
from app.policy.features import N_ACTIONS, N_STATES

# Fixed before any result was looked at.
REGIMES = {
    "normal": dict(world="real", load=(0.3, 0.8)),                                  # the Dehradun fleet in sim/seed.py
    "sparse": dict(world="random", fleet=6, load=(0.3, 0.8)),                       # only 6 ambulances, any layout
    "heavy":  dict(world="real", load=(0.7, 0.9)),                                  # busy fleet
    "surge":  dict(world="real", load=(0.3, 0.6), surge=(0.33, 0.66, 2.5)),         # a burst of calls mid-stream
}
TRAIN, VALID = range(50_000, 50_050), range(70_000, 70_100)
TOP_STATES, PASSES, MIN_GAIN = 80, 2, 0.01


def kwargs(reg):
    k = {x: y for x, y in reg.items() if x not in ("world", "fleet")}
    return k, (E.real_world() if reg["world"] == "real" else None), reg.get("fleet")


def score(act, seeds, reg, record=None):
    k, w, fleet = kwargs(reg)

    def choose(s, m):
        if record is not None:
            record[s] = record.get(s, 0) + 1
        return min(act[s], m - 1)

    return [E.run_episode(sd, choose=choose, world=w, fleet=fleet, **k)["weighted"] for sd in seeds]


def main(name):
    reg, t0 = REGIMES[name], time.time()
    act = [0] * N_STATES
    seen = {}
    base_train = score(act, TRAIN, reg, seen)
    cur = sum(base_train) / len(base_train)
    order = sorted(seen, key=seen.get, reverse=True)[:TOP_STATES]
    print("[%s] greedy on training streams: %.3f min; searching the %d most common states" % (name, cur, len(order)), flush=True)
    for p in range(PASSES):
        changed = 0
        for s in order:
            keep, best = act[s], cur
            for a in range(N_ACTIONS):
                if a == act[s]:
                    continue
                trial = list(act); trial[s] = a
                sc = sum(score(trial, TRAIN, reg)) / len(TRAIN)
                if sc < best - MIN_GAIN:
                    keep, best = a, sc
            if keep != act[s]:
                act[s], cur, changed = keep, best, changed + 1
        print("[%s] pass %d: %d states changed, training score %.3f  (%.0fs)" % (name, p + 1, changed, cur, time.time() - t0), flush=True)

    g, r = score([0] * N_STATES, VALID, reg), score(act, VALID, reg)       # unseen streams, paired
    d = [b - a for a, b in zip(g, r)]
    mu = sum(d) / len(d)
    se = math.sqrt(sum((x - mu) ** 2 for x in d) / (len(d) - 1) / len(d))
    verdict = "SEARCHED POLICY BETTER" if mu + 1.96 * se < 0 else "worse" if mu - 1.96 * se > 0 else "no clear difference"
    print("[%s] UNSEEN: greedy %.3f  searched %.3f  diff %+.3f min  (95%% CI %+.3f..%+.3f)  -> %s" % (
        name, sum(g) / len(g), sum(r) / len(r), mu, mu - 1.96 * se, mu + 1.96 * se, verdict), flush=True)
    names = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    print("[%s] states where it leaves 'nearest':  (severity, fleet free, coverage lost, gap, next-free) -> rank" % name)
    for s in order:
        if act[s]:
            s2, nxt = divmod(s, 3); s2, gap = divmod(s2, 3); s2, lost = divmod(s2, 3); sev, free = divmod(s2, 3)
            print("     %-8s free=%d lost=%d gap=%d next=%d  -> rank %d   (seen %d times)" % (names[sev], free, lost, gap, nxt, act[s], seen[s]))


if __name__ == "__main__":
    main(sys.argv[1])
