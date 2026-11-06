# Background Demonstration: Commands

Every command needed to show the system: the dashboard, the terminal demonstration, and how
it works behind them. Run them from the project folder, with the virtual environment active,
one at a time.

Use three terminal windows:

* **Window A** runs the server and shows its log.
* **Window B** is where you type everything else.
* **Window C** runs the dashboard, from the `frontend` folder.

---

## 1. Start the system

Window A. Build a fresh database (30 ambulances, 14 hospitals, 1,000 past calls):

```
python -m sim.seed --reset
```

Window A. Start the server. `--no-access-log` hides the line printed for every request, so
the dispatch steps are easy to read:

```
uvicorn app.main:app --no-access-log
```

Window B. The terminal demonstration (the six scenes):

```
python -m demo
```

Window C. The dashboard. Go to its folder:

```
cd frontend
```

The first time, and after every pull that changed the dashboard, install its packages:

```
npm install
```

Start it and leave it running:

```
npm run dev
```

Window B. Open it in the browser:

```
open http://localhost:5173
```

---

## 2. The dashboard

The dashboard calls the same API as everything else: it shows the system at work and
decides nothing itself. The bottom of its sidebar says `Running` while the server answers.

| Page | What to show |
|---|---|
| Live | Across the top: active calls, free ambulances, calls waiting for an ambulance, and the average response and share on time of the calls in progress. Below, the map: each ambulance drives along the roads to the patient, to the hospital, and back to its station. The button at the right end of the map's title bar shows it full screen, good on a projector; Esc or the same button brings it back. Beside it, the active dispatches with their response time against the target, the fleet by status, and the settings the server runs with |
| Live, Simulator | `Start` sends a call every few seconds. `+10 calls` and `+35 calls` send them all at once; 35 calls for 30 ambulances, so `Free` drops to 0, `Waiting` goes up to 5, and it counts back down as ambulances come free and the scheduler hands them out |
| Report emergency | Take a call: click the place on the map, choose severity, type and patients, press `Dispatch`. A place outside the service area cannot be sent |
| Incidents | Every call. Click one: its own page follows it live, step by step |
| History | Finished calls with their response time; red is late for the severity. Above the table, the totals and a chart of the share reached on time for each severity, in its colour |
| Scenarios | The six cases of `python -m demo`, each with its own `Run` button (below) |
| Fleet | Every ambulance, how long it has been in its status, and how long a returning one needs to reach its station |
| Hospitals | The beds, and the ambulances handing a patient over at each hospital now |
| System | Database and Redis; the clock and whether this server runs it; the queue; the worker threads and how many calls they handled; the settings |

The Scenarios page, in order:

| Scenario | What it shows |
|---|---|
| One emergency | A call is accepted at once; open the incident to follow it |
| Five callers, one accident | Five calls at the same instant become one incident with five reports and one ambulance: the race condition on the duplicate check, solved with a lock |
| Multiple patients | Three callers report 4, 5 and 5 injured; the accident gets five ambulances, all different |
| Concurrent emergencies | As many calls at once as there are free ambulances, and none is given twice: the row locks. Then five more calls wait, and the scheduler decides who gets each ambulance that becomes free |
| Outside the service area | A call from Delhi is refused (HTTP 422) and no ambulance moves |
| Fleet and queue snapshot | The fleet, and the queue, worker and clock counters |

Run one scenario at a time, and not while the terminal demonstration or the simulator is
running: they share the same ambulances. "Concurrent emergencies" needs free ambulances; if
none is free, wait a minute for the running calls to finish.

---

## 3. How the API works

Leave the server running in Window A and type these in Window B.

Report an emergency. The reply comes back at once with an incident id; it does not wait for
an ambulance:

```
curl -s -X POST http://127.0.0.1:8000/incidents -H "Content-Type: application/json" -d '{"lat":30.2740,"lon":78.0480,"severity":"CRITICAL","incident_type":"ROAD_ACCIDENT","description":"Accident on Haridwar Bypass"}' | python -m json.tool
```

Look the incident up. Use the `incident_id` from the reply above; right after a reset the
first new call is 1052, because the past calls take the first 1,051 rows. It shows the
ambulance, and after a few seconds the hospital:

```
curl -s http://127.0.0.1:8000/incidents/1052 | python -m json.tool
```

Send the same report again while the ambulance is still on its way. The reply says
`"duplicate": true` and no second ambulance is sent:

```
curl -s -X POST http://127.0.0.1:8000/incidents -H "Content-Type: application/json" -d '{"lat":30.2740,"lon":78.0480,"severity":"CRITICAL","incident_type":"ROAD_ACCIDENT","description":"Second caller"}' | python -m json.tool
```

Report an accident with three injured people. Three ambulances are requested:

```
curl -s -X POST http://127.0.0.1:8000/incidents -H "Content-Type: application/json" -d '{"lat":30.3420,"lon":77.9990,"severity":"CRITICAL","incident_type":"ROAD_ACCIDENT","patients":3}' | python -m json.tool
```

A call from outside the city (Delhi). It is refused:

```
curl -s -X POST http://127.0.0.1:8000/incidents -H "Content-Type: application/json" -d '{"lat":28.6139,"lon":77.2090,"severity":"HIGH"}' | python -m json.tool
```

The whole fleet with each ambulance's status:

```
curl -s http://127.0.0.1:8000/ambulances | python -m json.tool
```

The latest incidents, newest first. `?status=`, `?severity=`, `?limit=` and `?offset=` filter
and page the list. Times carry their time zone (`+05:30`):

```
curl -s "http://127.0.0.1:8000/incidents?limit=5" | python -m json.tool
```

One row per accident: an accident with several patients is shown once, by its first row:

```
curl -s "http://127.0.0.1:8000/incidents?top_level=true&limit=5" | python -m json.tool
```

The hospitals:

```
curl -s http://127.0.0.1:8000/hospitals | python -m json.tool
```

The latest dispatches. `response_minutes` is the wait plus the drive, to compare with the
target (CRITICAL 8, HIGH 15, MEDIUM 30, LOW 60):

```
curl -s "http://127.0.0.1:8000/dispatches?limit=3" | python -m json.tool
```

The queue, the worker threads and the clock:

```
curl -s http://127.0.0.1:8000/queue | python -m json.tool
```

The health check, with the settings in use (locking mode, scheduler, dispatch policy,
duplicate check, average speed, lock wait, service area):

```
curl -s http://127.0.0.1:8000/health | python -m json.tool
```

The same API in the browser, with a form for every endpoint:

```
open http://127.0.0.1:8000/docs
```

---

## 4. What to point at in the server log (Window A)

Each line is one step of the system. For one call they appear in this order:

| Line contains | What it shows |
|---|---|
| `QUEUED` | Intake accepted the call and put it on the queue |
| `DUPLICATE report merged` | The duplicate check matched it to an existing incident |
| `picked up ... queue_wait=` | A worker thread took it from the queue |
| `ASSIGNED ... locked= lock_wait= mode=for_update` | The row lock was taken and an ambulance assigned |
| `SERVED ... ON TIME` or `LATE` | The response time against the target for that severity |
| `ASSIGNED -> EN_ROUTE`, `EN_ROUTE -> AT_SCENE` and so on | The clock moving the ambulance |
| `hospital=` | The hospital chosen as the ambulance leaves the scene |
| `back at its station` | After the call, the ambulance has driven back to its station |
| `RE-QUEUED` | A call that had to wait is given another try |

The second column, for example `worker-1` or `sim-clock`, is the thread that did the step.

---

## 5. Row-level locking in MySQL

Open two more terminal windows. In each one, log in to the database (it asks for the MySQL
password):

```
mysql -u root -p ambulance_dispatch
```

Session 1. Start a transaction and lock ambulance 7:

```
START TRANSACTION;
```

```
SELECT id, call_sign, status FROM ambulances WHERE id = 7 FOR UPDATE;
```

Session 2. Try to lock the same row without waiting. MySQL refuses:

```
SELECT id, call_sign, status FROM ambulances WHERE id = 7 FOR UPDATE NOWAIT;
```

Session 2. Ask for rows 6, 7 and 8 and skip any that are locked. Only 6 and 8 come back:

```
SELECT id, call_sign, status FROM ambulances WHERE id IN (6,7,8) FOR UPDATE SKIP LOCKED;
```

Session 2. Show the lock itself. Row 7 has an exclusive (`X`) record lock:

```
SELECT OBJECT_NAME, LOCK_TYPE, LOCK_MODE, LOCK_STATUS, LOCK_DATA FROM performance_schema.data_locks;
```

Session 1. Release the lock:

```
ROLLBACK;
```

### A deadlock, and how the system prevents it

A deadlock is two transactions that each hold a row the other one is waiting for, so neither
can go on. Make one on purpose: the two sessions lock ambulances 7 and 8 in opposite orders.
Do it while no calls are running.

Session 1. Lock ambulance 7:

```
START TRANSACTION;
```

```
SELECT id, call_sign FROM ambulances WHERE id = 7 FOR UPDATE;
```

Session 2. Lock ambulance 8:

```
START TRANSACTION;
```

```
SELECT id, call_sign FROM ambulances WHERE id = 8 FOR UPDATE;
```

Session 1. Ask for ambulance 8. It waits, because session 2 holds it:

```
SELECT id, call_sign FROM ambulances WHERE id = 8 FOR UPDATE;
```

Session 2. Ask for ambulance 7. Now each session waits for the other:

```
SELECT id, call_sign FROM ambulances WHERE id = 7 FOR UPDATE;
```

MySQL finds the circle at once and ends one of the two transactions, here session 2:
`ERROR 1213 (40001): Deadlock found when trying to get lock; try restarting transaction`.
In the same moment session 1 gets ambulance 8.

Session 2. MySQL's own record of it, under `LATEST DETECTED DEADLOCK`:

```
SHOW ENGINE INNODB STATUS\G
```

Session 1. Release the locks:

```
ROLLBACK;
```

Now the way the system does it: both sessions lock 7 first, then 8.

Session 1:

```
START TRANSACTION;
```

```
SELECT id, call_sign FROM ambulances WHERE id = 7 FOR UPDATE;
```

Session 2. It waits at ambulance 7 while it holds nothing, so no circle can form:

```
START TRANSACTION;
```

```
SELECT id, call_sign FROM ambulances WHERE id = 7 FOR UPDATE;
```

Session 1. Within 50 seconds (MySQL's limit for a lock wait in these sessions), take
ambulance 8 and finish:

```
SELECT id, call_sign FROM ambulances WHERE id = 8 FOR UPDATE;
```

```
COMMIT;
```

Session 2 gets ambulance 7 now, with no error. Release it:

```
ROLLBACK;
```

This is the rule in `app/core/allocation.py`: the candidate ambulances are always locked in
ascending id order, so a circular wait cannot form. If MySQL still reports a deadlock,
`app/core/dispatcher.py` tries the call again with `SKIP LOCKED`, which waits for no lock.

Then type `exit` in both sessions. A session left open with an unfinished transaction
blocks the database reset.

### The system's own lock, while a call is being allocated

In normal running the system holds its row locks for a few milliseconds, which is too
short to see. This makes it hold them for 15 seconds.

Window A. Stop the server (Ctrl+C) and start it with the locks slowed down:

```
RACE_WINDOW_MS=15000 uvicorn app.main:app --no-access-log
```

Window B. Run scene 1 of the demonstration and enter one call:

```
python -m demo
```

In a MySQL session, within the 15 seconds. Type it beforehand, so that you only have to
press Enter:

```
SELECT a.id, a.call_sign, a.status, l.LOCK_MODE, l.LOCK_STATUS FROM performance_schema.data_locks l JOIN ambulance_dispatch.ambulances a ON a.id = l.LOCK_DATA WHERE l.OBJECT_NAME = 'ambulances' AND l.LOCK_TYPE = 'RECORD' ORDER BY a.id;
```

It lists the five nearest ambulances, each with an exclusive (`X`) lock held by the worker
thread. Run it again after the 15 seconds: it returns nothing, and one of the five is on
its way.

Send only one call at a time in this mode. The demonstration shows the ambulance allocated
about 15 minutes late, because 15 real seconds are 15 simulated minutes.

Window A. Stop the server and start it normally again. Do not skip this, or every later
call will take 15 seconds:

```
uvicorn app.main:app --no-access-log
```

---

## 6. The race condition, measured

Stop the server first (Ctrl+C in Window A). Each benchmark starts its own server and
resets the database.

Locking. 60 calls at the same instant with locking off, with `FOR UPDATE`, and with
`SKIP LOCKED`. Without locking, ambulances are double-booked. The `deadlocks` column stays at
0: the ascending lock order of section 5 at work:

```
python -m bench.loadtest
```

Duplicate check. 6 accidents with 5 callers each, with the check off and on:

```
python -m bench.dedup
```

Scheduling. FIFO against priority and Least Laxity First, by how long each severity waits:

```
python -m bench.scheduling
```

---

## 7. The same scene with a setting changed

Stop the server, start it again with one setting changed, then run the scene in Window B.
These commands are for macOS and Linux.

Locking switched off. Scene 4 then shows the same ambulance going to several calls:

```
LOCKING_MODE=none RACE_WINDOW_MS=20 uvicorn app.main:app --no-access-log
```

```
python -m demo --scene 4
```

On the dashboard the System page shows `Locking` as `None (unsafe)`, and "Concurrent
emergencies" on the Scenarios page shows the same result as a warning: "repeated ambulance
assignment detected".

After a run with locking off the data is wrong on purpose, so stop the server and reset:

```
python -m sim.seed --reset
```

First come, first served. In scene 4 the LOW calls are then served before the CRITICAL one.
The dashboard's System page shows `Queue order` as `FIFO`:

```
QUEUE_SCHEDULER=fifo uvicorn app.main:app --no-access-log
```

The Reinforcement Learning policy in place of the nearest ambulance:

```
DISPATCH_POLICY=learned uvicorn app.main:app --no-access-log
```

Back to the normal settings:

```
uvicorn app.main:app --no-access-log
```

---

## 8. Inside the database

In a MySQL session (`mysql -u root -p ambulance_dispatch`).

The four tables:

```
SHOW TABLES;
```

The fleet:

```
SELECT id, call_sign, station_name, status FROM ambulances;
```

The latest incidents. `report_count` is the number of callers, `patients` the number of
injured, and `parent_id` links the rows of one accident:

```
SELECT id, incident_type, severity, status, report_count, patients, parent_id FROM incidents ORDER BY id DESC LIMIT 12;
```

Which ambulance went to which incident, which hospital it used, and the response time:

```
SELECT d.id, d.incident_id, a.call_sign, h.name AS hospital, d.distance_km, d.eta_minutes, d.response_minutes, d.status FROM dispatches d JOIN ambulances a ON a.id = d.ambulance_id LEFT JOIN hospitals h ON h.id = d.hospital_id ORDER BY d.id DESC LIMIT 12;
```

The unique constraint and the foreign keys on `dispatches`:

```
SHOW CREATE TABLE dispatches\G
```

The indexes on `incidents`, including the unique duplicate key:

```
SHOW INDEX FROM incidents;
```

---

## 9. Reinforcement Learning

These need no database and no server.

The simulator's own check. It also checks that the learned policy was trained for the
fleet in the seed data (30 ambulances, 14 hospitals):

```
python -m rl.env
```

The learned policy at its starting weights makes the same choice as the lookahead rule:

```
python -m rl.pg check
```

Nearest, lookahead and learned compared on shifts the model was not trained on:

```
python -m rl.pg eval
```

---

## 10. Two servers with Redis

One server keeps its queue, the duplicate check's lock and its clock in its own memory. With
Redis, several servers share all three and work as one system. Stop the server in Window A
(Ctrl+C) first.

Window D. Start Redis and leave it running:

```
redis-server
```

Window A. The first server, on port 8000:

```
REDIS_URL=redis://127.0.0.1:6379/0 uvicorn app.main:app --port 8000 --no-access-log
```

Window E. The second server, on port 8001:

```
REDIS_URL=redis://127.0.0.1:6379/0 uvicorn app.main:app --port 8001 --no-access-log
```

The first one to start logs `this process now runs the clock`: only that server moves the
ambulances. The other logs `another process runs the clock`. Both log the
`Redis at 127.0.0.1:6379/0` line.

Window B. Send a call to the second server. The `picked up` line can appear in either
server's log, because both take calls from the same queue:

```
curl -s -X POST http://127.0.0.1:8001/incidents -H "Content-Type: application/json" -d '{"lat":30.3255,"lon":78.0413,"severity":"HIGH","incident_type":"CARDIAC"}' | python -m json.tool
```

The health check shows `"redis": "connected"`:

```
curl -s http://127.0.0.1:8001/health | python -m json.tool
```

The queue shows `"backend": "redis"`, and under `clock`, `"leader"` says whether this server
runs the clock:

```
curl -s http://127.0.0.1:8001/queue | python -m json.tool
```

The dashboard in Window C stays on the first server. Its System page shows Redis as
connected, and `Leader` under the clock says whether that server is the one running it.

What Redis holds for this database:

```
redis-cli --scan --pattern 'ambulance:*'
```

The clock's lease: the server holding it, and the seconds before it runs out unless renewed:

```
redis-cli GET ambulance:ambulance_dispatch:lease:clock
```

```
redis-cli TTL ambulance:ambulance_dispatch:lease:clock
```

Every command the servers send to Redis, live (Ctrl+C to stop). Run the demonstration in
another window to see the queue and the lock at work:

```
redis-cli MONITOR
```

Stop the server that runs the clock (Ctrl+C in its window). Within 5 seconds the other one
logs `this process now runs the clock` and the ambulances keep moving.

Measured: two servers without Redis and with it. Stop both servers first; it starts its own
and resets the database:

```
python -m bench.multiserver
```

Back to one server without Redis. Stop both servers, then reset and start as usual:

```
python -m sim.seed --reset
```

```
uvicorn app.main:app --no-access-log
```

---

## 11. With Docker

The server, MySQL and Redis in three containers, started with one command. Stop a server
running in Window A first, because Docker uses port 8000 too. The dashboard in Window C
keeps working, because the server in Docker answers on that same port. On this Mac Docker
runs in Colima, so start that first; it stays on until it is stopped or the Mac restarts.

```
colima start
```

All three containers, from the project folder (the first time takes about two minutes):

```
docker compose up --build -d
```

Each container and whether it is healthy:

```
docker compose ps
```

The server's log, live (Ctrl+C stops the watching, not the server). This is Window A when
the server runs in Docker:

```
docker compose logs -f app
```

The demonstration, from the laptop as usual:

```
python -m demo
```

Or from inside the server's container, which needs nothing installed on the computer:

```
docker compose exec app python -m demo
```

MySQL inside Docker, for the row-lock demonstration of section 5. It is on port 3307, so
use this in place of the `mysql` command there:

```
mysql -h 127.0.0.1 -P 3307 -u root -p ambulance_dispatch
```

What the server keeps in Redis:

```
docker compose exec redis redis-cli --scan --pattern 'ambulance:*'
```

Reset the data: stop the server, reset, start it again:

```
docker compose stop app
```

```
docker compose run --rm app python -m sim.seed --reset
```

```
docker compose start app
```

Stop everything; the data is kept for the next `docker compose up`:

```
docker compose down
```

Stop Colima too, to give its memory back to the Mac:

```
colima stop
```

---

## 12. If something does not work

"REDIS_URL is set but Redis does not answer": Redis is not running. Start it with
`redis-server` in its own window, or start the server without `REDIS_URL`.

"Cannot connect to the Docker daemon": Colima is not running. Start it with `colima start`.

"set DB_PASSWORD in the .env file": the `.env` file has no `DB_PASSWORD`. Copy
`.env.example` to `.env` and set it. "no configuration file provided": `docker compose` was
run outside the project folder.

The reset hangs: a MySQL window still has an open transaction. Type `ROLLBACK;` and
`exit` there.

"Address already in use": a server is still running. Find it:

```
lsof -i :8000
```

The bottom of the dashboard's sidebar says `Server offline`: the server is not running on
port 8000. Start it in Window A.

"vite: command not found" when starting the dashboard: its packages are not installed. Run
`npm install` in the `frontend` folder. Do the same after a pull that changed the dashboard,
then reload the page in the browser.

If port 5173 is taken, the dashboard starts on the next free port (5174) and prints the
address to open.

Start again from a clean state: stop the server, then

```
python -m sim.seed --reset
```
