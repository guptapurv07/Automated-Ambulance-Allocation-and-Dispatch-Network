# Automated Ambulance Allocation and Dispatch Network

> **A BTech Computer Science academic project developed at Graphic Era University.**
> **Team: The Mavericks** — Apurv Gupta (Lead), Kartikey Kashyap, Ambar Sarin

---

## 📌 Project Overview & Core Idea

When an emergency occurs in a public space, multiple bystanders dial 108/112 at the same
moment, and multiple unrelated emergencies compete for the same nearby vehicles. Standard
dispatch systems handle these requests sequentially or without proper isolation, which
corrupts allocation state under load.

This project builds a **concurrency-safe dispatch network**: a Reinforcement Learning
decision engine for *which* ambulance to send, wrapped in operating-system-level thread
management and database-level row locking that guarantee the decision is executed exactly
once, even when dozens of calls arrive simultaneously.

The central claim is that intelligent routing is only useful if the allocation underneath
it is safe. Existing research projects optimise the route and ignore the concurrency; this
project supplies the missing OS and DBMS layer.

---

## 🎯 The Problem: Two Distinct Race Conditions

A key design insight of this project is that emergency dispatch contains **two different
concurrency failures**, which require **two different mechanisms**. Treating them as one
problem is why naive systems still misallocate vehicles even when they use transactions.

### Problem A — Duplicate Incident Reports

> *One accident. Five bystanders call within thirty seconds.*

Each call creates its own incident record, each is assigned a *different* ambulance, and
every database transaction commits correctly. The data is perfectly consistent — and three
ambulances are dispatched to one patient while another neighbourhood goes uncovered.

Row-level locking does **not** solve this. This is a **deduplication / idempotency**
problem, solved by an atomic test-and-set on a derived key of
`(geo_cell, time_bucket, incident_type)` — enforced by a unique index, with Redis `SETNX`
as the fast path. The second caller's report is folded into the existing incident instead
of creating a new dispatch.

### Problem B — Contended Allocation

> *Two unrelated emergencies across town. Both are nearest to ambulance `DDN-07`.*

Two worker threads both read `DDN-07` as `AVAILABLE`, both write `ASSIGNED`. This is the
classic lost-update race — two processes inside the same critical section — and it is
solved exactly as intended by **MySQL InnoDB row-level locking**: `SELECT ... FOR UPDATE`
forces the second thread to wait and re-evaluate against committed state.

Problem A is mutual exclusion enforced by a **unique constraint**.
Problem B is mutual exclusion enforced by a **row lock**.
The system implements both.

---

## ⚙️ System Workflow & Architecture

```
                         Simulated 108/112 calls
                                    │
                                    ▼
                     ┌──────────────────────────┐
                     │     FastAPI Intake       │  async · validates · returns
                     │                          │  202 Accepted + incident_id
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │   Deduplication Gate     │  atomic test-and-set
                     │                          │  collapses repeat calls  (Problem A)
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │      Custom Queue        │  hand-built, condition-variable
                     │                          │  FIFO / priority / Least-Laxity-First
                     └────────────┬─────────────┘
                                  │
                   ┌──────────────┼──────────────┐
                   ▼              ▼              ▼
               worker-1       worker-2       worker-n     custom thread pool
                   └──────────────┼──────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │     Decision Engine      │  RL policy ranks candidate
                     │                          │  ambulances — OUTSIDE the txn
                     └────────────┬─────────────┘
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │     Allocation Txn       │  SELECT ... FOR UPDATE
                     │     MySQL InnoDB         │  verify · assign · COMMIT  (Problem B)
                     └────────────┬─────────────┘
                                  │
                                  ▼
                          Dispatch record written
                          Ambulance state advances
```

### Pipeline stages

1. **FastAPI Intake** — receives parallel emergency reports, validates them, persists the
   incident and immediately returns `202 Accepted` with an incident ID. The HTTP thread is
   never blocked by allocation work.
2. **Deduplication Gate** — an atomic test-and-set collapses repeat reports of the same
   event into a single incident, preventing redundant dispatch (Problem A).
3. **Custom Queue** — a hand-built thread-safe queue guarded by a condition variable, with
   a pluggable scheduling policy. Emergency calls carry severity-derived response deadlines,
   making **Least Laxity First** a natural scheduling discipline to evaluate against plain
   FIFO and static priority.
4. **Custom Worker Thread Pool** — a fixed set of worker threads consumes the queue. Because
   allocation work is I/O-bound on database waits, threads genuinely overlap despite the GIL.
   Each worker holds its **own database connection**; sessions are never shared across threads.
5. **Decision Engine** — the policy evaluates a snapshot of available vehicles and returns a
   **ranked list** of candidates, not a single pick. Inference runs *outside* the transaction
   so row locks are never held across a model forward pass.
6. **Allocation Transaction** — a short transaction locks candidate rows with
   `SELECT ... FOR UPDATE`, re-verifies availability against committed state, writes the
   dispatch and commits. If the top-ranked vehicle was taken, the worker falls through to the
   next candidate. **The policy proposes; the lock disposes.**
7. **Caching & Cloud** — Redis backs the deduplication key and caches hot vehicle lookups.
   The stack deploys to AWS (EC2 for the application, RDS for the database).

---

## 🔒 Concurrency Design

### Operating-System Layer

| Concern | Mechanism |
|---|---|
| Producer/consumer buffering | Custom queue built on `threading.Condition` |
| Parallel request handling | Custom worker thread pool with graceful shutdown |
| Critical section | Allocation transaction, entered by one worker per vehicle |
| Scheduling | Pluggable — FIFO, static priority, Least Laxity First |
| Starvation control | Deadline-aware ordering so low-severity calls still drain |

### Database Layer

| Concern | Mechanism |
|---|---|
| Lost update on a vehicle | `SELECT ... FOR UPDATE` on candidate ambulance rows |
| Non-blocking contention | `SELECT ... FOR UPDATE SKIP LOCKED` to claim an uncontended vehicle |
| Deadlock prevention | Total ordering — candidate rows always locked in ascending ID order |
| Duplicate incidents | Unique index on the derived deduplication key |
| Isolation | `READ COMMITTED`, to avoid unnecessary gap locking under `REPEATABLE READ` |
| Lock scope | Transactions kept short; no inference or network I/O inside them |

A configurable locking mode (`none` / `for_update` / `skip_locked`) lets the safe path be
measured directly against an unsafe baseline, which is how the project demonstrates that the
locking actually prevents misallocation rather than merely asserting it.

---

## 🗄️ Data Model

| Table | Purpose |
|---|---|
| `ambulances` | Vehicle registry — call sign, base, live coordinates, status |
| `hospitals` | Receiving facilities with coordinates and capacity |
| `incidents` | Emergency reports — location, severity, dedup key, status |
| `dispatches` | The allocation record joining an incident to an ambulance |

**Ambulance lifecycle:**

```
AVAILABLE → ASSIGNED → EN_ROUTE → AT_SCENE → TRANSPORTING → AT_HOSPITAL → AVAILABLE
```

This state machine is the unit of contention that locking protects, and it defines the
state and reward space for the Reinforcement Learning policy.

---

## 🛠️ Technology Stack

* **Backend Framework:** Python 3.13, FastAPI, Uvicorn
* **ORM / DB Driver:** SQLAlchemy 2.x, PyMySQL
* **Database Engine:** MySQL 8+ (InnoDB, for row-level locking)
* **Concurrency:** Custom queue and worker thread pool built on `threading`
* **High-Speed Caching:** Redis
* **AI/ML Logic:** Reinforcement Learning decision engine
* **Frontend:** React, Vite, TypeScript, Tailwind CSS, Leaflet (OpenStreetMap)
* **Cloud Infrastructure:** AWS — EC2 for the application server, RDS for the database

---

## 📁 Project Structure

```
app/
  main.py            FastAPI application and lifecycle
  config.py          Environment-driven settings
  db.py              Engine and per-thread session management
  models.py          SQLAlchemy ORM models
  schemas.py         Pydantic request/response schemas
  api/               HTTP route handlers
  core/              Queue, thread pool, dispatcher, locking
  policy/            Decision-engine interface and implementations
sim/
  seed.py            Reference data for the simulated service area
frontend/            Operations dashboard
```

---

## 🚀 Local Setup & Installation

### Prerequisites

* Python 3.10 or higher
* MySQL Server 8.0 or higher, running locally
* Git

### Installation Steps

1. **Clone the repository**
   ```bash
   git clone https://github.com/guptapurv07/Automated-Ambulance-Allocation-and-Dispatch-Network.git
   cd Automated-Ambulance-Allocation-and-Dispatch-Network
   ```

2. **Create and activate a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate        # Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create the database**
   ```bash
   mysql -u root -e "CREATE DATABASE IF NOT EXISTS ambulance_dispatch;"
   ```

5. **Configure the environment**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` if your MySQL credentials differ from the defaults.

6. **Seed the reference data**
   ```bash
   python -m sim.seed
   ```

7. **Run the server**
   ```bash
   uvicorn app.main:app --reload
   ```

   Interactive API documentation is served at **http://127.0.0.1:8000/docs**.

---

## 📡 API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service and database connectivity check |
| `GET` | `/ambulances` | List all ambulances with live status |
| `POST` | `/incidents` | Report an emergency and allocate an ambulance |
| `GET` | `/incidents/{id}` | Retrieve a single incident and its dispatch |
| `GET` | `/dispatches` | List all allocation records |

---

## 📋 Assumptions

* Emergency calls are **simulated**; the system does not connect to a live 108/112 feed.
* Coverage is limited to a **single city service area** (Dehradun), not a state or country.
* Travel times are computed from **standard average speeds** over great-circle distance,
  rather than a paid live-traffic API.
* Development and testing run locally before deployment to AWS.

---

## 👥 Team

| Role | Name | Student ID |
|---|---|---|
| Team Lead | Apurv Gupta | 24021131 |
| Member | Kartikey Kashyap | 24021132 |
| Member | Ambar Sarin | 24022059 |
