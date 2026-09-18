import os
import streamlit as st
from google import genai
import json
import firebase_admin
from firebase_admin import credentials, firestore
import requests

api_key = os.getenv("GOOGLE_API_KEY")
firebase_web_api_key = st.secrets["FIREBASE_WEB_API_KEY"]
client = genai.Client(api_key=api_key)
firebase_info = json.loads(st.secrets["FIREBASE_SERVICE_ACCOUNT"])

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

    response = requests.post(url, json=payload)

    return response.json()

@st.cache_resource
def initialize_firebase():
    cred = credentials.Certificate(firebase_info)

    try:
        app = firebase_admin.get_app()
    except ValueError:
        app = firebase_admin.initialize_app(cred)

    return firestore.client(app=app)

db = initialize_firebase()

st.subheader("Create Account")

register_email = st.text_input("Email", key="register_email")
register_password = st.text_input(
    "Password",
    type="password",
    key="register_password"
)

if st.button("Create Account"):
    result = register_user(register_email, register_password)

    if "localId" in result:
        st.success("Account created successfully!")
        st.write("User ID:", result["localId"])
    else:
        error_message = result.get("error", {}).get(
            "message",
            "Unknown registration error"
        )
        st.error(error_message)
st.title("Росен AI")
st.caption("v2.5 Cloud Test")
if "messages" not in st.session_state:
    st.session_state.messages = []
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

user_message = st.chat_input("Message Росен")

if user_message:
    st.session_state.messages.append({
        "role": "user",
        "content": user_message
    })

    with st.chat_message("user"):
        st.write(user_message)
        
    conversation = ""

    for message in st.session_state.messages:
        conversation += f'{message["role"]}: {message["content"]}\n'
    with st.chat_message("assistant"):
        with st.spinner("Росен is thinking..."):
            response = client.models.generate_content(
                model="gemma-4-26b-a4b-it",
                contents=conversation
            )
            
            

    st.write(response.text)

    st.session_state.messages.append({
        "role": "assistant",
        "content": response.text
    })
