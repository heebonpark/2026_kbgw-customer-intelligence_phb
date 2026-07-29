import json
import os
import sys
import warnings


class _SafeStream:
    """Absorbs writes when there's no real console to write to."""
    def write(self, *a, **k): pass
    def flush(self, *a, **k): pass
    def isatty(self): return False


def _make_streams_safe():
    """Windows .exe builds made with `pyinstaller --windowed` have no console,
    so sys.stdout/sys.stderr are either None (any print() raises
    AttributeError) or a stub stream whose encoding falls back to the
    Windows locale codec (cp949 etc), which can't encode characters like
    \xa0 that show up in pandas/openpyxl warning text -- any print() of
    those then raises UnicodeEncodeError. This runs before any other import
    so every print() and warning in the app (and in pandas/openpyxl) is safe
    regardless of which of those two situations we're in."""
    for name in ('stdout', 'stderr'):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, _SafeStream())
            continue
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except Exception:
                setattr(sys, name, _SafeStream())


_make_streams_safe()

# Suppress all warnings to prevent UnicodeEncodeError in PyInstaller
# when Pandas tries to print warnings containing \xa0 to sys.stderr on Windows
warnings.filterwarnings("ignore")

import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import webbrowser
import traceback

# Ensure we can import from app.core
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
from core.handlers import process_and_merge, load_data
from core.report import generate_html_report
from core.matching_config import load_matching_config

APP_DIR = os.path.expanduser("~/.dataintelligence_pro")
NOTES_FILE = os.path.join(APP_DIR, "file_notes.json")

BG = "#f1f5f9"
CARD_BG = "#ffffff"
BORDER = "#e2e8f0"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
TEXT_MUTED = "#64748b"
TEXT_DARK = "#0f172a"


def load_file_notes():
    """각 원본 파일 슬롯에 관리자가 남긴 부연설명 -- 한 대(로컬)에서만
    보이는 로컬 저장이므로 팀과 공유하려면 이 JSON 파일 자체를 복사해야 한다."""
    try:
        with open(NOTES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_file_notes(notes):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(NOTES_FILE, 'w', encoding='utf-8') as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)


class DataIntelGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Data Intel PRO - Admin Uploader")
        self.root.geometry("820x760")
        self.root.configure(bg=BG)

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TButton", font=("Helvetica", 11), padding=6)
        style.configure("Action.TButton", font=("Helvetica", 12, "bold"), background=ACCENT, foreground="white")
        style.map("Action.TButton", background=[("active", ACCENT_DARK)])
        style.configure("Ghost.TButton", font=("Helvetica", 10), padding=5)
        style.configure("Vertical.TScrollbar", background=BORDER)

        self.file_paths = {
            'db': tk.StringVar(),
            'voc': tk.StringVar(),
            'patrol': tk.StringVar(),
            'cancel': tk.StringVar(),
            'original': tk.StringVar(),
            'facility': tk.StringVar(),
            'cancelled_facility': tk.StringVar(),
        }
        saved_notes = load_file_notes()
        self.file_notes = {key: tk.StringVar(value=saved_notes.get(key, '')) for key in self.file_paths}

        self.report_password = tk.StringVar()
        self.report_expiry = tk.StringVar()

        self.create_widgets()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def create_widgets(self):
        header = tk.Frame(self.root, bg=ACCENT, height=70)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="📊 Data Intel PRO", bg=ACCENT, fg="white",
                 font=("Helvetica", 18, "bold")).pack(side=tk.LEFT, padx=(20, 8), pady=14)
        tk.Label(header, text="데이터 병합 및 리포트 생성기", bg=ACCENT, fg="#dbeafe",
                 font=("Helvetica", 11)).pack(side=tk.LEFT, pady=14)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(14, 0))

        tk.Label(body, text="원본 파일 (경로 입력 후 '찾아보기'로 선택, 설명란은 선택사항)",
                 bg=BG, fg=TEXT_MUTED, font=("Helvetica", 10)).pack(anchor="w", pady=(0, 6))

        # Scrollable file-card list -- keeps the window a fixed height even
        # with 7 file slots x 3 lines (path + browse + 설명) each.
        list_container = tk.Frame(body, bg=BG)
        list_container.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(list_container, bg=BG, highlightthickness=0, height=360)
        scrollbar = ttk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
        self.scroll_frame = tk.Frame(canvas, bg=BG)
        self.scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        fields = [
            ("1. 총괄관리DB", "필수, xlsx/xls", 'db'),
            ("2. 월/일일 SP관리활동 (VOC)", "선택", 'voc'),
            ("3. 월/일일 SE,SG 정기점검", "선택", 'patrol'),
            ("4. 월 해지파이프라인", "선택, 독립 섹션", 'cancel'),
            ("5. 2026년 관리고객원본", "선택", 'original'),
            ("6. 시설현황", "선택, csv", 'facility'),
            ("7. 해지시설 내역", "선택, 고액 미등록 알림용", 'cancelled_facility'),
        ]

        for label_text, tag_text, key in fields:
            self._build_file_card(self.scroll_frame, label_text, tag_text, key)

        # ---- password / expiry ----
        opts_card = self._card(body)
        opts_card.pack(fill=tk.X, pady=(14, 0))
        tk.Label(opts_card, text="⚙️ 리포트 옵션", bg=CARD_BG, fg=TEXT_DARK,
                 font=("Helvetica", 11, "bold")).pack(anchor="w", padx=14, pady=(10, 6))

        pwd_row = tk.Frame(opts_card, bg=CARD_BG)
        pwd_row.pack(fill=tk.X, padx=14, pady=4)
        tk.Label(pwd_row, text="★ 사용자 비밀번호 (빈칸=랜덤)", bg=CARD_BG, width=26, anchor="w",
                 font=("Helvetica", 10)).pack(side=tk.LEFT)
        pwd_ent = tk.Entry(pwd_row, textvariable=self.report_password, font=("Helvetica", 10))
        pwd_ent.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._bind_autocorrect(pwd_ent, self.report_password)

        exp_row = tk.Frame(opts_card, bg=CARD_BG)
        exp_row.pack(fill=tk.X, padx=14, pady=(4, 12))
        tk.Label(exp_row, text="★ 리포트 만료일 (YYYY-MM-DD)", bg=CARD_BG, width=26, anchor="w",
                 font=("Helvetica", 10)).pack(side=tk.LEFT)
        exp_ent = tk.Entry(exp_row, textvariable=self.report_expiry, font=("Helvetica", 10))
        exp_ent.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._bind_autocorrect(exp_ent, self.report_expiry)

        # ---- actions ----
        action_row = tk.Frame(body, bg=BG)
        action_row.pack(fill=tk.X, pady=14)
        ttk.Button(action_row, text="💾 설명 저장", style="Ghost.TButton",
                   command=self.save_notes).pack(side=tk.LEFT)
        self.run_btn = ttk.Button(action_row, text="🚀 데이터 병합 및 리포트 생성 실행",
                                   style="Action.TButton", command=self.run_process)
        self.run_btn.pack(side=tk.RIGHT)

        tk.Label(body, text="실행 로그", bg=BG, fg=TEXT_MUTED, font=("Helvetica", 10)).pack(anchor="w")
        log_frame = tk.Frame(body, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)
        log_frame.pack(fill=tk.BOTH, expand=False, pady=(4, 12))
        self.status_text = tk.Text(log_frame, height=8, bg=CARD_BG, fg=TEXT_DARK,
                                    font=("Consolas", 9), state=tk.DISABLED, bd=0, padx=8, pady=6)
        self.status_text.pack(fill=tk.BOTH, expand=True)

    def _card(self, parent):
        return tk.Frame(parent, bg=CARD_BG, highlightbackground=BORDER, highlightthickness=1)

    def _build_file_card(self, parent, label_text, tag_text, key):
        card = self._card(parent)
        card.pack(fill=tk.X, pady=5, padx=2)

        row1 = tk.Frame(card, bg=CARD_BG)
        row1.pack(fill=tk.X, padx=12, pady=(10, 4))
        tk.Label(row1, text=label_text, bg=CARD_BG, fg=TEXT_DARK,
                 font=("Helvetica", 11, "bold")).pack(side=tk.LEFT)
        tk.Label(row1, text=f"  ({tag_text})", bg=CARD_BG, fg=TEXT_MUTED,
                 font=("Helvetica", 9)).pack(side=tk.LEFT)

        row2 = tk.Frame(card, bg=CARD_BG)
        row2.pack(fill=tk.X, padx=12, pady=(0, 4))
        ent = tk.Entry(row2, textvariable=self.file_paths[key], font=("Helvetica", 10))
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._bind_autocorrect(ent, self.file_paths[key])
        ttk.Button(row2, text="찾아보기", command=lambda k=key: self.browse_file(k)).pack(side=tk.LEFT, padx=(8, 0))

        row3 = tk.Frame(card, bg=CARD_BG)
        row3.pack(fill=tk.X, padx=12, pady=(0, 10))
        tk.Label(row3, text="📝 설명:", bg=CARD_BG, fg=TEXT_MUTED, font=("Helvetica", 9)).pack(side=tk.LEFT)
        note_ent = tk.Entry(row3, textvariable=self.file_notes[key], font=("Helvetica", 9), fg="#334155")
        note_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self._bind_autocorrect(note_ent, self.file_notes[key], strip_quotes=False)

    # ------------------------------------------------------------------
    # 텍스트 자동보정 -- 탐색기에서 "경로 복사"하면 앞뒤에 큰따옴표가 붙어
    # 오는 경우가 흔해서, 포커스가 빠질 때 자동으로 정리해준다.
    # ------------------------------------------------------------------
    def _bind_autocorrect(self, entry, var, strip_quotes=True):
        def handler(event=None):
            val = var.get().strip()
            if strip_quotes and len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                val = val[1:-1].strip()
            if val != var.get():
                var.set(val)
        entry.bind("<FocusOut>", handler)

    def save_notes(self, silent=False):
        notes = {key: var.get().strip() for key, var in self.file_notes.items()}
        save_file_notes(notes)
        if not silent:
            messagebox.showinfo("저장됨", "각 파일 항목의 설명이 저장되었습니다.")

    def log(self, message):
        self.status_text.config(state=tk.NORMAL)
        self.status_text.insert(tk.END, message + "\n")
        self.status_text.see(tk.END)
        self.status_text.config(state=tk.DISABLED)
        self.root.update()

    def browse_file(self, key):
        filetypes = (("Excel/CSV files", "*.xlsx *.xls *.csv"), ("All files", "*.*"))
        filename = filedialog.askopenfilename(title="파일 선택", filetypes=filetypes)
        if filename:
            self.file_paths[key].set(filename)

    def run_process(self):
        db_path = self.file_paths['db'].get()
        if not db_path or not os.path.exists(db_path):
            messagebox.showerror("오류", "총괄관리DB 파일은 필수입니다.")
            return

        self.save_notes(silent=True)  # 실행할 때마다 설명도 같이 저장

        self.run_btn.config(state=tk.DISABLED)
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete(1.0, tk.END)
        self.status_text.config(state=tk.DISABLED)

        self.log("파일 로딩을 시작합니다...")

        try:
            files_dict = {}
            for key, string_var in self.file_paths.items():
                path = string_var.get()
                if path and os.path.exists(path):
                    self.log(f"로드 중: {os.path.basename(path)}")
                    is_csv = path.lower().endswith('.csv')
                    files_dict[key] = load_data(path, is_csv=is_csv)
                else:
                    files_dict[key] = None

            self.log("데이터 병합 처리를 진행합니다...")
            matching_config = load_matching_config()
            merged_df, msg, match_report = process_and_merge(files_dict, matching_config)

            if merged_df is None:
                self.log(f"병합 실패: {msg}")
                messagebox.showerror("오류", f"병합 실패: {msg}")
                self.run_btn.config(state=tk.NORMAL)
                return

            self.log("HTML 리포트를 생성합니다...")

            pwd_val = self.report_password.get().strip() or None
            exp_val = self.report_expiry.get().strip() or None

            html_content, pwd, expiry, admin_pwd = generate_html_report(
                merged_df,
                voc_df=files_dict.get('voc'),
                patrol_df=files_dict.get('patrol'),
                cancel_df=files_dict.get('cancel'),
                cancelled_facility_df=files_dict.get('cancelled_facility'),
                raw_files=files_dict,
                matching_config=matching_config,
                password=pwd_val,
                expiry_date=exp_val
            )

            output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data_Intel_PRO_Report.html")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.log("=========================================")
            self.log("성공적으로 HTML 리포트가 생성되었습니다!")
            self.log(f"저장 위치: {output_path}")
            self.log(f"만료일: {expiry}")
            self.log(f"사용자 비밀번호: {pwd}")
            self.log(f"관리자 비밀번호: {admin_pwd}")
            self.log("=========================================")

            messagebox.showinfo("성공", f"리포트 생성 완료!\n만료일: {expiry}\n사용자용 암호: {pwd}\n관리자용 암호: {admin_pwd}")
            webbrowser.open(f"file://{output_path}")

        except Exception as e:
            tb = traceback.format_exc()
            self.log(f"예기치 않은 오류 발생: {str(e)}\n\n[상세 오류 내역]\n{tb}")
            messagebox.showerror("오류", f"실행 중 오류가 발생했습니다: {str(e)}")
        finally:
            self.run_btn.config(state=tk.NORMAL)


if __name__ == "__main__":
    root = tk.Tk()
    app = DataIntelGUI(root)
    root.mainloop()
