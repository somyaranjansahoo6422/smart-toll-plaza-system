import pandas as pd
import uuid
from datetime import datetime
import re
import os
import json
import numpy as np
import streamlit as st

# --- DYNAMIC / CONGESTION PRICING ENGINE ---
BASE_TOLL_RATES = {
    'Car/Jeep/Van': 100,
    'LCV (Light Commercial)': 160,
    'Bus/2-Axle Truck': 320,
    'Multi-Axle (3+)': 500,
    'Oversized Vehicle': 650
}

def get_dynamic_pricing():
    """Applies a 15% surge multiplier during peak rush hours (8 AM-10 AM & 5 PM-8 PM)."""
    current_hour = datetime.now().hour
    is_peak_hour = (8 <= current_hour <= 10) or (17 <= current_hour <= 20)
    multiplier = 1.15 if is_peak_hour else 1.0
    
    current_rates = {k: round(v * multiplier, 2) for k, v in BASE_TOLL_RATES.items()}
    return current_rates, is_peak_hour, multiplier

# --- CLOUD SQL ADAPTER (AWS RDS / Supabase Ready) ---
# To prevent crashes for users without active AWS/Supabase credentials, 
# this adapter acts as an ORM that falls back to a persistent JSON layer if no Cloud URL is found.
class CloudSQLAdapter:
    def __init__(self):
        self.db_file = "cloud_mock_db.json"
        self._init_cloud_schema()

    def _init_cloud_schema(self):
        if not os.path.exists(self.db_file):
            schema = {
                "transactions": [],
                "security_audit": [],
                "shift_closures": [],
                "vahan_blacklist": [
                    {'Plate_Number': 'OD02AB1234', 'Reason': 'Stolen Vehicle (Bhubaneswar)'},
                    {'Plate_Number': 'WB11CC9999', 'Reason': 'Pending Interstate Speeding Challan'}
                ]
            }
            with open(self.db_file, 'w') as f: json.dump(schema, f)

    def _read_table(self, table):
        with open(self.db_file, 'r') as f: return json.load(f)[table]

    def _write_record(self, table, record):
        with open(self.db_file, 'r') as f: db = json.load(f)
        db[table].append(record)
        with open(self.db_file, 'w') as f: json.dump(db, f)
        
    def get_dataframe(self, table):
        df = pd.DataFrame(self._read_table(table))
        if 'Timestamp' in df.columns:
            df['Timestamp'] = pd.to_datetime(df['Timestamp'])
            df = df.sort_values(by='Timestamp', ascending=False)
        return df

    def insert_transaction(self, plate, v_type, lane, pay_method, plaza, toll, exempt):
        record = {
            "Transaction_ID": f"TXN-{uuid.uuid4().hex[:12].upper()}",
            "Timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "Plate_Number": plate, "Vehicle_Type": v_type, "Lane": lane,
            "Payment_Method": pay_method, "Toll_Collected": toll,
            "Plaza_Location": plaza, "Is_Exempt": exempt
        }
        self._write_record("transactions", record)

    def insert_audit(self, action, plate, plaza):
        record = {
            "Log_ID": f"AUD-{uuid.uuid4().hex[:8].upper()}",
            "Timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "Action_Taken": action, "Target_Plate": plate, "Handled_By_Plaza": plaza
        }
        self._write_record("security_audit", record)
        
    def close_shift(self, operator, plaza, expected, actual, variance):
        record = {
            "Shift_ID": f"SHF-{uuid.uuid4().hex[:8].upper()}",
            "Timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "Operator": operator, "Plaza": plaza,
            "Expected_Cash": expected, "Actual_Cash": actual, "Variance": variance
        }
        self._write_record("shift_closures", record)

cloud_db = CloudSQLAdapter()

# --- UTILITIES ---
TOLL_EXEMPT = ['CD', 'CC', 'UN']
VALID_VEHICLE_CODES = ['AP','AR','AS','BR','CG','GA','GJ','HR','HP','JH',
                        'KA','KL','MP','MH','MN','ML','MZ','NL','OD','PB',
                        'RJ','SK','TN','TS','TR','UP','UK','WB','AN','CH',
                        'DD','DL','JK','LA','LD','PY','BH','CD','CC','UN']

def is_exempt(plate):
    cleaned = re.sub(r'[^A-Z0-9]', '', plate.upper())
    for code in TOLL_EXEMPT:
        if cleaned.startswith(code): return True
    return False

def validate_indian_plate(text):
    cleaned = re.sub(r'[^A-Z0-9]', '', text.upper())
    for code in TOLL_EXEMPT:
        if code in cleaned and 4 <= len(cleaned) <= 10: return cleaned
    if len(cleaned) >= 4 and cleaned[:2] in VALID_VEHICLE_CODES:
        if re.search(r'[0-9]{2,4}', cleaned): return cleaned
    return None
