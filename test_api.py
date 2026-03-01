import requests
import json

def test_chat():
    url = "http://localhost:3000/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer changeme_local_only"
    }
    data = {
        "model": "salesmanchatbot-pro",
        "messages": [
            {"role": "user", "content": "hi"}
        ],
        "stream": False
    }

    print(f"Sending request to {url}...")
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        print(f"Status Code: {response.status_code}")
        print("Response Body:")
        print(json.dumps(response.json(), indent=2))
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == "__main__":
    test_chat()
