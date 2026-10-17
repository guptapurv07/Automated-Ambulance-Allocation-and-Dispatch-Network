# RL Decision Engine (Stage 8): what was built and what we found

Owner: Kartikey. Last updated 2026-10-14.
Status: merged into `main` (PR #2 and PR #3). Retrained on 2026-10-14 for the enlarged seed data (30 ambulances, 14 hospitals); what changed is in section 7.

## 1. Short answer: where is the RL?

- **The RL model is `app/policy/learned.py`** (`DISPATCH_POLICY=learned`). Its weights, `app/policy/learned_weights.json`, are trained by **reinforcement learning (policy gradient, REINFORCE)** in our simulator by `rl/pg.py`. The simulator drives the same class the allocator runs, so what is trained is exactly what is deployed.
- **Tabular Q-learning was built and tested first** (`rl/train.py`). It did not beat "send the nearest", so it is reported as a negative result (section 5).
- **The lookahead rule** (`app/policy/lookahead.py`) is the hand-derived formula the agent starts from and is compared with. The trained agent ends up close to it (section 6), which is evidence that the rule is right, not a replacement for the training.
- Default stays `DISPATCH_POLICY=greedy`, so merging changes nothing until someone switches it on.

## 2. How to run everything

From the repository root, with the project's requirements installed (plus `httpx` for the benchmarks):

```
python -m rl.env                       # simulator self-check (fails if sim/seed.py has another fleet than the one trained on)
python -m rl.pg check                  # learned policy at its starting weights == lookahead rule, decision for decision
python -m rl.pg train                  # ~13 minutes; writes app/policy/learned_weights.json
python -m rl.pg eval                   # nearest vs lookahead vs learned on 100 unseen streams per regime
python -m rl.lookahead normal          # beta sweep for the rule (also: sparse, heavy, surge)
python -m rl.oracle normal             # perfect-foresight upper bound
python -m rl.train                     # tabular Q-learning (the negative result)
python -m rl.search normal             # table policy search (overfits)

DISPATCH_POLICY=learned python -m bench.loadtest     # locking gate, needs MySQL
python -m bench.policy_ab --seeds 20                 # real-system A/B, needs MySQL, ~42 minutes; keep the laptop awake
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

**Simulator (`rl/env.py`).** Pure Python, no database, follows the real rules: hospital nearest to the incident, trip = distance / speed, a job takes about 10.5 min + ETA + trip, ambulances stay at the hospital, waiting calls served by Least Laxity First (targets 8 / 15 / 30 / 60 min). The simulated city is the box around the seeded stations and hospitals (`sim/seed.py`). Calls come from four hotspots (Clock Tower, ISBT, Rajpur Road, Prem Nagar) plus 25% anywhere in the box. A random layout places its stations and hospitals the same way, where calls happen. Regimes were fixed before testing: normal (the real 30-ambulance layout, load 0.3 to 0.8), sparse (6 ambulances, random layout), heavy (load 0.7 to 0.9), surge (2.5x burst mid-stream).

## 5. What we tried, in order

| # | Method | Outcome |
|---|---|---|
| 1 | Perfect-foresight oracle (`rl/oracle.py`) | 5 to 16% better than nearest (0.7 min normal, 2.2 min heavy). There is headroom, but only if the future is known. |
| 2 | Tabular Q-learning, 324 states x 5 actions (`rl/train.py`) | **Failed.** Fixed learning rate was noise (worse than random); decaying rate and gamma <= 0.5 still lost to nearest (about 10.2 vs 9.2 to 9.5 min). |
| 3 | Table policy search with common random numbers (`rl/search.py`) | **Overfit**: gains on training streams vanished on unseen ones. |
| 4 | One-parameter lookahead rule (`rl/lookahead.py`) | Works; beta about 1 to 1.5. |
| 5 | Policy-gradient agent, plain REINFORCE | **Did not learn**: one episode's cost is too noisy a signal for ~100 small decisions. |
| 6 | Policy-gradient agent, self-critical baseline (`rl/pg.py`) | **Works**: beats nearest, ties the rule. This is the deployed RL model. |

All six rows were measured on the first fleet of 12 ambulances and 4 hospitals; rows 1 to 3 also on the earlier 5 x 5 grid over the city box, before the demand grid moved to the service-area setting. Why the table methods failed: with 12 ambulances and moderate load most decisions change little, so the reward signal per state is tiny next to the noise, and 1,620 table values cannot be learnt from it. A handful of weights on meaningful features can.

## 6. Results

### 6.1 On the 30-ambulance fleet (2026-10-14)

**Simulator, 100 unseen streams per regime, severity-weighted mean response (minutes), paired:**

| Regime | nearest | lookahead | learned | learned vs nearest (95% CI) | learned vs lookahead (95% CI) |
|---|---|---|---|---|---|
| Normal | 8.22 | 8.04 | 8.06 | **-0.17** (-0.27 to -0.07), -2.0% | +0.01 (-0.03 to +0.06) |
| Heavy | 12.57 | 12.13 | 12.18 | **-0.39** (-0.61 to -0.18), -3.1% | +0.06 (-0.13 to +0.25) |
| Surge | 12.77 | 12.41 | 12.43 | **-0.34** (-0.48 to -0.20), -2.7% | +0.02 (-0.06 to +0.10) |
| Mixed fleets 8-40 | 12.57 | 12.50 | 12.49 | -0.08 (-0.18 to +0.03) | -0.01 (-0.06 to +0.05) |
| Sparse | 30.02 | 29.95 | 29.92 | -0.10 (-0.39 to +0.20) | -0.02 (-0.09 to +0.04) |

On the real layout the learned policy is clearly faster than nearest under normal load, heavy load and a surge. It still ties the lookahead rule. On random layouts (mixed, sparse) there is little room: nearest is already within 1% of both.

**Retraining changed nothing measurable.** The weights trained on the first fleet, run unchanged on the same streams, gave 8.05 (normal), 12.14 (heavy), 12.43 (surge), 12.50 (mixed) and 29.93 (sparse). The features are relative, so the policy trained on 12 ambulances already worked on 30. The deployed weights are the retrained ones, so they match the fleet in use.

**Learned weights** (scaled units): own ETA x severity +10.19, next-call cost +2.31, rank -0.26, coverage lost -0.18, next x severity -0.15, extra ETA +0.07 (best development score at iteration 250 of 500). In plain units the agent keeps the next-call weight at about 1.14 per minute of its own travel (the hand-picked beta is 1.25). The four small weights are near zero and changed sign between the two trainings, so they carry little signal; in practice the agent is the lookahead rule with a slightly lower beta.

**Real system (MySQL, `bench/policy_ab.py --seeds 20`):** 20 paired streams, 150 calls over 200 simulated minutes (5 per ambulance), 30 ambulances, LLF queue, 600 past incidents seeded; every call was served in every run. The laptop did not sleep during the run (checked with `pmset -g log`; an earlier run that a sleep interrupted was thrown away).

| Comparison | Weighted response | 95% CI | Streams better |
|---|---|---|---|
| learned minus greedy | **-0.54 min** (11.98 vs 12.53, -4.3%) | -0.98 to -0.10 | 14 of 20 |
| lookahead minus greedy | -0.53 min (11.99 vs 12.53, -4.2%) | -1.12 to +0.05 | 14 of 20 |
| learned minus lookahead | -0.01 min | -0.54 to +0.52 | 7 of 20 |

On the 30-ambulance fleet the learned policy is significantly faster than greedy on the real system, which it was not on the first fleet (-0.30, CI -0.78 to +0.18). Most of the gain is in the drive to the patient (11.79 vs 12.05 min); mean wait also fell slightly (0.44 vs 0.47 min). Lookahead has the same mean gain but varies more from stream to stream, so its interval still touches zero. These streams are busier than the simulator's normal regime, and the gain grows with load there too (2.0% normal, 3.1% heavy). Split after the fact by how busy a stream was, the learned policy saved 0.70 min on the 10 busier streams and 0.38 on the quieter ones (a hint, not a pre-planned test).

**Locking gate:** `bench.loadtest` with `DISPATCH_POLICY=learned`, 60 calls on 30 ambulances: `for_update` 30 dispatched / 30 vehicles / 0 double-booked / 0 deadlocks; `skip_locked` 27 / 27 / 0; `none` double-books (13 in this run), as it should.

### 6.2 On the first fleet of 12 ambulances and 4 hospitals (2026-10-11)

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

## 7. Update for the 30-ambulance fleet (2026-10-14)

The mentor asked for bigger seed data, so `sim/seed.py` now has 30 ambulances, 14 hospitals and 1,000 past calls. The simulator reads its city from that file, so its city changed too. What was changed in the RL code, and why:

1. **Self-check (`rl/env.py`).** It asserted exactly 12 ambulances and 4 hospitals. `rl.pg train` now writes the fleet it trained on into `learned_weights.json` (`"layout"`), and the self-check compares that with `sim/seed.py`. The next change of the seed data asks for retraining instead of failing on a fixed number.
2. **Random layouts (`random_world` in `rl/env.py`).** They placed stations and hospitals evenly over the city box. The outer stations (Mussoorie, Vikasnagar, Ranipokhari) grew that box from about 25 x 27 km to 40 x 41 km, so most random stations and hospitals landed in forest and hills far from any call. A job then took 51 to 55 minutes instead of about 30, the fleet could not keep up, and the sparse and mixed regimes measured the queue, not the dispatch decision (178 and 52 minutes under nearest). Random layouts now place their stations and hospitals where calls happen, drawn from the same generator as the calls. The real layout is untouched: its call streams, its demand map and its nearest-policy results are identical to before (checked on 1,000 streams).
3. **Load constant (`MEAN_JOB_MINUTES`) kept at 30.** On the 30-ambulance layout a job takes about 24 minutes at normal load and 28 at heavy load, so normal load keeps the real fleet about 46% busy and heavy load about 75%. Lowering the constant to the measured 24 would push heavy load past what the fleet can serve.
4. **Real-system A/B (`bench/policy_ab.py`).** It sent 60 calls per stream: a normal load for 12 ambulances, a light one for 30. The default is now 5 calls per ambulance (60 for 12, 150 for 30), so the fleet is about as busy as before.
5. **Retrained** with `python -m rl.pg train`, settings unchanged (500 iterations of 32 streams, seed 7).

## 8. Limitations (state these in the report)

- **The gain is small**: on the 30-ambulance fleet 2 to 3% in simulation (normal, heavy, surge) and 4.3% on the real system (significant over 20 streams); on the first fleet 1 to 2% and 2.6% (not significant). The oracle allowed 5 to 16% on the first fleet; the rest needs knowledge of future calls.
- **Travel time is straight-line distance at a fixed 32 km/h** (`AVG_SPEED_KMPH`). If that changes, retrain with `python -m rl.pg train`.
- **The learned policy ties the lookahead rule** on both fleets; it does not beat it. RL confirms the rule rather than finding a better one.
- **The learned policy is linear** (six weights), not a deep network. That is deliberate: with this little signal per decision, a big model would overfit, as the table methods did.
- **Demand model is assumed** (hotspots, severity mix), not fitted to real EMS data. The simulator has four hotspots; the 1,000 past calls loaded by `sim/seed.py` come from six, and the server's demand map learns from those. The policy reads its inputs from whatever the map holds, so this changes what it sees, not its rule.
- **No repositioning**: ambulances never return to base, so the fleet drifts to the hospitals. Sending them back to their own station did not help in a simulator test (most stations of the bigger fleet are far from where calls happen). Sending a free ambulance to the station that fills the biggest gap in coverage did: with the learned policy, 6.8% faster than nearest under heavy load and 5.7% under a surge, but nothing extra under normal load. Planned for the final phase.
- **Four workers decide at once**, so a policy's view may be slightly stale; the lock keeps allocation correct.
