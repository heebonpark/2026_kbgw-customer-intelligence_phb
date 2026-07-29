with open("generate_report.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

out = []
for line in lines:
    if "print(f\"=========================================" in line and "unterminated" not in line:
        pass # wait
out = []
with open("generate_report.py", "r", encoding="utf-8") as f:
    text = f.read()

import re
# Fix the unterminated string issue by just removing the bad part and appending it properly.
text = re.sub(r'print\(f"=========================================\n\s*', 'print(f"=========================================\\n")\n', text)
with open("generate_report.py", "w", encoding="utf-8") as f:
    f.write(text)
