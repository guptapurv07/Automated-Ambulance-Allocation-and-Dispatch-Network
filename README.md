# Automated Ambulance Allocation and Dispatch Network

> **A BTech Computer Science academic project developed at Graphic Era University.**
> **Team: The Mavericks** — Apurv Gupta (Lead), Kartikey Kashyap, Ambar Sarin

---

## Project Overview

When an emergency happens in public, several bystanders call 108/112 at the same moment,
and unrelated emergencies compete for the same nearby ambulances. A dispatch system that
handles these requests without proper concurrency control sends several ambulances to one
patient, or gives one ambulance to two patients.

This project is a dispatch backend built to stay correct under that load. It combines
operating-system concepts (threads, a producer-consumer queue, synchronisation, scheduling)
with database concepts (transactions, row-level locking, isolation, constraints), and it
measures the effect of each one with a benchmark.

Which ambulance is sent is decided by a dispatch policy. The default sends the nearest free
ambulance. A policy trained by Reinforcement Learning can be switched on with one setting.
A policy only ranks the candidates; the locking underneath stays the same.

## Current Status

| Part | Status |
|---|---|
| FastAPI intake, MySQL schema, seed data | Built |
| Service-area check on every call | Built |
| Two-phase allocation with row-level locking | Built |
| Ambulance lifecycle and simulation clock | Built |
| Own queue and worker thread pool | Built |
| Duplicate call check | Built |
| Several patients in one incident, one ambulance for each | Built |
| Retry of calls that found no ambulance | Built |
| FIFO, priority and Least Laxity First scheduling | Built |
| Dispatch policies: nearest, lookahead, and one trained by Reinforcement Learning | Built |
| Benchmarks for locking, duplicates, scheduling and dispatch policy | Built |
| Terminal demonstration | Built |
| Dashboard (React, Leaflet map) | Planned |
| Redis, Docker, deployment on AWS | Planned |

---

## The Problem: Two Different Race Conditions

Emergency dispatch contains two different concurrency failures, and they need two
different mechanisms. Treating them as one problem is why a system can use transactions
correctly and still misallocate ambulances.

### Problem A: Duplicate Reports

> *One accident. Five bystanders call within thirty seconds.*

Each call creates its own incident and each incident gets a different ambulance. Every
transaction commits correctly and the data is consistent, yet several ambulances go to one
patient while another area is left uncovered. Row-level locking does not help here.

The duplicate check solves it. Every incident stores a key built from its grid cell, its
time window and its type. A new report is a duplicate when an incident of the same
type exists in the same or a neighbouring cell (about 100 m), in the current or previous
2-minute window. A duplicate is counted on the existing incident and no second ambulance is
sent. The check is protected by a mutex, and a `UNIQUE` index on the key makes MySQL itself
refuse a second incident for the same emergency.

A caller can also say how many people are injured. One ambulance is sent for each patient,
however many callers report the accident: three callers reporting five injured get five
ambulances, not three and not fifteen. If a later caller counts more injured than are on
record, only the difference is added. The number is raised and never lowered.

### Problem B: Contended Allocation

> *Two unrelated emergencies. Both are nearest to ambulance `DDN-07`.*

Two worker threads both read `DDN-07` as `AVAILABLE` and both write `ASSIGNED`. This is the
lost update problem: two threads inside the same critical section. It is solved with InnoDB
row-level locking. `SELECT ... FOR UPDATE` makes the second transaction wait, and after the
lock is granted the status is checked again.

Problem A is mutual exclusion enforced by a **unique constraint**.
Problem B is mutual exclusion enforced by a **row lock**.

---

## System Workflow

```
                         Emergency call (HTTP JSON)
                                    │
                                    ▼
                     ┌──────────────────────────┐
                     │      FastAPI Intake      │  validates, replies 202 at once
                     └────────────┬─────────────┘
                                  ▼
                     ┌──────────────────────────┐
                     │     Duplicate Check      │  mutex + UNIQUE key   (Problem A)
                     └────────────┬─────────────┘
                                  ▼
                     ┌──────────────────────────┐
                     │        Own Queue         │  condition variable
                     │                          │  FIFO / priority / LLF
                     └────────────┬─────────────┘
                   ┌──────────────┼──────────────┐
                   ▼              ▼              ▼
               worker-1       worker-2  ...  worker-n      own thread pool
                   └──────────────┼──────────────┘
                                  ▼
                     ┌──────────────────────────┐
                     │         Ranking          │  free ambulances, ordered by the
                     │                          │  dispatch policy, no lock held
                     └────────────┬─────────────┘
                                  ▼
                     ┌──────────────────────────┐
                     │  Allocation Transaction  │  SELECT ... FOR UPDATE,
                     │      MySQL InnoDB        │  check again, assign   (Problem B)
                     └────────────┬─────────────┘
                                  ▼
                     ┌──────────────────────────┐
                     │     Simulation Clock     │  en route, at scene, nearest
                     │                          │  hospital, free again; retries
                     │                          │  calls that are still waiting
                     └──────────────────────────┘
```

1. **Intake.** `POST /incidents` validates the call and returns `202 Accepted` with an
   incident id. It does not wait for an ambulance to be chosen. A call from outside the
   service area is refused here and nothing is stored.
2. **Duplicate check.** A repeat report is added to the existing incident and is not queued.
   A new incident with several patients puts one request on the queue for each patient.
3. **Queue.** A thread-safe queue written for this project on `threading.Condition`. A
   scheduler decides which waiting call a free worker takes next.
4. **Worker thread pool.** A fixed set of threads takes calls from the queue. Each thread
   uses its own database session.
5. **Ranking.** The free ambulances are ranked by distance without holding any lock, and
   the dispatch policy puts them in the order to try. The default policy keeps the nearest
   first. The ranking returns a list, not a single choice.
6. **Allocation transaction.** The top five candidates are locked in ascending id order,
   their status is checked again, the first one in the policy's order that is still free
   is assigned, and the transaction commits. *The ranking proposes; the lock disposes.*
7. **Lifecycle.** A clock thread moves each ambulance through its call. The patient is taken
   to the hospital nearest to the incident, and the ambulance then becomes free. A call that
   found no ambulance is put back on the queue when one becomes free.

---

## Concurrency Design

### Operating System Concepts

| Concept | Where it is used |
|---|---|
| Multithreading, thread pool | `app/core/pool.py`: worker threads created and stopped by the application |
| Producer-consumer | Intake puts calls on the queue; workers take them off (`app/core/queue.py`) |
| Condition variable | The queue's `wait` and `notify` |
| Mutex | Around the duplicate check (`app/core/dedup.py`) and the pool counters |
| Critical section, race condition | The allocation transaction; shown with locking switched off in the benchmark |
| Deadlock prevention and recovery | Rows are always locked in ascending id order, so no circular wait can form. If MySQL still reports a deadlock, the call is tried again without waiting for locks |
| Scheduling | FIFO, priority, and Least Laxity First with a response deadline per severity |

### Database Concepts

| Concept | Where it is used |
|---|---|
| Transactions | Assigning an ambulance is one transaction: all of it happens or none of it |
| Row-level locking | `SELECT ... FOR UPDATE` on candidate ambulance rows; `SKIP LOCKED` as an alternative |
| Isolation level | `READ COMMITTED`, to avoid the gap locks of `REPEATABLE READ` |
| Unique constraints | One dispatch per incident; one incident per emergency (the duplicate key) |
| Foreign keys | A dispatch references its incident, ambulance and hospital |
| Self-referencing foreign key | The row of a further patient points to the first row of the same accident |
| Atomic update | The report counter is increased inside one `UPDATE` statement |
| Indexing | Status columns, report time and foreign keys are indexed, so the lookups that run every second read only the rows they need |

Each mechanism can be switched off with a setting, so the safe version is measured against
an unsafe one and not only described.

### When Something Goes Wrong

A call that has been accepted is never dropped.

* **A row lock that is not released.** The application waits at most 3 seconds for a row
  lock (MySQL's default is 50). If the wait fails, the call takes the nearest ambulance
  whose row is not locked, and for the next 30 seconds other calls do the same, so one
  stuck row cannot hold up the whole fleet.
* **Any other failure while a call is being handled.** The call goes back on the waiting
  list and is retried.
* **Server stop.** The workers finish the calls already in the queue before they stop.
* **Server crash.** A call that was accepted but not dispatched is still recorded in the
  database, and it is put back on the queue at the next start.
* **Lifecycle steps.** A step is decided only on data read after the row lock is held, so
  a manual step and the clock cannot both act on the same old state.

---

## Dispatch Policy

The policy decides the order in which the nearest free ambulances are tried. It is chosen
with the `DISPATCH_POLICY` setting and it runs before any lock is taken, so it cannot
cause a double-booking.

| Policy | What it does |
|---|---|
| `greedy` (default) | Sends the nearest free ambulance |
| `lookahead` | Among the five nearest, sends the one whose leaving hurts the next expected call the least |
| `learned` | Scores the five nearest with six weights trained by Reinforcement Learning |

The `lookahead` and `learned` policies use a demand map: a 9 x 9 grid over the service area
that holds the share of past incidents in each cell, read from the `incidents` table. A
delay counts more for a severe call (CRITICAL 4, HIGH 2, MEDIUM 1, LOW 0.5), so a CRITICAL
call almost always gets the nearest ambulance.

The `learned` policy is trained with policy gradient (REINFORCE) in a simulator of the city
(`rl/env.py`), which needs no database. One episode is a shift of 200 calls, and the reward
is the severity-weighted response time. Training starts from the weights of the lookahead
rule and is repeatable: running it again gives the same weights. Tabular Q-learning was
tried first and did not beat the nearest policy; it is kept in `rl/train.py` as a negative
result. The full account is in [`docs/RL_PLAN.md`](docs/RL_PLAN.md).

---

## Data Model

| Table | Holds |
|---|---|
| `ambulances` | Call sign, station, base and current position, status |
| `hospitals` | Name, position, bed capacity |
| `incidents` | Position, type, severity, status, report time, duplicate key, report count, number of patients, link to the first row of the same accident |
| `dispatches` | The incident, the ambulance and the hospital of one call, with times, distance and ETA |

Relationships: one ambulance has many dispatches; one incident has at most one dispatch;
one hospital receives many dispatches. An accident with several patients is stored as
several linked incident rows, one for each patient, so each row still gets exactly one
ambulance. The ER diagram is in [`docs/ER_Diagram.pdf`](docs/ER_Diagram.pdf). It was drawn
before the last additions, so it does not yet show `report_count`, `patients`, or the link
between the rows of one accident (`parent_id`) on `incidents`.

**Ambulance lifecycle**

```
AVAILABLE → ASSIGNED → EN_ROUTE → AT_SCENE → TRANSPORTING → AT_HOSPITAL → AVAILABLE
```

---

## Results

The benchmarks run against a freshly seeded MySQL database with 12 ambulances.
Numbers change slightly from run to run because threads are timed by the operating system.

### Row-level locking

40 calls at the same instant, 8 worker threads.

| Locking mode | Dispatches | Different ambulances used | Double-booked |
|---|---|---|---|
| none | 40 | 7 | 6 |
| `FOR UPDATE` | 12 | 12 | 0 |
| `FOR UPDATE SKIP LOCKED` | 7 | 7 | 0 |

Without locking, 40 calls were "served" by 7 ambulances. With `FOR UPDATE` all 12 ambulances
were used once each and the other 28 calls waited. `SKIP LOCKED` never waits for a lock, but
it leaves some ambulances unused because a thread skips any row another thread holds.

### Duplicate check

6 accidents, each reported by 5 callers at the same instant.

| Duplicate check | Reports | Incidents | Ambulances sent | Unnecessary | Left free |
|---|---|---|---|---|---|
| off | 30 | 30 | 12 | 6 | 0 |
| on | 30 | 6 | 6 | 0 | 6 |

With 8 accidents and 10 callers each, the run without the check also left 2 accidents with
no ambulance at all.

### Several patients

One accident, checked against the running system with its 12 ambulances.

| What was reported | Ambulances sent |
|---|---|
| One caller, 5 injured | 5, all different |
| Three callers saying 4, 5 and 3 injured | 5 (the second caller added one, the third added none) |
| Three callers at the same instant, each saying 5 injured | 5, not 15 |
| One caller, 6 injured, while only 4 ambulances are free | 4 at once, the other 2 as ambulances became free |

A repeat report made while some patients are still waiting is merged into the same
incident. After the last patient has been picked up, a call from the same place becomes a
new incident.

### Scheduling

48 calls of mixed severity over 60 simulated minutes, so calls have to wait.
Average wait for an ambulance, in minutes:

| Scheduler | CRITICAL | HIGH | MEDIUM | LOW |
|---|---|---|---|---|
| FIFO | 16.7 | 16.7 | 19.4 | 22.2 |
| Priority | 3.4 | 9.9 | 24.9 | 36.4 |
| Least Laxity First | 5.0 | 11.0 | 24.9 | 40.2 |

Priority and Least Laxity First cut the wait for CRITICAL calls from about 17 minutes to
3 to 5, and LOW calls pay for it. At a moderate load (36 calls) they also reduce the number
of late calls from 13 to 10. When the system is overloaded (48 calls) the number of late
calls rises from 26 to 30 and 33, because MEDIUM and LOW calls miss their targets. Priority
and Least Laxity First gave almost the same result in these runs.

The 8-minute target for CRITICAL calls is missed in most cases under every scheduler,
because the drive alone takes 9 to 10 minutes on average. That depends on which ambulance
is sent and on how many ambulances there are, not on the order of the queue.

### Dispatch policy

Severity-weighted average response time in minutes. Lower is better.

In the simulator, on 100 shifts the model was not trained on:

| Load | Nearest | Lookahead | Learned (RL) |
|---|---|---|---|
| Normal | 9.82 | 9.72 | 9.71 |
| Heavy | 15.52 | 15.44 | 15.37 |
| Mixed fleets of 8 to 40 ambulances | 20.00 | 19.59 | 19.65 |

On the real system (MySQL, worker pool, row locks), 20 shifts of 60 calls each:

| Policy | Response | Compared with nearest |
|---|---|---|
| Nearest | 11.55 | |
| Learned (RL) | 11.25 | 0.30 faster, better on 13 of 20 shifts |

The learned policy is faster than nearest by about 1 to 3 percent. The gain is small, and
on the real system it is not yet statistically significant. It gives about the same result
as the lookahead rule, so the training confirms the rule and does not find a better one.
With the learned policy switched on, the locking benchmark gives the same result as before:
12 ambulances used once each and none double-booked.

---

## Technology Stack

* **Backend:** Python 3.13, FastAPI, Uvicorn, Pydantic
* **Database:** MySQL 8 or newer (InnoDB), SQLAlchemy 2, PyMySQL
* **Concurrency:** Python `threading` (own queue and worker thread pool)
* **Reinforcement Learning:** policy gradient and the simulator written in plain Python,
  with no machine learning library
* **Demonstration and benchmarks:** `httpx`, calling the server over its API
* **Planned:** React, Vite, TypeScript, Tailwind and Leaflet for the dashboard; Redis;
  Docker; AWS (EC2 and RDS)

---

## Project Structure

```
app/
  main.py              FastAPI application; starts and stops the worker pool and the clock
  config.py            Settings read from .env
  db.py                Engine, connection pool, one session per thread
  models.py            Tables and status types
  schemas.py           Request and response shapes
  geo.py               Distance and travel time
  api/
    incidents.py       Intake (POST /incidents) and incident lookup
    ambulances.py      Fleet listing
    dispatches.py      Dispatch listing and manual lifecycle steps
    queue.py           Live queue, worker and clock statistics
    health.py          Health check
  core/
    dedup.py           Duplicate check                      (Problem A)
    queue.py           Thread-safe queue and the three schedulers
    scheduling.py      Response deadlines and laxity
    pool.py            Worker thread pool
    dispatcher.py      The worker's job, and the retry of waiting calls
    allocation.py      Two-phase allocation with row locks  (Problem B)
    lifecycle.py       Ambulance state machine and hospital choice
    runtime.py         The shared queue, pool and clock
  policy/
    base.py            The policy interface and the nearest-first policy
    demand.py          Demand map: where past incidents happened
    lookahead.py       Lookahead rule
    learned.py         Policy scored with weights trained by Reinforcement Learning
    learned_weights.json   The trained weights
    factory.py         Picks the policy named in the settings
    features.py        State encoding for the Q-learning experiment
sim/
  seed.py              Creates the database and loads ambulances and hospitals
  clock.py             Simulation clock thread
rl/
  env.py               Simulator of the city used for training (no database)
  pg.py                Policy-gradient training, check and evaluation
  train.py             Tabular Q-learning (negative result)
  search.py            Earlier experiment: search over a table policy
  oracle.py            Earlier experiment: best possible result if the future were known
  lookahead.py         Earlier experiment: tuning of the lookahead rule
bench/
  loadtest.py          Locking benchmark
  dedup.py             Duplicate check benchmark
  scheduling.py        Scheduler benchmark
  policy_ab.py         Dispatch policies compared on the real system
demo/                  Terminal demonstration (python -m demo)
background/
  commands.md          Every command for showing the background parts, in order
docs/                  ER diagram, concept notes and the Reinforcement Learning report
```

---

## Local Setup

### Prerequisites

* Python 3.10 or higher
* MySQL Server 8.0 or higher, running locally
* Git

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/guptapurv07/Automated-Ambulance-Allocation-and-Dispatch-Network.git
   cd Automated-Ambulance-Allocation-and-Dispatch-Network
   ```

2. **Create and activate a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
   On Windows use `venv\Scripts\activate`.

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure the environment**
   ```bash
   cp .env.example .env
   ```
   Open `.env` and set `DB_PASSWORD` to your MySQL password. This file is not committed.

5. **Create the database and load the data**
   ```bash
   python -m sim.seed
   ```
   This creates the `ambulance_dispatch` database, its tables, 12 ambulances and 4 hospitals.

6. **Run the server**
   ```bash
   uvicorn app.main:app --reload
   ```
   Interactive API documentation is at **http://127.0.0.1:8000/docs**.

7. **Run the terminal demonstration**, in a second terminal
   ```bash
   python -m demo
   ```

After pulling changes that alter the tables, rebuild the database with
`python -m sim.seed --reset`. The demonstration says so if the tables are older than the
code.

### Settings

All settings live in `.env`. The most useful ones:

| Setting | Default | Meaning |
|---|---|---|
| `LOCKING_MODE` | `for_update` | `none`, `for_update` or `skip_locked` |
| `QUEUE_SCHEDULER` | `llf` | `fifo`, `priority` or `llf` |
| `DISPATCH_POLICY` | `greedy` | `greedy`, `lookahead` or `learned` |
| `LOOKAHEAD_BETA` | `1.25` | How much the next expected call counts in the lookahead rule |
| `DEDUP_ENABLED` | `true` | Switch the duplicate check on or off |
| `WORKER_POOL_SIZE` | `4` | Number of worker threads |
| `SIM_CLOCK_ENABLED` | `true` | Move ambulances through their calls automatically |
| `SIM_TIME_SCALE` | `60` | Simulated minutes per real minute |
| `LOCK_WAIT_TIMEOUT_SECONDS` | `3` | Longest wait for a row lock |
| `AVG_SPEED_KMPH` | `32` | Average ambulance speed used for travel times |
| `SERVICE_AREA_LAT_MIN`, `_LAT_MAX`, `_LON_MIN`, `_LON_MAX` | `30.10`, `30.50`, `77.75`, `78.25` | The box around Dehradun inside which a call is accepted |
| `RACE_WINDOW_MS` | `0` | Pause between locking the ambulance rows and assigning one. Zero in normal use; raised only to make a race or a lock visible |

The server does not start if `QUEUE_SCHEDULER` or `DISPATCH_POLICY` has an unknown value.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/incidents` | Report an emergency. Replies `202` with an incident id, or says it is a duplicate. With `patients` it requests one ambulance for each |
| `GET` | `/incidents/{id}` | The incident with its dispatch, ambulance and hospital. With several patients, every patient's ambulance and hospital |
| `GET` | `/ambulances` | The fleet with position and status; filter with `?status=`, page with `?limit=` and `?offset=` |
| `GET` | `/dispatches` | Dispatch records, newest first, 100 per page; filter with `?status=`, page with `?limit=` and `?offset=` |
| `POST` | `/dispatches/{id}/advance` | Move a dispatch forward by one lifecycle step |
| `POST` | `/dispatches/{id}/complete` | Finish a dispatch and free its ambulance |
| `GET` | `/queue` | Queue depth, scheduler, worker counters and clock state |
| `GET` | `/health` | Service and database check, with the locking mode, scheduler and dispatch policy in use |

A call needs `lat` and `lon`. Optional fields are `severity` (`CRITICAL`, `HIGH`, `MEDIUM`,
`LOW`), `incident_type` (`ROAD_ACCIDENT`, `CARDIAC`, `BREATHING`, `INJURY`, `FIRE`,
`PREGNANCY`, `OTHER`), `patients` (how many people need an ambulance, 1 to 10, default 1),
`caller_phone` and `description`.

---

## Running the Demonstrations

### The terminal demonstration

```bash
python -m demo
```

A menu with six scenes. The program talks to the server over the API like any other
caller, so everything on the screen is the system itself at work.

| Scene | What it shows |
|---|---|
| 1. One emergency | Each step from the call to the hospital. The place, severity, type and number of injured are chosen at the keyboard |
| 2. Five callers, one accident | Five calls at the same instant become one incident with five reports and one ambulance |
| 3. Several injured, three callers | Callers who report 4, 5 and 5 injured get five ambulances, all different |
| 4. Seventeen emergencies, twelve ambulances | Twelve calls at once get twelve different ambulances. Five more wait, and the scheduler decides who gets each ambulance that becomes free |
| 5. A call from outside the city | The call is refused and nothing is stored |
| 6. The fleet and the queue | Every ambulance's status, and the queue, worker and clock counters |

The time at the start of a line is simulated minutes and seconds since the call; one real
second is one simulated minute. In the second half of scene 4 the running calls are
finished early, one at a time, so that nobody has to wait through whole trips; which
waiting call gets each ambulance is still the scheduler's decision.

It starts a server itself if none is running. To watch the server's log beside it, start
the server in a second terminal first with `uvicorn app.main:app --no-access-log`.
`python -m demo --scene 4` runs one scene with its prepared details and exits.

To see what a mechanism does, start the server with one setting changed and run scene 4
again.

First come, first served. The calls that arrived first are served first, and the CRITICAL
call is served last:

```bash
QUEUE_SCHEDULER=fifo uvicorn app.main:app --no-access-log
```

Locking switched off. The same ambulance is given to several calls. Reset the database
afterwards with `python -m sim.seed --reset`:

```bash
LOCKING_MODE=none RACE_WINDOW_MS=20 uvicorn app.main:app --no-access-log
```

Every command for showing the background parts (the API by hand, the row lock in MySQL, the
benchmarks, the tables) is listed in order in
[`background/commands.md`](background/commands.md). The steps below are the main ones.

Start the server first, unless a step says otherwise.

### 1. One call from start to finish

```bash
curl -s -X POST http://127.0.0.1:8000/incidents -H "Content-Type: application/json" -d '{"lat":30.2740,"lon":78.0480,"severity":"CRITICAL","incident_type":"ROAD_ACCIDENT","description":"Accident on Haridwar Bypass"}'
```

```bash
curl -s http://127.0.0.1:8000/incidents/1
```

The first reply comes back at once. The second shows the ambulance that was assigned and,
after about 15 seconds, the hospital. The server terminal prints each step of the trip.
The ambulance is free again after about 30 seconds.

### 2. Several callers, one incident

Send the first command above two more times straight away, while the first call is still
in progress. Each reply says `"duplicate": true` and the report count goes up, but no new
ambulance is sent.

### 3. The row lock in MySQL

Open two MySQL sessions on `ambulance_dispatch`.

Session 1:
```sql
START TRANSACTION;
SELECT id, call_sign, status FROM ambulances WHERE id = 7 FOR UPDATE;
```

Session 2:
```sql
SELECT id, call_sign, status FROM ambulances WHERE id = 7 FOR UPDATE NOWAIT;
SELECT id, call_sign, status FROM ambulances WHERE id IN (6,7,8) FOR UPDATE SKIP LOCKED;
SELECT OBJECT_NAME, LOCK_TYPE, LOCK_MODE, LOCK_STATUS, LOCK_DATA FROM performance_schema.data_locks;
```

Session 2 gets an error for the first query, only ambulances 6 and 8 for the second, and
sees the exclusive row lock on row 7 in the third. Finish with `ROLLBACK;` in session 1 and
close both sessions. A session left open with an unfinished transaction blocks the database
reset.

The system's own locks last a few milliseconds, too short to see. To watch them, start the
server with the locks held for 15 seconds, send one call, and run the query while the call
is being allocated:

```bash
RACE_WINDOW_MS=15000 uvicorn app.main:app --no-access-log
```

```sql
SELECT a.id, a.call_sign, a.status, l.LOCK_MODE, l.LOCK_STATUS FROM performance_schema.data_locks l JOIN ambulance_dispatch.ambulances a ON a.id = l.LOCK_DATA WHERE l.OBJECT_NAME = 'ambulances' AND l.LOCK_TYPE = 'RECORD' ORDER BY a.id;
```

It lists the five nearest ambulances, each with an exclusive lock held by the worker. After
the 15 seconds the query returns nothing and one of the five is on its way. Send only one
call at a time in this mode, and start the server normally again afterwards.

### 4. The benchmarks

Stop the server before running these. Each one starts its own server and resets the
database. Keep the computer awake while they run, because times are measured on the real
clock.

```bash
python -m bench.loadtest
python -m bench.dedup
python -m bench.scheduling
```

They print the tables shown under Results.

### 5. The dispatch policy and Reinforcement Learning

These three need no database and no server, and they finish in under a minute.

```bash
python -m rl.env
python -m rl.pg check
python -m rl.pg eval
```

The first checks the simulator. The second checks that the learned policy, at its starting
weights, makes the same choice as the lookahead rule. The third compares nearest, lookahead
and learned on shifts the model was not trained on.

To train again (about 9 minutes; it rewrites `app/policy/learned_weights.json`):

```bash
python -m rl.pg train
```

To compare the policies on the real system, stop the server first. This starts its own
server and resets the database, and it takes about 12 minutes for 6 shifts.

```bash
python -m bench.policy_ab --seeds 6
```

To run the server with the learned policy, set `DISPATCH_POLICY=learned` in `.env` and
start it as usual.

---

## Assumptions and Limits

* Emergency calls are simulated; the system is not connected to a live 108/112 feed.
* The service area is a single city, Dehradun. It is a rectangle (latitude 30.10 to 30.50,
  longitude 77.75 to 78.25), not the real boundary of the district.
* Travel time is straight-line distance at an average speed, not live traffic.
* Time is compressed: one simulated minute passes each real second by default.
* Two reports are merged only if the callers choose the same incident type. A repeat report
  can raise the severity of the incident and adds its description; once the ambulance has
  left with the patient (with several patients, once the last one has been picked up), a
  call from the same place is treated as a new incident.
* One ambulance carries one patient, and one call can ask for at most 10 ambulances. If
  fewer are free than an accident needs, the rest are sent as others become free.
* When six or more calls are handled in the same instant, a call can find all five of its
  nearest ambulances taken by the others and is then tried again one clock tick later, so
  now and then a less urgent call gets its ambulance a moment before a more urgent one.
* A call from outside the service area is refused. It is not passed on to the service of
  the neighbouring district, which a real control room would do.
* The patient goes to the nearest hospital; bed capacity is stored but not yet used.
* After a call the ambulance waits at the hospital; it does not drive back to its station.
* A dispatch cannot be cancelled once it has been made.
* The learned policy improves the response time only slightly. It was trained on simulated
  calls with assumed busy areas, not on real ambulance data.
* The trained weights fit the speed and the service area set in `.env`. If either changes,
  train again with `python -m rl.pg train`.
* Waiting calls are retried by the simulation clock, so the clock must be running.
* The system runs as one server process. The queue and the duplicate check's lock are in
  memory, so running several servers side by side needs Redis first.
* Tested with bursts of 3000 calls and with 1000 ambulances and 200,000 past calls in the
  tables. The simulation clock keeps its pace up to about 100 ambulances on calls at once
  at the default 60x speed; with more than that it falls behind.

---

## Documents

* [`docs/ER_Diagram.pdf`](docs/ER_Diagram.pdf): ER diagram of the database
* [`docs/OS_Concepts_Used.docx`](docs/OS_Concepts_Used.docx): operating system concepts used in the project
* [`docs/Deadlock_Prevention_Simple.docx`](docs/Deadlock_Prevention_Simple.docx): how deadlocks are prevented
* [`docs/RL_PLAN.md`](docs/RL_PLAN.md): the Reinforcement Learning part: what was built, the results, and how to reproduce them
* [`background/commands.md`](background/commands.md): every command for showing the background parts, in order

---

## Team

| Role | Name | Student ID |
|---|---|---|
| Team Lead | Apurv Gupta | 24021131 |
| Member | Kartikey Kashyap | 24021132 |
| Member | Ambar Sarin | 24022059 |