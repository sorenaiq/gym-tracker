# Gym Tracker — Spec

## What
A web app for tracking gym workouts. Enter exercises, log sets/reps/weight, and see your previous performance when doing an exercise again.

## Stack
- **Backend:** Python + Flask + SQLite (hosted on PythonAnywhere)
- **Frontend:** HTML + vanilla JS (no framework)

## Data Model
```
exercises
  - id (PK)
  - name (UNIQUE)
  - created_at

workout_sessions
  - id (PK)
  - exercise_id (FK)
  - date
  - created_at

sets
  - id (PK)
  - workout_session_id (FK)
  - set_number (INT)
  - reps (INT)
  - weight (FLOAT)
  - created_at
```

## API Routes
| Method | Route | Description |
|--------|-------|-------------|
| GET / | | Home — list exercises + recent sessions |
| POST /exercise | | Create new exercise |
| GET /exercise/<id>/history | | Get all sessions for an exercise |
| POST /session | | Start new workout session for an exercise |
| POST /set | | Add a set to a session |

## Features
1. **Add exercise** — type the name, it's saved to DB
2. **Log a workout** — pick exercise, enter sets (set#, reps, weight)
3. **Previous data** — when selecting an exercise, show last session's sets
4. **Session history** — view all past sessions for any exercise

## Pages
- **Home** — exercise list + quick-add form
- **Exercise detail** — history of all sessions for that exercise

## Auth
None for now (local use / simple deployment)
