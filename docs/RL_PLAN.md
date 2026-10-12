# RL Decision Engine (Stage 8): what was built and what we found

Owner: Kartikey. Last updated 2026-10-11.
Status: policy interface merged (PR #2). Lookahead rule, learned (RL) policy, simulator and A/B script on branch `lookahead-policy`, waiting for review.

## 1. Short answer: where is the RL?

- **The RL model is `app/policy/learned.py`** (`DISPATCH_POLICY=learned`). Its weights, `app/policy/learned_weights.json`, are trained by **reinforcement learning (policy gradient, REINFORCE)** in our simulator by `rl/pg.py`. The simulator drives the same class the allocator runs, so what is trained is exactly what is deployed.
- **Tabular Q-learning was built and tested first** (`rl/train.py`). It did not beat "send the nearest", so it is reported as a negative result (section 5).
- **The lookahead rule** (`app/policy/lookahead.py`) is the hand-derived formula the agent starts from and is compared with. The trained agent ends up close to it (section 6), which is evidence that the rule is right, not a replacement for the training.
- Default stays `DISPATCH_POLICY=greedy`, so merging changes nothing until someone switches it on.

## 2. How to run everything

From the repository root, with the project's requirements installed (plus `httpx` for the benchmarks):

```
python -m rl.env                       # simulator self-check
python -m rl.pg check                  # learned policy at its starting weights == lookahead rule, decision for decision
python -m rl.pg train                  # ~8 minutes; writes app/policy/learned_weights.json
python -m rl.pg eval                   # nearest vs lookahead vs learned on 100 unseen streams per regime
python -m rl.lookahead normal          # beta sweep for the rule (also: sparse, heavy, surge)
python -m rl.oracle normal             # perfect-foresight upper bound
python -m rl.train                     # tabular Q-learning (the negative result)
python -m rl.search normal             # table policy search (overfits)

DISPATCH_POLICY=learned python -m bench.loadtest     # locking gate, needs MySQL
python -m bench.policy_ab --seeds 20                 # real-system A/B, needs MySQL, ~40 minutes
```

## 3. Where things are

| Piece | Location |
|---|---|
| `Policy` interface, `GreedyPolicy` | `app/policy/base.py`, `app/core/allocation.py` (merged) |
| Demand map (share of incidents per grid cell, learnt from the `incidents` table) | `app/policy/demand.py` |
| Lookahead rule | `app/policy/lookahead.py` |
| **Learned (RL) policy + trained weights** | `app/policy/learned.py`, `app/policy/learned_weights.json` |
| Policy from settings (`greedy`, `lookahead`, `learned`) | `app/policy/factory.py`, `app/config.py` |
| Simulator | `rl/env.py` (+ `app/policy/features.py`, the Q-learning state encoder) |
| RL training (policy gradient) | `rl/pg.py` |
| Earlier experiments | `rl/train.py` (Q-learning), `rl/search.py`, `rl/oracle.py`, `rl/lookahead.py` |
| Real-system A/B | `bench/policy_ab.py` |

## 4. Design

The allocator ranks free ambulances nearest first (phase 1, no locks), asks the policy to order them, locks the first `CANDIDATE_POOL_SIZE = 5` of that order and claims the first one still free. **The ranking proposes; the lock disposes.** The policy never runs while a row lock is held, and the locking code is unchanged.

**Learned policy.** For each of the 5 nearest candidates it builds six features, all relative so one set of weights serves any fleet size:

| Feature | Meaning |
|---|---|
| own ETA x severity | minutes this ambulance needs, times the severity weight (CRITICAL 4, HIGH 2, MEDIUM 1, LOW 0.5) |
| next-call cost | extra expected response time to the next call if this ambulance leaves, averaged over the demand map |
| extra ETA | minutes slower than the nearest candidate |
| coverage lost | share of demand that no other free ambulance can reach within 8 minutes |
| next-call cost x severity | interaction |
| rank | 0 = nearest |

Score = weights . features; the lowest score is tried first.

**Training (`rl/pg.py`).** Episode = a stream of 200 calls in the simulator. Reward = minus the severity-weighted mean response time. While training the policy samples from a softmax over the scores; REINFORCE with a self-critical baseline (the policy's own best-score play on the same stream) updates the weights with Adam, 500 iterations x 32 episodes. Training streams mix all four regimes and random fleets of 8 to 40 ambulances; development and test seeds never overlap the training seeds. Training starts from the lookahead rule's weights.

**Demand map.** Grid over the service area from `config.py` (`SERVICE_AREA_*`). 9 x 9 cells, about 5 km each. Learnt from the last 2,000 incidents, refreshed at most every 30 s in phase 1, uniform until there are 50 incidents. A 5 x 5 grid over the full service area was tested and lost most of the gain under normal load (cells of about 9 km are too coarse); 9 x 9 keeps the cell size the policy was first tuned at.

**Simulator (`rl/env.py`).** Pure Python, no database, follows the real rules: hospital nearest to the incident, trip = distance / speed, a job takes about 10.5 min + ETA + trip, ambulances stay at the hospital, waiting calls served by Least Laxity First (targets 8 / 15 / 30 / 60 min). The simulated city is the box around the seeded stations and hospitals (`sim/seed.py`). Regimes were fixed before testing: normal (real 12-ambulance layout, load 0.3 to 0.8), sparse (6 ambulances, random layout), heavy (load 0.7 to 0.9), surge (2.5x burst mid-stream).

## 5. What we tried, in order

| # | Method | Outcome |
|---|---|---|
| 1 | Perfect-foresight oracle (`rl/oracle.py`) | 5 to 16% better than nearest (0.7 min normal, 2.2 min heavy). There is headroom, but only if the future is known. |
| 2 | Tabular Q-learning, 324 states x 5 actions (`rl/train.py`) | **Failed.** Fixed learning rate was noise (worse than random); decaying rate and gamma <= 0.5 still lost to nearest (about 10.2 vs 9.2 to 9.5 min). |
| 3 | Table policy search with common random numbers (`rl/search.py`) | **Overfit**: gains on training streams vanished on unseen ones. |
| 4 | One-parameter lookahead rule (`rl/lookahead.py`) | Works; beta about 1 to 1.5. |
| 5 | Policy-gradient agent, plain REINFORCE | **Did not learn**: one episode's cost is too noisy a signal for ~100 small decisions. |
| 6 | Policy-gradient agent, self-critical baseline (`rl/pg.py`) | **Works**: beats nearest, ties the rule. This is the deployed RL model. |

Rows 1 to 3 were measured on the earlier 5 x 5 grid over the city box, before the demand grid moved to the service-area setting. Why the table methods failed: with 12 ambulances and moderate load most decisions change little, so the reward signal per state is tiny next to the noise, and 1,620 table values cannot be learnt from it. A handful of weights on meaningful features can.

## 6. Results

**Simulator, 100 unseen streams per regime, severity-weighted mean response (minutes), paired:**

| Regime | nearest | lookahead | learned | learned vs nearest (95% CI) | learned vs lookahead (95% CI) |
|---|---|---|---|---|---|
| Normal | 9.82 | 9.72 | 9.71 | **-0.11** (-0.17 to -0.06) | -0.01 (-0.03 to +0.02) |
| Heavy | 15.52 | 15.44 | 15.37 | -0.15 (-0.38 to +0.08) | -0.08 (-0.19 to +0.04) |
| Mixed fleets 8-40 | 20.00 | 19.59 | 19.65 | **-0.35** (-0.53 to -0.18) | +0.06 (0.00 to +0.12) |
| Sparse | 54.04 | 54.04 | 53.90 | -0.13 (-0.48 to +0.22) | -0.13 (-0.37 to +0.10) |
| Surge | 16.99 | 16.91 | 16.92 | -0.07 (-0.19 to +0.05) | +0.01 (-0.09 to +0.12) |

**Learned weights** (scaled units): own ETA x severity +10.21, next-call cost +2.40, extra ETA +0.57, rank +0.52, next x severity +0.30, coverage lost +0.25. In plain units the agent keeps the next-call weight at about 1.2 (our hand-picked beta was 1.25), slightly higher for severe calls, and adds a small preference for nearer ambulances and for keeping coverage.

**Real system (MySQL, `bench/policy_ab.py --seeds 20`):** 20 paired streams, 60 calls over 200 simulated minutes, 12 ambulances, LLF queue, 600 past incidents seeded; every call was served in every run.

| Comparison | Weighted response | 95% CI | Streams better |
|---|---|---|---|
| learned minus greedy | **-0.30 min** (11.25 vs 11.55, -2.6%) | -0.78 to +0.18 | 13 of 20 |
| lookahead minus greedy | -0.48 min | -0.96 to 0.00 | 14 of 20 |
| learned minus lookahead | +0.18 min | -0.05 to +0.41 | 7 of 20 |

Both policies are faster than greedy on average, but on the real system neither difference is significant at 95% yet; more streams would tighten the interval. The learned policy also lowered mean wait (1.21 vs 1.39 min) and mean trip (10.23 vs 10.37 min). Split after the fact by how busy a stream was, it saved 0.76 min on the 10 busier streams and lost 0.16 on the quieter ones (a hint, not a pre-planned test). An earlier run with the old 5 x 5 city grid gave lookahead -0.96 min (significant); moving the grid to the service area roughly halved the real-system gain.

**Locking gate:** `bench.loadtest` with `DISPATCH_POLICY=learned`: `for_update` 12 dispatched / 12 vehicles / 0 double-booked / 0 deadlocks; `skip_locked` 0 double-booked; `none` double-books (5 in this run), as it should.

## 7. Limitations (state these in the report)

- **The gain is small**: about 1 to 2% in simulation and 2.6% (not yet significant) on the real system, next to the oracle's 5 to 16%. The rest needs knowledge of future calls.
- **Travel time is straight-line distance at a fixed 32 km/h** (`AVG_SPEED_KMPH`). If that changes, retrain with `python -m rl.pg train`.
- **The learned policy ties the lookahead rule**; it does not beat it. RL confirms the rule rather than finding a better one.
- **The learned policy is linear** (six weights), not a deep network. That is deliberate: with this little signal per decision, a big model would overfit, as the table methods did.
- **Demand model is assumed** (hotspots, severity mix), not fitted to real EMS data.
- **No repositioning**: ambulances never return to base, so the fleet drifts to the hospitals.
- **Four workers decide at once**, so a policy's view may be slightly stale; the lock keeps allocation correct.
