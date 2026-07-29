import time
import os
import sys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
from core.handlers import process_and_merge, load_data
from core.report import generate_html_report

WATCH_DIR = os.path.join(os.path.dirname(__file__), "auto_upload")
OUTPUT_HTML = os.path.join(os.path.dirname(__file__), "Data_Intel_PRO_Report_Auto.html")

class UploadHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_modified = time.time()
        
    def on_modified(self, event):
        if not event.is_directory:
            self.last_modified = time.time()
            
    def on_created(self, event):
        if not event.is_directory:
            self.last_modified = time.time()

def process_files():
    print("\n[Auto-Watcher] 변경 감지! 파일 병합 처리를 시작합니다...")
    
    # Simple mapping based on filename keywords
    files_dict = {'db': None, 'voc': None, 'patrol': None, 'cancel': None, 'original': None, 'facility': None}
    
    for filename in os.listdir(WATCH_DIR):
        if filename.startswith('.'): continue
        path = os.path.join(WATCH_DIR, filename)
        is_csv = filename.lower().endswith('.csv')
        
        print(f" - 로딩 중: {filename}")
        df = load_data(path, is_csv=is_csv)
        
        if "총괄DB" in filename or "총괄관리" in filename: files_dict['db'] = df
        elif "VOC" in filename or "SP관리" in filename: files_dict['voc'] = df
        elif "정기점검" in filename or "SE" in filename or "SG" in filename: files_dict['patrol'] = df
        elif "해지파이프라인" in filename or "해지" in filename: files_dict['cancel'] = df
        elif "재계약여부" in filename or "원본" in filename: files_dict['original'] = df
        elif "시설현황" in filename or "G009" in filename: files_dict['facility'] = df
        
    if files_dict['db'] is None:
        print("[Auto-Watcher] 필수 파일인 '총괄DB' 엑셀 파일이 없습니다. 처리를 취소합니다.\n")
        return
        
    merged_df, branch_stats, msg = process_and_merge(files_dict)
    
    if merged_df is not None:
        html_content, pwd, expiry = generate_html_report(merged_df, branch_stats)
        with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"[Auto-Watcher] 성공! HTML 리포트 생성 완료: {OUTPUT_HTML}")
        print(f"[Auto-Watcher] 비밀번호: {pwd}, 만료일: {expiry}\n")
    else:
        print(f"[Auto-Watcher] 병합 실패: {msg}\n")

if __name__ == "__main__":
    if not os.path.exists(WATCH_DIR):
        os.makedirs(WATCH_DIR)
        
    event_handler = UploadHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_DIR, recursive=False)
    observer.start()
    
    print(f"[*] 자동 업로드 감시를 시작합니다: {WATCH_DIR}")
    print("[*] 위 폴더에 엑셀 파일을 넣으시면 자동으로 병합 및 리포트가 생성됩니다.")
    print("[*] 종료하려면 Ctrl+C를 누르세요.\n")
    
    try:
        last_processed = time.time()
        while True:
            time.sleep(1)
            # If files changed and 3 seconds have passed since the last change, process them.
            if event_handler.last_modified > last_processed and (time.time() - event_handler.last_modified) > 3:
                process_files()
                last_processed = time.time()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
