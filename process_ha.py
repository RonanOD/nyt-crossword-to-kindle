#! /usr/bin/env python3

import os
import json
import urllib.request
from datetime import date
from google import genai
from dotenv import load_dotenv

def get_ha_state(base_url, token):
    """
    Fetches all states from Home Assistant API.
    """
    url = f"{base_url}/api/states"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f"Error fetching HA state: {e}", flush=True)
        return []

def filter_states(states):
    """
    Filters the massive state list down to interesting entities.
    """
    interesting_domains = [
        "sensor", "binary_sensor", "climate", "switch", "light", "lock", "cover", "calendar", "weather"
    ]
    
    filtered = []
    for entity in states:
        entity_id = entity.get("entity_id", "")
        domain = entity_id.split(".")[0]
        
        # Skip boring stuff
        if domain not in interesting_domains:
            continue
            
        # Skip unavailable/unknown unless critical
        state = entity.get("state")
        if state in ["unavailable", "unknown"]:
            continue

        attributes = entity.get("attributes", {})
        friendly_name = attributes.get("friendly_name", entity_id)
        
        # Build a simplified object for the LLM
        item = {
            "name": friendly_name,
            "entity_id": entity_id,
            "state": state,
            "unit": attributes.get("unit_of_measurement", ""),
            "class": attributes.get("device_class", "")
        }
        filtered.append(item)
        
    return filtered

def main():
    load_dotenv()

    # HA Config
    ha_url = os.getenv("HA_URL", "http://192.168.68.104:8123")
    ha_token = os.getenv("HA_TOKEN")
    
    # Gemini Config
    gemini_key = os.getenv("GEMINI_API_KEY")

    if not ha_token:
        print("<div id='ha-update'><p>Error: HA_TOKEN not set.</p></div>")
        return

    # 1. Fetch HA Data
    states = get_ha_state(ha_url, ha_token)
    filtered_states = filter_states(states)
    
    # Convert to JSON string for prompt
    ha_context = json.dumps(filtered_states, indent=2)

    # 2. Generate Content with Gemini
    if gemini_key:
        client = genai.Client(api_key=gemini_key)
        today = date.today().strftime("%B %d, %Y")
        
        prompt = f"""It is {today}.
        
I am providing you with the JSON state of my Smart Home (Home Assistant). 
Please analyze it and write a short, engaging "Daily Home Status" briefing.

Key things to look for and summarize:
- Weather forecast (if available).
- Climate status (indoor temps, thermostat settings).
- Any doors/windows left open?
- High energy usage?
- Battery levels of devices (warn if any are low).
- Upcoming calendar events (trash pickup?).
- Anything else unusual or interesting.

Keep it concise, bulleted, and friendly. Use emojis. 🏠⚡🌡️

HOME DATA:
{ha_context}
"""
        try:
            response = client.models.generate_content(
                model='gemini-2.0-flash-001',
                contents=prompt
            )
            formatted_text = response.text.replace('\n', '<br>')
        except Exception as e:
            formatted_text = f"Error generating summary: {e}"
    else:
        formatted_text = "GEMINI_API_KEY not set. Cannot generate summary."

    # 3. Output HTML
    html_content = f"""
    <div id="ha-daily-update" style="font-family: sans-serif; padding: 10px; border: 1px solid #ccc; border-radius: 5px;">
        <h2>🏠 Home Status</h2>
        <p>{formatted_text}</p>
    </div>
    """

    print(html_content)

if __name__ == "__main__":
    main()
