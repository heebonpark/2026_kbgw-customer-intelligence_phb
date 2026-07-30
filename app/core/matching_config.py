"""
Configurable matching (join) rules between the master DB (총괄DB) and each of
the auxiliary files. Replaces hardcoded join columns with an admin-editable,
persisted set of conditions so a match can require more than one column to
agree at once (a composite AND key) -- e.g. 계약번호 AND 서비스번호 must both
match, not just one.
"""

import json
import os

CONFIG_DIR = os.path.expanduser("~/.dataintelligence_pro")
CONFIG_PATH = os.path.join(CONFIG_DIR, "matching_config.json")

# The join-able files
MATCHABLE_FILES = ['original', 'facility', 'patrol', 'voc', 'cancel', 'cancelled_facility']

FILE_LABELS = {
    'original': '2026년 관리고객원본',
    'facility': '시설현황',
    'patrol': '순찰 정기점검 내역',
    'voc': 'VOC정보조회',
    'cancel': '해지 파이프라인',
    'cancelled_facility': '해지시설 내역',
}

# Columns offered as match-key candidates for each side. Kept to identifier
# columns on purpose -- these are the columns that can uniquely tie a row in
# one file to a row in another; picking a free-text column here would silently
# produce a garbage join.
DB_KEY_CANDIDATES = ['계약번호', '서비스번호']

FILE_KEY_CANDIDATES = {
    'original': ['계약번호', '고객번호', '서비스번호'],
    'facility': ['계약번호', '고객번호', '서비스번호'],
    'patrol': ['고객번호'],
    'voc': ['계약번호', '고객번호', '서비스번호'],
    'cancel': ['계약번호', '고객번호', '서비스번호'],
    'cancelled_facility': ['계약번호', '고객번호', '서비스번호'],
}

# Extra (non-key) columns worth carrying over from each file once matched --
# used both by the Python merge and by the embedded client-side matcher.
FILE_DISPLAY_COLUMNS = {
    'original': ['만기도래 월', '합산월정료(KTT+KT)', '계약상태', '재계약여부', '관리본부명', '관리지사명'],
    'facility': ['서비스재개시일', 'KTT월정료', '계약상태(중)', '계약상태(대)', '쟤계약여부', '재계약여부', '관리본부명', '관리지사명'],
    'patrol': ['결과', '특이사항', '도착시간', '출발시간'],
    'voc': ['상태', 'VOC유형대', '접수일시'],
    'cancel': ['계약상태'],
    'cancelled_facility': ['계약상태(중)', '계약상태(대)'],
}

DEFAULT_CONDITIONS = {
    'original': [{'db_col': '계약번호', 'file_col': '계약번호', 'enabled': True}],
    'facility': [{'db_col': '계약번호', 'file_col': '계약번호', 'enabled': True}],
    'patrol': [{'db_col': '서비스번호', 'file_col': '고객번호', 'enabled': True}],
    'voc': [{'db_col': '계약번호', 'file_col': '계약번호', 'enabled': True}],
    'cancel': [{'db_col': '계약번호', 'file_col': '계약번호', 'enabled': True}],
    'cancelled_facility': [{'db_col': '계약번호', 'file_col': '계약번호', 'enabled': True}],
}


def default_config():
    return {key: [dict(c) for c in conditions] for key, conditions in DEFAULT_CONDITIONS.items()}


def load_matching_config():
    """Loads the persisted matching config, falling back to defaults for any
    file that isn't present in the saved file (e.g. first run, or a newer
    file type added after the config was last saved)."""
    config = default_config()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            for key in MATCHABLE_FILES:
                if key in saved and saved[key]:
                    config[key] = saved[key]
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save_matching_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    payload = {key: config.get(key, []) for key in MATCHABLE_FILES}
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def enabled_conditions(config, file_key):
    return [c for c in config.get(file_key, []) if c.get('enabled')]
