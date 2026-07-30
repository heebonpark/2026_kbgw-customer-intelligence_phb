import json
import os

CONFIG_DIR = os.path.expanduser("~/.dataintelligence_pro")
EXPERT_CONFIG_PATH = os.path.join(CONFIG_DIR, "expert_config.json")

def load_expert_config():
    config = {
        "favorite_columns": {},
        "value_replacements": {}
    }
    if os.path.exists(EXPERT_CONFIG_PATH):
        try:
            with open(EXPERT_CONFIG_PATH, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                config["favorite_columns"] = saved.get("favorite_columns", {})
                config["value_replacements"] = saved.get("value_replacements", {})
        except (json.JSONDecodeError, OSError):
            pass
    return config

def save_expert_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(EXPERT_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def apply_value_replacements(df, file_key, config):
    """Applies admin-defined value replacement rules to the dataframe."""
    if df is None or df.empty:
        return df
    
    replacements = config.get("value_replacements", {}).get(file_key, [])
    for rule in replacements:
        col = rule.get("col")
        old_val = rule.get("old")
        new_val = rule.get("new")
        
        if col and col in df.columns and old_val is not None:
            mask = df[col].astype(str) == str(old_val)
            df.loc[mask, col] = new_val
            
    return df
