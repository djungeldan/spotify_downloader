import ctypes
import os

db_path_b = br"C:\Users\dnlsl\AppData\Roaming\Pioneer\rekordbox\master.db"
key_hex = b"402fd482c38817c35ffa8ffb8c7d93143b749e7d315df7a81732a1ff43608497"

try:
    dll_path = os.path.abspath('sqlcipher.dll')
    sqlite = ctypes.CDLL(dll_path)

    db = ctypes.c_void_p()
    sqlite.sqlite3_open(db_path_b, ctypes.byref(db))

    errMsg = ctypes.c_char_p()
    @ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p))
    def callback(user_data, argc, argv, azColName):
        print("Row:", [ctypes.string_at(argv[i]).decode('utf-8') if argv[i] else None for i in range(argc)])
        return 0

    print("Checking SQLCipher version...")
    sqlite.sqlite3_exec(db, b"PRAGMA cipher_version;", callback, None, ctypes.byref(errMsg))
    
    # The trick with sqlcipher raw keys is using the exact 64 char string
    # or using sqlite3_key passing the raw bytes
    
    pragmas = b"""
    PRAGMA key = '402fd482c38817c35ffa8ffb8c7d93143b749e7d315df7a81732a1ff43608497';
    PRAGMA cipher_page_size = 4096;
    PRAGMA kdf_iter = 64000;
    PRAGMA cipher_hmac_algorithm = HMAC_SHA1;
    PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1;
    """
    
    sqlite.sqlite3_exec(db, pragmas, None, None, ctypes.byref(errMsg))
    res = sqlite.sqlite3_exec(db, b"SELECT count(*) FROM sqlite_master;", callback, None, ctypes.byref(errMsg))
    if errMsg:
        print("String Key Error:", ctypes.string_at(errMsg))

    # If that fails, try sqlite3_key
    if res != 0:
        print("Trying sqlite3_key...")
        sqlite.sqlite3_close(db)
        db = ctypes.c_void_p()
        sqlite.sqlite3_open(db_path_b, ctypes.byref(db))
        
        sqlite.sqlite3_key(db, key_hex, len(key_hex))
        sqlite.sqlite3_exec(db, b"PRAGMA cipher_page_size = 4096;", None, None, None)
        sqlite.sqlite3_exec(db, b"PRAGMA kdf_iter = 64000;", None, None, None)
        sqlite.sqlite3_exec(db, b"PRAGMA cipher_hmac_algorithm = HMAC_SHA1;", None, None, None)
        sqlite.sqlite3_exec(db, b"PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1;", None, None, None)

        errMsg = ctypes.c_char_p()
        res = sqlite.sqlite3_exec(db, b"SELECT count(*) FROM sqlite_master;", callback, None, ctypes.byref(errMsg))
        if errMsg:
            print("sqlite3_key Error:", ctypes.string_at(errMsg))
            
    sqlite.sqlite3_close(db)
except Exception as e:
    print("CTypes Error:", e)
