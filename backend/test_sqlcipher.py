import sqlite3
import os
import sys

db_path = r'C:\Users\dnlsl\AppData\Roaming\Pioneer\rekordbox\master.db'
key = '402fd482c38817c35ffa8ffb8c7d93143b749e7d315df7a81732a1ff43608497'

try:
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    dll_path = os.path.abspath('sqlcipher.dll')
    print("Loading DLL from:", dll_path)
    conn.load_extension(dll_path)
    
    c = conn.cursor()
    c.execute(f"PRAGMA key = '{key}';")
    c.execute("PRAGMA cipher_page_size = 4096;")
    c.execute("PRAGMA kdf_iter = 64000;")
    c.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
    c.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1;")
    c.execute("SELECT count(*) FROM sqlite_master;")
    print("Success! Table count:", c.fetchone()[0])

    c.execute("SELECT Title, ArtistName, BitRate FROM djmdContent LIMIT 5;")
    for row in c.fetchall():
        print(row)

except Exception as e:
    print("Error:", e)
    sys.exit(1)
