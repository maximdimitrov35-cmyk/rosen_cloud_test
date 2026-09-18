import os
import streamlit as st
from google import genai
import json
import firebase_admin
from firebase_admin import credentials, firestore

api_key = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=api_key)
firebase_info = json.loads(st.secrets["FIREBASE_SERVICE_ACCOUNT"])

@st.cache_resource
def initialize_firebase():
    cred = credentials.Certificate(firebase_info)

    try:
        app = firebase_admin.get_app()
    except ValueError:
        app = firebase_admin.initialize_app(cred)

    return firestore.client(app=app)

db = initialize_firebase()

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
