import os
import streamlit as st
from google import genai

api_key = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=api_key)

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
