import os
import streamlit as st
from google import genai

api_key = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=api_key)

st.title("Росен AI")
st.caption("v2.5 Cloud Test")

user_message = st.text_input("Message Росен")

if st.button("Send") and user_message:
    with st.spinner("Росен is thinking..."):
        response = client.models.generate_content(
            model="gemma-4-26b-a4b-it",
            contents=user_message
        )

    st.write(response.text)