#!/usr/bin/env python3

import os
from datetime import date
import google.generativeai as genai
from dotenv import load_dotenv

def main():
    """
    Generates a daily update using the Gemini API and prints it as an HTML snippet.
    """
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")

    genai.configure(api_key=api_key)

    model = genai.GenerativeModel('gemini-pro')

    try:
        with open("USER_CONTEXT.md", "r") as f:
            user_context = f.read()
    except FileNotFoundError:
        user_context = "No user context file found."

    today = date.today().strftime("%B %d, %Y")

    prompt = f"""It is {today}. Based on my context below, give me a short, bulleted daily briefing. 
Include a weather note for Dartmouth NS, a chess puzzle, and a specific suggestion for my fiddle or piano practice.
CONTEXT: {user_context}"""
    
    response = model.generate_content(prompt)

    html_content = f"""
    <div id="gemini-daily-update">
        <h2>Daily Update</h2>
        <p>{response.text.replace('\n', '<br>')}</p>
    </div>
    """

    print(html_content)

if __name__ == "__main__":
    main()
