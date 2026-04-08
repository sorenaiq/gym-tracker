import sqlite3
from flask import Flask, render_template, request, redirect, url_for, g, jsonify, Response
from datetime import datetime, date, timedelta

DATABASE = 'gym_tracker.db'
app = Flask(__name__)
app.config['DATABASE'] = DATABASE

MUSCLE_GROUPS = ['Chest', 'Back', 'Shoulders', 'Biceps', 'Triceps', 'Legs', 'Core', 'Cardio', 'Full Body', 'Other']

REP_RANGES = {
    'Chest': (8, 12),
    'Back': (8, 12),
    'Shoulders': (8, 12),
    'Biceps': (10, 15),
    'Triceps': (10, 15),
    'Legs': (6, 10),
    'Core': (10, 20),
    'Cardio': (20, 30),
    'Full Body': (8, 12),
    'Other': (8, 12),
}

# Exercises matching these name patterns (case-insensitive) are treated as timed
TIMED_PATTERNS = ['plank', 'wall sit', 'wall-sit', 'l-sit', 'l sit', 'dead hang',
                  'hollow hold', 'v-up', 'v up', 'lateral hold', 'side plank',
                  'handstand hold', 'pistol squat', 'shrimp squat']

def is_timed_exercise(name):
    """Returns True if exercise name suggests it tracks time, not reps."""
    n = name.lower()
    return any(p in n for p in TIMED_PATTERNS)

# ─── Database helpers ─────────────────────────────────────────────────────────

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            muscle_group TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS workout_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            ended INTEGER DEFAULT 0,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS session_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            exercise_id INTEGER NOT NULL,
            note TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES workout_sessions(id),
            FOREIGN KEY (exercise_id) REFERENCES exercises(id),
            UNIQUE(session_id, exercise_id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_exercise_id INTEGER NOT NULL,
            set_number INTEGER NOT NULL,
            reps INTEGER NOT NULL,
            weight REAL NOT NULL,
            rpe REAL,
            is_pr INTEGER DEFAULT 0,
            duration_seconds INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_exercise_id) REFERENCES session_exercises(id)
        )
    ''')
    # Migration: add rpe column if missing (existing DBs)
    try:
        db.execute('SELECT added_at FROM session_exercises LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE session_exercises ADD COLUMN added_at TIMESTAMP DEFAULT 0')
    # Migration: add rpe column if missing (existing DBs)
    try:
        db.execute('SELECT rpe FROM sets LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE sets ADD COLUMN rpe REAL')
    # Migration: add duration_seconds column if missing
    try:
        db.execute('SELECT duration_seconds FROM sets LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE sets ADD COLUMN duration_seconds INTEGER')
    # Migration: add is_timed column to exercises if missing
    try:
        db.execute('SELECT is_timed FROM exercises LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE exercises ADD COLUMN is_timed INTEGER DEFAULT 0')
    # Migration: add rest_seconds column if missing
    try:
        db.execute('SELECT rest_seconds FROM exercises LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE exercises ADD COLUMN rest_seconds INTEGER DEFAULT 90')
    # Migration: add is_favorite column if missing
    try:
        db.execute('SELECT is_favorite FROM exercises LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE exercises ADD COLUMN is_favorite INTEGER DEFAULT 0')
    # Migration: add superset_group column if missing
    try:
        db.execute('SELECT superset_group FROM session_exercises LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE session_exercises ADD COLUMN superset_group INTEGER')
    # Migration: add is_bodyweight column if missing
    try:
        db.execute('SELECT is_bodyweight FROM exercises LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE exercises ADD COLUMN is_bodyweight INTEGER DEFAULT 0')
    # Migration: add parent_set_id column if missing
    try:
        db.execute('SELECT parent_set_id FROM sets LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE sets ADD COLUMN parent_set_id INTEGER REFERENCES sets(id)')
    # Migration: add rating column to workout_sessions if missing
    try:
        db.execute('SELECT rating FROM workout_sessions LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE workout_sessions ADD COLUMN rating INTEGER')
    # Migration: add tags column to workout_sessions if missing
    try:
        db.execute('SELECT tags FROM workout_sessions LIMIT 1')
    except Exception as e:
        db.execute('ALTER TABLE workout_sessions ADD COLUMN tags TEXT')
    # Migration: add difficulty column to exercises if missing
    try:
        db.execute('SELECT difficulty FROM exercises LIMIT 1')
    except Exception as e:
        db.execute("ALTER TABLE exercises ADD COLUMN difficulty TEXT DEFAULT 'intermediate'")
    # Migration: add workout_templates table
    db.execute('''
        CREATE TABLE IF NOT EXISTS workout_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            exercise_names TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.commit()

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    db = get_db()
    today = date.today()
    active_session = db.execute('''
        SELECT * FROM workout_sessions WHERE ended = 0 ORDER BY id DESC LIMIT 1
    ''').fetchone()
    exercises = db.execute('''
        SELECT e.*, 
               (SELECT COUNT(*) FROM session_exercises se WHERE se.exercise_id = e.id) as times_used,
               (SELECT MAX(ws.date) FROM workout_sessions ws JOIN session_exercises se ON se.session_id = ws.id WHERE se.exercise_id = e.id AND ws.ended = 1) as last_used,
               (SELECT MAX(s.weight) FROM sets s JOIN session_exercises se ON se.id = s.session_exercise_id JOIN workout_sessions ws ON ws.id = se.session_id WHERE se.exercise_id = e.id AND ws.ended = 1) as best_weight
        FROM exercises e 
        ORDER BY e.is_favorite DESC, e.name ASC
    ''').fetchall()

    # Volume trend data for the last 8 weeks
    from datetime import timedelta
    week_labels = []
    week_volumes = []
    for i in range(7, -1, -1):
        w_end = today - timedelta(weeks=i)
        w_start = w_end - timedelta(days=6)
        rows = db.execute('''
            SELECT SUM(s.reps * s.weight) as vol
            FROM sets s
            JOIN session_exercises se ON se.id = s.session_exercise_id
            JOIN workout_sessions ws ON ws.id = se.session_id
            WHERE ws.ended = 1 AND ws.date >= ? AND ws.date <= ?
        ''', (w_start.isoformat(), w_end.isoformat())).fetchone()
        vol = rows['vol'] if rows and rows['vol'] else 0
        week_labels.append(w_start.strftime('%b %d'))
        week_volumes.append(int(vol))

    has_enough_data = sum(week_volumes) > 0

    # Last workout recap: most recent ended session
    last_workout = None
    last_session = db.execute('''
        SELECT id, date FROM workout_sessions
        WHERE ended = 1
        ORDER BY date DESC, id DESC LIMIT 1
    ''').fetchone()
    if last_session:
        days_ago = (today - date.fromisoformat(last_session['date'])).days
        if days_ago == 0:
            days_str = 'Today'
        elif days_ago == 1:
            days_str = 'Yesterday'
        else:
            days_str = f'{days_ago} days ago'
        # Get top 3 exercises with their top set
        rows = db.execute('''
            SELECT e.name,
                   (SELECT s.reps FROM sets s WHERE s.session_exercise_id = se.id ORDER BY s.set_number ASC LIMIT 1) as reps,
                   (SELECT s.weight FROM sets s WHERE s.session_exercise_id = se.id ORDER BY s.set_number ASC LIMIT 1) as weight
            FROM session_exercises se
            JOIN exercises e ON e.id = se.exercise_id
            WHERE se.session_id = ?
            ORDER BY se.id ASC
            LIMIT 3
        ''', (last_session['id'],)).fetchall()
        last_workout = {
            'days_str': days_str,
            'exercises': []
        }
        for r in rows:
            if r['reps'] and r['weight'] is not None:
                last_workout['exercises'].append(f"{r['name']} {r['reps']}×{int(r['weight'])}")

    # Progressive overload: per-exercise volume this week vs last week
    import datetime as dt
    this_week_start = today - dt.timedelta(days=today.weekday())
    last_week_start = this_week_start - dt.timedelta(weeks=1)
    last_week_end = this_week_start - dt.timedelta(days=1)

    # This week's volume per exercise
    this_week_rows = db.execute('''
        SELECT e.id, e.name, SUM(s.reps * s.weight) as vol
        FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        JOIN exercises e ON e.id = se.exercise_id
        WHERE ws.ended = 1 AND ws.date >= ? AND ws.date <= ? AND s.duration_seconds IS NULL
        GROUP BY e.id
    ''', (this_week_start.isoformat(), today.isoformat())).fetchall()
    this_week_vol = {r['id']: int(r['vol'] or 0) for r in this_week_rows}

    # Last week's volume per exercise
    last_week_rows = db.execute('''
        SELECT e.id, e.name, SUM(s.reps * s.weight) as vol
        FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        JOIN exercises e ON e.id = se.exercise_id
        WHERE ws.ended = 1 AND ws.date >= ? AND ws.date <= ? AND s.duration_seconds IS NULL
        GROUP BY e.id
    ''', (last_week_start.isoformat(), last_week_end.isoformat())).fetchall()
    last_week_vol = {r['id']: int(r['vol'] or 0) for r in last_week_rows}

    # Build progressive overload list for exercises that appear in either week
    progressive_overload = []
    for ex in exercises:
        eid = ex['id']
        tw = this_week_vol.get(eid, 0)
        lw = last_week_vol.get(eid, 0)
        if tw > 0 or lw > 0:
            if lw > 0:
                pct = round((tw - lw) / lw * 100, 1)
                if tw > lw:
                    arrow = '↑'
                elif tw < lw:
                    arrow = '↓'
                else:
                    arrow = '→'
            else:
                pct = None
                arrow = '↑'
            progressive_overload.append({
                'id': eid,
                'name': ex['name'],
                'this_week': tw,
                'last_week': lw,
                'pct': pct,
                'arrow': arrow,
            })

    # Sort by this week's volume descending
    progressive_overload.sort(key=lambda x: x['this_week'], reverse=True)


    # Muscle group recovery heatmap: last 7 days, most recent session date per muscle group
    from datetime import timedelta
    seven_days_ago = (today - timedelta(days=7)).isoformat()
    muscle_last_hit = {}
    rows = db.execute('''
        SELECT e.muscle_group, MAX(ws.date) as last_date
        FROM session_exercises se
        JOIN exercises e ON e.id = se.exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        WHERE ws.ended = 1 AND ws.date >= ?
        GROUP BY e.muscle_group
    ''', (seven_days_ago,)).fetchall()
    for r in rows:
        if r['muscle_group']:
            muscle_last_hit[r['muscle_group']] = date.fromisoformat(r['last_date'])

    # Recent PRs (last 5)
    recent_prs = db.execute('''
        SELECT s.weight, s.reps, e.name, ws.date FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        JOIN exercises e ON e.id = se.exercise_id
        WHERE s.is_pr = 1 AND ws.ended = 1
        ORDER BY ws.date DESC, s.id DESC LIMIT 5
    ''').fetchall()

    return render_template('index.html', exercises=exercises, active_session=active_session,
                         muscle_groups=MUSCLE_GROUPS, week_labels=week_labels,
                         week_volumes=week_volumes, has_enough_data=has_enough_data,
                         last_workout=last_workout,
                         muscle_last_hit=muscle_last_hit, today=today,
                         recent_prs=recent_prs, progressive_overload=progressive_overload)

@app.route('/session/start', methods=['POST'])
def start_session():
    db = get_db()
    today = date.today().isoformat()
    cur = db.execute('SELECT id FROM workout_sessions WHERE date = ? AND ended = 0 ORDER BY id DESC LIMIT 1', (today,))
    existing = cur.fetchone()
    if existing:
        session_id = existing['id']
    else:
        db.execute('INSERT INTO workout_sessions (date) VALUES (?)', (today,))
        db.commit()
        session_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    return redirect(url_for('workout', session_id=session_id))

@app.route('/session/<int:session_id>')
def workout(session_id):
    db = get_db()
    session = db.execute('SELECT * FROM workout_sessions WHERE id = ?', (session_id,)).fetchone()
    if not session:
        return redirect(url_for('index'))

    session_exercises = db.execute('''
        SELECT se.id as se_id, e.id as eid, e.name, e.muscle_group, se.note, se.id,
               se.superset_group,
               (SELECT COUNT(*) FROM sets s WHERE s.session_exercise_id = se.id) as set_count
        FROM session_exercises se
        JOIN exercises e ON e.id = se.exercise_id
        WHERE se.session_id = ?
        ORDER BY se.superset_group IS NOT NULL, se.superset_group, se.id ASC
    ''', (session_id,)).fetchall()

    exercises_data = []
    for se in session_exercises:
        sets = db.execute('SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_number', (se['se_id'],)).fetchall()

        # Get previous session's sets (most recent session that actually has sets)
        prev_sets_raw = db.execute('''
            SELECT s.* FROM sets s
            JOIN session_exercises se ON se.id = s.session_exercise_id
            JOIN workout_sessions ws ON ws.id = se.session_id
            WHERE se.exercise_id = ? AND ws.id < ? AND ws.ended = 1
            ORDER BY ws.id DESC, s.set_number ASC
            LIMIT 10
        ''', (se['eid'], session_id)).fetchall()

        # Get PRs for this exercise
        prs = db.execute('''
            SELECT MAX(weight) as max_weight, MAX(reps) as max_reps FROM sets s
            JOIN session_exercises se ON se.id = s.session_exercise_id
            JOIN workout_sessions ws ON ws.id = se.session_id
            WHERE se.exercise_id = ? AND ws.ended = 1
        ''', (se['eid'],)).fetchone()

        # Determine if this exercise is timed (per-exercise flag from DB, or pattern match)
        ex_timed = is_timed_exercise(se['name'])

        exercises_data.append({
            'se_id': se['se_id'],
            'eid': se['eid'],
            'name': se['name'],
            'muscle_group': se['muscle_group'],
            'note': se['note'],
            'added_at': se['id'],
            'superset_group': se['superset_group'],
            'sets': [dict(s) for s in sets],
            'prev_sets': [dict(s) for s in prev_sets_raw],
            'pr_weight': prs['max_weight'] if prs else None,
            'pr_reps': prs['max_reps'] if prs else None,
            'is_timed': ex_timed,
        })

    # Workout summary
    total_sets = sum(len(ex['sets']) for ex in exercises_data)
    total_volume = sum(sum(s['reps'] * s['weight'] for s in ex['sets']) for ex in exercises_data)
    prs_hit = sum(1 for ex in exercises_data for s in ex['sets'] if s.get('is_pr'))
    exercises_done = len(exercises_data)

    summary = {
        'exercises': exercises_done,
        'sets': total_sets,
        'volume': int(total_volume),
        'prs': prs_hit
    } if exercises_done > 0 else None

    all_exercises = db.execute('SELECT * FROM exercises ORDER BY name ASC').fetchall()
    all_exercises_data = [{
        'id': e['id'],
        'name': e['name'],
        'muscle_group': e['muscle_group'],
        'is_timed': bool(e['is_timed']) or is_timed_exercise(e['name']),
    } for e in all_exercises]
    # Find previous session for comparison
    prev_session = None
    prev_session_row = db.execute('''
        SELECT id, date FROM workout_sessions
        WHERE ended = 1 AND id != ?
        ORDER BY date DESC, id DESC LIMIT 1
    ''', (session_id,)).fetchone()
    if prev_session_row:
        prev_se = db.execute('''
            SELECT se.id as se_id, e.id as eid, e.name, e.muscle_group,
                   (SELECT COUNT(*) FROM sets s WHERE s.session_exercise_id = se.id) as set_count
            FROM session_exercises se
            JOIN exercises e ON e.id = se.exercise_id
            WHERE se.session_id = ?
            ORDER BY se.id ASC
        ''', (prev_session_row['id'],)).fetchall()
        prev_sets_total = 0
        prev_volume = 0
        prev_exercise_names = []
        for pse in prev_se:
            s_rows = db.execute('''
                SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_number
            ''', (pse['se_id'],)).fetchall()
            prev_sets_total += len(s_rows)
            for sr in s_rows:
                if not is_timed_exercise(pse['name']):
                    prev_volume += (sr['reps'] or 0) * (sr['weight'] or 0)
            prev_exercise_names.append({'name': pse['name'], 'sets': len(s_rows)})
        prev_session = {
            'id': prev_session_row['id'],
            'date': prev_session_row['date'],
            'sets': prev_sets_total,
            'volume': int(prev_volume),
            'exercises': prev_exercise_names,
        }

    return render_template('workout.html', session=session, exercises_data=exercises_data,
                         all_exercises=all_exercises_data, summary=summary, muscle_groups=MUSCLE_GROUPS,
                         rep_ranges=REP_RANGES, prev_session=prev_session)

@app.route('/session/<int:session_id>/add_exercise', methods=['POST'])
def add_exercise_to_session(session_id):
    db = get_db()
    exercise_name = request.form['exercise_name'].strip()
    muscle_group = request.form.get('muscle_group', '')
    if not exercise_name:
        return redirect(url_for('workout', session_id=session_id))

    ex = db.execute('SELECT id, muscle_group FROM exercises WHERE name = ?', (exercise_name,)).fetchone()
    if not ex:
        db.execute('INSERT INTO exercises (name, muscle_group) VALUES (?, ?)', (exercise_name, muscle_group))
        db.commit()
        ex = db.execute('SELECT id, muscle_group FROM exercises WHERE name = ?', (exercise_name,)).fetchone()
    else:
        if not ex['muscle_group'] and muscle_group:
            db.execute('UPDATE exercises SET muscle_group = ? WHERE id = ?', (muscle_group, ex['id']))
            db.commit()

    try:
        db.execute('INSERT INTO session_exercises (session_id, exercise_id) VALUES (?, ?)',
                  (session_id, ex['id']))
        db.commit()
    except sqlite3.IntegrityError:
        pass
    return redirect(url_for('workout', session_id=session_id))

@app.route('/api/suggested_exercises')
def api_suggested_exercises():
    """Return the top 3 most-used exercises for a given muscle group."""
    db = get_db()
    mg = request.args.get('muscle_group', '')
    if not mg:
        return jsonify([])
    rows = db.execute('''
        SELECT e.id, e.name,
               COUNT(se.id) as times_used
        FROM exercises e
        LEFT JOIN session_exercises se ON se.exercise_id = e.id
        WHERE e.muscle_group = ?
        GROUP BY e.id
        ORDER BY times_used DESC, e.name ASC
        LIMIT 3
    ''', (mg,)).fetchall()
    return jsonify([{'id': r['id'], 'name': r['name'], 'times_used': r['times_used']} for r in rows])

@app.route('/session/<int:session_id>/exercise/<int:se_id>/superset', methods=['POST'])
def superset_exercise(session_id, se_id):
    """Pair this exercise with the next unsupersetted exercise as a superset.
    If already in a group, remove the entire group (toggle)."""
    db = get_db()
    cur = db.execute('SELECT id, superset_group FROM session_exercises WHERE id = ? AND session_id = ?', (se_id, session_id)).fetchone()
    if not cur:
        return redirect(url_for('workout', session_id=session_id))

    # If already in a superset, remove the group (toggle off)
    if cur['superset_group']:
        db.execute('UPDATE session_exercises SET superset_group = NULL WHERE superset_group = ?', (cur['superset_group'],))
        db.commit()
        return redirect(url_for('workout', session_id=session_id))

    # Find the next exercise in session order that is NOT in a superset
    next_ex = db.execute('''
        SELECT id FROM session_exercises
        WHERE session_id = ? AND id != ? AND superset_group IS NULL
        ORDER BY added_at ASC LIMIT 1
    ''', (session_id, se_id)).fetchone()

    if not next_ex:
        return redirect(url_for('workout', session_id=session_id))

    group = se_id
    db.execute('UPDATE session_exercises SET superset_group = ? WHERE id = ?', (group, se_id))
    db.execute('UPDATE session_exercises SET superset_group = ? WHERE id = ?', (group, next_ex['id']))
    db.commit()
    return redirect(url_for('workout', session_id=session_id))

@app.route('/set/<int:set_id>/delete', methods=['POST'])
def delete_set(set_id):
    db = get_db()
    se = db.execute('SELECT session_id FROM session_exercises WHERE id = (SELECT session_exercise_id FROM sets WHERE id = ?)', (set_id,)).fetchone()
    db.execute('DELETE FROM sets WHERE id = ?', (set_id,))
    db.commit()
    if se:
        return redirect(url_for('workout', session_id=se['session_id']))
    return redirect(url_for('index'))

@app.route('/set/add', methods=['POST'])
def add_set():
    db = get_db()
    se_id = request.form['session_exercise_id']
    session_id = request.form['session_id']
    exercise_id = request.form['exercise_id']
    set_number = int(request.form['set_number'])
    duration_seconds = request.form.get('duration_seconds', '')
    duration_seconds = int(duration_seconds) if duration_seconds else None

    is_timed = duration_seconds is not None

    reps = int(request.form.get('reps', 1) or 1) if not is_timed else 1
    weight = float(request.form.get('weight', 0) or 0) if not is_timed else 0
    rpe = request.form.get('rpe', '')
    rpe = float(rpe) if rpe else None

    # PR detection: for timed exercises compare duration, for rep-based compare weight
    if is_timed:
        best = db.execute('''
            SELECT MAX(s.duration_seconds) as best FROM sets s
            JOIN session_exercises se ON se.id = s.session_exercise_id
            JOIN workout_sessions ws ON ws.id = se.session_id
            WHERE se.exercise_id = ? AND ws.ended = 1 AND s.duration_seconds IS NOT NULL
        ''', (exercise_id,)).fetchone()
        is_pr = 1 if (not best['best'] or duration_seconds > best['best']) else 0
    else:
        best = db.execute('''
            SELECT MAX(s.weight) as best FROM sets s
            JOIN session_exercises se ON se.id = s.session_exercise_id
            JOIN workout_sessions ws ON ws.id = se.session_id
            WHERE se.exercise_id = ? AND ws.ended = 1
        ''', (exercise_id,)).fetchone()
        is_pr = 1 if (not best['best'] or weight > best['best']) else 0

    db.execute('INSERT INTO sets (session_exercise_id, set_number, reps, weight, rpe, is_pr, duration_seconds) VALUES (?, ?, ?, ?, ?, ?, ?)',
               (se_id, set_number, reps, weight, rpe, is_pr, duration_seconds))
    db.commit()

    # Superset clear flow: if this set belongs to a superset pair, check if both exercises have
    # at least one set logged. If so, clear the superset group so the pair is done.
    se = db.execute('SELECT superset_group FROM session_exercises WHERE id = ?', (se_id,)).fetchone()
    if se and se['superset_group']:
        group = se['superset_group']
        group_ses = db.execute('SELECT id FROM session_exercises WHERE superset_group = ?', (group,)).fetchall()
        if len(group_ses) == 2:
            all_have_sets = all(
                db.execute('SELECT COUNT(*) as cnt FROM sets WHERE session_exercise_id = ?', (g['id'],)).fetchone()['cnt'] > 0
                for g in group_ses
            )
            if all_have_sets:
                db.execute('UPDATE session_exercises SET superset_group = NULL WHERE superset_group = ?', (group,))
                db.commit()

    return redirect(url_for('workout', session_id=session_id))

@app.route('/session/<int:session_id>/remove_exercise/<int:se_id>', methods=['POST'])
def remove_exercise(session_id, se_id):
    db = get_db()
    db.execute('DELETE FROM sets WHERE session_exercise_id = ?', (se_id,))
    db.execute('DELETE FROM session_exercises WHERE id = ?', (se_id,))
    db.commit()
    return redirect(url_for('workout', session_id=session_id))

@app.route('/session/<int:session_id>/reorder', methods=['POST'])
def reorder_exercises(session_id):
    """Reorder exercises within a session. Moves dragged_se_id before or after target_se_id."""
    db = get_db()
    data = request.get_json() or {}
    dragged_se_id = data.get('dragged_se_id')
    target_se_id = data.get('target_se_id')
    insert_before = data.get('insert_before', True)

    if not dragged_se_id or not target_se_id:
        return jsonify({'error': 'Missing se_id'}), 400

    # Verify both belong to this session
    dragged = db.execute('SELECT added_at FROM session_exercises WHERE id = ? AND session_id = ?', (dragged_se_id, session_id)).fetchone()
    target = db.execute('SELECT added_at FROM session_exercises WHERE id = ? AND session_id = ?', (target_se_id, session_id)).fetchone()
    if not dragged or not target:
        return jsonify({'error': 'Exercise not found in session'}), 404

    # Get all session_exercises ordered by added_at
    all_ses = db.execute('SELECT id, added_at FROM session_exercises WHERE session_id = ? ORDER BY added_at ASC', (session_id,)).fetchall()

    # Build new ordered list
    ids = [dict(se)['id'] for se in all_ses]
    dragged_idx = ids.index(dragged_se_id)
    target_idx = ids.index(target_se_id)

    # Remove dragged from current position
    ids.pop(dragged_idx)
    # Insert at new position
    new_target_idx = ids.index(target_se_id)
    if insert_before:
        ids.insert(new_target_idx, dragged_se_id)
    else:
        ids.insert(new_target_idx + 1, dragged_se_id)

    # Assign new added_at timestamps spaced 10 seconds apart to preserve order
    now = datetime.now()
    for i, se_id in enumerate(ids):
        offset = i * 10  # 10 seconds between each
        new_added_at = datetime(now.year, now.month, now.day, now.hour, now.minute, now.second) + timedelta(seconds=offset)
        db.execute('UPDATE session_exercises SET added_at = ? WHERE id = ?', (new_added_at.isoformat(), se_id))

    db.commit()
    return jsonify({'ok': True})



@app.route('/session/<int:session_id>/end', methods=['POST'])
def end_workout(session_id):
    db = get_db()
    db.execute('UPDATE workout_sessions SET ended = 1 WHERE id = ?', (session_id,))
    db.commit()
    return redirect(url_for('index'))

@app.route('/session/<int:session_id>/summary')
def workout_summary(session_id):
    db = get_db()
    session = db.execute('SELECT * FROM workout_sessions WHERE id = ?', (session_id,)).fetchone()
    if not session:
        return redirect(url_for('index'))
    
    session_exercises = db.execute('''
        SELECT se.id as se_id, e.id as eid, e.name, e.muscle_group
        FROM session_exercises se
        JOIN exercises e ON e.id = se.exercise_id
        WHERE se.session_id = ?
    ''', (session_id,)).fetchall()

    data = []
    total_volume = 0
    total_duration = 0
    for se in session_exercises:
        sets = db.execute('SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_number', (se['se_id'],)).fetchall()
        sets_dict = [dict(s) for s in sets]
        ex_timed = is_timed_exercise(se['name'])
        volume = sum(s['reps'] * s['weight'] for s in sets_dict if not s.get('duration_seconds'))
        duration = sum(s['duration_seconds'] for s in sets_dict if s.get('duration_seconds'))
        prs = sum(1 for s in sets_dict if s['is_pr'])
        total_volume += volume
        total_duration += duration
        # Find best set: highest weight * reps (volume), skip timed
        best_set_number = None
        if sets_dict and not ex_timed:
            best_vol = -1
            for s in sets_dict:
                vol = (s.get('weight') or 0) * (s.get('reps') or 0)
                if vol > best_vol:
                    best_vol = vol
                    best_set_number = s['set_number']
        data.append({
            'name': se['name'],
            'muscle_group': se['muscle_group'],
            'sets': sets_dict,
            'volume': int(volume),
            'duration': duration,
            'prs': prs,
            'is_timed': ex_timed,
            'best_set_number': best_set_number,
        })

    total_sets = sum(len(d['sets']) for d in data)
    total_prs = sum(d['prs'] for d in data)

    return render_template('summary.html', session=session, data=data, 
                         total_volume=total_volume, total_sets=total_sets, total_prs=total_prs,
                         total_duration=total_duration)

# API
@app.route('/api/exercises')
def api_exercises():
    db = get_db()
    q = request.args.get('q', '')
    exercises = db.execute('''
        SELECT e.*, 
               (SELECT MAX(s.weight) FROM sets s JOIN session_exercises se ON se.id = s.session_exercise_id JOIN workout_sessions ws ON ws.id = se.session_id WHERE se.exercise_id = e.id AND ws.ended = 1) as best_weight
        FROM exercises e 
        WHERE e.name LIKE ? 
        ORDER BY e.name LIMIT 20
    ''', (f'%{q}%',)).fetchall()
    return jsonify([{
        'id': e['id'],
        'name': e['name'],
        'muscle_group': e['muscle_group'],
        'best_weight': e['best_weight'],
        'is_timed': bool(e['is_timed']) or is_timed_exercise(e['name']),
    } for e in exercises])

@app.route('/api/weekly_summary')
def api_weekly_summary():
    """Returns weekly stats (Mon–Sun) for the current week."""
    db = get_db()
    today = date.today()
    # Find Monday of current week
    monday = today - __import__('datetime').timedelta(days=today.weekday())
    sunday = monday + __import__('datetime').timedelta(days=6)

    sessions = db.execute('''
        SELECT id FROM workout_sessions
        WHERE ended = 1 AND date >= ? AND date <= ?
    ''', (monday.isoformat(), sunday.isoformat())).fetchall()

    session_ids = [s['id'] for s in sessions]
    workouts_completed = len(session_ids)

    if not session_ids:
        return jsonify({
            'total_sets': 0,
            'total_volume': 0,
            'workouts_completed': 0,
            'prs_hit': 0,
            'week_start': monday.isoformat(),
            'week_end': sunday.isoformat(),
        })

    placeholders = ','.join('?' * len(session_ids))
    sets_rows = db.execute(f'''
        SELECT s.* FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        WHERE se.session_id IN ({placeholders})
    ''', session_ids).fetchall()

    total_sets = len(sets_rows)
    total_volume = sum(s['reps'] * s['weight'] for s in sets_rows if not s.get('duration_seconds'))
    prs_hit = sum(1 for s in sets_rows if s.get('is_pr'))

    return jsonify({
        'total_sets': total_sets,
        'total_volume': int(total_volume),
        'workouts_completed': workouts_completed,
        'prs_hit': prs_hit,
        'week_start': monday.isoformat(),
        'week_end': sunday.isoformat(),
    })

@app.route('/api/warmup')
def api_warmup():
    """Return suggested warmup sets for a working weight.
    Query param: weight (float, required)
    Returns: [{"percent": 40, "reps": 5, "weight": 90}, ...]
    5 reps at 40%W, 3 reps at 60%W, 1 rep at 80%W
    """
    try:
        w = float(request.args.get('weight', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'weight query param required and must be numeric'}), 400
    if w <= 0:
        return jsonify({'error': 'weight must be positive'}), 400

    sets = [
        {'percent': 40, 'reps': 5,  'weight': round(w * 0.4, 1)},
        {'percent': 60, 'reps': 3,  'weight': round(w * 0.6, 1)},
        {'percent': 80, 'reps': 1,  'weight': round(w * 0.8, 1)},
    ]
    return jsonify(sets)

@app.route('/history')
def workout_history():
    """Show all past workout sessions (ended=1), most recent first."""
    import json
    db = get_db()
    sessions_raw = db.execute('''
        SELECT ws.*,
               (SELECT COUNT(*) FROM session_exercises se WHERE se.session_id = ws.id) as exercise_count,
               (SELECT COUNT(*) FROM sets s JOIN session_exercises se ON se.id = s.session_exercise_id WHERE se.session_id = ws.id) as set_count,
               (SELECT SUM(s.reps * s.weight) FROM sets s JOIN session_exercises se ON se.id = s.session_exercise_id WHERE se.session_id = ws.id AND s.duration_seconds IS NULL) as volume,
               (SELECT COUNT(*) FROM sets s JOIN session_exercises se ON se.id = s.session_exercise_id WHERE se.session_id = ws.id AND s.is_pr = 1) as prs
        FROM workout_sessions ws
        WHERE ws.ended = 1
        ORDER BY ws.date DESC, ws.id DESC
    ''').fetchall()
    # Parse tags JSON for each session
    sessions = []
    for s in sessions_raw:
        sd = dict(s)
        try:
            sd['tags'] = json.loads(sd['tags']) if sd.get('tags') else []
        except (ValueError, TypeError):
            sd['tags'] = []
        sessions.append(sd)
    return render_template('history.html', sessions=sessions)

@app.route('/records')
def personal_records():
    """Show all-time personal records for every exercise."""
    db = get_db()
    # Get best weight, best reps, and best single-session volume for each exercise
    records = db.execute('''
        SELECT
            e.id,
            e.name,
            (SELECT MAX(s.weight) FROM sets s
             JOIN session_exercises se ON se.id = s.session_exercise_id
             JOIN workout_sessions ws ON ws.id = se.session_id
             WHERE se.exercise_id = e.id AND ws.ended = 1 AND s.weight > 0) as best_weight,
            (SELECT MAX(s.reps) FROM sets s
             JOIN session_exercises se ON se.id = s.session_exercise_id
             JOIN workout_sessions ws ON ws.id = se.session_id
             WHERE se.exercise_id = e.id AND ws.ended = 1) as best_reps,
            (SELECT SUM(s.reps * s.weight) FROM sets s
             JOIN session_exercises se ON se.id = s.session_exercise_id
             WHERE se.exercise_id = e.id AND se.session_id IN
               (SELECT id FROM workout_sessions WHERE ended = 1)
               AND s.duration_seconds IS NULL
             GROUP BY se.session_id
             ORDER BY SUM(s.reps * s.weight) DESC
             LIMIT 1) as best_volume,
            (SELECT ws2.date FROM sets s
             JOIN session_exercises se ON se.id = s.session_exercise_id
             JOIN workout_sessions ws2 ON ws2.id = se.session_id
             WHERE se.exercise_id = e.id AND ws2.ended = 1 AND s.duration_seconds IS NULL
             GROUP BY se.session_id
             ORDER BY SUM(s.reps * s.weight) DESC
             LIMIT 1) as volume_date
        FROM exercises e
        WHERE EXISTS (
            SELECT 1 FROM sets s
            JOIN session_exercises se ON se.id = s.session_exercise_id
            JOIN workout_sessions ws ON ws.id = se.session_id
            WHERE se.exercise_id = e.id AND ws.ended = 1
        )
        ORDER BY e.name ASC
    ''').fetchall()
    return render_template('records.html', records=records)


@app.route('/exercise/<int:exercise_id>/favorite', methods=['POST'])
def toggle_favorite(exercise_id):
    """Toggle is_favorite for an exercise. Returns JSON."""
    db = get_db()
    ex = db.execute('SELECT id, is_favorite FROM exercises WHERE id = ?', (exercise_id,)).fetchone()
    if not ex:
        return jsonify({'error': 'Not found'}), 404
    new_val = 0 if ex['is_favorite'] else 1
    db.execute('UPDATE exercises SET is_favorite = ? WHERE id = ?', (new_val, exercise_id))
    db.commit()
    return jsonify({'id': exercise_id, 'is_favorite': new_val})



@app.route('/exercises/by-muscle')
def exercises_by_muscle():
    db = get_db()
    # Get all exercises grouped by muscle_group
    rows = db.execute('''
        SELECT e.id, e.name, e.muscle_group, e.is_favorite,
               (SELECT COUNT(*) FROM session_exercises se WHERE se.exercise_id = e.id) as times_used,
               (SELECT MAX(s.weight) FROM sets s JOIN session_exercises se ON se.id = s.session_exercise_id JOIN workout_sessions ws ON ws.id = se.session_id WHERE se.exercise_id = e.id AND ws.ended = 1) as best_weight
        FROM exercises e 
        ORDER BY e.muscle_group ASC, e.name ASC
    ''').fetchall()

    # Group by muscle_group
    grouped = {}
    for row in rows:
        mg = row['muscle_group'] or 'Other'
        if mg not in grouped:
            grouped[mg] = []
        grouped[mg].append(dict(row))

    return render_template('muscles.html', grouped=grouped, muscle_groups=MUSCLE_GROUPS)


@app.route('/api/exercise/<int:exercise_id>/rest_time', methods=['POST'])
def api_exercise_rest_time(exercise_id):
    """Store the actual rest time taken after a set for an exercise."""
    db = get_db()
    data = request.get_json() or {}
    rest_seconds = data.get('rest_seconds')
    if rest_seconds is not None:
        rest_seconds = int(rest_seconds)
        db.execute('UPDATE exercises SET rest_seconds = ? WHERE id = ?', (rest_seconds, exercise_id))
        db.commit()
    ex = db.execute('SELECT id, name, rest_seconds FROM exercises WHERE id = ?', (exercise_id,)).fetchone()
    return jsonify({'id': ex['id'], 'name': ex['name'], 'rest_seconds': ex['rest_seconds']})

@app.route('/api/exercise/<int:exercise_id>/progress')
def api_progress(exercise_id):
    db = get_db()
    # Get all sets with their timestamps for rest-time calculation
    rows = db.execute('''
        SELECT ws.date, s.weight, s.reps, s.set_number, s.rpe, s.created_at
        FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        WHERE se.exercise_id = ? AND ws.ended = 1
        ORDER BY ws.date ASC, s.set_number ASC
    ''', (exercise_id,)).fetchall()

    # Group sets by date for top-set + rest-time computation
    from collections import defaultdict
    by_date = {}
    for r in rows:
        d = r['date']
        if d not in by_date:
            by_date[d] = {'weight': 0, 'reps': 0, 'rpe': None, 'created_ats': []}
        by_date[d]['created_ats'].append(r['created_at'])
        score = r['weight'] * r['reps']
        best_score = by_date[d]['weight'] * by_date[d]['reps']
        if score >= best_score:
            by_date[d]['weight'] = r['weight']
            by_date[d]['reps'] = r['reps']
            by_date[d]['rpe'] = r['rpe']

    # Compute average rest time per session (avg seconds between consecutive sets)
    from datetime import datetime
    for d in by_date:
        ats = sorted(by_date[d]['created_ats'])
        if len(ats) >= 2:
            diffs = []
            for i in range(1, len(ats)):
                t1 = datetime.fromisoformat(ats[i-1])
                t2 = datetime.fromisoformat(ats[i])
                diffs.append((t2 - t1).total_seconds())
            by_date[d]['avg_rest'] = round(sum(diffs) / len(diffs))
        else:
            by_date[d]['avg_rest'] = None

    dates = sorted(by_date.keys())
    result = []
    for d in dates:
        result.append({
            'date': d,
            'weight': by_date[d]['weight'],
            'reps': by_date[d]['reps'],
            'rpe': by_date[d]['rpe'],
            'avg_rest': by_date[d]['avg_rest'],
        })
    return jsonify(result)


@app.route('/exercise/<int:exercise_id>/chart')
def exercise_chart(exercise_id):
    db = get_db()
    ex = db.execute('SELECT * FROM exercises WHERE id = ?', (exercise_id,)).fetchone()
    if not ex:
        return redirect(url_for('index'))

    # Get all completed sessions for this exercise
    rows = db.execute('''
        SELECT ws.date, s.weight, s.reps, s.rpe
        FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        WHERE se.exercise_id = ? AND ws.ended = 1
        ORDER BY ws.date ASC, s.set_number ASC
    ''', (exercise_id,)).fetchall()

    # Group by date, calculate top set (best combo of weight × reps) per session
    by_date = {}
    for r in rows:
        d = r['date']
        if d not in by_date:
            by_date[d] = {'weight': 0, 'reps': 0, 'rpe': None}
        # Best set = highest weight × reps combo
        score = r['weight'] * r['reps']
        best_score = by_date[d]['weight'] * by_date[d]['reps']
        if score >= best_score:
            by_date[d] = {'weight': r['weight'], 'reps': r['reps'], 'rpe': r['rpe']}

    dates = sorted(by_date.keys())
    weights = [by_date[d]['weight'] for d in dates]
    reps_list = [by_date[d]['reps'] for d in dates]
    rpes = [by_date[d]['rpe'] for d in dates]

    return render_template('exercise_chart.html',
                         ex=ex, dates=dates, weights=weights,
                         reps_list=reps_list, rpes=rpes)

BAR_WEIGHT = 45
AVAILABLE_PLATES = [45, 35, 25, 10, 5, 2.5]

@app.route('/api/plates')
def api_plates():
    """Return plate breakdown for a target weight.
    Query param: weight (float or int)
    Returns: {"bar": 45, "each_side": [45, 25]}"""
    try:
        target = float(request.args.get('weight', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'weight query param required and must be numeric'}), 400
    if target < BAR_WEIGHT:
        return jsonify({'error': f'target weight must be at least {BAR_WEIGHT} (bar weight)'}), 400

    per_side = (target - BAR_WEIGHT) / 2
    plates = []
    for plate in AVAILABLE_PLATES:
        while per_side >= plate:
            plates.append(plate)
            per_side -= plate

    return jsonify({'bar': BAR_WEIGHT, 'each_side': plates})


@app.route('/plates')
def plates():
    return render_template('plates.html')


@app.route('/api/pace')
def api_pace():
    """Return pace breakdown for a given distance and total time.
    Query params: distance (float, miles), time (str 'MM:SS' or 'H:MM:SS')
    Returns: { pace_per_mile: '8:24', pace_per_km: '5:13', distance, total_seconds }
    """
    try:
        distance = float(request.args.get('distance', 0))
        time_str = request.args.get('time', '')
    except (TypeError, ValueError):
        return jsonify({'error': 'distance and time are required'}), 400
    if distance <= 0 or not time_str:
        return jsonify({'error': 'distance must be positive, time required'}), 400

    # Parse time string
    parts = time_str.strip().split(':')
    try:
        if len(parts) == 2:
            total_seconds = int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            total_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        else:
            return jsonify({'error': 'time must be MM:SS or H:MM:SS format'}), 400
    except (ValueError, IndexError):
        return jsonify({'error': 'invalid time format'}), 400

    if total_seconds <= 0:
        return jsonify({'error': 'time must be positive'}), 400

    pace_mile = total_seconds / distance
    pace_km = pace_mile / 1.60934

    def fmt(secs):
        m = int(secs // 60)
        s = int(secs % 60)
        return f'{m}:{s:02d}'

    return jsonify({
        'distance': distance,
        'total_seconds': total_seconds,
        'pace_per_mile': fmt(pace_mile),
        'pace_per_km': fmt(pace_km),
        'time': time_str,
    })

@app.route('/pace')
def pace():
    return render_template('pace.html')

@app.route('/warmup')
def warmup():
    return render_template('warmup.html')

@app.route('/reset', methods=['POST'])
def reset_db():
    db = get_db()
    db.execute('DROP TABLE IF EXISTS sets')
    db.execute('DROP TABLE IF EXISTS session_exercises')
    db.execute('DROP TABLE IF EXISTS workout_sessions')
    db.execute('DROP TABLE IF EXISTS exercises')
    db.commit()
    init_db()
    return redirect(url_for('index'))

@app.route('/export/json')
def export_json():
    """Download all workout data as a JSON file."""
    db = get_db()

    # All sessions
    sessions = db.execute('''
        SELECT * FROM workout_sessions WHERE ended = 1 ORDER BY date ASC
    ''').fetchall()

    # All exercises
    exercises = db.execute('SELECT * FROM exercises ORDER BY name ASC').fetchall()

    # All session_exercises
    session_exercises = db.execute('''
        SELECT se.*, e.name as exercise_name, e.muscle_group
        FROM session_exercises se
        JOIN exercises e ON e.id = se.exercise_id
    ''').fetchall()

    # All sets
    sets = db.execute('''
        SELECT s.*, se.session_id, e.name as exercise_name, e.muscle_group, ws.date
        FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN exercises e ON e.id = se.exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        ORDER BY ws.date ASC, s.set_number ASC
    ''').fetchall()

    import json
    data = {
        'sessions': [dict(s) for s in sessions],
        'exercises': [dict(e) for e in exercises],
        'session_exercises': [dict(se) for se in session_exercises],
        'sets': [dict(s) for s in sets],
    }

    return Response(
        json.dumps(data, indent=2, default=str),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=gym_tracker_export.json'}
    )

@app.route('/export/csv')
def export_csv():
    """Download all sets as a CSV file."""
    db = get_db()
    sets = db.execute('''
        SELECT ws.date, e.name as exercise, e.muscle_group,
               s.reps, s.weight, s.rpe, s.duration_seconds, s.is_pr
        FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN exercises e ON e.id = se.exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        ORDER BY ws.date ASC, s.set_number ASC
    ''').fetchall()

    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['date', 'exercise', 'muscle_group', 'reps', 'weight', 'rir', 'duration_seconds', 'is_pr'])

    for s in sets:
        writer.writerow([
            s['date'],
            s['exercise'],
            s['muscle_group'] or '',
            s['reps'],
            s['weight'],
            s['rpe'] if s['rpe'] is not None else '',
            s['duration_seconds'] if s['duration_seconds'] is not None else '',
            s['is_pr'],
        ])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=gym_tracker_export.csv'}
    )

with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
