"""
ai_ops.py
=========
Handles AI Assistant natural language interactions and command parsing using local Ollama.
"""
import json as _json
import requests as _req

def parse_ai_command(cmd, model, city_name, base_path):
    system_prompt = f"""You are a command parser for a traffic modeling tool called RapidXPy inside Isaac Sim.
Current city: {city_name}
Current base path: {base_path}

Parse the user's natural language command and return ONLY a valid JSON object -no markdown, no explanation, no extra text.

Available actions:
{{"action": "set_city",          "city": "<full city name, country>"}}
{{"action": "download_network",  "city": "<full city name, country>"}}
{{"action": "build_network",     "city": "<full city name, country>"}}
{{"action": "load_network"}}
{{"action": "apply_zoning",      "zone_type": "H3",     "resolution": 7}}
{{"action": "apply_zoning",      "zone_type": "Square", "resolution": 7}}
{{"action": "fetch_travel_times","api": "tomtom"}}
{{"action": "fetch_travel_times","api": "google"}}
{{"action": "fetch_travel_times","api": "mapbox"}}
{{"action": "fix_network"}}
{{"action": "unknown",           "message": "<brief reason why the command cannot be executed>"}}

Rules:
- Return ONLY the JSON object. No other text at all.
- If a city name is mentioned, use it; otherwise use the current city.
- Default zone_type = H3, default resolution = 7.
- Default travel time api = tomtom.
- If the command is ambiguous but close to an action, pick the best match."""
    
    resp = _req.post(
        "http://localhost:11434/api/chat",
        json={
            "model":  model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": cmd},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    raw = resp.json()["message"]["content"].strip()
    # Strip markdown code fences if the model wraps the JSON
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.lower().startswith("json"):
            raw = raw[4:]
    return _json.loads(raw.strip())

def query_chatbot(query, model, city_name):
    system_ctx = (
        f"You are a traffic modeling assistant inside Isaac Sim (Omniverse). "
        f"The user is using RapidXPy for city: {city_name}. "
        f"Available operations: Download Network, Apply Zoning, Load Network (3D USD), "
        f"Fetch Travel Times (TomTom/Google/Mapbox), PCU Estimation (genetic algorithm), "
        f"MSA/FW/CFW/BFW Traffic Assignment, Fix Network Speeds, "
        f"Accessibility Analysis, Animate Traffic Flow in 3D, Style Intersection Nodes, "
        f"Add Volume Labels, Gini Accessibility, CO₂ Emissions Analysis, "
        f"Noise Leq Analysis, Zone Metric Layers in USD stage. "
        f"Answer concisely (300 words max). Help the user understand next steps or interpret results."
    )

    resp = _req.post(
        "http://localhost:11434/api/chat",
        json={
            "model":  model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_ctx},
                {"role": "user",   "content": query},
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]