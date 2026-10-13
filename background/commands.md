# Background Demonstration: Commands

Every command needed to show how the system works behind the terminal demonstration.
Run them from the project folder, with the virtual environment active, one at a time.

Use two terminal windows side by side:

* **Window A** runs the server and shows its log.
* **Window B** is where you type everything else.

---

## 1. Start the system

Window A. Build a fresh database (12 ambulances, 4 hospitals, no incidents):

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

---

## 2. How the API works

Leave the server running in Window A and type these in Window B.

Report an emergency. The reply comes back at once with an incident id; it does not wait for
an ambulance:

```
curl -s -X POST http://127.0.0.1:8000/incidents -H "Content-Type: application/json" -d '{"lat":30.2740,"lon":78.0480,"severity":"CRITICAL","incident_type":"ROAD_ACCIDENT","description":"Accident on Haridwar Bypass"}' | python -m json.tool
```

Look the incident up. Use the id from the reply above. It shows the ambulance, and after a
few seconds the hospital:

```
curl -s http://127.0.0.1:8000/incidents/1 | python -m json.tool
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

The queue, the worker threads and the clock:

```
curl -s http://127.0.0.1:8000/queue | python -m json.tool
```

The health check, with the locking mode, scheduler and dispatch policy in use:

```
curl -s http://127.0.0.1:8000/health | python -m json.tool
```

The same API in the browser, with a form for every endpoint:

```
open http://127.0.0.1:8000/docs
```

---

## 3. What to point at in the server log (Window A)

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
| `RE-QUEUED` | A call that had to wait is given another try |

The second column, for example `worker-1` or `sim-clock`, is the thread that did the step.

---

## 4. Row-level locking in MySQL

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

## 5. The race condition, measured

Stop the server first (Ctrl+C in Window A). Each benchmark starts its own server and
resets the database.

Locking. 40 calls at the same instant with locking off, with `FOR UPDATE`, and with
`SKIP LOCKED`. Without locking, ambulances are double-booked:

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

## 6. The same scene with a setting changed

Stop the server, start it again with one setting changed, then run the scene in Window B.
These commands are for macOS and Linux.

Locking switched off. Scene 4 then shows the same ambulance going to several calls:

```
LOCKING_MODE=none RACE_WINDOW_MS=20 uvicorn app.main:app --no-access-log
```

```
python -m demo --scene 4
```

After a run with locking off the data is wrong on purpose, so stop the server and reset:

```
python -m sim.seed --reset
```

First come, first served. In scene 4 the LOW calls are then served before the CRITICAL one:

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

## 7. Inside the database

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

Which ambulance went to which incident and which hospital it used:

```
SELECT d.id, d.incident_id, a.call_sign, h.name AS hospital, d.distance_km, d.eta_minutes, d.status FROM dispatches d JOIN ambulances a ON a.id = d.ambulance_id LEFT JOIN hospitals h ON h.id = d.hospital_id ORDER BY d.id DESC LIMIT 12;
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

## 8. Reinforcement Learning

These need no database and no server.

The simulator's own check:

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

## 9. If something does not work

The reset hangs: a MySQL window still has an open transaction. Type `ROLLBACK;` and
`exit` there.

"Address already in use": a server is still running. Find it:

```
lsof -i :8000
```

Start again from a clean state: stop the server, then

```
python -m sim.seed --reset
```
