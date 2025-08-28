from flask import Flask, redirect, url_for, request, session, render_template
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import os, json, time

import requests
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# -------------------- Flask --------------------
app = Flask(__name__)
app.secret_key = "ynov-secret"

API_KEY = os.getenv("BALL_KEY") or "beec8528-10d0-422f-8178-c1b0988e0639"
API_HEADERS = {"Authorization": API_KEY}
API_BASE = "https://api.balldontlie.io/v1"

CACHE_TTL_MINUTES = 10   # durée de vie du cache (par page) pour éviter 429
PER_PAGE_PLAYERS = 25
PER_PAGE_GAMES = 5

# -------------------- Database --------------------
Base = declarative_base()
DB_URL = "sqlite:///basket.db"
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)  # même id que l’API
    abbreviation = Column(String(20))
    full_name = Column(String(255))
    city = Column(String(255))
    conference = Column(String(50))
    division = Column(String(50))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)  # même id que l’API
    first_name = Column(String(255))
    last_name = Column(String(255))
    team_id = Column(Integer, ForeignKey("teams.id"))
    position = Column(String(10))
    height = Column(String(50))
    weight = Column(String(50))
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("Team")

class Game(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True)  # même id que l’API
    date = Column(String(50))  # iso string fournie par l’API
    season = Column(Integer)
    period = Column(Integer)
    status = Column(String(50))
    time = Column(String(50))
    postseason = Column(String(10))
    home_team_id = Column(Integer, ForeignKey("teams.id"))
    visitor_team_id = Column(Integer, ForeignKey("teams.id"))
    home_team_score = Column(Integer)
    visitor_team_score = Column(Integer)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    home_team = relationship("Team", foreign_keys=[home_team_id])
    visitor_team = relationship("Team", foreign_keys=[visitor_team_id])

class CachePage(Base):
    """
    Stocke la page brute renvoyée par l’API pour un endpoint + (page, query).
    Permet de renvoyer exactement la même structure JSON aux templates
    (y compris meta.previous_page / meta.next_page) sans retaper l’API.
    """
    __tablename__ = "cache_pages"
    id = Column(Integer, primary_key=True)
    resource = Column(String(50), index=True)     # "players" | "games" | "teams"
    page = Column(Integer, default=1)
    query = Column(String(255), default="")       # ex: search pour players
    json_blob = Column(Text)                      # réponse API complète
    fetched_at = Column(DateTime, default=datetime.utcnow, index=True)

Base.metadata.create_all(engine)

# -------------------- Helpers --------------------
def db_session():
    return SessionLocal()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def api_get(url, params=None, timeout=8):
    """Wrapper requests.get avec erreurs gérées."""
    r = requests.get(url, headers=API_HEADERS, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()

def cache_key(resource, page, query=""):
    query = (query or "").strip().lower()
    return resource, int(page), query

def load_cache(resource, page=1, query=""):
    """Charge une page en cache si pas expirée."""
    resource, page, query = cache_key(resource, page, query)
    with db_session() as s:
        row = (
            s.query(CachePage)
            .filter(CachePage.resource == resource,
                    CachePage.page == page,
                    CachePage.query == query)
            .first()
        )
        if not row:
            return None
        # Vérifie l’expiration
        if datetime.utcnow() - row.fetched_at > timedelta(minutes=CACHE_TTL_MINUTES):
            return None
        try:
            return json.loads(row.json_blob)
        except Exception:
            return None

def save_cache(resource, page, query, json_data):
    """Sauve/écrase le cache pour (resource, page, query)."""
    payload = json.dumps(json_data)
    with db_session() as s:
        row = (
            s.query(CachePage)
            .filter(CachePage.resource == resource,
                    CachePage.page == page,
                    CachePage.query == (query or "").strip().lower())
            .first()
        )
        if row:
            row.json_blob = payload
            row.fetched_at = datetime.utcnow()
        else:
            row = CachePage(
                resource=resource,
                page=int(page),
                query=(query or "").strip().lower(),
                json_blob=payload,
                fetched_at=datetime.utcnow()
            )
            s.add(row)
        s.commit()

def upsert_team(s, t):
    if not t:
        return None
    team = s.get(Team, t.get("id"))
    if not team:
        team = Team(id=t["id"])
    team.abbreviation = t.get("abbreviation")
    team.full_name = t.get("full_name")
    team.city = t.get("city")
    team.conference = t.get("conference")
    team.division = t.get("division")
    s.merge(team)
    return team

def upsert_player(s, p):
    if not p:
        return None
    player = s.get(Player, p.get("id"))
    if not player:
        player = Player(id=p["id"])
    player.first_name = p.get("first_name")
    player.last_name = p.get("last_name")
    player.position = p.get("position")
    player.height = str(p.get("height")) if p.get("height") else None
    player.weight = str(p.get("weight")) if p.get("weight") else None
    # team
    t = p.get("team")
    if t and t.get("id"):
        player.team_id = t["id"]
    s.merge(player)
    return player

def upsert_game(s, g):
    if not g:
        return None
    game = s.get(Game, g.get("id"))
    if not game:
        game = Game(id=g["id"])
    game.date = g.get("date")
    game.season = g.get("season")
    game.period = g.get("period")
    game.status = g.get("status")
    game.time = g.get("time")
    game.postseason = str(g.get("postseason"))
    game.home_team_id = g.get("home_team", {}).get("id")
    game.visitor_team_id = g.get("visitor_team", {}).get("id")
    game.home_team_score = g.get("home_team_score")
    game.visitor_team_score = g.get("visitor_team_score")
    s.merge(game)
    return game

def normalize_meta(meta):
    """Assure la présence de clés attendues par les templates."""
    meta = meta or {}
    # balldontlie fournit au minimum: current_page, next_page, per_page, total_count (selon endpoint)
    # On sécurise:
    cp = meta.get("current_page") or meta.get("page") or 1
    meta["current_page"] = cp
    meta["previous_page"] = meta.get("previous_page") or (cp - 1 if int(cp) > 1 else None)
    # next_page peut déjà être fourni. Si pas là, on le reconstruit très prudemment:
    if "next_page" not in meta:
        # On devine: si 'per_page' et 'total_count' existent
        per = meta.get("per_page")
        tot = meta.get("total_count")
        if per and tot:
            total_pages = (int(tot) + int(per) - 1) // int(per)
            meta["next_page"] = cp + 1 if cp < total_pages else None
        else:
            # sinon on laisse None (les templates testent la présence)
            meta["next_page"] = None
    return meta

# -------------------- Auth --------------------
@app.route("/")
def home():
    return redirect(url_for("players"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with db_session() as s:
            u = s.query(User).filter(User.email == email).first()
            if u and check_password_hash(u.password_hash, password):
                session["user"] = email
                return redirect(url_for("players"))
        return render_template("auth_message.html", message="Login ou mot de passe incorrect", back="/login")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not email or not password:
            return render_template("auth_message.html", message="Email et mot de passe requis.", back="/register")
        with db_session() as s:
            if s.query(User).filter(User.email == email).first():
                return render_template("auth_message.html", message="Utilisateur déjà existant", back="/register")
            u = User(email=email, password_hash=generate_password_hash(password))
            s.add(u)
            s.commit()
        return render_template("auth_message.html", message="Compte créé avec succès !", back="/login")
    return render_template("register.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# -------------------- Players --------------------
@app.route("/players")
@login_required
def players():
    page = request.args.get("page", 1, type=int)
    q = request.args.get("q", "", type=str).strip()

    # 1) chercher en cache persistant
    cached = load_cache("players", page, q)
    if cached:
        data = cached
    else:
        # 2) appeler l’API puis stocker en cache et en base
        url = f"{API_BASE}/players"
        params = {"per_page": PER_PAGE_PLAYERS, "page": page}
        if q:
            params["search"] = q
        try:
            data = api_get(url, params=params)
        except requests.exceptions.RequestException as e:
            # fallback : dernière version en cache (même si expirée)
            with db_session() as s:
                row = (
                    s.query(CachePage)
                    .filter(CachePage.resource == "players",
                            CachePage.page == page,
                            CachePage.query == q.lower())
                    .order_by(CachePage.fetched_at.desc())
                    .first()
                )
                if row:
                    data = json.loads(row.json_blob)
                else:
                    return render_template("error.html", message=f"Erreur lors de la récupération des joueurs : {e}")

        # upsert en base depuis la réponse API
        with db_session() as s:
            for p in data.get("data", []):
                # upsert team d’abord (si présent dans player)
                if p.get("team"):
                    upsert_team(s, p["team"])
                upsert_player(s, p)
            s.commit()

        save_cache("players", page, q, data)

    players_list = data.get("data", [])
    meta = normalize_meta(data.get("meta", {}))
    # le template players.html utilise: meta.previous_page / meta.next_page / q
    return render_template("players.html", players=players_list, q=q, meta=meta)

@app.route("/player/<int:player_id>")
@login_required
def player_detail(player_id):
    # Essayer DB d’abord
    with db_session() as s:
        p = s.get(Player, player_id)
        if p:
            # reconstruire un dict comme l’API pour compatibilité template
            payload = {
                "id": p.id,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "position": p.position,
                "height": p.height,
                "weight": p.weight,
                "team": None
            }
            if p.team:
                payload["team"] = {
                    "id": p.team.id,
                    "full_name": p.team.full_name,
                    "abbreviation": p.team.abbreviation,
                    "city": p.team.city,
                    "conference": p.team.conference,
                    "division": p.team.division
                }
            return render_template("player.html", p=payload)

    # Sinon API (et mise à jour DB)
    url = f"{API_BASE}/players/{player_id}"
    try:
        pdata = api_get(url)
    except requests.exceptions.RequestException as e:
        return render_template("error.html", message=f"Erreur lors de la récupération du joueur : {e}")

    with db_session() as s:
        if pdata.get("team"):
            upsert_team(s, pdata["team"])
        upsert_player(s, pdata)
        s.commit()

    return render_template("player.html", p=pdata)

# -------------------- Teams --------------------
@app.route("/teams")
@login_required
def teams():
    # Teams sans pagination → on met en cache unique 'page 1' (API retourne tout)
    cached = load_cache("teams", 1, "")
    if cached:
        teams_data = cached
    else:
        url = f"{API_BASE}/teams"
        try:
            teams_data = api_get(url)
        except requests.exceptions.RequestException as e:
            return render_template("error.html", message=f"Erreur lors de la récupération des équipes : {e}")

        with db_session() as s:
            for t in teams_data.get("data", []):
                upsert_team(s, t)
            s.commit()
        save_cache("teams", 1, "", teams_data)

    teams_list = teams_data.get("data", [])
    return render_template("teams.html", teams=teams_list)

@app.route("/team/<int:team_id>")
@login_required
def team_detail(team_id):
    # DB d’abord
    with db_session() as s:
        t = s.get(Team, team_id)
        if t:
            team_payload = {
                "id": t.id,
                "abbreviation": t.abbreviation,
                "full_name": t.full_name,
                "city": t.city,
                "conference": t.conference,
                "division": t.division
            }
            # joueurs de l’équipe depuis DB (si on en a déjà)
            players = (
                s.query(Player)
                .filter(Player.team_id == team_id)
                .order_by(Player.last_name.asc(), Player.first_name.asc())
                .all()
            )
            players_payload = [
                {
                    "id": p.id,
                    "first_name": p.first_name,
                    "last_name": p.last_name
                } for p in players
            ]
            # Si on n’a aucun joueur en base, tenter l’API pour enrichir
            if not players_payload:
                url = f"{API_BASE}/players"
                params = {"per_page": 100, "team_ids[]": team_id}
                try:
                    pdata = api_get(url, params=params)
                    for p in pdata.get("data", []):
                        upsert_player(s, p)
                    s.commit()
                    players_payload = [
                        {"id": p["id"], "first_name": p["first_name"], "last_name": p["last_name"]}
                        for p in pdata.get("data", [])
                    ]
                except:
                    pass
            return render_template("team.html", team=team_payload, players=players_payload)

    # Sinon API pour l’équipe, puis en DB
    url = f"{API_BASE}/teams/{team_id}"
    try:
        team_data = api_get(url)
    except requests.exceptions.RequestException as e:
        return render_template("error.html", message=f"Erreur lors de la récupération de l'équipe : {e}")

    with db_session() as s:
        upsert_team(s, team_data)
        s.commit()

    # joueurs de l’équipe via API
    players_url = f"{API_BASE}/players"
    params = {"per_page": 100, "team_ids[]": team_id}
    players_payload = []
    try:
        pdata = api_get(players_url, params=params)
        with db_session() as s:
            for p in pdata.get("data", []):
                upsert_player(s, p)
            s.commit()
        players_payload = [
            {"id": p["id"], "first_name": p["first_name"], "last_name": p["last_name"]}
            for p in pdata.get("data", [])
        ]
    except:
        pass

    return render_template("team.html", team=team_data, players=players_payload)

# -------------------- Games (Matches) --------------------
@app.route("/matches")
@login_required
def matches():
    page = request.args.get("page", 1, type=int)

    # 1) cache persistant (par page)
    cached = load_cache("games", page, "")
    if cached:
        data = cached
    else:
        # 2) API
        url = f"{API_BASE}/games"
        params = {"per_page": PER_PAGE_GAMES, "page": page}
        try:
            data = api_get(url, params=params)
        except requests.exceptions.RequestException as e:
            # fallback cache le plus récent (même page)
            with db_session() as s:
                row = (
                    s.query(CachePage)
                    .filter(CachePage.resource == "games",
                            CachePage.page == page,
                            CachePage.query == "")
                    .order_by(CachePage.fetched_at.desc())
                    .first()
                )
                if row:
                    data = json.loads(row.json_blob)
                else:
                    return render_template("error.html", message=f"Erreur lors de la récupération des matchs : {e}")

        # upsert DB depuis réponse API (équipes + matchs)
        with db_session() as s:
            for g in data.get("data", []):
                if g.get("home_team"):
                    upsert_team(s, g["home_team"])
                if g.get("visitor_team"):
                    upsert_team(s, g["visitor_team"])
                upsert_game(s, g)
            s.commit()

        save_cache("games", page, "", data)

    matches_list = data.get("data", [])
    meta = normalize_meta(data.get("meta", {}))
    # matches.html s’attend à un dict match avec ['id'], ['home_team']['full_name'], etc.
    return render_template("matches.html", matches=matches_list, meta=meta)

@app.route("/match/<int:match_id>")
@login_required
def match_detail(match_id):
    # DB d’abord
    with db_session() as s:
        g = s.get(Game, match_id)
        if g:
            payload = {
                "id": g.id,
                "date": g.date,
                "season": g.season,
                "period": g.period,
                "status": g.status,
                "time": g.time,
                "postseason": g.postseason,
                "home_team_score": g.home_team_score,
                "visitor_team_score": g.visitor_team_score,
                "home_team": None,
                "visitor_team": None
            }
            if g.home_team:
                payload["home_team"] = {
                    "id": g.home_team.id,
                    "full_name": g.home_team.full_name,
                    "abbreviation": g.home_team.abbreviation,
                }
            if g.visitor_team:
                payload["visitor_team"] = {
                    "id": g.visitor_team.id,
                    "full_name": g.visitor_team.full_name,
                    "abbreviation": g.visitor_team.abbreviation,
                }
            return render_template("match.html", match=payload)

    # Sinon API → DB
    url = f"{API_BASE}/games/{match_id}"
    try:
        gdata = api_get(url)
        # format du /games/{id} peut être direct dict (ou {data: ...} selon version)
        if "data" in gdata:
            gdata = gdata["data"]
    except requests.exceptions.RequestException as e:
        return render_template("error.html", message=f"Erreur lors de la récupération du match : {e}")

    with db_session() as s:
        if gdata.get("home_team"):
            upsert_team(s, gdata["home_team"])
        if gdata.get("visitor_team"):
            upsert_team(s, gdata["visitor_team"])
        upsert_game(s, gdata)
        s.commit()

    return render_template("match.html", match=gdata)

# -------------------- Run --------------------
if __name__ == "__main__":
    app.run(debug=True)
