import streamlit as st

# Five light, bright themes (no dark theme) - pick any as the active one via
# apply_custom_css(theme_key). Each entry only needs a handful of tokens;
# every rule below is written in terms of these so adding a 6th theme is a
# one-entry change, not a CSS rewrite.
THEMES = {
    "classic": {
        "label": "☀️ 클래식 화이트",
        "bg": "#f8fafc",
        "card_bg": "#ffffff",
        "border": "#e2e8f0",
        "text": "#1e293b",
        "text_muted": "#64748b",
        "accent1": "#2563eb",
        "accent2": "#1d4ed8",
    },
    "sky": {
        "label": "🌤️ 스카이 블루",
        "bg": "#eff6ff",
        "card_bg": "#ffffff",
        "border": "#bfdbfe",
        "text": "#1e3a5f",
        "text_muted": "#64748b",
        "accent1": "#0ea5e9",
        "accent2": "#0284c7",
    },
    "mint": {
        "label": "🌿 프레시 민트",
        "bg": "#f0fdf4",
        "card_bg": "#ffffff",
        "border": "#bbf7d0",
        "text": "#14532d",
        "text_muted": "#4b5563",
        "accent1": "#16a34a",
        "accent2": "#15803d",
    },
    "sand": {
        "label": "🌅 웜 샌드",
        "bg": "#fffbeb",
        "card_bg": "#ffffff",
        "border": "#fde68a",
        "text": "#78350f",
        "text_muted": "#78716c",
        "accent1": "#d97706",
        "accent2": "#b45309",
    },
    "lavender": {
        "label": "💜 소프트 라벤더",
        "bg": "#faf5ff",
        "card_bg": "#ffffff",
        "border": "#e9d5ff",
        "text": "#4c1d95",
        "text_muted": "#6b7280",
        "accent1": "#9333ea",
        "accent2": "#7e22ce",
    },
}

DEFAULT_THEME = "classic"


def apply_custom_css(theme_key: str = DEFAULT_THEME):
    """
    Applies custom CSS for the Data Intel PRO look: Pretendard font, a
    light/bright card-based layout, and one of the five THEMES above for
    background/accent colors.
    """
    theme = THEMES.get(theme_key, THEMES[DEFAULT_THEME])

    custom_css = f"""
    <style>
        @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');

        /* Material icon ligatures (e.g. the expander arrow) must keep their
           own icon font - overriding it makes them render as literal text
           like "keyboard_arrow_down" instead of a glyph. */
        *:not([data-testid="stIconMaterial"]) {{
            font-family: 'Pretendard', sans-serif !important;
        }}

        /* Hide standard Streamlit header and footer */
        #MainMenu {{visibility: hidden;}}
        header {{visibility: hidden;}}
        footer {{visibility: hidden;}}

        /* The sidebar is only ever used for the theme picker, which now
           floats over the main content instead (see .st-key-theme_float
           below) - hide the now-empty sidebar entirely. */
        [data-testid="stSidebar"] {{display: none !important;}}

        /* Floating "water drop" theme-picker button, top-right corner of
           the screen, always in the same spot regardless of scroll
           position or which page/tab is open. */
        .st-key-theme_float {{
            position: fixed !important;
            top: 1rem !important;
            right: 1.5rem !important;
            z-index: 9999 !important;
            width: auto !important;
        }}
        .st-key-theme_float.st-key-theme_float,
        .st-key-theme_float.st-key-theme_float [data-testid="stVerticalBlock"] > div > div,
        .st-key-theme_float [data-testid="stPopover"] {{
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0 !important;
        }}
        .st-key-theme_float [data-testid="stPopover"] button {{
            width: 3rem !important;
            height: 3rem !important;
            min-height: 3rem !important;
            border-radius: 50% 50% 50% 4px !important;
            padding: 0 !important;
            font-size: 1.3rem !important;
            line-height: 1 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            background: linear-gradient(135deg, {theme['accent1']} 0%, {theme['accent2']} 100%) !important;
            box-shadow: 0 4px 14px 0 rgba(15, 23, 42, 0.3) !important;
            border: 2px solid {theme['card_bg']} !important;
            transform: rotate(45deg) !important;
        }}
        .st-key-theme_float [data-testid="stPopover"] button div,
        .st-key-theme_float [data-testid="stPopover"] button p {{
            transform: rotate(-45deg) !important;
        }}
        .st-key-theme_float [data-testid="stPopover"] button:hover {{
            transform: rotate(45deg) scale(1.08) !important;
        }}
        /* Drop the popover's default dropdown-chevron icon - the droplet
           button should show only the 💧 emoji, nothing else. */
        .st-key-theme_float [data-testid="stPopover"] button [data-testid="stIconMaterial"] {{
            display: none !important;
        }}

        /* Use as much of the screen as possible - Streamlit's default wide
           layout still leaves a large fixed side/top margin around
           .block-container, which wastes space on data-dense screens. */
        .block-container {{
            max-width: 100% !important;
            padding-left: 2rem !important;
            padding-right: 2rem !important;
            padding-top: 0.75rem !important;
            padding-bottom: 0.75rem !important;
        }}

        /* Vertical gap between stacked blocks (each card, metric row, etc.
           is its own flex child here) - Streamlit's default is a full 1rem
           between every single one, which adds up fast on tall pages. */
        [data-testid="stVerticalBlock"] {{
            gap: 0.5rem !important;
        }}

        /* st.markdown("---") dividers and headings default to generous
           margins meant for document-style text, not a dense dashboard. */
        hr {{
            margin: 0.4rem 0 !important;
        }}

        /* Main background */
        .stApp {{
            background-color: {theme['bg']};
            color: {theme['text']};
        }}

        /* Card look for containers - flat, sharp-cornered, bordered panels
           (like a traditional corporate 전산 system screen) rather than the
           big rounded-corner/drop-shadow "app" look. overflow: hidden keeps
           wide inner content (e.g. a data table sized wider than its
           column) clipped to the card instead of spilling past the border
           into the next column. */
        div[data-testid="stVerticalBlock"] > div > div {{
            background: {theme['card_bg']} !important;
            border: 1px solid {theme['border']} !important;
            border-radius: 3px !important;
            padding: 0.65rem 1rem !important;
            box-shadow: none !important;
            overflow: hidden !important;
        }}

        /* Forms (login, checklist, account management) hold many repeated
           fields - boxing every single one in its own card (the rule above)
           makes data entry slow to scroll through. Flatten to a plain,
           compact row with a hairline divider instead. */
        [data-testid="stForm"] div[data-testid="stVerticalBlock"] > div > div {{
            background: transparent !important;
            border: none !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            padding: 0.35rem 0 !important;
        }}
        [data-testid="stForm"] div[data-testid="stHorizontalBlock"] {{
            border-bottom: 1px solid {theme['border']} !important;
            padding: 0.5rem 0 !important;
        }}

        /* Buttons (including form-submit and download buttons, which
           Streamlit renders outside the .stButton wrapper used by regular
           st.button) - flat solid color and a sharp corner instead of a
           gradient pill, matching a traditional 조회/등록 업무 button. */
        .stButton > button,
        [data-testid="stFormSubmitButton"] button,
        [data-testid="stDownloadButton"] button {{
            background: {theme['accent1']} !important;
            color: white !important;
            border: 1px solid {theme['accent2']} !important;
            border-radius: 3px !important;
            padding: 0.45rem 1.1rem !important;
            font-weight: 600 !important;
            transition: background-color 0.15s ease !important;
            box-shadow: none !important;
        }}
        .stButton > button:hover,
        [data-testid="stFormSubmitButton"] button:hover,
        [data-testid="stDownloadButton"] button:hover {{
            background: {theme['accent2']} !important;
        }}

        /* 통합 데이터 조회 tab's left-side category nav (main.py
           _render_data_explorer_tab, st.container(key="data_explorer_nav")).
           Every button defaults to the same solid accent color as the rest
           of the app, which made the whole list look like one undifferentiated
           block of identical buttons with no way to tell which category is
           currently selected. Scoped to this one container so it doesn't
           change the look of any other button (로그인/저장 etc.) elsewhere. */
        .st-key-data_explorer_nav [data-testid="stVerticalBlock"] > div > div {{
            background: transparent !important;
            border: none !important;
            box-shadow: none !important;
            padding: 0.15rem 0 !important;
        }}
        .st-key-data_explorer_nav button[kind="secondary"] {{
            background: {theme['card_bg']} !important;
            color: {theme['text_muted']} !important;
            border: 1px solid {theme['border']} !important;
            box-shadow: none !important;
            font-weight: 500 !important;
        }}
        .st-key-data_explorer_nav button[kind="secondary"]:hover {{
            border-color: {theme['accent1']} !important;
            color: {theme['accent1']} !important;
            transform: none !important;
        }}
        .st-key-data_explorer_nav button[kind="primary"] {{
            font-weight: 700 !important;
        }}

        /* Inputs */
        .stTextInput > div > div > input {{
            background-color: #ffffff !important;
            color: {theme['text']} !important;
            border: 1px solid {theme['border']} !important;
            border-radius: 3px !important;
        }}
        .stTextInput > div > div > input:focus {{
            border-color: {theme['accent1']} !important;
            box-shadow: 0 0 0 1px {theme['accent1']} !important;
        }}

        /* BaseWeb selectbox/multiselect control */
        div[data-baseweb="select"] > div {{
            background-color: #ffffff !important;
            border: 1px solid {theme['border']} !important;
            border-radius: 3px !important;
        }}

        /* Headings */
        h1, h2, h3, h4, h5, h6 {{
            color: {theme['text']} !important;
            font-weight: 700 !important;
            margin-top: 0 !important;
            margin-bottom: 0.4rem !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }}

        /* Metric Cards */
        [data-testid="stMetricValue"] {{
            color: {theme['accent1']} !important;
        }}
        [data-testid="stMetricLabel"] {{
            color: {theme['text_muted']} !important;
        }}

        /* Expander header */
        [data-testid="stExpander"] summary {{
            background-color: {theme['card_bg']} !important;
            border: 1px solid {theme['border']} !important;
            border-radius: 3px !important;
        }}

        /* Data grid (st.dataframe / st.data_editor) - a visible outer grid
           border makes it read more like a bordered enterprise data table
           instead of a borderless floating list. */
        [data-testid="stDataFrame"], [data-testid="stDataEditor"] {{
            border: 1px solid {theme['border']} !important;
            border-radius: 3px !important;
        }}

        /* Progress bar fill */
        [data-testid="stProgress"] > div > div > div {{
            background-color: {theme['accent1']} !important;
        }}

        /* Active tab underline/label */
        .stTabs [aria-selected="true"] {{
            color: {theme['accent1']} !important;
        }}

        /* Login page - pushes the (already horizontally-centered via
           st.columns) login card down from the very top of the screen so it
           reads as a centered landing card instead of content stuck in the
           corner. */
        .st-key-login_center {{
            margin-top: 8vh !important;
        }}

        /* Mobile (phone-width) layout - the desktop styles above assume a
           wide screen with room to spare; on a ~390px phone the same
           2rem/1.5rem paddings and default heading sizes eat most of the
           screen and make titles wrap awkwardly across 2-3 lines. */
        @media (max-width: 640px) {{
            .block-container {{
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
                padding-top: 0.5rem !important;
            }}
            h1 {{ font-size: 1.35rem !important; line-height: 1.35 !important; }}
            h2 {{ font-size: 1.15rem !important; }}
            h3, h4 {{ font-size: 1.02rem !important; }}
            div[data-testid="stVerticalBlock"] > div > div {{
                padding: 0.6rem 0.9rem !important;
            }}
            .stButton > button,
            [data-testid="stFormSubmitButton"] button,
            [data-testid="stDownloadButton"] button {{
                padding: 0.6rem 1rem !important;
                font-size: 0.92rem !important;
            }}
            [data-testid="stMetricValue"] {{ font-size: 1.5rem !important; }}
            .stTabs [data-baseweb="tab"] {{
                font-size: 0.85rem !important;
                padding: 0.5rem 0.6rem !important;
            }}
        }}
    </style>
    """
    st.markdown(custom_css, unsafe_allow_html=True)
