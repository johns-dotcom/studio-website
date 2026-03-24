#!/usr/bin/env python3
"""
Run this locally to export your token.pickle as a base64 string.
Copy the output and paste it as the GOOGLE_TOKEN_PICKLE environment variable on Render.
"""
import base64
from pathlib import Path

token_path = Path(__file__).parent / "token.pickle"
if not token_path.exists():
    print("ERROR: token.pickle not found. Run the server locally first to authorize Google Calendar.")
else:
    with open(token_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
    print("\n📋 Copy EVERYTHING below this line and paste it as GOOGLE_TOKEN_PICKLE on Render:\n")
    print(encoded)
    print(f"\n(Length: {len(encoded)} characters)\n")
