from flask import Flask, redirect, url_for, request, session, render_template
import requests, json, os, time
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.secret_key = "ynov-secret"

USERS_FILE = "users.json"
API_KEY = os.getenv("BALL_KEY") or "beec8528-10d0-422f-8178-c1b0988e0639"
API_HEADERS = {"Authorization": API_KEY}
API_BASE = "https://api.balldontlie.io/v1"

# ---------- cache persistant ----------
CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def cache_load(key):
    filepath = os.path.join(CACHE_DIR, f"{key}.json")
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def cache_save(key, data):
    filepath = os.path.join(CACHE_DIR, f"{key}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# ---------- utils ----------
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {}

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=4)

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

# ---------- auth ----------
@app.route("/")
def home():
    return redirect(url_for("players"))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        users = load_users()
        if email in users and check_password_hash(users[email]['password'], password):
            session["user"] = email
            return redirect(url_for('players'))
        return render_template("auth_message.html", message="Login ou mot de passe incorrect", back="/login")
    return render_template("login.html")

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        users = load_users()
        if not email or not password:
            return render_template("auth_message.html", message="Email et mot de passe requis.", back="/register")
        if email in users:
            return render_template("auth_message.html", message="Utilisateur déjà existant", back="/register")
        users[email] = {"password": generate_password_hash(password)}
        save_users(users)
        return render_template("auth_message.html", message="Compte créé avec succès !", back="/login")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------- PLAYERS ----------
@app.route('/players')
@login_required
def players():
    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "", type=str)
    cache_key = f"players_page{page}_q{q}"

    data = cache_load(cache_key)
    if not data:
        url = f"{API_BASE}/players"
        params = {"per_page": 25, "page": page}
        if q:
            params["search"] = q
        try:
            r = requests.get(url, headers=API_HEADERS, params=params, timeout=8)
            r.raise_for_status()
            data = r.json()
            cache_save(cache_key, data)
        except requests.exceptions.RequestException as e:
            return render_template("error.html", message=f"Erreur lors de la récupération des joueurs : {e}")
        except ValueError:
            return render_template("error.html", message="Réponse API non valide (JSON attendu).")

    players = data.get("data", [])
    meta = data.get("meta", {})
    return render_template("players.html", players=players, q=q, meta=meta)

@app.route('/player/<int:player_id>')
@login_required
def player_detail(player_id):
    cache_key = f"player_{player_id}"
    p = cache_load(cache_key)
    if not p:
        url = f"{API_BASE}/players/{player_id}"
        try:
            r = requests.get(url, headers=API_HEADERS, timeout=8)
            r.raise_for_status()
            p = r.json()
            cache_save(cache_key, p)
        except requests.exceptions.RequestException as e:
            return render_template("error.html", message=f"Erreur lors de la récupération du joueur : {e}")
        except ValueError:
            return render_template("error.html", message="Réponse API non valide (JSON attendu).")

    if "team" not in p or not p["team"]:
        p["team"] = {
            "id": 0,
            "full_name": "Inconnu",
            "abbreviation": "?",
            "city": "?",
            "conference": "?",
            "division": "?"
        }

    return render_template("player.html", p=p)

# ---------- TEAMS ----------
@app.route('/teams')
@login_required
def teams():
    cache_key = "teams"
    teams = cache_load(cache_key)
    if not teams:
        url = f"{API_BASE}/teams"
        try:
            r = requests.get(url, headers=API_HEADERS, timeout=8)
            r.raise_for_status()
            data = r.json()
            teams = data.get("data", [])
            cache_save(cache_key, teams)
        except requests.exceptions.RequestException as e:
            return render_template("error.html", message=f"Erreur lors de la récupération des équipes : {e}")
        except ValueError:
            return render_template("error.html", message="Réponse API non valide (JSON attendu).")
    return render_template("teams.html", teams=teams)

@app.route('/team/<int:team_id>')
@login_required
def team_detail(team_id):
    cache_key = f"team_{team_id}"
    team = cache_load(cache_key)
    if not team:
        url = f"{API_BASE}/teams/{team_id}"
        try:
            r = requests.get(url, headers=API_HEADERS, timeout=8)
            r.raise_for_status()
            team = r.json()
            cache_save(cache_key, team)
        except:
            team = {}
    players_cache_key = f"team_players_{team_id}"
    players = cache_load(players_cache_key)
    if not players:
        players_url = f"{API_BASE}/players"
        params = {"per_page": 100, "team_ids[]": team_id}
        try:
            r = requests.get(players_url, headers=API_HEADERS, params=params, timeout=8)
            r.raise_for_status()
            data = r.json()
            players = data.get("data", [])
            cache_save(players_cache_key, players)
        except:
            players = []
    return render_template("team.html", team=team, players=players)

# ---------- MATCHES ----------
@app.route('/matches')
@login_required
def matches():
    page = request.args.get("page", 1, type=int)
    cache_key = f"matches_page{page}"

    data = cache_load(cache_key)
    if not data:
        url = f"{API_BASE}/games"
        params = {"per_page": 5, "page": page}
        try:
            r = requests.get(url, headers=API_HEADERS, params=params, timeout=8)
            r.raise_for_status()
            data = r.json()
            cache_save(cache_key, data)
        except requests.exceptions.RequestException as e:
            return render_template("error.html", message=f"Erreur lors de la récupération des matchs : {e}")
        except ValueError:
            return render_template("error.html", message="Réponse API non valide (JSON attendu).")

    matches = data.get("data", [])
    meta = data.get("meta", {})
    return render_template("matches.html", matches=matches, meta=meta)

@app.route('/match/<int:match_id>')
@login_required
def match_detail(match_id):
    cache_key = f"match_{match_id}"
    match_data = cache_load(cache_key)
    if not match_data:
        url = f"{API_BASE}/games/{match_id}"
        try:
            r = requests.get(url, headers=API_HEADERS, timeout=8)
            r.raise_for_status()
            match_data = r.json().get("data", {})
            cache_save(cache_key, match_data)
        except:
            match_data = {}

    # Récupérer les équipes complètes
    home_team_id = match_data.get("home_team", {}).get("id", 0)
    visitor_team_id = match_data.get("visitor_team", {}).get("id", 0)

    home_team = cache_load(f"team_{home_team_id}") or {}
    visitor_team = cache_load(f"team_{visitor_team_id}") or {}
    if not home_team and home_team_id:
        try:
            r = requests.get(f"{API_BASE}/teams/{home_team_id}", headers=API_HEADERS, timeout=8)
            r.raise_for_status()
            home_team = r.json()
            cache_save(f"team_{home_team_id}", home_team)
        except:
            home_team = {}
    if not visitor_team and visitor_team_id:
        try:
            r = requests.get(f"{API_BASE}/teams/{visitor_team_id}", headers=API_HEADERS, timeout=8)
            r.raise_for_status()
            visitor_team = r.json()
            cache_save(f"team_{visitor_team_id}", visitor_team)
        except:
            visitor_team = {}

    match_data["home_team_full"] = home_team
    match_data["visitor_team_full"] = visitor_team

    return render_template("match.html", match=match_data)

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(debug=True)
