#! /usr/bin/env python3

import os
import json
import urllib.request
import datetime
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
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
    except Exception as e:
        print(f"Error fetching HA state: {e}", flush=True)
        return []

def get_history(base_url, token, entity_ids, hours=24):
    """
    Fetches history for specific entities.
    """
    start_time = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    ids_str = ",".join(entity_ids)
    # minimal_response=true reduces payload size (no attributes)
    url = f"{base_url}/api/history/period/{start_time}?filter_entity_id={ids_str}&minimal_response=true"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"Error fetching history: {e}", flush=True)
        return []

def create_line_chart(histories):
    """
    Generates a simple SVG line chart from HA history data.
    """
    if not histories:
        return ""
        
    width = 600
    height = 150
    padding = 20
    
    parsed_series = []
    all_temps = []
    all_times = []
    
    for series in histories:
        if not series: continue
        points = []
        name = series[0].get('entity_id')
        
        for point in series:
            try:
                val = float(point['state'])
                # Handle Zulu time or standard ISO
                ts_str = point['last_changed'].replace('Z', '+00:00')
                dt = datetime.datetime.fromisoformat(ts_str)
                ts = dt.timestamp()
                
                points.append((ts, val))
                all_temps.append(val)
                all_times.append(ts)
            except (ValueError, TypeError):
                continue
        
        if points:
            parsed_series.append({'name': name, 'points': points})

    if not parsed_series:
        return ""

    min_t, max_t = min(all_times), max(all_times)
    min_y, max_y = min(all_temps), max(all_temps)
    
    # Add buffer
    min_y -= 1
    max_y += 1
    if max_t == min_t: max_t += 1
    if max_y == min_y: max_y += 1
    
    def scale_x(t):
        return padding + (t - min_t) / (max_t - min_t) * (width - 2 * padding)
    
    def scale_y(y):
        return height - padding - (y - min_y) / (max_y - min_y) * (height - 2 * padding)

    svg_lines = []
    colors = ["black", "#666666"] # High contrast for Kindle
    
    for i, s in enumerate(parsed_series):
        pts_str = ""
        for t, y in s['points']:
            sx = scale_x(t)
            sy = scale_y(y)
            pts_str += f"{sx:.1f},{sy:.1f} "
        
        color = colors[i % len(colors)]
        # Dashed line for secondary series
        dash = 'stroke-dasharray="5,5"' if i > 0 else ""
        svg_lines.append(f'<polyline points="{pts_str.strip()}" fill="none" stroke="{color}" stroke-width="2" {dash} />')

    svg = f"""
    <div style="margin-top: 15px;">
        <h3>Termperature (24h)</h3>
        <svg width="100%" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="border: 1px solid #eee;">
            <!-- Grid -->
            <line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height-padding}" stroke="#ddd" stroke-width="1"/>
            <line x1="{padding}" y1="{height-padding}" x2="{width-padding}" y2="{height-padding}" stroke="#ddd" stroke-width="1"/>
            
            <!-- Labels -->
            <text x="{padding+5}" y="{padding+10}" font-family="sans-serif" font-size="12" fill="black">{max_y:.0f}°</text>
            <text x="{padding+5}" y="{height-padding-5}" font-family="sans-serif" font-size="12" fill="black">{min_y:.0f}°</text>
            <text x="{width/2}" y="{height-5}" text-anchor="middle" font-family="sans-serif" font-size="10" fill="#666">Time &rarr;</text>
            
            {''.join(svg_lines)}
        </svg>
        <p style="font-size: 10px; color: #666;">Solid: Living Room | Dashed: Office</p>
    </div>
    """
    
    return svg

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
        
        if domain not in interesting_domains:
            continue
            
        state = entity.get("state")
        if state in ["unavailable", "unknown"]:
            continue

        attributes = entity.get("attributes", {})
        friendly_name = attributes.get("friendly_name", entity_id)
        
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
    ha_context = json.dumps(filtered_states, indent=2)
    
    # 1b. Fetch History for Graph
    # Using Living Room and Office as they were found in discovery
    graph_entities = ["sensor.living_room_thermostat_temperature", "sensor.office_thermostat_temperature"]
    history_data = get_history(ha_url, ha_token, graph_entities)
    svg_chart = create_line_chart(history_data)

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

Keep it concise, bulleted, and friendly. Do not use emojis or icons (Kindle does not render them). Use plain text symbols if needed (like * or -).

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
        <h2>Home Status</h2>
        <p>{formatted_text}</p>
        {svg_chart}
    </div>
    """

    print(html_content)

if __name__ == "__main__":
    main()
