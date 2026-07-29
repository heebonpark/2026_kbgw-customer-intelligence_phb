import json
import os

APP_DIR = os.path.expanduser("~/.dataintelligence_pro")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "term_mappings": {
        "본부": "관리본부",
        "지사": "관리지사",
        "강원본부": "강북/강원",
        "서부본부": "강남/서부",
        "중앙지사": "중앙"
    },
    "sort_orders": {
        "hq_order": ["강남/서부", "강북/강원", "부산/경남", "전남/전북", "충남/충북", "대구/경북"],
        "branch_order": ["중앙", "강북", "서대문", "고양", "의정부", "남양주", "강릉", "원주"]
    },
    "required_columns": [
        "관리본부", "본부", "지사", "관리지사", "영업구역", "기술구역", "구역", "출동구역", 
        "KTT월정료", "KTT월정료(조정)", "합산월정료"
    ]
}

def init_settings():
    """Initializes the settings file if it doesn't exist."""
    if not os.path.exists(APP_DIR):
        os.makedirs(APP_DIR)
        
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4, ensure_ascii=False)

def load_settings():
    """Loads settings from the JSON file."""
    if not os.path.exists(SETTINGS_FILE):
        init_settings()
        
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return DEFAULT_SETTINGS
