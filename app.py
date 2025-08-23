from flask import Flask, redirect, url_for, request, session, render_template
import requests, json, os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "ynov-secret"

USERS_FILE = "users.json"
API_KEY = os.getenv("BALL_KEY") or "beec8528-10d0-422f-8178-c1b0988e0639"
API_HEADERS = {"Authorization": API_KEY}
API_BASE = "https://api.balldontlie.io/v1"

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
    from functools import wraps
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

# ---------- PLAYERS (cards) ----------
@app.route('/players')
@login_required
def players():
    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "", type=str)

    url = f"{API_BASE}/players"
    params = {"per_page": 25, "page": page}
    if q:
        params["search"] = q

    try:
        r = requests.get(url, headers=API_HEADERS, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as e:
        return render_template("error.html", message=f"Erreur lors de la récupération des joueurs : {e}")
    except ValueError:
        return render_template("error.html", message="Réponse API non valide (JSON attendu).")

    players = data.get("data", [])
    meta = data.get("meta", {})
    return render_template("players.html", players=players, q=q, meta=meta)

# ---------- PLAYER detail ----------
@app.route('/player/<int:player_id>')
@login_required
def player_detail(player_id):
    url = f"{API_BASE}/players/{player_id}"
    try:
        r = requests.get(url, headers=API_HEADERS, timeout=8)
        r.raise_for_status()
        p = r.json()
    except requests.exceptions.RequestException as e:
        return render_template("error.html", message=f"Erreur lors de la récupération du joueur : {e}")
    except ValueError:
        return render_template("error.html", message="Réponse API non valide (JSON attendu).")

    # S'assurer que 'team' existe pour éviter l'erreur Jinja2
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

# ---------- TEAM placeholder ----------
@app.route('/team/<int:team_id>')
@login_required
def team_redirect(team_id):
    return render_template("error.html", message=f"Page équipe ({team_id}) à implémenter prochainement.")

# ---------- run ----------
if __name__ == "__main__":
    app.run(debug=True)
