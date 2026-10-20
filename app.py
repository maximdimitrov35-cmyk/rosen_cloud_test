import os
import json
from datetime import datetime, timedelta
import time
import base64
from io import BytesIO
from PIL import Image
import requests
import streamlit as st
import extra_streamlit_components as stx
import firebase_admin
from firebase_admin import credentials, firestore, auth
from google import genai
from google.genai import types

ROSEN_PERSONALITY = """
You are Росен AI (pronounced Rosen), an AI assistant created by Maxim. Your personality is inspired by Uncle Росен: confident, practical, slightly humorous, straightforward, and friendly. 
You should:
- Give clear and useful answers.
- Be concise when a question is simple and detailed when needed.
- Admit when you are uncertain instead of inventing information.
- Use light humor naturally when appropriate.
- Never force jokes into serious conversations.
- Speak like a capable assistant, not a character performing a comedy routine.
- Refer to yourself as Росен AI or Росен when relevant.
- Never claim to actually be a human or Maxim's real uncle.
- In casual conversation, sound relaxed and conversational rather than formal or corporate.
- Match the user's energy and humor when appropriate.
- Do not turn simple jokes or casual comments into long explanations unless the user asks for one.
- Avoid unnecessary phrases such as "as an AI", "as a practical assistant", or other robotic disclaimers.
- You may be witty, playful, and mildly sarcastic, but remain helpful.
- Prefer natural conversation over numbered lists when the user is casually chatting.
Your main priority is helping the user accurately and clearly.
"""

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
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={firebase_web_api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    response = requests.post(url, json=payload, timeout=15)
    return response.json()

def login_user(email, password):
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={firebase_web_api_key}"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    response = requests.post(url, json=payload, timeout=15)
    return response.json()

def create_session_cookie(id_token):
    return auth.create_session_cookie(id_token, expires_in=SESSION_LENGTH)

def verify_session_cookie(session_cookie):
    try:
        decoded = auth.verify_session_cookie(session_cookie, check_revoked=True)
        return {"uid": decoded["uid"], "email": decoded.get("email")}
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
cookie_manager = stx.CookieManager(key="rosen_cookie_manager")

# ============================================================
# SESSION STATE
# ============================================================
if "user" not in st.session_state:
    st.session_state.user = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = None
if "page" not in st.session_state:
    st.session_state.page = "chat"
if "force_logged_out" not in st.session_state:
    st.session_state.force_logged_out = False

# ============================================================
# RESTORE LOGIN
# ============================================================
if st.session_state.user is None and not st.session_state.force_logged_out:
    saved_session = st.context.cookies.get("rosen_session")
    if not saved_session:
        saved_session = cookie_manager.get("rosen_session")
    if saved_session:
        restored_user = verify_session_cookie(saved_session)
        if restored_user:
            st.session_state.user = restored_user

# ============================================================
# LOGIN / REGISTER
# ============================================================
if st.session_state.user is None:
    st.image("rosen.png", width=180)
    st.title("Росен AI")
    st.caption("Login or create an account")
    account_mode = st.radio("Account", ["Login", "Create Account"], horizontal=True)
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if account_mode == "Login":
        if st.button("Login"):
            if not email or not password:
                st.error("Please enter your email and password.")
            else:
                try:
                    result = login_user(email, password)
                    if "localId" in result:
                        session_cookie = create_session_cookie(result["idToken"])
                        cookie_manager.set(
                            "rosen_session", 
                            session_cookie, 
                            expires_at=(datetime.now() + SESSION_LENGTH), 
                            path="/", 
                            secure=True, 
                            same_site="lax"
                        )
                        st.session_state.force_logged_out = False
                        st.session_state.user = {"uid": result["localId"], "email": result["email"]}
                        time.sleep(1.0)
                        st.rerun()
                    else:
                        error_message = result.get("error", {}).get("message", "Unknown login error")
                        st.error(error_message)
                except requests.RequestException:
                    st.error("Could not connect to the login service. Please try again.")
    else:
        if st.button("Create Account"):
            if not email or not password:
                st.error("Please enter an email and password.")
            else:
                try:
                    result = register_user(email, password)
                    if "localId" in result:
                        session_cookie = create_session_cookie(result["idToken"])
                        cookie_manager.set(
                            "rosen_session", 
                            session_cookie, 
                            expires_at=(datetime.now() + SESSION_LENGTH), 
                            path="/", 
                            secure=True, 
                            same_site="lax"
                        )
                        st.session_state.force_logged_out = False
                        st.session_state.user = {"uid": result["localId"], "email": result["email"]}
                        time.sleep(1.0)
                        st.rerun()
                    else:
                        error_message = result.get("error", {}).get("message", "Unknown registration error")
                        st.error(error_message)
                except requests.RequestException:
                    st.error("Could not connect to the registration service. Please try again.")
    st.stop()

# ============================================================
# FIRESTORE PATHS
# ============================================================
def get_chats_collection():
    uid = st.session_state.user["uid"]
    return db.collection("users").document(uid).collection("chats")

def get_chat_reference(chat_id):
    return get_chats_collection().document(chat_id)

def get_messages_collection(chat_id):
    return get_chat_reference(chat_id).collection("messages")

# ============================================================
# CREATE CHAT
# ============================================================
def create_chat(first_message):
    chat_ref = get_chats_collection().document()
    title = first_message.strip()[:40]
    if not title:
        title = "New Chat"
    chat_ref.set({
        "title": title,
        "created_at": firestore.SERVER_TIMESTAMP,
        "updated_at": firestore.SERVER_TIMESTAMP,
        "next_sequence": 0
    })
    return chat_ref.id

# ============================================================
# SCREENSHOT STORAGE
# ============================================================
def prepare_screenshot_for_storage(image_bytes):
    image = Image.open(BytesIO(image_bytes))
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((1600, 1600))
    for quality in (85, 75, 65, 55):
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        compressed = output.getvalue()
        if len(compressed) <= 900_000:
            return compressed
    image.thumbnail((1280, 1280))
    output = BytesIO()
    image.save(output, format="JPEG", quality=50, optimize=True)
    compressed = output.getvalue()
    if len(compressed) <= 900_000:
        return compressed
    raise RuntimeError("Screenshot is too large to store safely.")

# ============================================================
# SAVE MESSAGE
# ============================================================
def save_message(chat_id, role, content, image=None, screenshots=None, screenshot_names=None
