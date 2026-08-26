import requests
import json
import time
import sys

API_URL = "http://localhost:3000/api"
PLAYLIST_URL = "https://open.spotify.com/playlist/4xNPSp175sQcPJ1237IhLY?si=7f9d8636f17b4d8c"

def reset_state():
    # Optional: Clear state if needed, but for now we just overwrite
    pass

def import_playlist():
    print(f"Importing playlist: {PLAYLIST_URL}")
    try:
        res = requests.post(f"{API_URL}/playlist", json={"url": PLAYLIST_URL})
        res.raise_for_status()
        data = res.json()
        print(f"Imported {len(data['tracks'])} tracks from '{data['playlist_name']}'")
        return data['tracks']
    except Exception as e:
        print(f"Import failed: {e}")
        sys.exit(1)

def trigger_search_all():
    print("Triggering search for all tracks...")
    try:
        res = requests.post(f"{API_URL}/search/all", json=[])
        res.raise_for_status()
        print(f"Search started: {res.json()}")
    except Exception as e:
        print(f"Search trigger failed: {e}")
        sys.exit(1)

def monitor_status(expected_count):
    print("Monitoring track status...")
    start_time = time.time()
    while True:
        try:
            res = requests.get(f"{API_URL}/tracks")
            data = res.json()
            tracks = data['tracks']
            
            searching = len([t for t in tracks if t.get('status') == 'searching'])
            found = len([t for t in tracks if t.get('status') == 'found' or t.get('selectedMatch')])
            error = len([t for t in tracks if t.get('status') == 'error'])
            idle = len([t for t in tracks if t.get('status') == 'idle'])
            
            print(f"Status: Searching={searching}, Found={found}, Error={error}, Idle={idle} (Total: {len(tracks)})")
            
            if searching == 0 and idle == 0:
                print("Search complete.")
                break
            
            if time.time() - start_time > 300: # 5 min timeout
                print("Timeout waiting for search.")
                break
                
            time.sleep(2)
        except Exception as e:
            print(f"Monitor failed: {e}")
            time.sleep(2)

if __name__ == "__main__":
    tracks = import_playlist()
    trigger_search_all()
    monitor_status(len(tracks))
