import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
import os
import sys
import webbrowser

# Ensure we can import from app.core
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
from core.handlers import process_and_merge, load_data
from core.report import generate_html_report
from core.matching_config import load_matching_config

class DataIntelGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Data Intel PRO - Admin Uploader")
        self.root.geometry("750x600")
        self.root.configure(padx=20, pady=20)
        
        # Style configuration for ttk
        style = ttk.Style()
        style.theme_use('clam') # 'clam' or 'alt' often works better for coloring on Mac
        style.configure("TButton", font=("Helvetica", 11), padding=5)
        style.configure("Action.TButton", font=("Helvetica", 12, "bold"), background="#2563eb", foreground="white")
        
        self.file_paths = {
            'db': tk.StringVar(),
            'voc': tk.StringVar(),
            'patrol': tk.StringVar(),
            'cancel': tk.StringVar(),
            'original': tk.StringVar(),
            'facility': tk.StringVar()
        }
        
        self.report_password = tk.StringVar()
        self.report_expiry = tk.StringVar()
        
        self.create_widgets()
        
    def create_widgets(self):
        title = tk.Label(self.root, text="Data Intel PRO 데이터 병합 및 리포트 생성기", font=("Helvetica", 16, "bold"))
        title.pack(pady=(0, 20))
        
        frame = tk.Frame(self.root)
        frame.pack(fill=tk.X)
        
        # Labels and keys mapping
        fields = [
            ("1. 총괄관리DB (필수, xlsx/xls):", 'db'),
            ("2. 월/일일 SP관리활동 (VOC):", 'voc'),
            ("3. 월/일일 SE,SG 정기점검:", 'patrol'),
            ("4. 월 해지파이프라인:", 'cancel'),
            ("5. 2026년 관리고객원본:", 'original'),
            ("6. 시설현황 (csv):", 'facility')
        ]
        
        for i, (label_text, key) in enumerate(fields):
            lbl = tk.Label(frame, text=label_text, width=32, anchor="w", font=("Helvetica", 12))
            lbl.grid(row=i, column=0, pady=10, sticky="w")
            
            ent = tk.Entry(frame, textvariable=self.file_paths[key], width=40, font=("Helvetica", 11))
            ent.grid(row=i, column=1, pady=10, padx=10)
            
            btn = ttk.Button(frame, text="찾아보기", command=lambda k=key: self.browse_file(k))
            btn.grid(row=i, column=2, pady=10)
            
        # Add password and expiry inputs
        pwd_lbl = tk.Label(frame, text="★사용자 비밀번호 (빈칸=랜덤):", width=32, anchor="w", font=("Helvetica", 12))
        pwd_lbl.grid(row=6, column=0, pady=10, sticky="w")
        pwd_ent = tk.Entry(frame, textvariable=self.report_password, width=40, font=("Helvetica", 11))
        pwd_ent.grid(row=6, column=1, pady=10, padx=10)
        
        exp_lbl = tk.Label(frame, text="★리포트 만료일 (YYYY-MM-DD):", width=32, anchor="w", font=("Helvetica", 12))
        exp_lbl.grid(row=7, column=0, pady=10, sticky="w")
        exp_ent = tk.Entry(frame, textvariable=self.report_expiry, width=40, font=("Helvetica", 11))
        exp_ent.grid(row=7, column=1, pady=10, padx=10)
            
        self.run_btn = ttk.Button(self.root, text="데이터 병합 및 리포트 생성 실행", 
                                 style="Action.TButton", command=self.run_process)
        self.run_btn.pack(pady=30)
        
        self.status_text = tk.Text(self.root, height=8, width=80, state=tk.DISABLED)
        self.status_text.pack()
        
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
                cancelled_facility_df=files_dict.get('facility') if files_dict.get('facility') is not None else None, # For now
                raw_files=files_dict,
                matching_config=None,
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
            
            # Show summary popup
            messagebox.showinfo("성공", f"리포트 생성 완료!\n만료일: {expiry}\n사용자용 암호: {pwd}\n관리자용 암호: {admin_pwd}")
            
            # Automatically open the generated HTML
            webbrowser.open(f"file://{output_path}")
            
        except Exception as e:
            self.log(f"예기치 않은 오류 발생: {str(e)}")
            messagebox.showerror("오류", f"실행 중 오류가 발생했습니다: {str(e)}")
        finally:
            self.run_btn.config(state=tk.NORMAL)

if __name__ == "__main__":
    root = tk.Tk()
    app = DataIntelGUI(root)
    root.mainloop()
