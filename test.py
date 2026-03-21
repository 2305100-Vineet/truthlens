import requests

# Test Nitter instances
query = "5G+COVID+fake"
instances = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]
for inst in instances:
    try:
        r = requests.get(f"{inst}/search/rss?q={query}&f=tweets", timeout=5)
        print(f"{inst} -> {r.status_code}")
    except Exception as e:
        print(f"{inst} -> FAILED: {e}")

# Test Gemini
try:
    r2 = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=AIzaSyC5KsJnfJ3uDBkWSFfVgtu0-Ih7FKBbcvo",
        json={"contents": [{"parts": [{"text": "say hello"}]}]},
        timeout=10
    )
    print("Gemini ->", r2.status_code, r2.text[:300])
except Exception as e:
    print("Gemini -> FAILED:", e)