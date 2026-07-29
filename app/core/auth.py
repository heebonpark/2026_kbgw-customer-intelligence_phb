import streamlit as st
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime

APP_DIR = os.path.expanduser("~/.dataintelligence_pro")
USERS_FILE = os.path.join(APP_DIR, "users.json")
LOGS_FILE = os.path.join(APP_DIR, "logs.json")

PBKDF2_ITERATIONS = 200_000


def _hash_password(password, salt=None):
    """PBKDF2-HMAC-SHA256, stdlib-only (no extra dependency like bcrypt/argon2
    needed for a local single-machine admin tool). Returns (salt_hex, hash_hex)."""
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return salt, digest.hex()


def _verify_password(password, salt, expected_hash):
    _, computed = _hash_password(password, salt)
    return hmac.compare_digest(computed, expected_hash)


def init_app_dir():
    """Initializes the local application directory for settings and users."""
    if not os.path.exists(APP_DIR):
        os.makedirs(APP_DIR)

    if not os.path.exists(USERS_FILE):
        # Create default admin/user accounts. Passwords are stored as
        # PBKDF2 hashes, never in plaintext -- see _hash_password().
        default_users = {}
        for username, role in (("admin", "admin"), ("user", "user")):
            salt, pwd_hash = _hash_password(username)  # default password == username, same as before
            default_users[username] = {
                "salt": salt,
                "password_hash": pwd_hash,
                "role": role,
                "license_expiry": "2099-12-31",
            }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_users, f, indent=4)

    if not os.path.exists(LOGS_FILE):
        with open(LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4)


def check_login(username, password):
    """Validates user credentials against local JSON storage.

    Transparently migrates any account still storing a plaintext "password"
    field (from before password hashing was added) to a PBKDF2 hash on
    first successful login, so existing ~/.dataintelligence_pro/users.json
    files upgrade themselves without an admin having to reset anyone."""
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)

    if username not in users:
        return False, "Invalid username or password."
    record = users[username]

    if "password_hash" in record:
        ok = _verify_password(password, record["salt"], record["password_hash"])
    else:
        # Legacy plaintext record.
        ok = hmac.compare_digest(record.get("password", ""), password)
        if ok:
            salt, pwd_hash = _hash_password(password)
            record["salt"] = salt
            record["password_hash"] = pwd_hash
            record.pop("password", None)
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(users, f, indent=4)

    if not ok:
        return False, "Invalid username or password."

    expiry = datetime.strptime(record["license_expiry"], "%Y-%m-%d").date()
    if datetime.now().date() > expiry:
        return False, "License expired."
    return True, record

def add_log(action, username):
    """Records significant actions."""
    with open(LOGS_FILE, "r", encoding="utf-8") as f:
        logs = json.load(f)
        
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "user": username
    }
    logs.append(log_entry)
    
    with open(LOGS_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4)

def setup_session_state():
    """Initializes standard session state variables. Real login is
    required -- default accounts are admin/admin and user/user (see
    init_app_dir); change them in ~/.dataintelligence_pro/users.json."""
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if "user_role" not in st.session_state:
        st.session_state["user_role"] = None
    if "username" not in st.session_state:
        st.session_state["username"] = None

def render_login():
    """Renders the login UI."""
    st.markdown("<h2 style='text-align: center; color: white;'>Data Intel PRO Login</h2>", unsafe_allow_html=True)
    
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")
        
        if submit:
            is_valid, data = check_login(username, password)
            if is_valid:
                st.session_state["authenticated"] = True
                st.session_state["user_role"] = data["role"]
                st.session_state["username"] = username
                add_log("login", username)
                st.rerun()
            else:
                st.error(data)
