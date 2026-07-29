"""
Canonical on-disk names for each source file the pipeline reads.

Real exports come with a timestamp baked into the filename (e.g.
"VOC정보조회-20260729-065307042.xls") that changes every time someone
re-exports it, so hardcoding that exact name breaks the next month. Instead
each source has a stable "stem"; the actual file can be .xlsx/.xls/.csv --
`find_source_path` locates whichever extension is currently on disk.

This is what makes "관리자: 원본 파일 업데이트" (admin file replace) and
`generate_report.py` agree on where a file lives without either of them
needing to know the other's naming history.
"""

import glob
import os
import shutil
from datetime import datetime

SOURCE_FILES = {
    'db': {'stem': '총괄DB', 'label': '총괄관리DB', 'is_csv_default': False, 'required': True},
    'voc': {'stem': 'VOC정보조회', 'label': 'VOC정보조회 (월/일일 SP관리활동)', 'is_csv_default': False, 'required': False},
    'patrol': {'stem': '순찰정기점검내역', 'label': '순찰 정기점검 내역 (SE,SG)', 'is_csv_default': False, 'required': False},
    'original': {'stem': '관리고객원본', 'label': '2026년 관리고객원본', 'is_csv_default': False, 'required': False},
    'facility': {'stem': '시설현황', 'label': '시설현황', 'is_csv_default': True, 'required': False},
    'cancel': {'stem': '해지파이프라인', 'label': '해지 파이프라인 (독립 섹션)', 'is_csv_default': False, 'required': False},
    'cancelled_facility': {'stem': '해지시설내역', 'label': '해지시설내역 (고액 미등록 알림용, 확장)', 'is_csv_default': False, 'required': False},
}

SOURCE_ORDER = ['db', 'voc', 'patrol', 'original', 'facility', 'cancel', 'cancelled_facility']

BACKUP_DIRNAME = 'backups'


def find_source_path(base_dir, key):
    """Returns the current on-disk path for a source, whatever its extension
    is, or None if it hasn't been uploaded yet."""
    stem = SOURCE_FILES[key]['stem']
    matches = sorted(glob.glob(os.path.join(base_dir, stem + '.*')))
    return matches[0] if matches else None


def is_csv_path(path):
    return path is not None and path.lower().endswith('.csv')


def save_uploaded_file(base_dir, key, uploaded_file, make_backup=True):
    """Writes an uploaded file to its canonical path, backing up whatever was
    there before (into base_dir/backups/) and removing the old file if its
    extension differs from the new one -- otherwise both a stale .xls and a
    fresh .xlsx would exist side by side and find_source_path() would need to
    guess which one is current.

    Returns (target_path, backup_path_or_None).
    """
    stem = SOURCE_FILES[key]['stem']
    ext = os.path.splitext(uploaded_file.name)[1].lower() or '.xlsx'
    target_path = os.path.join(base_dir, stem + ext)

    existing = find_source_path(base_dir, key)
    backup_path = None
    if existing and os.path.exists(existing):
        if make_backup:
            backup_dir = os.path.join(base_dir, BACKUP_DIRNAME)
            os.makedirs(backup_dir, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(backup_dir, f"{ts}__{os.path.basename(existing)}")
            shutil.copy2(existing, backup_path)
        if os.path.abspath(existing) != os.path.abspath(target_path):
            os.remove(existing)

    uploaded_file.seek(0)
    with open(target_path, 'wb') as f:
        f.write(uploaded_file.read())

    return target_path, backup_path


def source_status(base_dir):
    """Status list for the admin UI: label, path, exists, last-modified."""
    rows = []
    for key in SOURCE_ORDER:
        meta = SOURCE_FILES[key]
        path = find_source_path(base_dir, key)
        exists = path is not None and os.path.exists(path)
        rows.append({
            'key': key,
            'label': meta['label'],
            'required': meta['required'],
            'path': path,
            'filename': os.path.basename(path) if path else None,
            'exists': exists,
            'modified': datetime.fromtimestamp(os.path.getmtime(path)) if exists else None,
        })
    return rows
