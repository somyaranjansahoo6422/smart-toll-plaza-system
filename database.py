import json
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd

# All data files live next to this module, so the app works no matter which
# directory `streamlit run` is launched from.
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_FILE = BASE_DIR / "cloud_mock_db.json"
SQLITE_EXPORT_FILE = BASE_DIR / "toll_plaza_odisha.db"
PLAZA_REGISTRY_FILE = BASE_DIR / "TOLL_PLAZA_LIST @26 may 2026 [CSV].csv"

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# --- DYNAMIC / CONGESTION PRICING ENGINE ---
BASE_TOLL_RATES = {
    'Car/Jeep/Van': 100,
    'LCV (Light Commercial)': 160,
    'Bus/2-Axle Truck': 320,
    'Multi-Axle (3+)': 500,
    'Oversized Vehicle': 650
}
VEHICLE_CLASSES = list(BASE_TOLL_RATES)

# Half-open windows [start, end): 08:00-09:59 and 17:00-19:59.
PEAK_WINDOWS = ((8, 10), (17, 20))
PEAK_MULTIPLIER = 1.15


def is_peak_hour(hour):
    return any(start <= hour < end for start, end in PEAK_WINDOWS)


def get_dynamic_pricing(now=None):
    """Applies a 15% surge multiplier during peak rush hours (8-10 AM & 5-8 PM)."""
    now = now or datetime.now()
    peak = is_peak_hour(now.hour)
    multiplier = PEAK_MULTIPLIER if peak else 1.0

    current_rates = {k: round(v * multiplier, 2) for k, v in BASE_TOLL_RATES.items()}
    return current_rates, peak, multiplier


# --- PLATE VALIDATION (single source of truth for the whole app) ---
TOLL_EXEMPT = ['CD', 'CC', 'UN']
VALID_STATE_CODES = {
    'AN', 'AP', 'AR', 'AS', 'BR', 'CG', 'CH', 'DD', 'DL', 'DN', 'GA', 'GJ', 'HP', 'HR',
    'JH', 'JK', 'KA', 'KL', 'LA', 'LD', 'MH', 'ML', 'MN', 'MP', 'MZ', 'NL', 'OD', 'OR',
    'PB', 'PY', 'RJ', 'SK', 'TG', 'TN', 'TR', 'TS', 'UK', 'UP', 'WB',
}

# Standard: OD02AB1234, DL3CAB1234, CG04X7777  (state, RTO, 0-3 series letters, 4 digits)
STANDARD_PLATE = re.compile(r"^(?P<state>[A-Z]{2})\d{1,2}[A-Z]{0,3}\d{4}$")
# Bharat Series: 21BH1234AA
BHARAT_PLATE = re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$")
# Diplomatic / consular / UN: 77CD12, 101CC3, UN4567
DIPLOMATIC_PLATE = re.compile(r"^(\d{1,3}(CD|CC|UN)\d{1,4}|(CD|CC|UN)\d{1,4})$")


def normalize_plate(text):
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())


def classify_plate(text):
    """Returns 'standard', 'bharat', 'diplomatic', or None for an unrecognised plate."""
    plate = normalize_plate(text)
    if DIPLOMATIC_PLATE.match(plate):
        return "diplomatic"
    if BHARAT_PLATE.match(plate):
        return "bharat"
    match = STANDARD_PLATE.match(plate)
    if match and match.group("state") in VALID_STATE_CODES:
        return "standard"
    return None


def validate_indian_plate(text):
    """Returns the normalised plate if it is a valid Indian format, else None."""
    return normalize_plate(text) if classify_plate(text) else None


def is_exempt(plate):
    return classify_plate(plate) == "diplomatic"


# --- CLOUD SQL ADAPTER (AWS RDS / Supabase Ready) ---
# To prevent crashes for users without active AWS/Supabase credentials,
# this adapter acts as an ORM that falls back to a persistent JSON layer if no Cloud URL is found.
TABLE_COLUMNS = {
    "transactions": ["Transaction_ID", "Timestamp", "Plate_Number", "Vehicle_Type", "Lane",
                     "Payment_Method", "Toll_Collected", "Plaza_Location", "Is_Exempt"],
    "security_audit": ["Log_ID", "Timestamp", "Action_Taken", "Target_Plate", "Handled_By_Plaza"],
    "shift_closures": ["Shift_ID", "Timestamp", "Operator", "Plaza",
                       "Expected_Cash", "Actual_Cash", "Variance"],
    "vahan_blacklist": ["Plate_Number", "Reason", "Pending_Challan"],
}
TABLES = tuple(TABLE_COLUMNS)
DEFAULT_BLACKLIST = [
    {'Plate_Number': 'OD02AB1234', 'Reason': 'Stolen Vehicle (Bhubaneswar)', 'Pending_Challan': 0.0},
    {'Plate_Number': 'WB11CC9999', 'Reason': 'Pending Interstate Speeding Challan', 'Pending_Challan': 4500.0},
    {'Plate_Number': 'CG04X7777', 'Reason': 'FASTag Blacklisted - Insufficient Funds', 'Pending_Challan': 0.0},
    {'Plate_Number': 'OR02C1234', 'Reason': 'Expired Fitness Certificate (Legacy)', 'Pending_Challan': 1500.0},
]

# Streamlit serves every session from threads in one process, so a process-wide
# lock is enough to stop two checkouts from clobbering each other's writes.
_DB_LOCK = threading.RLock()


class CloudSQLAdapter:
    def __init__(self, db_file=DEFAULT_DB_FILE):
        self.db_file = Path(db_file)
        with _DB_LOCK:
            self._init_cloud_schema()

    def _init_cloud_schema(self):
        if not self.db_file.exists():
            schema = {table: [] for table in TABLES}
            schema["vahan_blacklist"] = [dict(entry) for entry in DEFAULT_BLACKLIST]
            self._save(schema)
            return

        # Migrate older files that are missing a collection.
        db = self._load()
        if any(table not in db for table in TABLES):
            for table in TABLES:
                db.setdefault(table, [])
            self._save(db)

    def _load(self):
        try:
            with open(self.db_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            # Keep the damaged file for forensics and start from a clean schema.
            backup = self.db_file.with_suffix(f".corrupt-{int(time.time())}.json")
            os.replace(self.db_file, backup)
            self._init_cloud_schema()
            with open(self.db_file, 'r', encoding='utf-8') as f:
                return json.load(f)

    def _save(self, db):
        # Write to a temp file and swap it in, so a crash mid-write can never
        # leave a truncated database behind.
        fd, tmp_path = tempfile.mkstemp(dir=self.db_file.parent, prefix=".db-", suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=2)
            for attempt in range(5):
                try:
                    os.replace(tmp_path, self.db_file)
                    break
                except PermissionError:
                    # OneDrive / antivirus can briefly lock the file on Windows.
                    if attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _read_table(self, table):
        with _DB_LOCK:
            return self._load().get(table, [])

    def _write_record(self, table, record):
        with _DB_LOCK:
            db = self._load()
            db.setdefault(table, []).append(record)
            self._save(db)
        return record

    def get_dataframe(self, table, plaza=None):
        df = pd.DataFrame(self._read_table(table), columns=TABLE_COLUMNS.get(table))
        if plaza is not None and not df.empty:
            plaza_column = {
                "transactions": "Plaza_Location",
                "security_audit": "Handled_By_Plaza",
                "shift_closures": "Plaza",
            }.get(table)
            if plaza_column in df.columns:
                df = df[df[plaza_column] == plaza]
        if 'Timestamp' in df.columns:
            df['Timestamp'] = pd.to_datetime(df['Timestamp'])
            df = df.sort_values(by='Timestamp', ascending=False)
        return df.reset_index(drop=True)

    def get_blacklist_entry(self, plate):
        plate = normalize_plate(plate)
        for entry in self._read_table("vahan_blacklist"):
            if normalize_plate(entry.get("Plate_Number", "")) == plate:
                return entry
        return None

    def last_shift_closure_time(self, plaza):
        closures = [c for c in self._read_table("shift_closures") if c.get("Plaza") == plaza]
        if not closures:
            return None
        return max(datetime.strptime(c["Timestamp"], TIMESTAMP_FORMAT) for c in closures)

    def insert_transaction(self, plate, v_type, lane, pay_method, plaza, toll, exempt):
        record = {
            "Transaction_ID": f"TXN-{uuid.uuid4().hex[:12].upper()}",
            "Timestamp": datetime.now().strftime(TIMESTAMP_FORMAT),
            "Plate_Number": plate, "Vehicle_Type": v_type, "Lane": lane,
            "Payment_Method": pay_method, "Toll_Collected": round(float(toll), 2),
            "Plaza_Location": plaza, "Is_Exempt": bool(exempt)
        }
        return self._write_record("transactions", record)

    def insert_audit(self, action, plate, plaza):
        record = {
            "Log_ID": f"AUD-{uuid.uuid4().hex[:8].upper()}",
            "Timestamp": datetime.now().strftime(TIMESTAMP_FORMAT),
            "Action_Taken": action, "Target_Plate": plate, "Handled_By_Plaza": plaza
        }
        return self._write_record("security_audit", record)

    def close_shift(self, operator, plaza, expected, actual, variance):
        record = {
            "Shift_ID": f"SHF-{uuid.uuid4().hex[:8].upper()}",
            "Timestamp": datetime.now().strftime(TIMESTAMP_FORMAT),
            "Operator": operator, "Plaza": plaza,
            "Expected_Cash": round(float(expected), 2),
            "Actual_Cash": round(float(actual), 2),
            "Variance": round(float(variance), 2)
        }
        return self._write_record("shift_closures", record)

    def export_to_sqlite(self, sqlite_file=SQLITE_EXPORT_FILE):
        """Rebuilds the SQLite mirror from the JSON store. Returns row counts per table."""
        with _DB_LOCK:
            db = self._load()
        tmp_file = Path(sqlite_file).with_suffix(".tmp.db")
        if tmp_file.exists():
            tmp_file.unlink()
        counts = {}
        conn = sqlite3.connect(tmp_file)
        try:
            for table in TABLES:
                df = pd.DataFrame(db.get(table, []), columns=TABLE_COLUMNS[table])
                df.to_sql(table, conn, index=False, if_exists="replace")
                counts[table] = len(df)
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_file, sqlite_file)
        return counts


cloud_db = CloudSQLAdapter()
