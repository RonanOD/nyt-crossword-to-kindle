#! /usr/bin/env python3

from process_email import fetch_recent_emails
import os
from datetime import date
from google import genai
from dotenv import load_dotenv

def main():
    """
    Generates a daily update using the Gemini API and prints it as an HTML snippet.
    """
    load_dotenv()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")

    client = genai.Client(api_key=api_key)

    try:
        with open("USER_CONTEXT.md", "r") as f:
            user_context = f.read()
    except FileNotFoundError:
        user_context = "No user context file found."

    # Fetch recent emails
    email_summary = fetch_recent_emails(hours=24)

    today = date.today().strftime("%B %d, %Y")

    prompt = f"""It is {today}. based on my context below, give me a short, bulleted daily briefing. 
Include a weather note for Dartmouth NS, a chess puzzle, and a specific suggestion for my fiddle or piano practice.

Also, I have included a list of my recent emails below. Please summarize them and highlight any important personal messages or action items. If there are no important emails, you can say "No important new emails".

CONTEXT: 
{user_context}

RECENT EMAILS:
{email_summary}
"""
    
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash-001',
            contents=prompt
        )
    except Exception as e:
        import sys
        print(f"Error generating content: {e}", flush=True)
        # Detailed debugging for model availability might differ in new SDK, 
        # keeping it simple for now as the main goal is migration.
        raise e

    formatted_text = response.text.replace('\n', '<br>')
    html_content = f"""
    <div id="gemini-daily-update">
        <h2>Daily Update</h2>
        <p>{formatted_text}</p>
    </div>
    """

    print(html_content)

if __name__ == "__main__":
    main()
