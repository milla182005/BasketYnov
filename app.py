from flask import Flask, redirect, url_for, request, session
import requests, json, os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "ynov-secret"

USERS_FILE = "users.json"

# --- Fonction utilitaire pour charger les utilisateurs ---
def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        return json.load(f)

# --- Fonction utilitaire pour sauvegarder les utilisateurs ---
def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

# --- LOGIN ---
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get("email")
        password = request.form.get("password")
        users = load_users()

        if email in users and check_password_hash(users[email]['password'], password):
            session["user"] = email
            return redirect(url_for('players'))
        else:
            return "<p>Login ou mot de passe incorrect</p><a href='/login'>Retour</a>"

    return """
    <h2>Login</h2>
    <form method='post'>
        <label>Email :</label><br>
        <input type='email' name='email' required><br><br>
        
        <label>Mot de passe :</label><br>
        <input type='password' name='password' required><br><br>
        
        <button type='submit'>Se connecter</button>
    </form>
    <p>Pas encore de compte ? <a href='/register'>Créer un compte</a></p>
    """

# --- REGISTER ---
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        email = request.form.get("email")
        password = request.form.get("password")
        users = load_users()

        if email in users:
            return "<p>Utilisateur déjà existant</p><a href='/register'>Retour</a>"

        users[email] = {"password": generate_password_hash(password)}
        save_users(users)
        return "<p>Compte créé avec succès !</p><a href='/login'>Se connecter</a>"

    return """
    <h2>Créer un compte</h2>
    <form method='post'>
        <label>Email :</label><br>
        <input type='email' name='email' required><br><br>
        
        <label>Mot de passe :</label><br>
        <input type='password' name='password' required><br><br>
        
        <button type='submit'>S'inscrire</button>
    </form>
    <p>Déjà un compte ? <a href='/login'>Se connecter</a></p>
    """

# --- PLAYERS ---
@app.route('/players')
def players():
    if not session.get("user"):
        return redirect(url_for('login'))
    r = requests.get("https://www.balldontlie.io/api/v1/players?per_page=10")
    return r.json()

if __name__ == "__main__":
    app.run(debug=True)
