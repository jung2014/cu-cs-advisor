import requests
import json

# Test the API call with the exact parameters from user's cURL
headers = {
    'sec-ch-ua-platform': '"macOS"',
    'Referer': 'https://classes.colorado.edu/',
    'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    'sec-ch-ua-mobile': '?0',
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'Content-Type': 'application/json',
}

params = {
    'page': 'fose',
    'route': 'details',
}

# The data from user's cURL (URL decoded)
data = '{"group":"code:CSCI 1000","key":"crn:31433","srcdb":"2267","matched":"crn:31433"}'

print("Testing API call...")
try:
    response = requests.post('https://classes.colorado.edu/api/', params=params, headers=headers, data=data, timeout=10)
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:500]}")
    if response.status_code == 200:
        try:
            json_data = response.json()
            print("JSON parsed successfully")
            print(f"Keys: {list(json_data.keys()) if isinstance(json_data, dict) else 'Not a dict'}")
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
except Exception as e:
    print(f"Error: {e}")