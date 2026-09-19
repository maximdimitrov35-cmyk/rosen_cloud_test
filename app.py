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
firebase_info = json.loads(
    st.secrets["FIREBASE_SERVICE_ACCOUNT"]
)

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
# SESSION STATE
# ============================================================

if "user" not in st.session_state:
    st.session_state.user = None

if "messages" not in st.session_state:
    st.session_state.messages = []

if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = None


# ============================================================
# RESTORE LOGIN
# ============================================================

if st.session_state.user is None:
    saved_session = cookie_manager.get("rosen_session")

    if saved_session:
        restored_user = verify_session_cookie(
            saved_session
        )

        if restored_user:
            st.session_state.user = restored_user


# ============================================================
# LOGIN / REGISTER
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
                st.error(
                    "Please enter your email and password."
                )

            else:
                try:
                    result = login_user(
                        email,
                        password
                    )

                    if "localId" in result:
                        session_cookie = (
                            create_session_cookie(
                                result["idToken"]
                            )
                        )

                        cookie_manager.set(
                            "rosen_session",
                            session_cookie,
                            expires_at=(
                                datetime.now()
                                + SESSION_LENGTH
                            )
                        )

                        st.session_state.user = {
                            "uid": result["localId"],
                            "email": result["email"]
                        }

                        st.rerun()

                    else:
                        error_message = (
                            result.get(
                                "error", {}
                            ).get(
                                "message",
                                "Unknown login error"
                            )
                        )

                        st.error(error_message)

                except requests.RequestException:
                    st.error(
                        "Could not connect to the "
                        "login service. Please try again."
                    )

    else:

        if st.button("Create Account"):

            if not email or not password:
                st.error(
                    "Please enter an email and password."
                )

            else:
                try:
                    result = register_user(
                        email,
                        password
                    )

                    if "localId" in result:
                        session_cookie = (
                            create_session_cookie(
                                result["idToken"]
                            )
                        )

                        cookie_manager.set(
                            "rosen_session",
                            session_cookie,
                            expires_at=(
                                datetime.now()
                                + SESSION_LENGTH
                            )
                        )

                        st.session_state.user = {
                            "uid": result["localId"],
                            "email": result["email"]
                        }

                        st.rerun()

                    else:
                        error_message = (
                            result.get(
                                "error", {}
                            ).get(
                                "message",
                                "Unknown registration error"
                            )
                        )

                        st.error(error_message)

                except requests.RequestException:
                    st.error(
                        "Could not connect to the "
                        "registration service. "
                        "Please try again."
                    )

    st.stop()


# ============================================================
# FIRESTORE PATHS
# ============================================================

def get_chats_collection():
    uid = st.session_state.user["uid"]

    return (
        db.collection("users")
        .document(uid)
        .collection("chats")
    )


def get_chat_reference(chat_id):
    return (
        get_chats_collection()
        .document(chat_id)
    )


def get_messages_collection(chat_id):
    return (
        get_chat_reference(chat_id)
        .collection("messages")
    )


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
# SAVE MESSAGE
# ============================================================

def save_message(chat_id, role, content):
    chat_ref = get_chat_reference(chat_id)

    # Transaction gives every message a reliable sequence number.
    transaction = db.transaction()

    @firestore.transactional
    def save_in_transaction(transaction):
        chat_snapshot = chat_ref.get(
            transaction=transaction
        )

        chat_data = chat_snapshot.to_dict() or {}

        sequence = chat_data.get(
            "next_sequence",
            0
        )

        message_ref = (
            chat_ref
            .collection("messages")
            .document()
        )

        transaction.set(
            message_ref,
            {
                "role": role,
                "content": content,
                "sequence": sequence,
                "created_at": firestore.SERVER_TIMESTAMP
            }
        )

        transaction.update(
            chat_ref,
            {
                "next_sequence": sequence + 1,
                "updated_at": firestore.SERVER_TIMESTAMP
            }
        )

    save_in_transaction(transaction)


# ============================================================
# LOAD CHAT
# ============================================================

def load_chat(chat_id):
    messages_ref = get_messages_collection(chat_id)

    message_docs = list(
        messages_ref.stream()
    )

    loaded_data = []

    for doc in message_docs:
        data = doc.to_dict()

        loaded_data.append({
            "role": data.get("role", "assistant"),
            "content": data.get("content", ""),
            "sequence": data.get("sequence"),
            "created_at": data.get("created_at")
        })

    # New v2.5.2 messages have sequence numbers.
    # Older test messages may only have timestamps.
    def message_sort_key(message):
        sequence = message["sequence"]

        if sequence is not None:
            return (
                0,
                sequence
            )

        created_at = message["created_at"]

        if created_at is not None:
            return (
                1,
                created_at.timestamp()
            )

        return (
            2,
            0
        )

    loaded_data.sort(
        key=message_sort_key
    )

    loaded_messages = []

    for message in loaded_data:
        loaded_messages.append({
            "role": message["role"],
            "content": message["content"]
        })

    st.session_state.messages = loaded_messages
    st.session_state.current_chat_id = chat_id


# ============================================================
# DELETE CHAT
# ============================================================

def delete_chat(chat_id):
    message_docs = (
        get_messages_collection(chat_id)
        .stream()
    )

    batch = db.batch()
    operation_count = 0

    for message_doc in message_docs:
        batch.delete(
            message_doc.reference
        )

        operation_count += 1

        if operation_count >= 400:
            batch.commit()

            batch = db.batch()
            operation_count = 0

    if operation_count > 0:
        batch.commit()

    get_chat_reference(chat_id).delete()

    if (
        st.session_state.current_chat_id
        == chat_id
    ):
        st.session_state.current_chat_id = None
        st.session_state.messages = []


# ============================================================
# GET SAVED CHATS
# ============================================================

def get_saved_chats():
    docs = (
        get_chats_collection()
        .order_by(
            "updated_at",
            direction=firestore.Query.DESCENDING
        )
        .stream()
    )

    chats = []

    for doc in docs:
        data = doc.to_dict()

        chats.append({
            "id": doc.id,
            "title": data.get(
                "title",
                "Untitled Chat"
            )
        })

    return chats


# ============================================================
# LOGOUT
# ============================================================

def logout():
    # extra-streamlit-components can throw KeyError
    # if its local cookie dictionary does not currently
    # contain the cookie. Only delete when it sees it.

    try:
        saved_session = cookie_manager.get(
            "rosen_session"
        )

        if saved_session:
            cookie_manager.delete(
                "rosen_session"
            )

    except KeyError:
        pass

    st.session_state.user = None
    st.session_state.messages = []
    st.session_state.current_chat_id = None

    st.rerun()


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.subheader("Росен AI")

    st.caption(
        st.session_state.user.get(
            "email",
            ""
        )
    )

    if st.button(
        "＋ New Chat",
        use_container_width=True
    ):
        st.session_state.messages = []
        st.session_state.current_chat_id = None

        st.rerun()

    st.divider()

    st.write("Saved Chats")

    try:
        saved_chats = get_saved_chats()

        if not saved_chats:
            st.caption(
                "No saved chats yet."
            )

        for chat in saved_chats:

            if st.button(
                chat["title"],
                key=f'load_{chat["id"]}',
                use_container_width=True
            ):
                load_chat(chat["id"])
                st.rerun()

            if st.button(
                "Delete",
                key=f'delete_{chat["id"]}',
                use_container_width=True
            ):
                delete_chat(chat["id"])
                st.rerun()

    except Exception as error:
        st.caption(
            "Saved chats could not be loaded."
        )

    st.divider()

    if st.button(
        "Log out",
        use_container_width=True
    ):
        logout()


# ============================================================
# ROSEN CHAT
# ============================================================

st.title("Росен AI")
st.caption("v2.5.2 Cloud")


# ============================================================
# DISPLAY EXISTING MESSAGES
# ============================================================

for message in st.session_state.messages:

    with st.chat_message(
        message["role"]
    ):
        st.write(
            message["content"]
        )


# ============================================================
# CHAT INPUT
# ============================================================

user_message = st.chat_input(
    "Message Росен"
)


if user_message:

    # --------------------------------------------------------
    # CREATE A NEW SAVED CHAT
    # --------------------------------------------------------

    if (
        st.session_state.current_chat_id
        is None
    ):
        st.session_state.current_chat_id = (
            create_chat(user_message)
        )

    chat_id = (
        st.session_state.current_chat_id
    )


    # --------------------------------------------------------
    # SAVE USER MESSAGE
    # --------------------------------------------------------

    st.session_state.messages.append({
        "role": "user",
        "content": user_message
    })

    save_message(
        chat_id,
        "user",
        user_message
    )


    with st.chat_message("user"):
        st.write(user_message)


    # --------------------------------------------------------
    # BUILD CONVERSATION
    # --------------------------------------------------------

    conversation = ""

    for message in st.session_state.messages:

        conversation += (
            f'{message["role"]}: '
            f'{message["content"]}\n'
        )


    # --------------------------------------------------------
    # GENERATE RESPONSE
    # --------------------------------------------------------

    try:

        with st.chat_message("assistant"):

            with st.spinner(
                "Росен is thinking..."
            ):
                response = (
                    client.models.generate_content(
                        model="gemma-4-26b-a4b-it",
                        contents=conversation
                    )
                )

            assistant_text = response.text

            st.write(
                assistant_text
            )


        # ----------------------------------------------------
        # SAVE ASSISTANT RESPONSE
        # ----------------------------------------------------

        st.session_state.messages.append({
            "role": "assistant",
            "content": assistant_text
        })

        save_message(
            chat_id,
            "assistant",
            assistant_text
        )


        # ----------------------------------------------------
        # REFRESH SIDEBAR
        # ----------------------------------------------------
        #
        # The sidebar was rendered BEFORE this new chat
        # existed. Rerun now so Saved Chats immediately
        # displays the new/updated conversation.
        #
        # Messages are already in session_state, so they
        # reappear normally after the rerun.
        # ----------------------------------------------------

        st.rerun()


    except Exception as error:
        st.error(
            "Росен had trouble generating a response. "
            "Your message was still saved."
        )
