import streamlit as st
import json
import os
from datetime import datetime

APP_DIR = os.path.expanduser("~/.dataintelligence_pro")
USERS_FILE = os.path.join(APP_DIR, "users.json")
LOGS_FILE = os.path.join(APP_DIR, "logs.json")

def init_app_dir():
    """Initializes the local application directory for settings and users."""
    if not os.path.exists(APP_DIR):
        os.makedirs(APP_DIR)
        
    if not os.path.exists(USERS_FILE):
        # Create default admin user
        default_users = {
            "admin": {
                "password": "admin", # In production, this should be hashed
                "role": "admin",
                "license_expiry": "2099-12-31"
            },
            "user": {
                "password": "user",
                "role": "user",
                "license_expiry": "2099-12-31"
            }
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_users, f, indent=4)
            
    if not os.path.exists(LOGS_FILE):
        with open(LOGS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4)

def check_login(username, password):
    """Validates user credentials against local JSON storage."""
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
        
    if username in users and users[username]["password"] == password:
        # Check license
        expiry = datetime.strptime(users[username]["license_expiry"], "%Y-%m-%d").date()
        if datetime.now().date() > expiry:
            return False, "License expired."
        return True, users[username]
    return False, "Invalid username or password."

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
