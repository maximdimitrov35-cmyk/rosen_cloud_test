import os
import json
from datetime import datetime, timedelta

import requests
import streamlit as st
import extra_streamlit_components as stx
import firebase_admin

from google import genai
from firebase_admin import credentials, firestore, auth


# ============================================================
# CONFIGURATION
# ============================================================

api_key = os.getenv("GOOGLE_API_KEY")
firebase_web_api_key = st.secrets["FIREBASE_WEB_API_KEY"]
firebase_info = json.loads(st.secrets["FIREBASE_SERVICE_ACCOUNT"])

client = genai.Client(api_key=api_key)

SESSION_LENGTH = timedelta(days=10)


# ============================================================
# FIREBASE AUTHENTICATION
# ============================================================

def register_user(email, password):
    url = (
        "https://identitytoolkit.googleapis.com/v1/"
        f"accounts:signUp?key={firebase_web_api_key}"
    )

    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True
    }

    response = requests.post(
        url,
        json=payload,
        timeout=15
    )

    return response.json()


def login_user(email, password):
    url = (
        "https://identitytoolkit.googleapis.com/v1/"
        f"accounts:signInWithPassword?key={firebase_web_api_key}"
    )

    payload = {
        "email": email,
        "password": password,
        "returnSecureToken": True
    }

    response = requests.post(
        url,
        json=payload,
        timeout=15
    )

    return response.json()


def create_session_cookie(id_token):
    return auth.create_session_cookie(
        id_token,
        expires_in=SESSION_LENGTH
    )


def verify_session_cookie(session_cookie):
    try:
        decoded = auth.verify_session_cookie(
            session_cookie,
            check_revoked=True
        )

        return {
            "uid": decoded["uid"],
            "email": decoded.get("email")
        }

    except Exception:
        return None


# ============================================================
# FIREBASE / FIRESTORE INITIALIZATION
# ============================================================

@st.cache_resource
def initialize_firebase():
    cred = credentials.Certificate(firebase_info)

    try:
        app = firebase_admin.get_app()

    except ValueError:
        app = firebase_admin.initialize_app(cred)

    return firestore.client(app=app)


db = initialize_firebase()


# ============================================================
# COOKIE MANAGER
# ============================================================

cookie_manager = stx.CookieManager()


# ============================================================
# USER SESSION
# ============================================================

if "user" not in st.session_state:
    st.session_state.user = None


# Try restoring an existing 10-day login.

if st.session_state.user is None:
    saved_session = cookie_manager.get("rosen_session")

    if saved_session:
        restored_user = verify_session_cookie(saved_session)

        if restored_user:
            st.session_state.user = restored_user


# ============================================================
# LOGIN / REGISTER PAGE
# ============================================================

if st.session_state.user is None:
    st.title("Росен AI")
    st.caption("Login or create an account")

    account_mode = st.radio(
        "Account",
        ["Login", "Create Account"],
        horizontal=True
    )

    email = st.text_input("Email")

    password = st.text_input(
        "Password",
        type="password"
    )

    if account_mode == "Login":

        if st.button("Login"):

            if not email or not password:
                st.error("Please enter your email and password.")

            else:
                try:
                    result = login_user(email, password)

                    if "localId" in result:
                        session_cookie = create_session_cookie(
                            result["idToken"]
                        )

                        cookie_manager.set(
                            "rosen_session",
                            session_cookie,
                            expires_at=datetime.now() + SESSION_LENGTH
                        )

                        st.session_state.user = {
                            "uid": result["localId"],
                            "email": result["email"]
                        }

                        st.rerun()

                    else:
                        error_message = result.get(
                            "error", {}
                        ).get(
                            "message",
                            "Unknown login error"
                        )

                        st.error(error_message)

                except requests.RequestException:
                    st.error(
                        "Could not connect to the login service. "
                        "Please try again."
                    )


    else:

        if st.button("Create Account"):

            if not email or not password:
                st.error("Please enter an email and password.")

            else:
                try:
                    result = register_user(email, password)

                    if "localId" in result:
                        session_cookie = create_session_cookie(
                            result["idToken"]
                        )

                        cookie_manager.set(
                            "rosen_session",
                            session_cookie,
                            expires_at=datetime.now() + SESSION_LENGTH
                        )

                        st.session_state.user = {
                            "uid": result["localId"],
                            "email": result["email"]
                        }

                        st.rerun()

                    else:
                        error_message = result.get(
                            "error", {}
                        ).get(
                            "message",
                            "Unknown registration error"
                        )

                        st.error(error_message)

                except requests.RequestException:
                    st.error(
                        "Could not connect to the registration service. "
                        "Please try again."
                    )


    # Prevent the Росен chat from appearing underneath the login page.
    st.stop()


# ============================================================
# LOGOUT
# ============================================================

with st.sidebar:
    st.write(st.session_state.user["email"])

    if st.button("Log out"):
        cookie_manager.delete("rosen_session")

        st.session_state.user = None
        st.session_state.messages = []

        st.rerun()


# ============================================================
# ROSEN CHAT
# ============================================================

st.title("Росен AI")
st.caption("v2.5 Cloud Test")


# Temporary conversation memory.

if "messages" not in st.session_state:
    st.session_state.messages = []


# Display existing messages.

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])


# ============================================================
# USER INPUT
# ============================================================

user_message = st.chat_input("Message Росен")


if user_message:

    # Add the user's message to temporary memory.
    st.session_state.messages.append({
        "role": "user",
        "content": user_message
    })


    # Display user message.
    with st.chat_message("user"):
        st.write(user_message)


    # Build the conversation that Gemma receives.
    conversation = ""

    for message in st.session_state.messages:
        conversation += (
            f'{message["role"]}: '
            f'{message["content"]}\n'
        )


    # Generate Росен's response.
    with st.chat_message("assistant"):

        with st.spinner("Росен is thinking..."):
            response = client.models.generate_content(
                model="gemma-4-26b-a4b-it",
                contents=conversation
            )

        st.write(response.text)


    # Add Росен's response to temporary memory.
    st.session_state.messages.append({
        "role": "assistant",
        "content": response.text
    })
