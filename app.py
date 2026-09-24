import os
import json
from datetime import datetime, timedelta
import time
import base64

import requests
import streamlit as st
import extra_streamlit_components as stx
import firebase_admin

from google import genai
from firebase_admin import credentials, firestore, auth

ROSEN_PERSONALITY = """
You are Росен AI (pronounced Rosen), an AI assistant created by Maxim.

Your personality is inspired by Uncle Росен: confident, practical,
slightly humorous, straightforward, and friendly.

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

if (
    st.session_state.user is None
    and not st.session_state.force_logged_out
):
    # On a brand-new Streamlit session, st.context.cookies reads the
    # cookies that arrived with the browser's initial request. This is
    # more reliable for restoring a persistent login than depending on
    # the custom cookie component to finish loading first.
    saved_session = st.context.cookies.get("rosen_session")

    # Fallback for the current session immediately after the component
    # has created the cookie but before a completely fresh browser load.
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
                            ),
                            path="/",
                            secure=True,
                            same_site="lax"
                        )

                        st.session_state.force_logged_out = False

                        st.session_state.user = {
                            "uid": result["localId"],
                            "email": result["email"]
                        }

                        # Give the browser-side cookie component time
                        # to actually write the persistent cookie before
                        # Streamlit interrupts this run.
                        time.sleep(1.0)
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
                            ),
                            path="/",
                            secure=True,
                            same_site="lax"
                        )

                        st.session_state.force_logged_out = False

                        st.session_state.user = {
                            "uid": result["localId"],
                            "email": result["email"]
                        }

                        # Give the browser-side cookie component time
                        # to actually write the persistent cookie before
                        # Streamlit interrupts this run.
                        time.sleep(1.0)
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
# IMAGE GENERATION
# ============================================================

def is_image_request(message):
    message = message.lower().strip()

    image_words = [
        "image",
        "picture",
        "photo",
        "illustration",
        "artwork",
        "drawing",
        "wallpaper",
        "poster",
        "logo"
    ]

    action_words = [
        "create",
        "generate",
        "make",
        "draw",
        "render",
        "design",
        "paint"
    ]

    has_image_word = any(
        word in message
        for word in image_words
    )

    has_action_word = any(
        word in message
        for word in action_words
    )

    return has_image_word and has_action_word


def generate_image(prompt, status_box):
    api_key = st.secrets.get(
        "AI_HORDE_API_KEY",
        "0000000000"
    )

    base_url = "https://stablehorde.net/api/v2"

    headers = {
        "apikey": api_key,
        "Client-Agent": "Rosen.APP:2.5.4"
    }

    payload = {
        "prompt": prompt,
        "models": [
            "Flux.1-Schnell fp8 (Compact)"
        ],
        "params": {
            "width": 512,
            "height": 512,
            "steps": 4,
            "n": 1
        }
    }

    response = requests.post(
        f"{base_url}/generate/async",
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()

    job = response.json()
    job_id = job.get("id")

    if not job_id:
        raise RuntimeError(
            "AI Horde did not return a generation ID."
        )

        # Wait for the volunteer workers to finish.
    while True:

        time.sleep(2)

        status_response = requests.get(
            f"{base_url}/generate/check/{job_id}",
            headers=headers,
            timeout=20
        )

        status_response.raise_for_status()

        status = status_response.json()

        if status.get("faulted"):
            raise RuntimeError(
                "AI Horde reported that image generation failed."
            )

        if status.get("done"):
            status_box.info(
                "✅ Image generation complete! Loading image..."
            )
            break

        queue_position = status.get(
            "queue_position"
        )

        wait_time = status.get(
            "wait_time"
        )

        if queue_position is not None:

            if wait_time is not None:
                status_box.info(
                    f"🎨 Rosen is generating your image...\n\n"
                    f"Queue position: {queue_position}\n"
                    f"Estimated wait: {wait_time}s"
                )

            else:
                status_box.info(
                    f"🎨 Rosen is generating your image...\n\n"
                    f"Queue position: {queue_position}"
                )

        else:
            status_box.info(
                "🎨 Rosen is waiting for a worker..."
            )


    result_response = requests.get(
        f"{base_url}/generate/status/{job_id}",
        headers=headers,
        timeout=30
    )

    result_response.raise_for_status()

    result = result_response.json()
    generations = result.get("generations", [])

    if not generations:
        raise RuntimeError(
            "AI Horde finished without returning an image."
        )

    image_value = generations[0].get("img")

    if not image_value:
        raise RuntimeError(
            "AI Horde returned an empty image."
        )

    # AI Horde can return an image URL or encoded image data.
    if image_value.startswith(
        ("http://", "https://")
    ):
        image_response = requests.get(
            image_value,
            timeout=30
        )

        image_response.raise_for_status()

        return image_response.content

    if image_value.startswith("data:"):
        image_value = image_value.split(
            ",",
            1
        )[1]

    return base64.b64decode(image_value)
# ============================================================
# LOGOUT
# ============================================================

def logout():
    # Suppress automatic cookie restoration for the rest of this
    # Streamlit session. st.context.cookies is a snapshot from the
    # initial request, so it can still contain the old cookie until a
    # completely new browser session begins.
    st.session_state.force_logged_out = True

    try:
        saved_session = cookie_manager.get("rosen_session")

        if saved_session:
            cookie_manager.delete("rosen_session")

    except (KeyError, Exception):
        # Clearing the in-memory login below is still safe. On the next
        # fresh browser request, an expired/deleted cookie will no longer
        # be present.
        pass

    st.session_state.user = None
    st.session_state.messages = []
    st.session_state.current_chat_id = None

    # Let the browser process the cookie deletion before rerunning.
    time.sleep(0.5)
    st.rerun()


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.image("rosen.png", width=120)
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
        "📜 Update Log",
        use_container_width=True
    ):
        st.session_state.page = "update_log"
        st.rerun()
    st.divider()

    if st.button(
        "Log out",
        use_container_width=True
    ):
        logout()

# ============================================================
# UPDATE LOG
# ============================================================

if st.session_state.page == "update_log":

    st.image(
        "rosen.png",
        width=150
    )

    st.title("Rosen.APP Update Log")

    st.markdown("""
### v2.5.3 — QoL Update

- Added the Росен personality to Rosen.APP.
- Added live streaming responses.
- Added Uncle Росен branding.
- Added the Uncle Росен assistant avatar.
- Added the Update Log menu.

### v2.5.2 — Accounts & Saved Chats

- Added Rosen.APP accounts.
- Added persistent cloud saved chats.
- Added New Chat.
- Added saved-chat loading.
- Added saved-chat deletion.
- Added 10-day remembered login sessions.

### v2.5.1 — Chat QoL

- Added the modern chat interface.
- Added Enter-to-send.
- Added temporary conversation memory.
- Improved the overall Rosen.APP chat experience.

### v2.5 — Rosen.APP

- Introduced the online version of Росен AI.
- Moved Росен to a cloud-based architecture.
- Added Gemma 4 as the AI model.
- Added browser-based access through Rosen.APP.
""")

    if st.button(
        "← Back to Chat",
        use_container_width=True
    ):
        st.session_state.page = "chat"
        st.rerun()

    st.stop()
# ============================================================
# ROSEN CHAT
# ============================================================

st.title("Росен AI")
st.caption("v2.5.3 Cloud")


# ============================================================
# DISPLAY EXISTING MESSAGES
# ============================================================

for message in st.session_state.messages:

    with st.chat_message(
        message["role"],
        avatar=(
            "rosen.png"
            if message["role"] == "assistant"
            else None
        )
    ):

        if message.get("type") == "image":
            image = message.get("image")

            if image is not None:
                st.image(
                    image,
                    use_container_width=True
                )
            else:
                st.write(
                    message["content"]
                )

        else:
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

    conversation = ROSEN_PERSONALITY + "\n\n"

    for message in st.session_state.messages:

        conversation += (
            f'{message["role"]}: '
            f'{message["content"]}\n'
        )


    # --------------------------------------------------------
    # GENERATE RESPONSE
    # --------------------------------------------------------

    try:

        # ====================================================
        # IMAGE REQUEST
        # ====================================================

        if is_image_request(user_message):

            with st.chat_message(
                "assistant",
                avatar="rosen.png"
            ):

                with st.spinner(
                    "Росен is creating your image..."
                ):

                   status_box = st.empty()

                generated_image = generate_image(
                    user_message,
                        status_box
                )

                status_box.empty()
                    

                if generated_image is None:

                    st.error(
                        "Росен could not generate the image."
                    )

                else:

                    st.image(
                        generated_image,
                        use_container_width=True
                    )

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": (
                            f"Generated image: "
                            f"{user_message}"
                        ),
                        "type": "image",
                        "image": generated_image
                    })

                    save_message(
                        chat_id,
                        "assistant",
                        f"Generated image: {user_message}"
                    )


        # ====================================================
        # NORMAL TEXT REQUEST
        # ====================================================

        else:

            with st.chat_message(
                "assistant",
                avatar="rosen.png"
            ):

                with st.spinner(
                    "Росен is thinking..."
                ):

                    response_stream = (
                        client.models.generate_content_stream(
                            model="gemma-4-26b-a4b-it",
                            contents=conversation
                        )
                    )

                    assistant_text = ""

                    def stream_response():

                        global assistant_text

                        for chunk in response_stream:

                            if chunk.text:

                                assistant_text += chunk.text

                                yield chunk.text

                    st.write_stream(
                        stream_response()
                    )


            # ------------------------------------------------
            # SAVE NORMAL RESPONSE
            # ------------------------------------------------

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

        st.rerun()


    except Exception as error:

        st.error(
            "Росен had trouble generating a response. "
            "Your message was still saved."
        )

        st.exception(error)


    except Exception as error:
        st.error(
            "Росен had trouble generating a response. "
            "Your message was still saved."
        )
        st.exception(error)
