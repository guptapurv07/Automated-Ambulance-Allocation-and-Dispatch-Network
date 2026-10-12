"""Train the tabular Q-learning dispatch policy in the simulator, then compare it with greedy.

    python -m rl.train                     # 50,000 episodes (a few minutes)
    python -m rl.train --episodes 2000     # quick run

Trains on random layouts; the headline comparison is on the real Dehradun layout the model never saw.
"""

import argparse
import json
import math
import random
import time
from pathlib import Path

from app.policy.features import N_ACTIONS, N_STATES
from rl.env import real_world, run_episode

ALPHA_MIN = 0.005   # step size is 1/visits^0.7 per (state, rank), never below this: noise averages out
EPS_START, EPS_END, EPS_DECAY_SHARE = 1.0, 0.05, 0.6
EVAL_SEEDS = 30
OUT = Path(__file__).resolve().parent / "q_table.json"


def train(episodes, seed, gamma):
    q = [[0.0] * N_ACTIONS for _ in range(N_STATES)]
    visits = [0] * N_STATES
    counts = [[0] * N_ACTIONS for _ in range(N_STATES)]
    rng = random.Random(seed)
    state = {"eps": EPS_START, "ret": 0.0}

    def choose(s, m):
        visits[s] += 1
        if rng.random() < state["eps"]:
            return rng.randrange(m)
        return max(range(m), key=q[s].__getitem__)

    def learn(s, a, r, s2, m2, done):
        state["ret"] += r
        target = r if done else r + gamma * max(q[s2][:m2])   # only ranks that exist in the next state
        counts[s][a] += 1
        q[s][a] += max(ALPHA_MIN, counts[s][a] ** -0.7) * (target - q[s][a])

    t0, block = time.time(), max(1, episodes // 10)
    for ep in range(episodes):
        state["eps"] = max(EPS_END, EPS_START - (EPS_START - EPS_END) * ep / (EPS_DECAY_SHARE * episodes))
        run_episode(10_000_000 + ep, choose=choose, learn=learn)   # training seeds never overlap eval seeds
        if (ep + 1) % block == 0:
            print("  episode %6d  eps %.2f  mean return %8.1f  states seen %3d/%d  %.0fs" % (
                ep + 1, state["eps"], state["ret"] / block, sum(v > 0 for v in visits), N_STATES, time.time() - t0))
            state["ret"] = 0.0
    return q, visits


def evaluate(q, visits, label, world=None, base=20_000):
    """Paired comparison on identical incident streams. Unseen states fall back to rank 0 (greedy)."""
    rng = random.Random(1)

    def rl(s, m):
        return max(range(m), key=q[s].__getitem__) if visits[s] else 0

    policies = {"greedy": None, "random": lambda s, m: rng.randrange(m), "RL": rl}
    res = {name: [run_episode(seed, choose=ch, world=world) for seed in range(base, base + EVAL_SEEDS)]
           for name, ch in policies.items()}
    print("\n%s  (%d paired seeds)" % (label, EVAL_SEEDS))
    print("  %-8s %10s %10s %8s %8s" % ("policy", "weighted", "mean", "p95", "wait"))
    for name, rs in res.items():
        print("  %-8s %10.2f %10.2f %8.1f %8.2f" % (name, *(sum(r[k] for r in rs) / EVAL_SEEDS
                                                         for k in ("weighted", "mean", "p95", "wait"))))
    d = [a["weighted"] - b["weighted"] for a, b in zip(res["RL"], res["greedy"])]
    mean = sum(d) / len(d)
    se = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1) / len(d))
    print("  RL minus greedy, severity-weighted response: %+.2f min  (95%% CI %+.2f to %+.2f)  %s" % (
        mean, mean - 1.96 * se, mean + 1.96 * se,
        "RL better" if mean + 1.96 * se < 0 else "RL worse" if mean - 1.96 * se > 0 else "no clear difference"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--episodes", type=int, default=50_000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--gamma", type=float, default=0.9)
    args = p.parse_args()
    print("training %d episodes on random layouts" % args.episodes)
    q, visits = train(args.episodes, args.seed, args.gamma)
    OUT.write_text(json.dumps({"q": q, "visits": visits, "episodes": args.episodes, "gamma": args.gamma}))
    print("saved", OUT.relative_to(OUT.parent.parent))
    evaluate(q, visits, "REAL Dehradun layout (12 ambulances, 4 hospitals) - never seen in training", real_world())
    evaluate(q, visits, "RANDOM layouts, 8-40 ambulances (new seeds)")


if __name__ == "__main__":
    main()
