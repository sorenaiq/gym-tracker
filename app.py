import sqlite3
from flask import Flask, render_template, request, redirect, url_for, g, jsonify
from datetime import datetime, date

DATABASE = 'gym_tracker.db'
app = Flask(__name__)
app.config['DATABASE'] = DATABASE

MUSCLE_GROUPS = ['Chest', 'Back', 'Shoulders', 'Biceps', 'Triceps', 'Legs', 'Core', 'Cardio', 'Full Body', 'Other']

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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS session_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            exercise_id INTEGER NOT NULL,
            note TEXT,
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
            is_pr INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_exercise_id) REFERENCES session_exercises(id)
        )
    ''')
    db.commit()

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    db = get_db()
    active_session = db.execute('''
        SELECT * FROM workout_sessions WHERE ended = 0 ORDER BY id DESC LIMIT 1
    ''').fetchone()
    exercises = db.execute('''
        SELECT e.*, 
               (SELECT COUNT(*) FROM session_exercises se WHERE se.exercise_id = e.id) as times_used,
               (SELECT MAX(ws.date) FROM workout_sessions ws JOIN session_exercises se ON se.session_id = ws.id WHERE se.exercise_id = e.id AND ws.ended = 1) as last_used
        FROM exercises e 
        ORDER BY e.name ASC
    ''').fetchall()
    return render_template('index.html', exercises=exercises, active_session=active_session, muscle_groups=MUSCLE_GROUPS)

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
        SELECT se.id as se_id, e.id as eid, e.name, e.muscle_group, se.note,
               (SELECT COUNT(*) FROM sets s WHERE s.session_exercise_id = se.id) as set_count
        FROM session_exercises se
        JOIN exercises e ON e.id = se.exercise_id
        WHERE se.session_id = ?
        ORDER BY se.id ASC
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
        ''', (se['eid'], session_id)).fetchall()

        # Get PRs for this exercise
        prs = db.execute('''
            SELECT MAX(weight) as max_weight, MAX(reps) as max_reps FROM sets s
            JOIN session_exercises se ON se.id = s.session_exercise_id
            JOIN workout_sessions ws ON ws.id = se.session_id
            WHERE se.exercise_id = ? AND ws.ended = 1
        ''', (se['eid'],)).fetchone()

        exercises_data.append({
            'se_id': se['se_id'],
            'eid': se['eid'],
            'name': se['name'],
            'muscle_group': se['muscle_group'],
            'note': se['note'],
            'sets': [dict(s) for s in sets],
            'prev_sets': [dict(s) for s in prev_sets_raw],
            'pr_weight': prs['max_weight'] if prs else None,
            'pr_reps': prs['max_reps'] if prs else None,
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
    return render_template('workout.html', session=session, exercises_data=exercises_data,
                         all_exercises=all_exercises, summary=summary, muscle_groups=MUSCLE_GROUPS)

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
    elif not ex['muscle_group'] and muscle_group:
        db.execute('UPDATE exercises SET muscle_group = ? WHERE id = ?', (muscle_group, ex['id']))
        db.commit()

    try:
        db.execute('INSERT INTO session_exercises (session_id, exercise_id) VALUES (?, ?)',
                  (session_id, ex['id']))
        db.commit()
    except sqlite3.IntegrityError:
        pass
    return redirect(url_for('workout', session_id=session_id))

@app.route('/set/add', methods=['POST'])
def add_set():
    db = get_db()
    se_id = request.form['session_exercise_id']
    session_id = request.form['session_id']
    exercise_id = request.form['exercise_id']
    set_number = int(request.form['set_number'])
    reps = int(request.form['reps'])
    weight = float(request.form['weight'])

    # Check if this is a PR
    best = db.execute('''
        SELECT MAX(s.weight) as best FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        WHERE se.exercise_id = ? AND ws.ended = 1
    ''', (exercise_id,)).fetchone()
    is_pr = 1 if (not best['best'] or weight > best['best']) else 0

    db.execute('INSERT INTO sets (session_exercise_id, set_number, reps, weight, is_pr) VALUES (?, ?, ?, ?, ?)',
               (se_id, set_number, reps, weight, is_pr))
    db.commit()
    return redirect(url_for('workout', session_id=session_id))

@app.route('/note/update', methods=['POST'])
def update_note():
    db = get_db()
    se_id = request.form['session_exercise_id']
    session_id = request.form['session_id']
    note = request.form['note'].strip()
    db.execute('UPDATE session_exercises SET note = ? WHERE id = ?', (note, se_id))
    db.commit()
    return redirect(url_for('workout', session_id=session_id))

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
    for se in session_exercises:
        sets = db.execute('SELECT * FROM sets WHERE session_exercise_id = ? ORDER BY set_number', (se['se_id'],)).fetchall()
        volume = sum(s['reps'] * s['weight'] for s in sets)
        prs = sum(1 for s in sets if s['is_pr'])
        data.append({
            'name': se['name'],
            'muscle_group': se['muscle_group'],
            'sets': [dict(s) for s in sets],
            'volume': int(volume),
            'prs': prs
        })

    total_volume = sum(d['volume'] for d in data)
    total_sets = sum(len(d['sets']) for d in data)
    total_prs = sum(d['prs'] for d in data)

    return render_template('summary.html', session=session, data=data, 
                         total_volume=total_volume, total_sets=total_sets, total_prs=total_prs)

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
        'best_weight': e['best_weight']
    } for e in exercises])

@app.route('/api/exercise/<int:exercise_id>/progress')
def api_progress(exercise_id):
    db = get_db()
    rows = db.execute('''
        SELECT ws.date, s.weight, s.reps, s.set_number
        FROM sets s
        JOIN session_exercises se ON se.id = s.session_exercise_id
        JOIN workout_sessions ws ON ws.id = se.session_id
        WHERE se.exercise_id = ? AND ws.ended = 1
        ORDER BY ws.date ASC, s.set_number ASC
    ''', (exercise_id,)).fetchall()
    return jsonify([dict(r) for r in rows])

if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
