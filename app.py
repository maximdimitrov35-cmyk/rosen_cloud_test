import os
import json
from datetime import datetime, timedelta
import time
import base64
from io import BytesIO
from PIL import Image
import time
import logging


import requests
import streamlit as st
import extra_streamlit_components as stx
import firebase_admin
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4
from google import genai
from google.genai import types
from groq import Groq
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
groq_api_key = os.getenv("GROQ_API_KEY")
firebase_web_api_key = st.secrets["FIREBASE_WEB_API_KEY"]
firebase_info = json.loads(
    st.secrets["FIREBASE_SERVICE_ACCOUNT"]
)

client = genai.Client(api_key=api_key)  # Retained for non-image documents such as PDFs
groq_client = Groq(api_key=groq_api_key or st.secrets.get("GROQ_API_KEY", ""))
GROQ_MODEL = "openai/gpt-oss-20b"

class GenerationManager:

    def __init__(self):
        self.executor = ThreadPoolExecutor(
            max_workers=4
        )
        self.lock = Lock()
        self.jobs = {}

    def submit(self, conversation):

        job_id = uuid4().hex

        with self.lock:
            self.jobs[job_id] = {
                "text": "",
                "done": False,
                "error": None,
                "started_at": None,
                "first_text_seconds": None,
                "first_ui_seconds": None,
                "api_first_chunk_seconds": None,
                "chunk_processing_seconds": 0.0,
                "total_seconds": None
            }

        self.executor.submit(
            self._run_generation,
            job_id,
            conversation
        )

        return job_id

    def _run_generation(
        self,
        job_id,
        conversation
    ):
        started_at = time.perf_counter()
        with self.lock:
            self.jobs[job_id]["started_at"] = started_at
        
        try:

            api_started_at = time.perf_counter()
            response_stream = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": conversation}],
                stream=True,
            )

            for chunk in response_stream:
                chunk_processing_started = time.perf_counter()
                first_text_received = False
                chunk_text = chunk.choices[0].delta.content or ""

                if chunk_text:
                    with self.lock:
                        job = self.jobs[job_id]
                        if job["api_first_chunk_seconds"] is None:
                            job["api_first_chunk_seconds"] = (
                                time.perf_counter() - api_started_at
                            )
                        job["text"] += chunk_text
                        if job["first_text_seconds"] is None:
                            job["first_text_seconds"] = (
                                time.perf_counter() - started_at
                            )
                            first_text_received = True

                processing_elapsed = time.perf_counter() - chunk_processing_started
                with self.lock:
                    self.jobs[job_id]["chunk_processing_seconds"] += processing_elapsed

                if first_text_received:
                    logging.info(
                        "Rosen first text received after %.2f seconds",
                        self.jobs[job_id]["first_text_seconds"]
                    )

        except Exception as error:

            logging.exception(
                "Rosen generation failed (job_id=%s)",
                job_id
            )

            with self.lock:
                self.jobs[job_id]["error"] = str(error)

        finally:
            logging.info(
                "Rosen generation finished after %.2f seconds",
                time.perf_counter() - started_at
            )
            with self.lock:
                self.jobs[job_id]["total_seconds"] = (
                    time.perf_counter() - started_at
                )
                self.jobs[job_id]["done"] = True

    def mark_first_ui(self, job_id):
        """Record when the first generated text is picked up by the UI."""
        with self.lock:
            job = self.jobs.get(job_id)
            if (
                job
                and job["text"]
                and job["first_ui_seconds"] is None
                and job["started_at"] is not None
            ):
                job["first_ui_seconds"] = (
                    time.perf_counter() - job["started_at"]
                )

    def get_job(self, job_id):

        with self.lock:

            job = self.jobs.get(job_id)

            if job is None:
                return None

            return {
                "text": job["text"],
                "done": job["done"],
                "error": job["error"],
                "first_text_seconds": job["first_text_seconds"],
                "first_ui_seconds": job["first_ui_seconds"],
                "api_first_chunk_seconds": job["api_first_chunk_seconds"],
                "chunk_processing_seconds": job["chunk_processing_seconds"],
                "total_seconds": job["total_seconds"]
            }


@st.cache_resource
def get_generation_manager():

    return GenerationManager()


generation_manager = get_generation_manager()

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

if "generation_job_id" not in st.session_state:
    st.session_state.generation_job_id = None

if "generation_chat_id" not in st.session_state:
    st.session_state.generation_chat_id = None


# ============================================================
# RESTORE LOGIN
# ============================================================

if (
    st.session_state.user is None
    and not st.session_state.force_logged_out
):
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
# PERSONAL MEMORY (v2.9w)
# ============================================================

def get_memory_reference():
    uid = st.session_state.user["uid"]
    return (
        db.collection("users")
        .document(uid)
        .collection("memory")
        .document("profile")
    )


def load_user_memory():
    try:
        snapshot = get_memory_reference().get()
        if not snapshot.exists:
            return []
        items = snapshot.to_dict().get("items", [])
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, str) and item.strip()][:30]
    except Exception:
        logging.exception("Could not load Rosen personal memory")
        return []


memory_extraction_executor = ThreadPoolExecutor(max_workers=1)


def should_check_implicit_memory(user_message):
    text = (user_message or "").strip().casefold()
    if len(text) < 20:
        return False
    if text.startswith(("remember ", "remember:", "remember that ", "forget ", "forget:", "forget that ")):
        return False
    personal_markers = (
        "i like ", "i love ", "i prefer ", "i hate ", "i enjoy ",
        "i usually ", "i always ", "i often ", "i'm working on ",
        "i am working on ", "my project ", "my goal ", "my favorite ",
        "my favourite ", "i study ", "i'm studying ", "i am studying ",
        "i'm learning ", "i am learning ", "i plan to ", "i want to ",
        "i use ", "i have a ", "i have an ", "i have my ", "i don't like ", "i do not like ",
        "i'm building ", "i am building ", "i want to become ", "i want to learn ", "i want to build ", "i want to study ", "i want to work ", "for my project", "for school",
        "next week i", "tomorrow i", "i need to learn", "i need to study", "i need to build",
        "i used to", "i normally", "i learn better", "i learn best", "i learned better",
        "i've realized", "i have realized", "i get more done", "i work better",
        "i was working on", "i've switched", "i have switched", "i switched back",
        "i'm back to", "i am back to", "i'm focusing on", "i am focusing on",
        "my current focus", "my focus is", "i'm currently working on", "i am currently working on",
        "actually,", "actually ", "instead,", "instead ", "anymore", "no longer", "from now on",
        "i changed my", "my preference changed", "i don't want", "i do not want", "rather than",
        "not short answers", "not detailed answers", "i prefer longer", "i prefer shorter"
    )
    return any(marker in text for marker in personal_markers)


def _normalise_memory_text(value):
    import re
    value = (value or "").casefold().strip()
    value = re.sub(r"^user\s+", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def _memory_is_duplicate(candidate, existing_items):
    from difflib import SequenceMatcher
    normalized = _normalise_memory_text(candidate)
    if not normalized:
        return True
    for item in existing_items:
        other = _normalise_memory_text(item)
        if normalized == other:
            return True
        if min(len(normalized), len(other)) >= 18 and SequenceMatcher(None, normalized, other).ratio() >= 0.90:
            return True
    return False


def extract_and_save_implicit_memory(uid, user_message, recent_context):
    try:
        ref = db.collection("users").document(uid).collection("memory").document("profile")
        snapshot = ref.get()
        data = snapshot.to_dict() if snapshot.exists else {}
        existing_items = data.get("items", [])
        if not isinstance(existing_items, list):
            existing_items = []
        existing_items = [" ".join(x.split()) for x in existing_items if isinstance(x, str) and x.strip()]

        extraction_prompt = (
            "Manage durable memories for ONE user. Treat this as a structured decision task, not a keyword task. "
            "Read the latest user message, identify any durable fact, then compare it with EVERY existing memory. "
            "Use recent conversation only to resolve references. Only the latest user message can establish a new fact.\\n\\n"
            "DECISION RULES:\\n"
            "A) ADD a clearly durable fact that is not already represented.\\n"
            "B) UPDATE an existing memory when the user corrects it or changes their mind.\\n"
            "C) For a preference that evolves, store the current full preference.\\n"
            "D) For project focus, update accordingly.\\n"
            "E) NO CHANGE for temporary requests.\\n"
            "F) Never save sensitive personal data.\\n\\n"
            "Return valid JSON only with this exact shape: "
            '{"add":["memory"],"update":[{"old":"exact existing memory","new":"replacement memory"}],'
            '"remove":["exact existing memory"]}. Empty arrays are allowed.\\n\\n'
            f"EXISTING MEMORIES:\\n{json.dumps(existing_items, ensure_ascii=False)}\\n\\n"
            f"RECENT CONVERSATION (reference only):\\n{recent_context[-5000:]}\\n\\n"
            f"LATEST USER MESSAGE (the only source of new facts):\\n{user_message}"
        )
        result = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": "You are a cautious personal-memory manager. Return valid JSON only."},
                {"role": "user", "content": extraction_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        parsed = json.loads(result.choices[0].message.content or "{}")
        items = list(existing_items)
        changed = False

        updates = parsed.get("update", [])
        if isinstance(updates, list):
            for operation in updates[:2]:
                if not isinstance(operation, dict):
                    continue
                old = operation.get("old", "")
                new_item = " ".join(str(operation.get("new", "")).split()).strip(" -•\t\n")
                if not isinstance(old, str) or not new_item or len(new_item) > 180:
                    continue
                index = next((i for i, item in enumerate(items) if item.casefold() == old.casefold()), None)
                if index is not None and not _memory_is_duplicate(new_item, [x for i, x in enumerate(items) if i != index]):
                    items[index] = new_item
                    changed = True

        removals = parsed.get("remove", [])
        if isinstance(removals, list):
            for old in removals[:2]:
                if isinstance(old, str) and old in items:
                    items.remove(old)
                    changed = True

        additions = parsed.get("add", [])
        if isinstance(additions, list):
            for candidate in additions[:2]:
                if not isinstance(candidate, str):
                    continue
                candidate = " ".join(candidate.split()).strip(" -•\t\n")
                if candidate and len(candidate) <= 180 and not _memory_is_duplicate(candidate, items):
                    items.append(candidate)
                    changed = True

        if changed:
            ref.set({"items": items[-30:], "updated_at": firestore.SERVER_TIMESTAMP})
            logging.info("Updated personal memory for user %s", uid)
    except Exception:
        logging.exception("Could not extract/update Rosen personal memory")


def process_memory_command(user_message):
    text = (user_message or "").strip()
    lowered = text.casefold()
    remember_prefixes = ("remember that ", "remember: ", "remember ")
    forget_prefixes = ("forget that ", "forget: ", "forget ")

    action = None
    detail = ""
    for prefix in remember_prefixes:
        if lowered.startswith(prefix):
            action, detail = "remember", text[len(prefix):].strip()
            break
    if action is None:
        for prefix in forget_prefixes:
            if lowered.startswith(prefix):
                action, detail = "forget", text[len(prefix):].strip()
                break
    if action is None:
        return None

    try:
        memory_ref = get_memory_reference()
        snapshot = memory_ref.get()
        data = snapshot.to_dict() if snapshot.exists else {}
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        items = [item.strip() for item in items if isinstance(item, str) and item.strip()]

        if action == "remember":
            if not detail:
                return "No memory was saved because you didn't specify what to remember."
            duplicate_exists = _memory_is_duplicate(detail, items)
            if duplicate_exists:
                return f'A matching memory already exists for "{detail}".'
            items.append(detail[:180])
            memory_ref.set({"items": items[-30:], "updated_at": firestore.SERVER_TIMESTAMP})
            return f'Memory saved: "{detail[:180]}".'

        if not detail or detail.casefold() in ("everything", "all", "all my memories"):
            memory_ref.set({"items": [], "updated_at": firestore.SERVER_TIMESTAMP})
            return "All saved personal memories were cleared."

        target_norm = _normalise_memory_text(detail)
        matching_index = next((i for i, item in enumerate(items) if _normalise_memory_text(item) == target_norm), None)

        if matching_index is not None:
            removed_item = items.pop(matching_index)
            memory_ref.set({"items": items, "updated_at": firestore.SERVER_TIMESTAMP})
            return f'Memory removed: "{removed_item}".'
        return f'No matching saved memory found for "{detail}".'
    except Exception:
        logging.exception("Could not update Rosen personal memory")
        return "Memory storage failed."


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
# SCREENSHOT & FILE STORAGE
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
    return output.getvalue()


# ============================================================
# SAVE MESSAGE
# ============================================================

def save_message(
    chat_id,
    role,
    content,
    image=None,
    screenshots=None,
    screenshot_names=None,
    timing=None
):
    chat_ref = get_chat_reference(chat_id)
    transaction = db.transaction()

    @firestore.transactional
    def save_in_transaction(transaction):
        chat_snapshot = chat_ref.get(transaction=transaction)
        chat_data = chat_snapshot.to_dict() or {}
        sequence = chat_data.get("next_sequence", 0)
        message_ref = chat_ref.collection("messages").document()

        if screenshots is not None:
            message_type = "screenshots"
        elif image is not None:
            message_type = "image"
        else:
            message_type = "text"

        transaction.set(
            message_ref,
            {
                "role": role,
                "content": content,
                "sequence": sequence,
                "created_at": firestore.SERVER_TIMESTAMP,
                "type": message_type,
                "image": image,
                "timing": timing,
                "attachment_count": len(screenshots) if screenshots is not None else 0
            }
        )

        if screenshots is not None:
            names = screenshot_names or []
            for index, screenshot in enumerate(screenshots):
                attachment_ref = message_ref.collection("attachments").document()
                transaction.set(
                    attachment_ref,
                    {
                        "data": screenshot,
                        "name": names[index] if index < len(names) else f"attachment_{index + 1}.jpg",
                        "index": index,
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
    message_docs = list(messages_ref.stream())
    loaded_data = []

    for doc in message_docs:
        data = doc.to_dict()
        screenshot_images = []
        screenshot_names = []

        if data.get("type") == "screenshots":
            attachments = []
            for attachment_doc in doc.reference.collection("attachments").stream():
                attachment_data = attachment_doc.to_dict() or {}
                attachments.append({
                    "index": attachment_data.get("index", 0),
                    "data": attachment_data.get("data"),
                    "name": attachment_data.get("name", "attachment.jpg")
                })
            attachments.sort(key=lambda a: a["index"])
            for attachment in attachments:
                if attachment["data"] is not None:
                    file_bytes = attachment["data"]
                    name = attachment["name"].lower()
                    # Only treat it as an image if it's actually an image extension
                    if name.endswith((".png", ".jpg", ".jpeg")):
                        screenshot_images.append(file_bytes)
                        screenshot_names.append(attachment["name"])
                    else:
                        # For code/text files, we store them as text/files rather than images
                        screenshot_names.append(attachment["name"])

        loaded_data.append({
            "role": data.get("role", "assistant"),
            "content": data.get("content", ""),
            "sequence": data.get("sequence"),
            "created_at": data.get("created_at"),
            "type": data.get("type", "text"),
            "image": data.get("image"),
            "timing": data.get("timing"),
            "images": screenshot_images,
            "image_names": screenshot_names
        })

    def message_sort_key(message):
        sequence = message["sequence"]
        if sequence is not None:
            return (0, sequence)
        created_at = message["created_at"]
        if created_at is not None:
            return (1, created_at.timestamp())
        return (2, 0)

    loaded_data.sort(key=message_sort_key)
    loaded_messages = []

    for message in loaded_data:
        loaded_messages.append({
            "role": message["role"],
            "content": message["content"],
            "type": message["type"],
            "image": message["image"],
            "images": message["images"],
            "image_names": message["image_names"],
            "timing": message.get("timing")
        })

    st.session_state.messages = loaded_messages
    st.session_state.current_chat_id = chat_id


# ============================================================
# DELETE CHAT
# ============================================================

def delete_chat(chat_id):
    message_docs = get_messages_collection(chat_id).stream()
    batch = db.batch()
    operation_count = 0

    for message_doc in message_docs:
        attachment_docs = message_doc.reference.collection("attachments").stream()
        for attachment_doc in attachment_docs:
            batch.delete(attachment_doc.reference)
            operation_count += 1
            if operation_count >= 400:
                batch.commit()
                batch = db.batch()
                operation_count = 0

        batch.delete(message_doc.reference)
        operation_count += 1
        if operation_count >= 400:
            batch.commit()
            batch = db.batch()
            operation_count = 0

    if operation_count > 0:
        batch.commit()

    get_chat_reference(chat_id).delete()

    if st.session_state.current_chat_id == chat_id:
        st.session_state.current_chat_id = None
        st.session_state.messages = []


# ============================================================
# GET SAVED CHATS
# ============================================================

def get_saved_chats():
    docs = (
        get_chats_collection()
        .order_by("updated_at", direction=firestore.Query.DESCENDING)
        .stream()
    )
    chats = []
    for doc in docs:
        data = doc.to_dict()
        chats.append({
            "id": doc.id,
            "title": data.get("title", "Untitled Chat")
        })
    return chats


# ============================================================
# IMAGE GENERATION
# ============================================================

def is_image_request(message):
    message = message.lower().strip()
    image_words = ["image", "picture", "photo", "illustration", "artwork", "drawing", "wallpaper", "poster", "logo"]
    action_words = ["create", "generate", "make", "draw", "render", "design", "paint"]
    return any(w in message for w in image_words) and any(w in message for w in action_words)


def generate_image(prompt, status_box):
    account_id = st.secrets["CLOUDFLARE_ACCOUNT_ID"]
    api_token = st.secrets["CLOUDFLARE_API_TOKEN"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell"

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    payload = {"prompt": prompt, "steps": 8}

    status_box.info("🎨 Росен is generating your image with Cloudflare...")
    response = requests.post(url, headers=headers, json=payload, timeout=120)

    if not response.ok:
        raise RuntimeError(f"Cloudflare image request failed ({response.status_code}): {response.text}")

    result = response.json()
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare image generation failed: {result.get('errors', result)}")

    image_value = result.get("result", {}).get("image")
    if not image_value:
        raise RuntimeError("Cloudflare did not return an image.")

    status_box.info("✅ Image generation complete! Loading image...")
    return base64.b64decode(image_value)


import re

def clean_latex_for_streamlit(text):
    def replace_brackets(match):
        inner_math = match.group(1).replace("\n", " ")
        return f"\n$$\n{inner_math}\n$$\n"
    return re.sub(r'\\\[(.*?)\\\]', replace_brackets, text, flags=re.DOTALL)


@st.fragment(run_every=0.5)
def render_active_generation():
    job_id = st.session_state.get("generation_job_id")
    if not job_id:
        return

    job = generation_manager.get_job(job_id)
    if job is None:
        return

    with st.chat_message("assistant", avatar="rosen.png"):
        if job["error"]:
            st.error("Росен had trouble generating a response.")
            with st.expander("Technical error details"):
                st.code(job["error"])
            return

        if job["text"]:
            generation_manager.mark_first_ui(job_id)
            st.markdown(clean_latex_for_streamlit(job["text"]))
        else:
            st.write("Росен is thinking...")

    if job["done"]:
        generation_chat_id = st.session_state.get("generation_chat_id")
        if generation_chat_id and job["text"]:
            job = generation_manager.get_job(job_id) or job
            timing = {
                "first_text_seconds": job.get("first_text_seconds"),
                "first_ui_seconds": job.get("first_ui_seconds"),
                "api_first_chunk_seconds": job.get("api_first_chunk_seconds"),
                "chunk_processing_seconds": job.get("chunk_processing_seconds"),
                "total_seconds": job.get("total_seconds")
            }

            try:
                save_message(generation_chat_id, "assistant", job["text"], timing=timing)
            except Exception:
                logging.exception("Could not save Rosen response timing")

            if st.session_state.current_chat_id == generation_chat_id:
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": job["text"],
                    "timing": timing
                })

        st.session_state.generation_job_id = None
        st.session_state.generation_chat_id = None
        st.rerun()


# ============================================================
# LOGOUT
# ============================================================

def logout():
    st.session_state.force_logged_out = True
    try:
        saved_session = cookie_manager.get("rosen_session")
        if saved_session:
            cookie_manager.delete("rosen_session")
    except (KeyError, Exception):
        pass

    st.session_state.user = None
    st.session_state.messages = []
    st.session_state.current_chat_id = None
    time.sleep(0.5)
    st.rerun()


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.image("rosen.png", width=120)
    st.subheader("Росен AI")
    st.caption(st.session_state.user.get("email", ""))

    if st.button("＋ New Chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.current_chat_id = None
        st.rerun()

    st.divider()
    st.write("Saved Chats")

    try:
        saved_chats = get_saved_chats()
        if not saved_chats:
            st.caption("No saved chats yet.")

        for chat in saved_chats:
            cols = st.sidebar.columns([0.8, 0.2])
            with cols[0]:
                if st.button(chat["title"], key=f'load_{chat["id"]}', use_container_width=True):
                    load_chat(chat["id"])
                    st.rerun()
            with cols[1]:
                with st.popover("⋮", use_container_width=True):
                    st.write(f"**{chat['title']}**")
                    if st.button("Delete chat", key=f'delete_{chat["id"]}', use_container_width=True):
                        delete_chat(chat["id"])
                        st.rerun()

    except Exception as error:
        st.caption("Saved chats could not be loaded.")

    st.divider()
    if st.button("📜 Update Log", use_container_width=True):
        st.session_state.page = "update_log"
        st.rerun()

    st.divider()
    if st.button("Log out", use_container_width=True):
        logout()


# ============================================================
# UPDATE LOG
# ============================================================

if st.session_state.page == "update_log":
    if st.button("⬅ Back to Chat", use_container_width=True):
        st.session_state.page = "chat"
        st.rerun()
    st.image("rosen.png", width=150)
    st.title("Rosen.APP Update Log")
    st.markdown("""

# 🟢 ROSEN AI — PAST UPDATES

## **Version 2.5w — Cloud Development**
- Continued development of Rosen's cloud-based website.
- Improved the web interface and AI integration.
- Developed cloud-based image generation.

---

## **Version 2.6w — Image Generation Fixes**
- Fixed a bug that caused code or trace text to appear instead of generated images.
- Improved image generation reliability.
- Integrated Firebase storage for generated images.

---

## **Version 2.7w — Mobile Functionality Improvements**
- Fixed mobile image rotation issues.
- Fixed problems with deleting chats and starting new conversations.
- Improved update log opening and closing behavior.

---

## **Version 2.7.5w — Performance Improvements**
- Integrated Groq for AI text generation.
- Improved cloud-based response generation.

---

## **Version 2.9w — Memory Management**
- Introduced persistent AI memory functionality.
- Developed memory storage using Firebase Firestore.
- Improved Rosen's ability to remember information between conversations.

---

# 🟡 ROSEN AI — CURRENT UPDATES

## **Version 2.9.5w — File Handling Improvements**
**Release Date:** October 23, 2026

- Fixed file handling for various file types, including `.txt` and `.py`.
- Improved support for additional file formats.

---

## **Version 2.9.5w Emergency Bug Fix 1 — Update Log**
**Release Date:** October 23, 2026

- Fixed the update log.
- Prepared the update log for transfer to Rosen's actual website.
- Began consolidating past updates and future development plans.

---

# 🔵 ROSEN AI — FUTURE UPDATES

> **Planned Development Order:** 2.8w → 3.2w → 3.3w → Selected 3.4w Features → 2.99w → 3.0w

---

## **Version 2.8w — Mobile UI Improvements**
**Expected Release:** After 2.9.5w

- Improve Rosen's visual presentation on phones.
- Refine the mobile interface for a better user experience.
- Improve usability on smaller screens.

---

## **Version 3.2w — Massive UI Additions**
**Expected Release:** TBD

- Introduce **interactive checklists**.
- Add **interactive cards and data tables**.
- Introduce **progress bars** and other interactive elements.
- Smooth out interface corners.
- Add clickable buttons and improve responsiveness.
- Make UI interactions feel faster.

---

## **Version 3.3w — Memory Management**
**Expected Release:** TBD

- Allow users to **edit saved information**.
- Allow users to **delete saved information**.
- Introduce **temporary chats** that are not saved to persistent chat history.

---

## **Version 3.4w — AI Productivity Tools**
**Expected Release:** TBD

- Create task lists and reminders.
- Introduce schedules and study plans.
- Add reusable templates.
- Support document summarization and question answering.
- Allow users to upload PDFs, Word documents, and other supported files.

**Note:** Selected features may be implemented before versions 2.99w and 3.0w.

---

## **Version 2.99w — Final Bug Fixes**
**Expected Release:** Before 3.0w

- Fix remaining bugs before the phone release.
- Stabilize existing features.
- Test the application and prepare for release.

---

## **Version 3.0w — Official Phone Release**
**Expected Release:** December 1, 2026

- Officially release Rosen for phones.
- Deliver a polished mobile experience.
- Make Rosen accessible to family members through mobile devices.

---

## **Version 3.1w — Post-Release Bug Fixes**
**Expected Release:** TBD

- Address issues discovered after the 3.0w release.
- Improve stability and reliability.
- Fix bugs reported after launch.

---

## **Version 3.5w — Rosen Voice**
**Expected Release:** TBD

- Introduce **speech-to-text functionality**.
- Allow users to record their voice and send spoken input to Rosen as text.
- Provide a voice-input experience similar to ChatGPT and Gemini.

---

## **Version 3.6w — Custom Personalities**
**Expected Release:** TBD

- Allow users to **choose Rosen's conversational personality**.
- Introduce customizable interaction styles.
- Allow users to select their preferred way of interacting with Rosen.
""")
    if st.button("← Back to Chat", use_container_width=True):
        st.session_state.page = "chat"
        st.rerun()
    st.stop()


# ============================================================
# ROSEN CHAT
# ============================================================

st.title("Росен AI")
st.caption("v2.9.5w Cloud")


# ============================================================
# DISPLAY EXISTING MESSAGES
# ============================================================

for message_index, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"], avatar=("rosen.png" if message["role"] == "assistant" else None)):
        if message.get("type") == "image":
            image = message.get("image")
            if image is not None:
                st.image(image, use_container_width=True)
                st.download_button(
                    "Download image",
                    data=image,
                    file_name="rosen-image.png",
                    mime="image/png",
                    on_click="ignore",
                    key=f"download_{message_index}"
                )
            else:
                st.markdown(clean_latex_for_streamlit(message["content"]))

        elif message.get("type") == "screenshots":
            images = message.get("images", [])
            names = message.get("image_names", [])
            if message.get("content"):
                st.markdown(clean_latex_for_streamlit(message["content"]))
            
            for i, item in enumerate(images):
                filename = names[i] if i < len(names) else "file"
                if filename.lower().endswith((".png", ".jpg", ".jpeg")):
                    st.image(item, use_container_width=True)
                else:
                    st.caption(f"📁 Attached file: {filename}")

        else:
            st.markdown(clean_latex_for_streamlit(message["content"]))

        timing = message.get("timing")
        if message["role"] == "assistant" and timing:
            first_text = timing.get("first_text_seconds")
            first_ui = timing.get("first_ui_seconds")
            total = timing.get("total_seconds")
            timing_parts = []
            if first_text is not None:
                timing_parts.append(f"First text: {first_text:.2f}s")
            if first_ui is not None:
                timing_parts.append(f"Visible in chat: {first_ui:.2f}s")
            if total is not None:
                timing_parts.append(f"Total: {total:.2f}s")
            if timing_parts:
                st.caption("⚡ " + " · ".join(timing_parts))


# ============================================================
# CHAT INPUT
# ============================================================

chat_input = st.chat_input(
    "Message Росен or attach files/screenshots",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "txt", "py", "csv", "json", "pdf"]
)

if chat_input:
    user_message = chat_input.text.strip()
    uploaded_files = chat_input.files
else:
    user_message = ""
    uploaded_files = []


if user_message or uploaded_files:
    if not user_message and uploaded_files:
        user_message = "Please analyze the attached file(s)."

    if st.session_state.current_chat_id is None:
        st.session_state.current_chat_id = create_chat(user_message)

    chat_id = st.session_state.current_chat_id

    if uploaded_files:
        file_data = [file.getvalue() for file in uploaded_files]
        file_names = [file.name for file in uploaded_files]

        st.session_state.messages.append({
            "role": "user",
            "content": user_message,
            "type": "screenshots",
            "images": file_data,
            "image_names": file_names
        })

        processed_attachments = []
        for i, file_bytes in enumerate(file_data):
            name = file_names[i].lower()
            if name.endswith((".png", ".jpg", ".jpeg")):
                processed_attachments.append(prepare_screenshot_for_storage(file_bytes))
            else:
                # Text/code files can be stored as raw bytes / text directly
                processed_attachments.append(file_bytes)

        save_message(
            chat_id,
            "user",
            user_message,
            screenshots=processed_attachments,
            screenshot_names=file_names
        )

    else:
        st.session_state.messages.append({
            "role": "user",
            "content": user_message
        })
        save_message(chat_id, "user", user_message)

    with st.chat_message("user"):
        if user_message:
            st.markdown(clean_latex_for_streamlit(user_message))
        for uploaded_file in uploaded_files:
            if uploaded_file.name.lower().endswith((".png", ".jpg", ".jpeg")):
                st.image(uploaded_file, use_container_width=True)
            else:
                st.caption(f"📁 Attached file: {uploaded_file.name}")

    memory_action_notice = None
    if not uploaded_files and user_message:
        memory_action_notice = process_memory_command(user_message)

    conversation = ROSEN_PERSONALITY + "\n\n"
    saved_memory = load_user_memory()
    if saved_memory:
        conversation += (
            "PERSONAL MEMORY FOR THE CURRENT SIGNED-IN USER:\n"
            + "\n".join(f"- {item}" for item in saved_memory)
            + "\n\n"
        )

    if memory_action_notice:
        conversation += "MEMORY ACTION RESULT: " + memory_action_notice + "\n\n"

    for message in st.session_state.messages:
        conversation += f'{message["role"]}: {message["content"]}\n'

    if (
        not uploaded_files
        and user_message
        and not memory_action_notice
        and should_check_implicit_memory(user_message)
        and st.session_state.get("user", {}).get("uid")
    ):
        memory_extraction_executor.submit(
            extract_and_save_implicit_memory,
            st.session_state.user["uid"],
            user_message,
            conversation,
        )

    try:
        if uploaded_files:
            with st.chat_message("assistant", avatar="rosen.png"):
                with st.spinner("Росен is reviewing your files..."):
                    text_file_payloads = ""
                    groq_image_parts = []
                    google_document_parts = []

                    for uploaded_file in uploaded_files:
                        file_bytes = uploaded_file.getvalue()
                        if not file_bytes:
                            continue

                        name = uploaded_file.name.lower()
                        mime_type = uploaded_file.type or "application/octet-stream"

                        # Plain-text/code formats are included directly in the prompt.
                        if name.endswith((".txt", ".py", ".csv", ".json", ".md")):
                            try:
                                text_content = file_bytes.decode("utf-8")
                                text_file_payloads += (
                                    f"\\n\\n--- Contents of {uploaded_file.name} ---\\n"
                                    f"{text_content}\\n"
                                )
                            except Exception:
                                text_file_payloads += (
                                    f"\\n\\n--- [Could not decode {uploaded_file.name}] ---\\n"
                                )

                        # Screenshots and images go to Groq's vision model.
                        elif mime_type.startswith("image/") or name.endswith(
                            (".png", ".jpg", ".jpeg")
                        ):
                            if not mime_type.startswith("image/"):
                                mime_type = "image/jpeg" if name.endswith((".jpg", ".jpeg")) else "image/png"

                            image_data = base64.b64encode(file_bytes).decode("utf-8")
                            groq_image_parts.append({
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{mime_type};base64,{image_data}"
                                }
                            })

                        # Keep PDF/document handling on Google rather than sending
                        # non-image bytes to a vision-only Groq endpoint.
                        else:
                            google_document_parts.append(
                                types.Part.from_bytes(
                                    data=file_bytes,
                                    mime_type=mime_type
                                )
                            )

                    final_prompt = conversation + text_file_payloads
                    analysis_results = []

                    if groq_image_parts:
                        if len(groq_image_parts) > 3:
                            raise ValueError(
                                "Groq vision currently accepts up to 3 images per request. "
                                "Please attach 3 or fewer screenshots at a time."
                            )

                        groq_response = groq_client.chat.completions.create(
                            model="qwen/qwen3.8-27b",
                            messages=[{
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": final_prompt},
                                    *groq_image_parts
                                ]
                            }],
                            temperature=0.2,
                            max_completion_tokens=2048
                        )
                        groq_text = groq_response.choices[0].message.content
                        if groq_text:
                            analysis_results.append(groq_text)

                    if google_document_parts:
                        google_document_parts.append(final_prompt)
                        google_response = client.models.generate_content(
                            model="gemma-4-26b-a4b-it",
                            contents=google_document_parts
                        )
                        google_text = google_response.text
                        if google_text:
                            analysis_results.append(google_text)

                    assistant_text = "\\n\\n".join(analysis_results).strip()
                    if not assistant_text:
                        assistant_text = "Росен could not analyze the uploaded files."
                    st.markdown(clean_latex_for_streamlit(assistant_text))

            st.session_state.messages.append({
                "role": "assistant",
                "content": assistant_text
            })
            save_message(chat_id, "assistant", assistant_text)

        elif is_image_request(user_message):
            with st.chat_message("assistant", avatar="rosen.png"):
                with st.spinner("Росен is creating your image..."):
                    status_box = st.empty()
                    generated_image = generate_image(user_message, status_box)
                status_box.empty()

                if generated_image is None:
                    st.error("Росен could not generate the image.")
                else:
                    st.image(generated_image, use_container_width=True)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"Generated image: {user_message}",
                        "type": "image",
                        "image": generated_image,
                    })
                    save_message(chat_id, "assistant", f"Generated image: {user_message}", image=generated_image)

        else:
            generation_job_id = generation_manager.submit(conversation)
            st.session_state.generation_job_id = generation_job_id
            st.session_state.generation_chat_id = chat_id

    except Exception as error:
        st.error("Росен had trouble generating a response. Your message was still saved.")
        st.exception(error)

render_active_generation()
