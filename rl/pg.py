"""Policy-gradient (REINFORCE) training of the learned dispatch policy. Standard library only.

State  : the whole situation, as one feature vector per candidate ambulance (app/policy/learned.py). All
         features are relative, so one set of weights serves any fleet size.
Action : which of the (up to 5) nearest free ambulances to send.
Policy : softmax over  -KAPPA * (weights . features)  while training; the lowest score when deployed.
Reward : episode cost = severity-weighted mean response time. Baseline = the policy's own best-score play on
         the SAME incident stream ("self-critical"), so the gradient only sees what exploring changed.
Start  : the weights of the lookahead rule, so training begins at a policy that is already known to be sound.

The simulator drives the real LearnedPolicy class, so what is trained here is exactly what the allocator runs.

    python -m rl.pg check                  # LearnedPolicy at its starting weights == LookaheadPolicy
    python -m rl.pg train [iterations]     # trains, writes app/policy/learned_weights.json
    python -m rl.pg eval                   # paired comparison on unseen streams
"""

import json
import math
import random
import sys
import time

import rl.env as E
from app.policy import features
from app.policy.learned import FEATURES, SCALE, WEIGHTS_FILE, LearnedPolicy
from app.policy.lookahead import LookaheadPolicy
from rl.search import REGIMES, kwargs

KAPPA = 2.0                                                  # 1 point of score = e**2 odds while training
BETA0 = 1.25                                                 # the lookahead rule's weight

# Training streams mix every regime plus random fleets of 8 to 40 ambulances (never the dev or test seeds).
TRAIN_REGIMES = (REGIMES["normal"], REGIMES["sparse"], REGIMES["heavy"], REGIMES["surge"],
                 dict(world="random", load=(0.3, 0.8)), dict(world="random", load=(0.5, 0.9)))
TRAIN_SEEDS, DEV_SEEDS, TEST_SEEDS = 90_000, range(80_000, 80_060), range(70_000, 70_100)
TEST_REGIMES = dict(REGIMES, mixed=dict(world="random", load=(0.3, 0.8)))
DEV_REGIMES = ("normal", "heavy", "mixed")


class _Incident:
    """The only thing a policy reads from an incident."""

    def __init__(self, severity):
        self.severity = severity


def initial_weights():
    w = [0.0] * len(FEATURES)
    w[0], w[1] = SCALE[0], BETA0 * SCALE[1]                  # score = sev*ETA + beta*next: the lookahead rule
    return w


def situation(inc, ranked, d, where, world):
    """What the allocator would hand the policy: candidates nearest first, and their positions."""
    return _Incident(inc[3]), [(a, d[a]) for a in ranked], {a: world.locs[where[a]] for a in ranked}


class Agent:
    """rank_fn for the simulator. train=True samples and records the gradient; train=False is the deployed policy."""

    def __init__(self, weights, train=False, rng=None):
        self.policy = LearnedPolicy(features.DEMAND_MAP, weights=weights, speed_kmph=E.SPEED_KMPH,
                                    pool_size=E.N_ACTIONS)
        self.train, self.rng = train, rng
        self.grad = [0.0] * len(weights)

    def __call__(self, inc, ranked, m, d, where, world, info=None):
        incident, candidates, positions = situation(inc, ranked, d, where, world)
        if not self.train:
            return ranked.index(self.policy.order(incident, candidates, positions)[0][0])
        xs = self.policy.features(incident, candidates, positions)
        score = self.policy.scores(xs)
        lo = min(score)
        p = [math.exp(-KAPPA * (s - lo)) for s in score]
        z = sum(p)
        p = [v / z for v in p]
        k = self.rng.choices(range(m), p)[0]
        for j in range(len(self.grad)):                      # d log pi(k) / d weight_j
            self.grad[j] += -KAPPA * (xs[k][j] - sum(p[i] * xs[i][j] for i in range(m)))
        return k


def run(seed, reg, agent):
    k, w, fleet = kwargs(reg)
    return E.run_episode(seed, rank_fn=agent, world=w, fleet=fleet, **k)["weighted"]


def evaluate(weights, seeds, reg):
    return [run(sd, reg, Agent(weights)) for sd in seeds]


def dev_costs(weights):
    return [c for name in DEV_REGIMES for c in evaluate(weights, DEV_SEEDS, TEST_REGIMES[name])]


def paired(a, b):
    """mean(a - b) with a 95% CI; negative = a is faster."""
    dl = [x - y for x, y in zip(a, b)]
    mu = sum(dl) / len(dl)
    se = math.sqrt(sum((x - mu) ** 2 for x in dl) / (len(dl) - 1) / len(dl))
    return mu, mu - 1.96 * se, mu + 1.96 * se


def save(weights, it, dev_gap):
    WEIGHTS_FILE.write_text(json.dumps({
        "features": FEATURES, "weights": [round(v, 4) for v in weights],
        "trained_by": "python -m rl.pg train", "iteration": it,
        "dev_change_vs_lookahead_min": round(dev_gap, 4),
    }, indent=2) + "\n")


def train(iterations=500, batch=32, lr=0.02, seed=7):
    rng = random.Random(seed)
    weights = initial_weights()
    m1, m2 = [0.0] * len(weights), [0.0] * len(weights)      # Adam
    best = math.inf
    dev_ref = dev_costs(weights)                             # fixed development set (never the test seeds)
    t0 = time.time()
    for it in range(1, iterations + 1):
        grad, advs = [0.0] * len(weights), []
        for b in range(batch):
            sd = TRAIN_SEEDS + rng.randrange(1_000_000)
            reg = TRAIN_REGIMES[b % len(TRAIN_REGIMES)]
            ref = run(sd, reg, Agent(weights))               # what the policy does when it does not explore
            ag = Agent(weights, train=True, rng=rng)
            adv = run(sd, reg, ag) - ref                     # >0: exploring made this stream worse
            for j in range(len(weights)):
                grad[j] += adv * ag.grad[j] / batch
            advs.append(adv)
        for j in range(len(weights)):                        # Adam step (minimise expected cost)
            m1[j] = 0.9 * m1[j] + 0.1 * grad[j]
            m2[j] = 0.999 * m2[j] + 0.001 * grad[j] ** 2
            weights[j] -= lr * (m1[j] / (1 - 0.9 ** it)) / (math.sqrt(m2[j] / (1 - 0.999 ** it)) + 1e-8)
        if it % 25 == 0 or it == iterations:
            dev = dev_costs(weights)
            gap = sum(x - y for x, y in zip(dev, dev_ref)) / len(dev)
            print("it %4d | exploring costs %+.3f | dev change vs start %+.3f | %.0fs" % (
                it, sum(advs) / len(advs), gap, time.time() - t0), flush=True)
            if gap < best:
                best = gap
                save(weights, it, gap)


def check(n=300):
    """At its starting weights LearnedPolicy must order exactly like LookaheadPolicy(beta=BETA0)."""
    look = LookaheadPolicy(features.DEMAND_MAP, beta=BETA0, speed_kmph=E.SPEED_KMPH, pool_size=E.N_ACTIONS)
    learned = LearnedPolicy(features.DEMAND_MAP, weights=initial_weights(), speed_kmph=E.SPEED_KMPH,
                            pool_size=E.N_ACTIONS)
    seen = [0, 0]

    def probe(inc, ranked, m, d, where, world, info=None):
        sit = situation(inc, ranked, d, where, world)
        a, b = look.order(*sit), learned.order(*sit)
        assert [x[0] for x in a] == [x[0] for x in b], (a, b)
        seen[0] += 1
        seen[1] += a[0][0] != ranked[0]
        return ranked.index(a[0][0])

    for sd in range(n):
        E.run_episode(sd, rank_fn=probe, n_incidents=60)
    print("check ok: %d decisions identical to LookaheadPolicy (%d of them not the nearest)" % tuple(seen))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if cmd == "check":
        return check()
    if cmd == "train":
        return train(int(sys.argv[2]) if len(sys.argv) > 2 else 500)
    from rl.lookahead import make_rule
    weights = json.loads(WEIGHTS_FILE.read_text())["weights"]
    print("learned weights (scaled units):")
    for n, v in zip(FEATURES, weights):
        print("   %-20s %+.3f" % (n, v))
    print("\nUNSEEN streams (%d per regime), mean severity-weighted response, paired:" % len(TEST_SEEDS))
    for name, reg in TEST_REGIMES.items():
        k, w, fleet = kwargs(reg)
        near = [E.run_episode(s, world=w, fleet=fleet, **k)["weighted"] for s in TEST_SEEDS]
        look = [E.run_episode(s, rank_fn=make_rule(BETA0), world=w, fleet=fleet, **k)["weighted"] for s in TEST_SEEDS]
        agent = evaluate(weights, TEST_SEEDS, reg)
        print("[%-6s] nearest %.2f  lookahead %.2f  learned %.2f | vs nearest %+.3f (%+.3f..%+.3f) | "
              "vs lookahead %+.3f (%+.3f..%+.3f)" % (
                  name, sum(near) / len(near), sum(look) / len(look), sum(agent) / len(agent),
                  *paired(agent, near), *paired(agent, look)), flush=True)


if __name__ == "__main__":
    main()
