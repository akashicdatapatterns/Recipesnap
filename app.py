import io
import json
import html
import os
import re
import sqlite3
from base64 import b64encode
from datetime import date, timedelta
from fractions import Fraction
from typing import Optional
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st
import numpy as np
from PIL import Image, ImageOps

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

from db import (
    add_meal_plan_entry,
    authenticate_user,
    create_user,
    create_recipe,
    create_user_session,
    delete_recipe,
    delete_user_session,
    delete_meal_plan_entry,
    export_recipes_records,
    get_categories,
    get_difficulties,
    get_openai_api_key as db_get_openai_api_key,
    get_recipe,
    get_session_user,
    get_user_by_id,
    import_recipes_records,
    init_db,
    list_meal_plan_entries,
    list_recipes,
    list_recipe_options,
    list_users_with_stats,
    reset_user_password,
    save_openai_api_key as db_save_openai_api_key,
    set_user_blocked,
    set_favorite,
    set_rating,
    update_user_profile,
    update_recipe,
)


APP_NAME = "RecipeSnap"


def get_active_user() -> Optional[dict]:
    user = st.session_state.get("auth_user")
    return user if isinstance(user, dict) else None


def get_active_user_id() -> int:
    user = get_active_user() or {}
    return int(user.get("id") or -1)


def is_active_user_admin() -> bool:
    user = get_active_user() or {}
    return bool(user.get("is_admin"))


def page_auth() -> None:
    st.title("🍽️ RecipeSnap")
    st.markdown("### Your Personal AI-Powered Recipe Library")
    st.markdown("---")
    st.caption("Manage, organize, and cook with your recipes. Connect with ChatGPT to generate new recipes instantly.")

    login_tab, register_tab, forgot_tab = st.tabs(["🔐 Login", "📝 Register", "🔑 Forgot Password"])

    with login_tab:
        with st.form("login_form"):
            st.markdown("#### Welcome Back")
            identifier = st.text_input("Username or Email", placeholder="Enter your username or email")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Login", type="primary", use_container_width=True)
        if submitted:
            user = authenticate_user(identifier=identifier, password=password)
            if user:
                st.session_state["auth_user"] = user
                st.session_state["page"] = "Browse Recipes"
                # Load saved OpenAI API key from database
                saved_key = db_get_openai_api_key(user["id"])
                if saved_key:
                    st.session_state["openai_api_key"] = saved_key
                # Create persistent session token and store in cookie
                try:
                    import extra_streamlit_components as stx
                    import datetime
                    token = create_user_session(user["id"])
                    st.session_state["cv_session_token"] = token
                    _cm = stx.CookieManager(key="cv_cookie_mgr_login")
                    _cm.set(
                        "cv_session",
                        token,
                        expires_at=datetime.datetime.now() + datetime.timedelta(days=30),
                    )
                except Exception:
                    pass
                st.success(f"Welcome, {user['username']}! 👋")
                st.rerun()
            else:
                st.error("❌ Invalid username/email or password.")

    with register_tab:
        with st.form("register_form"):
            st.markdown("#### Create Your Account")
            full_name = st.text_input("Full Name", placeholder="John Doe")
            username = st.text_input("Username", placeholder="johndoe")
            email = st.text_input("Email", placeholder="john@example.com")
            phone = st.text_input("Phone (optional)", placeholder="+1 (555) 123-4567")
            city = st.text_input("City", placeholder="San Francisco")
            country = st.text_input("Country", placeholder="USA")
            cooking_pref = st.text_input("Food Preference", placeholder="e.g., Vegetarian, Vegan, High Protein")
            password = st.text_input("Password", type="password", placeholder="Create a strong password")
            confirm_password = st.text_input("Confirm Password", type="password", placeholder="Re-enter your password")
            submitted = st.form_submit_button("✨ Create Account", type="primary", use_container_width=True)
        if submitted:
            if password != confirm_password:
                st.error("❌ Passwords do not match. Please try again.")
            else:
                ok, message = create_user(
                    username=username,
                    email=email,
                    password=password,
                    full_name=full_name,
                    phone=phone,
                    city=city,
                    country=country,
                    cooking_preference=cooking_pref,
                )
                if ok:
                    st.success(message)
                else:
                    st.error(message)

    with forgot_tab:
        with st.form("forgot_password_form"):
            st.markdown("#### Reset Your Password")
            username = st.text_input("Registered Username", placeholder="Enter your username")
            email = st.text_input("Registered Email", placeholder="Enter your registered email")
            new_password = st.text_input("New Password", type="password", placeholder="Create a new password")
            confirm_new_password = st.text_input("Confirm New Password", type="password", placeholder="Re-enter your password")
            submitted = st.form_submit_button("🔄 Reset Password", type="primary", use_container_width=True)
        if submitted:
            if new_password != confirm_new_password:
                st.error("❌ New passwords do not match. Please try again.")
            else:
                ok, message = reset_user_password(username=username, email=email, new_password=new_password)
                if ok:
                    st.success(f"✅ {message}")
                else:
                    st.error(f"❌ {message}")


def page_admin_users() -> None:
    if not is_active_user_admin():
        st.error("Only admin users can access this page.")
        return

    active_user_id = get_active_user_id()
    st.markdown("### 👥 User Administration")
    st.markdown("Manage user accounts and permissions")
    st.markdown("---")
    if st.button("Edit My Profile"):
        st.session_state["page"] = "My Profile"
        st.rerun()

    users = list_users_with_stats()
    if not users:
        st.info("No users found.")
        return

    display_rows: list[dict] = []
    for user in users:
        display_rows.append(
            {
                "ID": int(user.get("id") or 0),
                "Username": user.get("username") or "",
                "Full Name": user.get("full_name") or "",
                "Email": user.get("email") or "",
                "Phone": user.get("phone") or "",
                "Role": "Admin" if int(user.get("is_admin") or 0) == 1 else "User",
                "Status": "Blocked" if int(user.get("is_blocked") or 0) == 1 else "Active",
                "Recipes": int(user.get("recipe_count") or 0),
                "Created": user.get("created_at") or "",
            }
        )

    filter_col_1, filter_col_2, filter_col_3 = st.columns([2.4, 1.2, 1.2])
    with filter_col_1:
        search_text = st.text_input(
            "Search users",
            placeholder="Search by username, full name, email, or phone",
            key="admin_user_search_text",
        ).strip().lower()
    with filter_col_2:
        role_filter = st.selectbox("Role", ["All", "Admin", "User"], key="admin_user_role_filter")
    with filter_col_3:
        status_filter = st.selectbox("Status", ["All", "Active", "Blocked"], key="admin_user_status_filter")

    filtered_rows = display_rows
    if search_text:
        filtered_rows = [
            row
            for row in filtered_rows
            if search_text in str(row.get("Username", "")).lower()
            or search_text in str(row.get("Full Name", "")).lower()
            or search_text in str(row.get("Email", "")).lower()
            or search_text in str(row.get("Phone", "")).lower()
        ]
    if role_filter != "All":
        filtered_rows = [row for row in filtered_rows if row.get("Role") == role_filter]
    if status_filter != "All":
        filtered_rows = [row for row in filtered_rows if row.get("Status") == status_filter]

    st.caption(f"Showing {len(filtered_rows)} of {len(display_rows)} user(s)")

    if not filtered_rows:
        st.info("No users match the current filters.")
        return

    users_df = pd.DataFrame(filtered_rows).drop(columns=["ID"], errors="ignore")
    st.dataframe(users_df, use_container_width=True, hide_index=True)

    user_ids = [int(row["ID"]) for row in filtered_rows]
    selected_user_id = st.selectbox(
        "Select user to manage",
        options=user_ids,
        format_func=lambda uid: next(
            (
                f"{row['Username']} ({row['Role']}, {row['Status']})"
                for row in filtered_rows
                if int(row["ID"]) == int(uid)
            ),
            str(uid),
        ),
    )
    selected_row = next((row for row in filtered_rows if int(row["ID"]) == int(selected_user_id)), None)
    if not selected_row:
        return

    st.markdown(
        f"**Selected:** {selected_row['Username']} | {selected_row['Email']} | "
        f"{selected_row['Role']} | {selected_row['Status']}"
    )

    can_toggle = int(selected_user_id) != active_user_id and selected_row["Role"] != "Admin"
    block_col, unblock_col = st.columns([1, 1])
    with block_col:
        if st.button("Block User", disabled=(not can_toggle or selected_row["Status"] == "Blocked"), type="primary"):
            if set_user_blocked(selected_user_id, True):
                st.success("User blocked successfully.")
                st.rerun()
            st.error("Could not block user.")
    with unblock_col:
        if st.button("Unblock User", disabled=(not can_toggle or selected_row["Status"] == "Active")):
            if set_user_blocked(selected_user_id, False):
                st.success("User unblocked successfully.")
                st.rerun()
            st.error("Could not unblock user.")

    if not can_toggle:
        st.info("Admin users and your own account cannot be blocked from this screen.")


def page_my_profile() -> None:
    active_user = get_active_user() or {}
    user_id = int(active_user.get("id") or -1)
    if user_id <= 0:
        st.error("User session is invalid. Please login again.")
        return

    user = get_user_by_id(user_id)
    if not user:
        st.error("User profile could not be loaded.")
        return

    st.markdown("### 👤 My Profile")
    st.markdown("Update your account information")
    st.markdown("---")

    with st.form("my_profile_form"):
        full_name = st.text_input("Full Name", value=str(user.get("full_name") or ""))
        username = st.text_input("Username", value=str(user.get("username") or ""))
        email = st.text_input("Email", value=str(user.get("email") or ""))
        phone = st.text_input("Phone (optional)", value=str(user.get("phone") or ""))
        city = st.text_input("City", value=str(user.get("city") or ""))
        country = st.text_input("Country", value=str(user.get("country") or ""))
        cooking_pref = st.text_input(
            "Food Preference",
            value=str(user.get("cooking_preference") or ""),
            placeholder="e.g., Vegetarian, Vegan, High Protein",
        )
        st.markdown("#### Change Password (optional)")
        new_password = st.text_input("New Password", type="password")
        confirm_new_password = st.text_input("Confirm New Password", type="password")
        submitted = st.form_submit_button("Save Profile", type="primary")

    if submitted:
        if new_password and new_password != confirm_new_password:
            st.error("New password and confirm password must match.")
            return

        ok, message = update_user_profile(
            user_id=user_id,
            username=username,
            email=email,
            full_name=full_name,
            phone=phone,
            city=city,
            country=country,
            cooking_preference=cooking_pref,
            new_password=(new_password or "").strip() or None,
        )
        if not ok:
            st.error(message)
            return

        refreshed = get_user_by_id(user_id)
        if refreshed:
            st.session_state["auth_user"] = {
                "id": int(refreshed.get("id") or -1),
                "username": str(refreshed.get("username") or ""),
                "email": str(refreshed.get("email") or ""),
                "full_name": str(refreshed.get("full_name") or ""),
                "phone": str(refreshed.get("phone") or ""),
                "city": str(refreshed.get("city") or ""),
                "country": str(refreshed.get("country") or ""),
                "cooking_preference": str(refreshed.get("cooking_preference") or ""),
                "is_admin": bool(int(refreshed.get("is_admin") or 0)),
                "is_blocked": bool(int(refreshed.get("is_blocked") or 0)),
                "created_at": str(refreshed.get("created_at") or ""),
            }
        st.success(message)


def inject_styles() -> None:
    css = """
        :root {
            --primary-color: #ff6b6b;
            --secondary-color: #4ecdc4;
            --accent-color: #ffe66d;
            --ui-font: "Trebuchet MS", "Segoe UI", Verdana, Tahoma, sans-serif;
            --chat-user-bg: #fff2bf;
            --chat-user-border: #e5c452;
            --chat-assistant-bg: #e6faf7;
            --chat-assistant-border: #3fb5ab;
        }

        html, body, .stApp, p, h1, h2, h3, h4, h5, h6,
        input, textarea, button, select, label, li, td, th {
            font-family: var(--ui-font) !important;
            line-height: 1.5 !important;
        }
        /* Preserve Material Icons / Symbols fonts */
        [class*="material"], .material-icons, .material-symbols-rounded,
        [data-testid*="Icon"] {
            font-family: "Material Symbols Rounded", "Material Icons", "Material Symbols Outlined" !important;
        }

        h1, h2, h3, h4, h5, h6 {
            font-weight: 700 !important;
        }

        h1 { font-size: 2.5rem !important; }
        h2 { font-size: 2rem !important; }
        h3 { font-size: 1.5rem !important; }

        [data-testid="stAppViewContainer"] {
            background-image:
                radial-gradient(circle at 20% 15%, rgba(255, 107, 107, 0.14), transparent 40%),
                radial-gradient(circle at 80% 5%, rgba(78, 205, 196, 0.12), transparent 35%),
                url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='320' viewBox='0 0 320 320'%3E%3Cg fill='none' stroke='%2399a0aa' stroke-opacity='0.10' stroke-width='1.4'%3E%3Ccircle cx='64' cy='72' r='26'/%3E%3Ccircle cx='64' cy='72' r='14'/%3E%3Ccircle cx='244' cy='208' r='30'/%3E%3Ccircle cx='244' cy='208' r='16'/%3E%3Cpath d='M170 40v54M162 40v54M154 40v54M184 40c0 16-2 32-9 46v28'/%3E%3C/g%3E%3C/svg%3E"),
                url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='260' height='260' viewBox='0 0 260 260'%3E%3Cg fill='none' stroke='%23a7b0bb' stroke-opacity='0.09' stroke-width='1.2'%3E%3Cpath d='M40 190c20-28 52-38 84-26-12 30-38 50-70 58'/%3E%3Cpath d='M44 178c18 2 34 10 46 24'/%3E%3Cpath d='M188 72c0 20 14 20 14 40s-14 20-14 40'/%3E%3Cpath d='M208 72c0 20 14 20 14 40s-14 20-14 40'/%3E%3C/g%3E%3C/svg%3E"),
                url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='240' height='240' viewBox='0 0 240 240'%3E%3Cg fill='none' stroke='%2399a0aa' stroke-opacity='0.12' stroke-width='1'%3E%3Cpath d='M0 40h240M0 120h240M0 200h240M40 0v240M120 0v240M200 0v240'/%3E%3C/g%3E%3C/svg%3E");
            background-attachment: fixed, fixed, fixed, fixed, fixed;
            background-size: auto, auto, 320px 320px, 260px 260px, 240px 240px;
            background-position: left top, right top, 0 0, 120px 80px, 0 0;
            background-repeat: no-repeat, no-repeat, repeat, repeat, repeat;
        }

        .stTextInput > div > div > input,
        .stTextArea textarea,
        .stSelectbox [data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] > div,
        .stNumberInput input,
        .stDateInput > div > div > input {
            border-radius: 8px !important;
            padding: 10px 12px !important;
            transition: all 0.3s ease !important;
        }

        /* Force selectbox selected value visibility */
        .stSelectbox [data-baseweb="select"] [role="combobox"] {
            color: inherit !important;
            fill: currentColor !important;
            opacity: 1 !important;
            visibility: visible !important;
            display: flex !important;
            align-items: center !important;
        }

        .stSelectbox [data-baseweb="select"] [role="combobox"] > div,
        .stSelectbox [data-baseweb="select"] [role="combobox"] span {
            color: inherit !important;
            opacity: 1 !important;
            visibility: visible !important;
            display: inline-block !important;
        }

        .stSelectbox [role="combobox"] > *,
        .stMultiSelect [role="combobox"] > * {
            color: inherit !important;
            opacity: 1 !important;
            visibility: visible !important;
        }

        .stTextInput > div > div > input:focus,
        .stTextArea textarea:focus,
        .stNumberInput input:focus {
            border-color: var(--secondary-color) !important;
            box-shadow: 0 0 0 3px rgba(78, 205, 196, 0.15) !important;
        }

        .stButton > button {
            background: linear-gradient(135deg, var(--secondary-color) 0%, var(--primary-color) 100%) !important;
            color: white !important;
            border: none !important;
            border-radius: 8px !important;
            font-weight: 600 !important;
            padding: 10px 24px !important;
            transition: all 0.3s ease !important;
        }

        .stButton > button:hover {
            transform: translateY(-1px) !important;
            box-shadow: 0 6px 14px rgba(78, 205, 196, 0.25) !important;
        }

        .stButton > button[type="primary"] {
            background: linear-gradient(135deg, #ff6b6b 0%, #ee5a6f 100%) !important;
        }

        .cv-meta {
            background-color: var(--secondary-background-color) !important;
            padding: 16px !important;
            border-radius: 8px !important;
            border: 1px solid rgba(127, 127, 127, 0.25) !important;
        }

        .cv-meta p {
            margin: 8px 0 !important;
        }

        .cv-pill {
            display: inline-block;
            background: linear-gradient(135deg, var(--secondary-color) 0%, var(--primary-color) 100%);
            color: white;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            margin: 4px 4px 4px 0;
            font-weight: 500;
        }

        /* Chat message layout - prevent icon/text overlap */
        [data-testid="stChatMessage"] {
            display: flex !important;
            align-items: flex-start !important;
            gap: 12px !important;
            padding: 12px 16px !important;
            border-radius: 10px !important;
            margin-bottom: 8px !important;
            overflow: visible !important;
        }
        [data-testid="stChatMessageAvatarIcon"],
        [data-testid="stChatMessageAvatar"] {
            flex-shrink: 0 !important;
            min-width: 36px !important;
            width: 36px !important;
            height: 36px !important;
        }
        [data-testid="stChatMessageContent"] {
            flex: 1 !important;
            min-width: 0 !important;
            overflow-wrap: break-word !important;
            word-break: break-word !important;
            position: relative !important;
        }

        .cv-chat-bubble {
            padding: 10px 12px;
            border-radius: 12px;
            margin-bottom: 10px;
            border: 1px solid transparent;
        }

        .cv-chat-bubble strong {
            display: block;
            margin-bottom: 4px;
            font-weight: 700;
        }

        .cv-chat-bubble-user {
            background: var(--chat-user-bg);
            border-color: var(--chat-user-border);
        }

        .cv-chat-bubble-assistant {
            background: var(--chat-assistant-bg);
            border-color: var(--chat-assistant-border);
        }

        /* Make save choices stand out clearly */
        [data-testid="stRadio"] label {
            font-weight: 700 !important;
        }

        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(127, 127, 127, 0.4);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: var(--secondary-color);
        }

        /* ── Responsive typography ── */
        /* Fluid base font: scales from 14px on small phones to 16px on desktop */
        html {
            font-size: clamp(14px, 3.5vw, 16px) !important;
        }

        /* Headings scale proportionally on narrow viewports */
        @media (max-width: 768px) {
            h1 { font-size: clamp(1.5rem, 6vw, 2.5rem) !important; }
            h2 { font-size: clamp(1.25rem, 5vw, 2rem) !important; }
            h3 { font-size: clamp(1.1rem, 4vw, 1.5rem) !important; }

            /* Body text and inputs stay readable */
            p, li, td, th, label,
            .stTextInput input,
            .stTextArea textarea,
            .stSelectbox [data-baseweb="select"],
            .stNumberInput input,
            .stRadio label,
            .stCheckbox label {
                font-size: clamp(0.875rem, 3.5vw, 1rem) !important;
                line-height: 1.55 !important;
            }

            /* Sidebar text */
            [data-testid="stSidebar"] * {
                font-size: clamp(0.8rem, 3vw, 0.95rem) !important;
            }

            /* Pill badges */
            .cv-pill {
                font-size: clamp(0.75rem, 2.8vw, 0.85rem) !important;
            }

            /* Buttons: keep text legible, allow natural wrapping */
            .stButton > button {
                font-size: clamp(0.85rem, 3vw, 1rem) !important;
                padding: 10px 16px !important;
                white-space: normal !important;
                font-weight: 700 !important;
            }

            /* Chat messages */
            [data-testid="stChatMessageContent"] {
                font-size: clamp(0.85rem, 3.2vw, 1rem) !important;
            }
        }

        /* Very small screens (phones < 480px) */
        @media (max-width: 480px) {
            html { font-size: 14px !important; }

            h1 { font-size: clamp(1.25rem, 7vw, 1.75rem) !important; }
            h2 { font-size: clamp(1.1rem, 6vw, 1.4rem) !important; }
            h3 { font-size: clamp(1rem, 5vw, 1.2rem) !important; }

            .stButton > button {
                padding: 9px 12px !important;
            }
        }
    """
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_header() -> None:
    return


def image_to_bytes(uploaded_file) -> Optional[bytes]:
    if uploaded_file is None:
        return None
    return uploaded_file.getvalue()


def bytes_to_image(image_data: Optional[bytes]) -> Optional[Image.Image]:
    if not image_data:
        return None
    try:
        return Image.open(io.BytesIO(image_data))
    except Exception:
        return None


def parse_tags(tags: str) -> list[str]:
    return [tag.strip() for tag in tags.split(",") if tag.strip()]


def normalize_reference_url(url_text: str) -> str:
    text = (url_text or "").strip()
    if not text:
        return ""
    if re.match(r"^https?://", text, flags=re.IGNORECASE):
        return text
    if re.match(r"^[\w.-]+\.[a-z]{2,}[/\w\-?=&#.%]*$", text, flags=re.IGNORECASE):
        return f"https://{text}"
    return text


def parse_minutes(text: str) -> int:
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else 0


def parse_instruction_steps(instructions: str) -> list[str]:
    text = (instructions or "").strip()
    if not text:
        return []

    # Split by numbered markers like "1.", "2)" while preserving plain line-based instructions.
    numbered = re.split(r"\n?\s*\d+[\.)]\s+", text)
    numbered_steps = [step.strip() for step in numbered if step.strip()]
    if len(numbered_steps) >= 2:
        return numbered_steps

    line_steps = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
    return line_steps if line_steps else [text]


def format_scaled_quantity(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))

    fraction = Fraction(value).limit_denominator(8)
    whole = fraction.numerator // fraction.denominator
    remainder = fraction.numerator % fraction.denominator
    if whole and remainder:
        return f"{whole} {remainder}/{fraction.denominator}"
    if remainder:
        return f"{remainder}/{fraction.denominator}"
    return str(whole)


def parse_leading_quantity(token: str) -> Optional[float]:
    cleaned = token.strip()
    if not cleaned:
        return None

    if re.fullmatch(r"\d+\s+\d+/\d+", cleaned):
        whole, frac = cleaned.split()
        return float(int(whole) + Fraction(frac))
    if re.fullmatch(r"\d+/\d+", cleaned):
        return float(Fraction(cleaned))
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        return float(cleaned)
    return None


def scale_ingredient_line(line: str, factor: float) -> str:
    match = re.match(r"^(?P<quantity>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)(?P<rest>\b.*)$", line.strip())
    if not match:
        return line

    quantity = parse_leading_quantity(match.group("quantity"))
    if quantity is None:
        return line

    scaled = format_scaled_quantity(quantity * factor)
    return f"{scaled}{match.group('rest')}"


def scale_ingredients_text(ingredients: str, base_servings: int, target_servings: int) -> str:
    base = max(1, int(base_servings or 1))
    target = max(1, int(target_servings or 1))
    factor = target / base
    return "\n".join(scale_ingredient_line(line, factor) for line in (ingredients or "").splitlines())


def scale_measurement_text(measurement: str, factor: float) -> str:
    text = (measurement or "").strip()
    if not text:
        return ""

    match = re.match(r"^(?P<quantity>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)(?P<rest>.*)$", text)
    if not match:
        return text

    quantity = parse_leading_quantity(match.group("quantity"))
    if quantity is None:
        return text

    scaled = format_scaled_quantity(quantity * factor)
    return f"{scaled}{match.group('rest')}"


def render_ingredients_table(
    ingredients_text: str,
    fmt: str = "quantity_item",
    base_servings: Optional[int] = None,
    target_servings: Optional[int] = None,
) -> None:
    """
    fmt="quantity_item"  → left of colon = Measurement, right = Ingredient
    fmt="item_quantity"  → left of colon = Ingredient,   right = Measurement
    """
    rows = []
    show_scaled_column = base_servings is not None and target_servings is not None
    base = max(1, int(base_servings or 1)) if show_scaled_column else 1
    target = max(1, int(target_servings or 1)) if show_scaled_column else 1
    factor = target / base if show_scaled_column else 1.0
    scaled_col_name = f"Measurement for {target} persons"

    for line in (ingredients_text or "").splitlines():
        line = line.strip().lstrip("-\u2022* ")
        if not line:
            continue
        if ":" in line:
            left, right = [part.strip() for part in line.split(":", 1)]
        else:
            left, right = "", line

        if fmt == "item_quantity":
            ingredient = left
            measurement = right
        else:
            measurement = left
            ingredient = right

        row = {"Measurement": measurement, "Ingredient": ingredient}
        if show_scaled_column:
            row[scaled_col_name] = scale_measurement_text(measurement, factor)
        rows.append(row)

    if rows:
        if show_scaled_column:
            # Keep scaled quantity adjacent to base measurement for quick comparison.
            if fmt == "item_quantity":
                col_order = ["Ingredient", "Measurement", scaled_col_name]
            else:
                col_order = ["Measurement", scaled_col_name, "Ingredient"]
        else:
            col_order = ["Ingredient", "Measurement"] if fmt == "item_quantity" else ["Measurement", "Ingredient"]

        df = pd.DataFrame(rows)[col_order]
        # st.table keeps all columns visible in-place better than dataframe in narrow layout panes.
        st.table(df)
    else:
        st.text("-")


def set_cook_tip_popup(recipe: Optional[dict]) -> None:
    if not recipe:
        return
    tip = (recipe.get("tips_for_best_result") or "").strip()
    title = recipe.get("title") or f"Recipe {recipe.get('id')}"
    message = tip if tip else "No tips added for this recipe."
    st.session_state["cook_tip_popup_visible"] = True
    st.session_state["cook_tip_popup_recipe_id"] = int(recipe.get("id")) if recipe.get("id") else None
    st.session_state["cook_tip_popup_title"] = title
    st.session_state["cook_tip_popup_message"] = message


def extract_text_from_recipe_photo(uploaded_file) -> str:
    image = Image.open(io.BytesIO(uploaded_file.getvalue())).convert("L")
    image = ImageOps.autocontrast(image)

    if RapidOCR is not None:
        ocr = get_rapidocr_engine()
        image_array = np.array(image.convert("RGB"))
        result, _ = ocr(image_array)
        if result:
            return "\n".join(str(item[1]).strip() for item in result if len(item) > 1 and str(item[1]).strip())

    if pytesseract is not None:
        thresholded = image.point(lambda px: 0 if px < 145 else 255, mode="1")
        return pytesseract.image_to_string(thresholded).strip()

    raise RuntimeError("OCR engine is unavailable. Install project requirements to enable photo-to-text.")


@st.cache_resource
def get_rapidocr_engine():
    if RapidOCR is None:
        return None
    return RapidOCR()


def parse_recipe_from_text(raw_text: str) -> dict:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if not lines:
        return {}

    defaults = {
        "title": lines[0][:120],
        "description": "",
        "ingredients": "",
        "instructions": "",
        "tips_for_best_result": "",
        "reference_url": "",
        "servings": 1,
        "prep_time": 15,
        "cook_time": 20,
        "difficulty": "Easy",
        "category": "Dinner",
        "tags": "",
        "ingredient_format": "quantity_item",
    }

    section_aliases = {
        "ingredients": ["ingredients", "ingredient"],
        "instructions": ["instructions", "method", "steps", "directions"],
        "description": ["description", "about", "summary"],
    }
    current_section = "description"
    buckets = {"description": [], "ingredients": [], "instructions": []}

    for line in lines[1:]:
        lower = line.lower().strip(":")

        for section, aliases in section_aliases.items():
            if any(lower.startswith(alias) for alias in aliases):
                current_section = section
                line = re.sub(r"^[A-Za-z ]+:?", "", line).strip()
                break

        if lower.startswith("prep"):
            defaults["prep_time"] = parse_minutes(line)
            continue
        if lower.startswith("servings") or lower.startswith("yield"):
            defaults["servings"] = max(1, parse_minutes(line))
            continue
        if lower.startswith("cook"):
            defaults["cook_time"] = parse_minutes(line)
            continue
        if lower.startswith("difficulty"):
            if "hard" in lower:
                defaults["difficulty"] = "Hard"
            elif "medium" in lower:
                defaults["difficulty"] = "Medium"
            else:
                defaults["difficulty"] = "Easy"
            continue
        if lower.startswith("category"):
            defaults["category"] = line.split(":", 1)[-1].strip() or "Dinner"
            continue
        if lower.startswith("tags"):
            defaults["tags"] = line.split(":", 1)[-1].strip()
            continue

        if line:
            buckets[current_section].append(line)

    defaults["description"] = "\n".join(buckets["description"]).strip()
    defaults["ingredients"] = "\n".join(buckets["ingredients"]).strip()
    defaults["instructions"] = "\n".join(buckets["instructions"]).strip()

    if not defaults["ingredients"] and not defaults["instructions"]:
        joined = "\n".join(lines[1:])
        split_match = re.split(r"instructions|method|steps|directions", joined, flags=re.IGNORECASE, maxsplit=1)
        if len(split_match) == 2:
            defaults["ingredients"] = split_match[0].strip()
            defaults["instructions"] = split_match[1].strip()

    return defaults


def build_empty_recipe_defaults() -> dict:
    return {
        "title": "",
        "description": "",
        "ingredients": "",
        "instructions": "",
        "tips_for_best_result": "",
        "reference_url": "",
        "servings": 1,
        "prep_time": 15,
        "cook_time": 20,
        "difficulty": "Easy",
        "category": "Dinner",
        "tags": "",
        "ingredient_format": "quantity_item",
    }


def strip_html_to_text(html_content: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html_content, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = html.unescape(text)
    text = re.sub(r"\r", "", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def parse_iso8601_duration_to_minutes(value: str) -> int:
    if not value:
        return 0
    # Supports common recipe durations like PT45M, PT1H30M, P1DT2H.
    match = re.match(r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?)?$", value)
    if not match:
        return 0
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    return days * 24 * 60 + hours * 60 + minutes


def extract_recipe_from_json_ld(html_content: str) -> dict:
    scripts = re.findall(
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>",
        html_content,
        flags=re.IGNORECASE,
    )
    for script in scripts:
        try:
            payload = json.loads(html.unescape(script).strip())
        except Exception:
            continue

        nodes: list[dict] = []
        if isinstance(payload, dict):
            if isinstance(payload.get("@graph"), list):
                nodes.extend([item for item in payload["@graph"] if isinstance(item, dict)])
            nodes.append(payload)
        elif isinstance(payload, list):
            nodes.extend([item for item in payload if isinstance(item, dict)])

        for node in nodes:
            node_type = node.get("@type")
            if isinstance(node_type, list):
                type_values = [str(item).lower() for item in node_type]
            else:
                type_values = [str(node_type).lower()] if node_type else []
            if "recipe" not in type_values:
                continue

            defaults = build_empty_recipe_defaults()
            defaults["title"] = str(node.get("name") or "").strip()[:120]
            defaults["description"] = str(node.get("description") or "").strip()

            recipe_category = node.get("recipeCategory")
            if isinstance(recipe_category, list):
                defaults["category"] = str(recipe_category[0]).strip() if recipe_category else "Dinner"
            elif recipe_category:
                defaults["category"] = str(recipe_category).strip()

            keywords = node.get("keywords")
            if isinstance(keywords, list):
                defaults["tags"] = ", ".join(str(item).strip() for item in keywords if str(item).strip())
            elif keywords:
                defaults["tags"] = str(keywords).strip()

            recipe_yield = str(node.get("recipeYield") or "").strip()
            if recipe_yield:
                defaults["servings"] = max(1, parse_minutes(recipe_yield))

            defaults["prep_time"] = parse_iso8601_duration_to_minutes(str(node.get("prepTime") or "")) or 15
            defaults["cook_time"] = parse_iso8601_duration_to_minutes(str(node.get("cookTime") or "")) or 20

            ingredients = node.get("recipeIngredient")
            if isinstance(ingredients, list):
                defaults["ingredients"] = "\n".join(str(item).strip() for item in ingredients if str(item).strip())

            instructions = node.get("recipeInstructions")
            if isinstance(instructions, list):
                steps: list[str] = []
                for item in instructions:
                    if isinstance(item, str):
                        if item.strip():
                            steps.append(item.strip())
                    elif isinstance(item, dict):
                        step_text = str(item.get("text") or "").strip()
                        if step_text:
                            steps.append(step_text)
                defaults["instructions"] = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(steps))
            elif isinstance(instructions, str):
                defaults["instructions"] = instructions.strip()

            if defaults["title"] and (defaults["ingredients"] or defaults["instructions"]):
                return defaults

    return {}


def parse_recipe_from_web_url(url_text: str) -> tuple[dict, str]:
    normalized_url = normalize_reference_url(url_text)
    if not re.match(r"^https?://", normalized_url, flags=re.IGNORECASE):
        raise ValueError("Enter a valid web link starting with http:// or https://")

    request = Request(
        normalized_url,
        headers={"User-Agent": "Mozilla/5.0 RecipeSnap/1.0"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=12) as response:
            raw_bytes = response.read()
            html_content = raw_bytes.decode("utf-8", errors="ignore")
    except HTTPError as exc:
        raise RuntimeError(f"Could not fetch URL (HTTP {exc.code}).") from exc
    except URLError as exc:
        raise RuntimeError("Could not reach the URL. Check your internet connection and link.") from exc

    parsed = extract_recipe_from_json_ld(html_content)
    if not parsed:
        text_content = strip_html_to_text(html_content)
        parsed = parse_recipe_from_text(text_content)

    if not parsed:
        raise RuntimeError("Could not extract recipe fields from this page.")

    parsed["reference_url"] = normalized_url
    parsed["title"] = parsed.get("title") or "Web Recipe"
    return parsed, strip_html_to_text(html_content)


def get_ai_provider() -> str:
    """Always return openai provider (ChatGPT only)."""
    return "openai"


def get_ai_token(provider: str | None = None) -> tuple[str, str]:
    """
    Retrieve OpenAI API key with priority: session state -> secrets -> env var.
    Returns: (token, source) where source is "session", "secrets", "env", or "none".
    """
    # Check session state first (loaded from database on login or entered in sidebar)
    session_value = str(st.session_state.get("openai_api_key", "")).strip()
    if session_value:
        return session_value, "session"

    # Check streamlit secrets
    secret_value = ""
    try:
        secret_value = str(st.secrets.get("OPENAI_API_KEY", "")).strip()
    except Exception:
        secret_value = ""
    if secret_value:
        return secret_value, "secrets"

    # Check environment variable
    env_value = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if env_value:
        return env_value, "env"
    
    return "", "none"


def setup_ai_api_key_sidebar() -> None:
    """Render OpenAI API key input in sidebar and save to database."""
    # Initialize session state for ChatGPT model
    if "chatgpt_model" not in st.session_state:
        st.session_state["chatgpt_model"] = "gpt-4o-mini"

    st.sidebar.markdown("---")
    st.sidebar.markdown("**🤖 AI Recipe Assistant (ChatGPT)**")

    # Display current model selection as radio buttons
    model_options = ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]
    current_model = st.session_state.get("chatgpt_model", "gpt-4o-mini")
    model_idx = model_options.index(current_model) if current_model in model_options else 0

    st.sidebar.radio(
        "Model",
        options=model_options,
        index=model_idx,
        key="chatgpt_model",
        horizontal=False,
    )

    # OpenAI API key input - with save to database
    api_key_input = st.sidebar.text_input(
        "OpenAI API key",
        value=st.session_state.get("openai_api_key", ""),
        type="password",
        placeholder="sk-...",
    )
    api_key_clean = (api_key_input or "").strip()

    # Save API key to database when it changes
    if api_key_clean and api_key_clean != st.session_state.get("openai_api_key", ""):
        if st.session_state.get("auth_user"):
            user_id = st.session_state["auth_user"]["id"]
            success = db_save_openai_api_key(user_id, api_key_clean)
            if success:
                st.session_state["openai_api_key"] = api_key_clean
                st.sidebar.success("✅ API key saved!")
            else:
                st.sidebar.error("Failed to save API key")

    # Clear key button
    if st.sidebar.button("Clear Key", key="clear_api_key"):
        if st.session_state.get("auth_user"):
            user_id = st.session_state["auth_user"]["id"]
            db_save_openai_api_key(user_id, "")
            st.session_state["openai_api_key"] = ""
            st.rerun()

    # Check if API key is available
    api_key, source = get_ai_token()
    status_icon = "✅" if api_key else "⚠️"
    
    with st.sidebar.expander(f"{status_icon} Connect AI", expanded=not bool(api_key)):
        st.markdown(
            "**Get your OpenAI API key:**\n"
            "1. Go to [platform.openai.com](https://platform.openai.com/account/api-keys)\n"
            "2. Create a new API key\n"
            "3. Paste it below (saves to your profile for next login)"
        )


# Keep the old name as an alias so existing call sites keep working.
def get_openai_api_key() -> str:
    """
    Backward compatibility wrapper. Returns just the token string.
    For detailed token source info, use get_github_token() which returns (token, source).
    """
    token, _ = get_ai_token("openai")
    return token


def call_chatgpt_for_recipe(user_prompt: str) -> dict:
    provider = get_ai_provider()
    token, _ = get_ai_token(provider)
    if not token:
        if provider == "openai":
            raise RuntimeError(
                "❌ OpenAI API key not found.\n\n"
                "**How to fix:**\n"
                "1. Get your API key: [platform.openai.com/account/api-keys](https://platform.openai.com/account/api-keys)\n"
                "2. Enter it in the **Connect AI** section in the sidebar"
            )
        raise RuntimeError(
            "❌ GitHub token not found.\n\n"
            "**How to fix:**\n"
            "1. Get your token: [github.com/settings/tokens](https://github.com/settings/tokens)\n"
            "2. Enter it in the **Connect AI** section in the sidebar"
        )

    payload = {
        "model": str(st.session_state.get("chatgpt_model", "gpt-4o-mini")),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a recipe-only assistant. If the user asks anything unrelated to food or recipes, "
                    "respond with the single JSON object: {\"error\": \"off_topic\"}. "
                    "For recipe requests, respond with JSON only and no markdown. "
                    "Use exactly these keys: title, description, ingredients, instructions, tips_for_best_result, "
                    "servings, prep_time, cook_time, difficulty, category, tags, ingredient_format. "
                    "ingredients must be a newline-separated string where EVERY line follows the format: "
                    "'quantity : item'  (e.g. '2 cups : all-purpose flour'). "
                    "instructions must be a newline-separated string of numbered steps. "
                    "ingredient_format must always be 'quantity_item'. Never use 'item_quantity'."
                ),
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "temperature": 0.6,
    }

    endpoint = "https://api.openai.com/v1/chat/completions" if provider == "openai" else "https://models.inference.ai.azure.com/chat/completions"
    req = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=35) as response:
            result = json.loads(response.read().decode("utf-8", errors="ignore"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore") if hasattr(exc, "read") else ""
        service_name = "OpenAI" if provider == "openai" else "GitHub Models"
        if exc.code == 401:
            raise RuntimeError(
                f"❌ {service_name} authentication failed.\n\n"
                "**Your API key might be invalid or expired.**\n"
                "1. Check that your key is correct and not truncated\n"
                "2. Visit [platform.openai.com/account/api-keys](https://platform.openai.com/account/api-keys) to regenerate it\n"
                "3. Re-enter it in the **Connect AI** section"
            ) from exc
        if exc.code == 429:
            raise RuntimeError(
                f"❌ Rate limit exceeded on {service_name}.\n\n"
                "You've hit the API quota. Try again in a moment."
            ) from exc
        raise RuntimeError(f"❌ {service_name} request failed (HTTP {exc.code}).\n\n{body[:200]}") from exc
    except URLError as exc:
        service_name = "OpenAI" if provider == "openai" else "GitHub Models"
        raise RuntimeError(
            f"❌ Unable to reach {service_name}. Check your internet connection."
        ) from exc

    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    if not content:
        service_name = "OpenAI" if provider == "openai" else "GitHub Models"
        raise RuntimeError(f"❌ {service_name} returned an empty response.")

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", content)
        if not match:
            service_name = "OpenAI" if provider == "openai" else "GitHub Models"
            raise RuntimeError(f"❌ {service_name} response was not valid JSON.")
        return json.loads(match.group(0))


def normalize_generated_recipe(raw: dict, source_prompt: str) -> dict:
    if not isinstance(raw, dict):
        return {}

    draft = build_empty_recipe_defaults()
    draft["title"] = str(raw.get("title") or "AI Recipe").strip()[:120]
    draft["description"] = str(raw.get("description") or f"AI-generated recipe for: {source_prompt}").strip()
    draft["ingredients"] = str(raw.get("ingredients") or "").strip()
    draft["instructions"] = str(raw.get("instructions") or "").strip()
    draft["tips_for_best_result"] = str(raw.get("tips_for_best_result") or "").strip()
    draft["reference_url"] = normalize_reference_url(str(raw.get("reference_url") or "").strip())

    try:
        draft["servings"] = max(1, int(raw.get("servings") or 1))
    except (TypeError, ValueError):
        draft["servings"] = 1

    try:
        draft["prep_time"] = max(0, int(raw.get("prep_time") or 15))
    except (TypeError, ValueError):
        draft["prep_time"] = 15

    try:
        draft["cook_time"] = max(0, int(raw.get("cook_time") or 20))
    except (TypeError, ValueError):
        draft["cook_time"] = 20

    difficulty = str(raw.get("difficulty") or "Easy").strip().title()
    if difficulty not in ["Easy", "Medium", "Hard"]:
        difficulty = "Easy"
    draft["difficulty"] = difficulty

    draft["category"] = str(raw.get("category") or "Dinner").strip() or "Dinner"

    tags = raw.get("tags")
    if isinstance(tags, list):
        draft["tags"] = ", ".join(str(tag).strip() for tag in tags if str(tag).strip())
    else:
        draft["tags"] = str(tags or "").strip()

    draft["ingredient_format"] = "quantity_item"  # always enforce quantity : item format

    return draft


def build_local_chatbot_recipe_draft(user_prompt: str) -> dict:
    prompt = (user_prompt or "").strip()
    if not prompt:
        return {}

    lower = prompt.lower()
    draft = build_empty_recipe_defaults()

    if "breakfast" in lower:
        draft["category"] = "Breakfast"
    elif "lunch" in lower:
        draft["category"] = "Lunch"
    elif "dessert" in lower or "sweet" in lower:
        draft["category"] = "Dessert"
    elif "snack" in lower:
        draft["category"] = "Snack"

    if "hard" in lower:
        draft["difficulty"] = "Hard"
    elif "medium" in lower:
        draft["difficulty"] = "Medium"

    quick_request = bool(re.search(r"\b(quick|fast|under\s*30|30\s*min)\b", lower))
    if quick_request:
        draft["prep_time"] = 10
        draft["cook_time"] = 20

    if "indian" in lower:
        cuisine = "Indian"
    elif "italian" in lower:
        cuisine = "Italian"
    elif "chinese" in lower or "asian" in lower:
        cuisine = "Chinese"
    elif "mexican" in lower:
        cuisine = "Mexican"
    else:
        cuisine = "Global"

    protein_candidates = ["chicken", "paneer", "tofu", "egg", "fish", "lentil", "beans", "mushroom"]
    protein = next((item for item in protein_candidates if item in lower), "vegetables")

    is_vegan = "vegan" in lower
    is_vegetarian = "vegetarian" in lower or is_vegan
    high_protein = "high protein" in lower or "protein" in lower
    low_carb = "low carb" in lower or "keto" in lower
    spicy = "spicy" in lower

    if is_vegan and protein in ["chicken", "fish", "egg", "paneer"]:
        protein = "tofu"
    elif is_vegetarian and protein in ["chicken", "fish"]:
        protein = "paneer"

    ingredient_templates = {
        "Indian": [
            f"2 cups: {protein}",
            "1 cup: onion, chopped",
            "1 cup: tomato puree",
            "1 tsp: ginger garlic paste",
            "1 tsp: cumin",
            "1 tsp: turmeric",
            "1 tsp: coriander powder",
            "1 tbsp: oil",
            "to taste: salt",
        ],
        "Italian": [
            f"2 cups: {protein}",
            "2 cups: pasta or zucchini noodles",
            "1 cup: tomato sauce",
            "3 cloves: garlic, minced",
            "1 tbsp: olive oil",
            "1 tsp: oregano",
            "1 tsp: chili flakes",
            "to taste: salt and pepper",
        ],
        "Chinese": [
            f"2 cups: {protein}",
            "1 cup: bell pepper, sliced",
            "1 cup: cabbage, shredded",
            "1 tbsp: soy sauce",
            "1 tbsp: sesame oil",
            "1 tsp: ginger, grated",
            "2 cloves: garlic, minced",
            "1 tsp: cornflour slurry",
            "to taste: salt and pepper",
        ],
        "Mexican": [
            f"2 cups: {protein}",
            "1 cup: black beans",
            "1 cup: corn",
            "1 cup: tomato salsa",
            "1 tsp: cumin",
            "1 tsp: paprika",
            "1 tbsp: olive oil",
            "to taste: salt and pepper",
        ],
        "Global": [
            f"2 cups: {protein}",
            "1 cup: mixed vegetables",
            "1 tbsp: oil",
            "1 tsp: garlic",
            "to taste: salt and pepper",
        ],
    }

    instruction_templates = {
        "Indian": [
            "1. Heat oil and saute cumin for 20 seconds.",
            "2. Add onion and cook until light golden.",
            "3. Stir in ginger garlic paste, then tomato puree and dry spices.",
            f"4. Add {protein} and simmer until cooked through.",
            "5. Adjust seasoning and serve hot.",
        ],
        "Italian": [
            "1. Cook pasta according to package directions and reserve some water.",
            "2. Heat olive oil and saute garlic until fragrant.",
            f"3. Add {protein} and cook until tender.",
            "4. Add tomato sauce, oregano, and chili flakes, then simmer.",
            "5. Toss in pasta, adjust seasoning, and serve.",
        ],
        "Chinese": [
            "1. Heat sesame oil in a wok over high heat.",
            f"2. Stir fry {protein} for 2 to 3 minutes.",
            "3. Add ginger, garlic, and vegetables and cook until crisp tender.",
            "4. Add soy sauce and cornflour slurry; toss until glossy.",
            "5. Serve immediately while hot.",
        ],
        "Mexican": [
            "1. Heat oil in a pan and bloom cumin and paprika for 30 seconds.",
            f"2. Add {protein} and cook until lightly browned.",
            "3. Add beans, corn, and salsa and simmer for 5 to 7 minutes.",
            "4. Taste and adjust seasoning.",
            "5. Serve with rice, tortillas, or salad.",
        ],
        "Global": [
            "1. Prepare and chop all ingredients.",
            "2. Heat oil in a pan and saute garlic for 30 seconds.",
            f"3. Add {protein} and cook until partially done.",
            "4. Add vegetables, seasoning, and cook until tender.",
            "5. Taste, adjust seasoning, and serve hot.",
        ],
    }

    ingredients = list(ingredient_templates.get(cuisine, ingredient_templates["Global"]))
    if low_carb:
        ingredients = [item for item in ingredients if "pasta" not in item and "corn" not in item]
        ingredients.append("1 cup: mushrooms or zucchini")

    if spicy:
        ingredients.append("1 tsp: red chili or hot sauce")

    if high_protein and "lentil" not in protein and "beans" not in protein:
        ingredients.append("0.5 cup: lentils or chickpeas")

    if is_vegan:
        ingredients = [item for item in ingredients if "paneer" not in item and "egg" not in item]

    draft["title"] = f"{cuisine} {protein.title()} {draft['category']} Bowl"
    draft["description"] = f"AI-assisted draft based on your request: {prompt}"
    draft["ingredients"] = "\n".join(ingredients)
    draft["instructions"] = "\n".join(instruction_templates.get(cuisine, instruction_templates["Global"]))
    draft["tips_for_best_result"] = "Prep all ingredients before cooking and finish on high heat for better flavor."

    tags: list[str] = []
    if quick_request:
        tags.append("quick")
    for tag in ["healthy", "high protein", "vegan", "vegetarian", "spicy", "low carb"]:
        if tag in lower:
            tags.append(tag)
    tags.append(cuisine.lower())
    draft["tags"] = ", ".join(dict.fromkeys(tags))

    return draft


_RECIPE_KEYWORDS = (
    "recipe", "cook", "bake", "dish", "meal", "food", "ingredient", "cuisine",
    "breakfast", "lunch", "dinner", "snack", "dessert", "drink", "sauce",
    "soup", "salad", "curry", "pasta", "rice", "bread", "cake", "fry",
    "grill", "roast", "steam", "boil", "marinate", "stir", "appetizer",
    "starter", "main", "side", "vegan", "vegetarian", "protein", "healthy",
    "spicy", "sweet", "savory", "italian", "indian", "chinese", "mexican",
    "french", "thai", "japanese", "mediterranean", "make", "prepare", "want",
)


def _is_recipe_prompt(prompt: str) -> bool:
    lower = prompt.lower()
    return any(kw in lower for kw in _RECIPE_KEYWORDS)


def build_chatbot_recipe_draft(user_prompt: str) -> tuple[dict, str]:
    prompt = (user_prompt or "").strip()
    if not prompt:
        return {}, "empty"

    if not _is_recipe_prompt(prompt):
        return {}, "off_topic"

    remote_error = ""
    try:
        remote_raw = call_chatgpt_for_recipe(prompt)
        # API may signal off-topic
        if isinstance(remote_raw, dict) and remote_raw.get("error") == "off_topic":
            return {}, "off_topic"
        remote_draft = normalize_generated_recipe(remote_raw, prompt)
        if remote_draft.get("title") and remote_draft.get("ingredients") and remote_draft.get("instructions"):
            return remote_draft, "copilot"
    except Exception as e:
        # Save the remote error so UI can explain why fallback was used.
        remote_error = str(e).strip()

    local_draft = build_local_chatbot_recipe_draft(prompt)
    if local_draft:
        if remote_error:
            st.session_state["chatbot_last_error"] = remote_error
        return local_draft, "fallback"
    if remote_error:
        st.session_state["chatbot_last_error"] = remote_error
    return {}, "empty"


def format_draft_preview(draft: dict) -> str:
    ingredients_preview = (draft.get("ingredients") or "-").strip()
    instructions_preview = (draft.get("instructions") or "-").strip()
    if len(ingredients_preview) > 700:
        ingredients_preview = ingredients_preview[:700] + "..."
    if len(instructions_preview) > 900:
        instructions_preview = instructions_preview[:900] + "..."

    return (
        f"**{draft.get('title') or 'Draft Recipe'}**\n\n"
        f"Category: {draft.get('category') or 'Dinner'} | Difficulty: {draft.get('difficulty') or 'Easy'}\n\n"
        f"Servings: {draft.get('servings', 1)} | Prep: {draft.get('prep_time', 0)} min | Cook: {draft.get('cook_time', 0)} min\n\n"
        f"**Ingredients**\n{ingredients_preview}\n\n"
        f"**Process**\n{instructions_preview}\n\n"
        "Reply with another request if you want a different draft, then choose where to save it."
    )


def tags_to_text(tags: str) -> str:
    tag_list = parse_tags(tags)
    if not tag_list:
        return ""
    return " ".join([f"<span class='cv-pill'>{t}</span>" for t in tag_list])


def form_recipe_fields(defaults: Optional[dict] = None) -> dict:
    def safe_int(val, fallback: int) -> int:
        try:
            return int(val) if val is not None else fallback
        except (ValueError, TypeError):
            return fallback

    defaults = defaults or {}
    predefined_categories = ["Breakfast", "Lunch", "Dinner", "Dessert", "Snack", "Beverage"]
    default_category = (defaults.get("category") or "Dinner")
    category_index = predefined_categories.index(default_category) if default_category in predefined_categories else 2
    default_custom_category = "" if default_category in predefined_categories else str(default_category or "")
    difficulty_options = ["Easy", "Medium", "Hard"]
    raw_difficulty = str(defaults.get("difficulty", "Easy") or "Easy").strip().lower()
    difficulty_map = {
        "easy": "Easy",
        "medium": "Medium",
        "hard": "Hard",
    }
    normalized_difficulty = difficulty_map.get(raw_difficulty, "Easy")

    title = st.text_input("Title *", value=defaults.get("title", ""), max_chars=120)
    description = st.text_area("Description", value=defaults.get("description", ""), height=90)
    _fmt_options = ["Quantity : Item", "Item : Quantity"]
    _fmt_default = "Item : Quantity" if defaults.get("ingredient_format") == "item_quantity" else "Quantity : Item"
    ingredient_format_label = st.radio(
        "Ingredient entry format",
        options=_fmt_options,
        index=_fmt_options.index(_fmt_default),
        horizontal=True,
        help="Choose how each ingredient line is structured. Use a colon (:) as the separator.",
    )
    ingredient_format = "item_quantity" if ingredient_format_label == "Item : Quantity" else "quantity_item"
    _placeholder = (
        "eggs: 2\nflour: 1 cup\nolive oil: 1 tbsp"
        if ingredient_format == "item_quantity"
        else "2: eggs\n1 cup: flour\n1 tbsp: olive oil"
    )
    ingredients = st.text_area(
        "Ingredients * (one per line)",
        value=defaults.get("ingredients", ""),
        height=180,
        placeholder=_placeholder,
    )
    instructions = st.text_area(
        "Instructions * (step-by-step)",
        value=defaults.get("instructions", ""),
        height=220,
        placeholder="1. Preheat oven to 180C\n2. Mix ingredients\n3. Bake for 20 minutes",
    )
    tips_for_best_result = st.text_area(
        "Tips for best result",
        value=defaults.get("tips_for_best_result", ""),
        height=90,
        placeholder="Example: Rest the dough for 15 minutes before baking.",
    )
    reference_url = st.text_input(
        "Reference URL (optional)",
        value=defaults.get("reference_url", ""),
        placeholder="https://example.com/source-recipe",
    )

    col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
    with col1:
        servings = st.number_input(
            "Servings",
            min_value=1,
            max_value=100,
            value=safe_int(defaults.get("servings"), 1),
            step=1,
        )
    with col2:
        prep_time = st.number_input(
            "Preparation time (min)",
            min_value=0,
            max_value=1440,
            value=safe_int(defaults.get("prep_time"), 15),
            step=5,
        )
    with col3:
        cook_time = st.number_input(
            "Cooking time (min)",
            min_value=0,
            max_value=1440,
            value=safe_int(defaults.get("cook_time"), 20),
            step=5,
        )
    with col4:
        difficulty = st.selectbox(
            "Difficulty",
            difficulty_options,
            index=difficulty_options.index(normalized_difficulty),
        )

    cat_col_1, cat_col_2 = st.columns([1, 1])
    with cat_col_1:
        category = st.selectbox("Category", predefined_categories, index=category_index)
    with cat_col_2:
        category_custom = st.text_input(
            "Custom category (optional)",
            value=default_custom_category,
            help="If provided, custom category overrides selected category.",
        )

    tags = st.text_input(
        "Tags (comma-separated)",
        value=defaults.get("tags", ""),
        placeholder="quick, healthy, vegetarian",
    )
    image = st.file_uploader("Optional image", type=["png", "jpg", "jpeg", "webp"])

    final_category = category_custom.strip() if category_custom.strip() else category
    return {
        "title": title.strip(),
        "description": (description or "").strip(),
        "ingredients": (ingredients or "").strip(),
        "instructions": (instructions or "").strip(),
        "tips_for_best_result": (tips_for_best_result or "").strip(),
        "reference_url": normalize_reference_url(reference_url),
        "servings": int(servings),
        "prep_time": int(prep_time),
        "cook_time": int(cook_time),
        "difficulty": difficulty,
        "category": final_category,
        "tags": (tags or "").strip(),
        "image": image_to_bytes(image),
        "ingredient_format": ingredient_format,
    }


def validate_recipe_input(payload: dict) -> tuple[bool, str]:
    if not payload["title"]:
        return False, "Title is required."
    if not payload["ingredients"]:
        return False, "Ingredients are required."
    if not payload["instructions"]:
        return False, "Instructions are required."
    reference_url = (payload.get("reference_url") or "").strip()
    if reference_url and not re.match(r"^https?://", reference_url, flags=re.IGNORECASE):
        return False, "Reference URL must start with http:// or https://"
    return True, ""


def render_recipe_card(recipe: dict) -> None:
    user_id = get_active_user_id()
    is_admin = is_active_user_admin()
    total_time = int(recipe.get("prep_time") or 0) + int(recipe.get("cook_time") or 0)
    favorite_mark = "★" if int(recipe.get("is_favorite") or 0) == 1 else "☆"
    with st.expander(f"{recipe['title']}  •  {recipe.get('category') or 'Uncategorized'}", expanded=False):
        left, right = st.columns([2, 1])

        with left:
            st.markdown(f"**Description**\n\n{recipe.get('description') or 'No description.'}")
            st.markdown("**Ingredients**")
            render_ingredients_table(recipe.get("ingredients") or "", recipe.get("ingredient_format", "quantity_item"))
            st.markdown("**Instructions**")
            st.text(recipe.get("instructions") or "-")
            if recipe.get("tips_for_best_result"):
                st.markdown("**Tips for best result**")
                st.text(recipe.get("tips_for_best_result"))
            if recipe.get("reference_url"):
                st.markdown(f"**Reference**: [Open source]({recipe.get('reference_url')})")
            if recipe.get("tags"):
                st.markdown(tags_to_text(recipe["tags"]), unsafe_allow_html=True)

        with right:
            img = bytes_to_image(recipe.get("image"))
            if img:
                st.image(img, use_container_width=True)

            st.markdown(
                f"""
                <div class="cv-meta">
                    <p><b>Servings:</b> {recipe.get('servings', 1)}</p>
                    <p><b>Prep:</b> {recipe.get('prep_time', 0)} min</p>
                    <p><b>Cook:</b> {recipe.get('cook_time', 0)} min</p>
                    <p><b>Total:</b> {total_time} min</p>
                    <p><b>Difficulty:</b> {recipe.get('difficulty') or 'N/A'}</p>
                    <p><b>Rating:</b> {recipe.get('rating') if recipe.get('rating') is not None else 'Not rated'}</p>
                    <p><b>Favorite:</b> {favorite_mark}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

        actions_1, actions_2, actions_3, actions_4 = st.columns([1, 1, 1, 2])
        with actions_1:
            if st.button("Edit", key=f"edit_btn_{recipe['id']}"):
                st.session_state["page"] = "Edit Recipe"
                st.session_state["selected_recipe_id"] = recipe["id"]
                st.rerun()
        with actions_2:
            if st.button("Delete", key=f"delete_btn_{recipe['id']}"):
                st.session_state["confirm_delete_id"] = recipe["id"]
                st.rerun()
        with actions_3:
            if st.button("Favorite" if int(recipe.get("is_favorite") or 0) == 0 else "Unfavorite", key=f"fav_btn_{recipe['id']}"):
                set_favorite(
                    recipe["id"],
                    not bool(int(recipe.get("is_favorite") or 0)),
                    user_id=user_id,
                    is_admin=is_admin,
                )
                st.rerun()
        with actions_4:
            default_rating = float(recipe.get("rating") or 0.0)
            rating_value = st.select_slider(
                "Rate",
                options=[0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
                value=default_rating,
                key=f"rating_slider_{recipe['id']}",
            )
            if st.button("Save Rating", key=f"rating_btn_{recipe['id']}"):
                set_rating(
                    recipe["id"],
                    None if rating_value == 0.0 else float(rating_value),
                    user_id=user_id,
                    is_admin=is_admin,
                )
                st.rerun()


def page_browse() -> None:
    user_id = get_active_user_id()
    is_admin = is_active_user_admin()
    st.markdown("### 📚 Recipe Library")
    st.markdown("Search, filter, and manage your recipe collection")
    st.markdown("---")
    categories = ["All"] + get_categories(user_id=user_id, is_admin=is_admin)
    difficulties = ["All"] + get_difficulties(user_id=user_id, is_admin=is_admin)

    search_col, cat_col, diff_col, fav_col, rating_col = st.columns([2, 1, 1, 1, 1])
    with search_col:
        search = st.text_input("🔍 Search", placeholder="Search title, ingredients, description, tags, reference URL")
    with cat_col:
        category = st.selectbox("Category", categories)
    with diff_col:
        difficulty = st.selectbox("Difficulty", difficulties)
    with fav_col:
        favorites_only = st.checkbox("❤️ Favorites only", value=False)
    with rating_col:
        min_rating = st.select_slider(
            "⭐ Min rating",
            options=[0.0, 1.0, 2.0, 3.0, 4.0],
            value=0.0,
        )

    recipes = list_recipes(
        search=search.strip() or None,
        category=None if category == "All" else category,
        difficulty=None if difficulty == "All" else difficulty,
        favorites_only=favorites_only,
        min_rating=None if min_rating == 0.0 else float(min_rating),
        user_id=user_id,
        is_admin=is_admin,
    )

    st.caption(f"{len(recipes)} recipe(s) found")

    if not recipes:
        st.info("No recipes found. Add your first recipe from the sidebar.")
        return

    table_rows: list[dict] = []
    for recipe in recipes:
        total_time = int(recipe.get("prep_time") or 0) + int(recipe.get("cook_time") or 0)
        table_rows.append(
            {
                "ID": int(recipe["id"]),
                "Title": recipe.get("title") or "Untitled",
                "Category": recipe.get("category") or "Uncategorized",
                "Difficulty": recipe.get("difficulty") or "N/A",
                "Servings": int(recipe.get("servings") or 1),
                "Total Time (min)": total_time,
                "Tags": recipe.get("tags") or "",
                "Rating": recipe.get("rating") if recipe.get("rating") is not None else "-",
                "Favorite": "Yes" if int(recipe.get("is_favorite") or 0) == 1 else "No",
                "Reference URL": recipe.get("reference_url") or "",
            }
        )

    browse_df = pd.DataFrame(table_rows)
    browse_df_visible = browse_df.drop(columns=["ID"], errors="ignore")
    st.dataframe(
        browse_df_visible,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Tags": st.column_config.TextColumn(
                "Tags",
                help="Comma-separated tags for this recipe",
                width="medium",
            ),
            "Reference URL": st.column_config.LinkColumn(
                "Reference URL",
                help="Click to open the source recipe URL",
                display_text="Open ↗",
            ),
        },
    )

    selected_recipe_id = st.selectbox(
        "Select a recipe for full details",
        options=[int(item["ID"]) for item in table_rows],
        format_func=lambda rid: next(
            (item["Title"] for item in table_rows if int(item["ID"]) == int(rid)),
            str(rid),
        ),
        key="browse_recipe_details_id",
    )
    detailed_recipe = next((recipe for recipe in recipes if int(recipe["id"]) == int(selected_recipe_id)), None)
    if detailed_recipe:
        render_recipe_card(detailed_recipe)

    confirm_id = st.session_state.get("confirm_delete_id")
    if confirm_id:
        st.warning("Confirm deletion. This action cannot be undone.")
        col1, col2 = st.columns([1, 5])
        with col1:
            if st.button("Confirm Delete", type="primary"):
                deleted = delete_recipe(confirm_id, user_id=user_id, is_admin=is_admin)
                st.session_state["confirm_delete_id"] = None
                if deleted:
                    st.success("Recipe deleted.")
                else:
                    st.error("You are not allowed to delete this recipe.")
                st.rerun()
        with col2:
            if st.button("Cancel"):
                st.session_state["confirm_delete_id"] = None
                st.rerun()


def page_add() -> None:
    user_id = get_active_user_id()
    st.markdown("### ➕ Add New Recipe")
    st.markdown("Create, extract, or import recipes into your library")
    st.markdown("---")

    source_options = ["Manual Entry", "Photo to Text (OCR)", "Web Link", "Chatbot Assistant"]
    if "add_recipe_source" not in st.session_state:
        st.session_state["add_recipe_source"] = "Manual Entry"

    st.radio(
        "Choose source for adding recipe",
        options=source_options,
        key="add_recipe_source",
        horizontal=True,
    )

    selected_source = st.session_state["add_recipe_source"]

    if selected_source == "Manual Entry":
        st.info("Use the form below to enter recipe details manually.")

    elif selected_source == "Photo to Text (OCR)":
        st.markdown("### Photo to Text (OCR)")
        st.caption("Upload or capture a recipe photo to extract text and auto-fill the form.")

        if "show_camera_input" not in st.session_state:
            st.session_state["show_camera_input"] = False

        if st.button("📷 Launch Camera", use_container_width=False):
            st.session_state["show_camera_input"] = True

        camera_file = None
        if st.session_state.get("show_camera_input"):
            camera_file = st.camera_input("Capture recipe with camera", key="recipe_camera_ocr_input")

        photo_file = st.file_uploader(
            "Recipe photo",
            type=["png", "jpg", "jpeg", "webp"],
            key="recipe_photo_ocr_uploader",
        )
        selected_photo = camera_file if camera_file is not None else photo_file
        if selected_photo is not None:
            preview_caption = "Camera capture preview" if camera_file is not None else "Photo preview"
            st.image(selected_photo, caption=preview_caption, width=280)

        if st.button("Extract Text from Photo", disabled=selected_photo is None):
            try:
                ocr_text = extract_text_from_recipe_photo(selected_photo)
                if not ocr_text:
                    st.warning("No readable text found in the uploaded image.")
                else:
                    st.session_state["ocr_text"] = ocr_text
                    parsed = parse_recipe_from_text(ocr_text)
                    if parsed:
                        st.session_state["add_recipe_defaults"] = parsed
                    st.success("Text extracted. Review and save below.")
                    st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
                st.info("OCR is unavailable in the current environment. Reinstall project requirements.")
            except Exception:
                st.error("Could not process image for OCR. Try a clearer photo.")

        if st.session_state.get("ocr_text"):
            st.text_area("Extracted OCR text", value=st.session_state["ocr_text"], height=160, disabled=True)

    elif selected_source == "Web Link":
        st.markdown("### Extract from Web Link")
        st.caption("Paste a recipe URL. RecipeSnap will traverse structured data and keywords to auto-fill fields.")
        web_url = st.text_input(
            "Recipe web link",
            placeholder="https://example.com/your-recipe",
            key="web_recipe_url_input",
        )
        if st.button("Extract Recipe from Web Link", disabled=not (web_url or "").strip()):
            try:
                parsed_web, web_text = parse_recipe_from_web_url(web_url)
                st.session_state["add_recipe_defaults"] = parsed_web
                st.session_state["web_extracted_text"] = web_text[:3000]
                st.success("Recipe extracted from web link. Review and save below.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except RuntimeError as exc:
                st.error(str(exc))
            except Exception:
                st.error("Could not parse this web page. Try another link.")

        if st.session_state.get("web_extracted_text"):
            st.text_area(
                "Extracted web text preview",
                value=st.session_state["web_extracted_text"],
                height=140,
                disabled=True,
            )

    else:
        st.markdown("### 🤖 Chatbot Recipe Assistant")
        st.caption(
            "Describe what you want to cook. If your provider key is connected, live AI generates the draft; otherwise local fallback is used."
        )

        provider = get_ai_provider()
        api_key, source = get_ai_token(provider)
        provider_label = "OpenAI (ChatGPT API)" if provider == "openai" else "GitHub Models"
        source_descriptions = {
            "session": "Using sidebar/profile key",
            "secrets": "Using Streamlit secrets key",
            "env": "Using environment variable key",
            "none": "No API key found",
        }

        source_msg = source_descriptions.get(source, "Unknown source")
        if source == "none":
            st.warning(f"⚠️ **{provider_label}** — {source_msg}. Using local fallback templates.")
            st.markdown(
                "💡 **To use live AI:** \n"
                f"- Get your API key from [platform.openai.com](https://platform.openai.com/account/api-keys) (OpenAI) "
                f"or [github.com/settings/tokens](https://github.com/settings/tokens) (GitHub)\n"
                f"- Enter it in the **Connect AI** section in the sidebar"
            )
        else:
            st.success(f"✅ **{provider_label}** connected — {source_msg}")

        if "recipe_chat_messages" not in st.session_state:
            st.session_state["recipe_chat_messages"] = [
                {
                    "role": "assistant",
                    "content": "Tell me what recipe you want, for example: high-protein Indian dinner under 30 min.",
                }
            ]

        # Display chat history
        for msg in st.session_state["recipe_chat_messages"]:
            role = str(msg.get("role") or "assistant").lower()
            content = html.escape(str(msg.get("content") or "")).replace("\n", "<br>")
            bubble_class = "cv-chat-bubble-user" if role == "user" else "cv-chat-bubble-assistant"
            speaker = "You" if role == "user" else "RecipeSnap AI"
            st.markdown(
                f"<div class='cv-chat-bubble {bubble_class}'><strong>{speaker}</strong>{content}</div>",
                unsafe_allow_html=True,
            )

        # Chat input
        chat_prompt = st.chat_input("e.g., 'Quick pasta for 2 people' or 'Vegan chocolate cake'", key="recipe_chat_prompt")
        
        # Quick example buttons
        st.markdown("**Quick suggestions:**")
        suggest_col1, suggest_col2, suggest_col3, suggest_col4 = st.columns(4)
        example_prompts = [
            "Quick pasta dinner",
            "Vegan dessert",
            "High-protein breakfast",
            "Party appetizer",
        ]
        
        for idx, (col, prompt) in enumerate(zip([suggest_col1, suggest_col2, suggest_col3, suggest_col4], example_prompts)):
            with col:
                if st.button(prompt, key=f"example_prompt_{idx}", use_container_width=True):
                    chat_prompt = prompt
        
        if chat_prompt:
            draft, chat_source = build_chatbot_recipe_draft(chat_prompt)
            st.session_state["recipe_chat_messages"].append({"role": "user", "content": chat_prompt})
            chatbot_error_text = (st.session_state.pop("chatbot_last_error", "") or "").strip()
            if draft:
                st.session_state["chatbot_recipe_draft"] = draft
                st.session_state["chatbot_recipe_source"] = chat_source
                source_label = provider_label if chat_source == "copilot" else "Local fallback"
                fallback_notice = ""
                if chatbot_error_text and chat_source == "fallback":
                    fallback_notice = f"\n\n_OpenAI request failed: {chatbot_error_text}_"
                st.session_state["recipe_chat_messages"].append(
                    {
                        "role": "assistant",
                        "content": f"{format_draft_preview(draft)}\n\n_Source: {source_label}_{fallback_notice}",
                    }
                )
            elif chat_source == "off_topic":
                st.session_state["recipe_chat_messages"].append(
                    {
                        "role": "assistant",
                        "content": "I can only help with food and recipe requests. Please ask me to generate a recipe (e.g. 'give me a spicy chicken curry recipe').",
                    }
                )
            else:
                if chatbot_error_text:
                    st.session_state["recipe_chat_messages"].append(
                        {
                            "role": "assistant",
                            "content": f"OpenAI request failed: {chatbot_error_text}",
                        }
                    )
                st.session_state["recipe_chat_messages"].append(
                    {"role": "assistant", "content": "I could not generate a recipe for that request. Please add more detail."}
                )
            st.rerun()

        if st.session_state.get("chatbot_recipe_draft"):
            source_label = provider_label if st.session_state.get("chatbot_recipe_source") == "copilot" else "Local fallback"
            st.info(f"📋 Generated recipe (source: {source_label})")
            st.markdown("**Saving Option**")
            save_destination = st.radio(
                "What would you like to do?",
                options=["Keep as draft only", "Move to Add Recipe form", "Save directly to Recipe Library"],
                key="chatbot_save_destination",
                horizontal=True,
            )

            chatbot_col_1, chatbot_col_2 = st.columns([1.4, 1])
            with chatbot_col_1:
                if st.button("Apply Save Choice", type="secondary"):
                    draft = dict(st.session_state["chatbot_recipe_draft"])
                    should_rerun = False
                    if save_destination == "Move to Add Recipe form":
                        st.session_state["add_recipe_defaults"] = draft
                        st.success("✅ Draft moved to Add Recipe form below. You can edit before saving.")
                        should_rerun = True
                    elif save_destination == "Save directly to Recipe Library":
                        ok, message = validate_recipe_input(draft)
                        if not ok:
                            st.error(f"❌ Generated draft is incomplete: {message}")
                            st.info("💡 Tip: Choose 'Move to Add Recipe form' to edit missing fields before saving.")
                        else:
                            create_recipe(draft, user_id=user_id)
                            st.success("✅ Recipe saved directly to your library!")
                            should_rerun = True
                    else:
                        st.success("✅ Draft kept in chatbot preview. You can save it later.")
                    if should_rerun:
                        st.rerun()
            with chatbot_col_2:
                if st.button("Clear chatbot"):
                    st.session_state.pop("chatbot_recipe_draft", None)
                    st.session_state.pop("chatbot_recipe_source", None)
                    st.session_state["recipe_chat_messages"] = [
                        {
                            "role": "assistant",
                            "content": "Tell me what recipe you want, for example: high-protein Indian dinner under 30 min.",
                        }
                    ]
                    st.rerun()

    add_defaults = st.session_state.get("add_recipe_defaults") or {}
    # For Chatbot Assistant, only show the form once a draft has been moved to it.
    # For all other sources the form is always visible.
    show_form = (selected_source != "Chatbot Assistant") or bool(add_defaults)

    if show_form:
        if selected_source == "Chatbot Assistant":
            st.markdown("---")
            st.markdown("### Edit & Save Chatbot Draft")
        with st.form("add_recipe_form", clear_on_submit=True):
            payload = form_recipe_fields(add_defaults)
            submitted = st.form_submit_button("Save Recipe", type="primary")
    else:
        submitted = False
        payload = {}

    if submitted and payload:
        ok, message = validate_recipe_input(payload)
        if not ok:
            st.error(message)
            return

        create_recipe(payload, user_id=user_id)
        st.session_state.pop("add_recipe_defaults", None)
        st.session_state.pop("ocr_text", None)
        st.session_state.pop("web_extracted_text", None)
        st.success("Recipe added successfully.")


def page_edit() -> None:
    user_id = get_active_user_id()
    is_admin = is_active_user_admin()
    st.markdown("### ✏️ Edit Recipe")
    st.markdown("Update your recipe details, images, and information")
    st.markdown("---")
    options = list_recipe_options(user_id=user_id, is_admin=is_admin)
    if not options:
        st.info("No recipes available to edit.")
        return

    selected_id = st.session_state.get("selected_recipe_id")
    option_ids = [opt[0] for opt in options]

    default_index = 0
    if selected_id in option_ids:
        default_index = option_ids.index(selected_id)

    selected_recipe_id = st.selectbox(
        "Select recipe",
        options=option_ids,
        index=default_index,
        format_func=lambda rid: next((name for recipe_id, name in options if recipe_id == rid), str(rid)),
    )

    recipe = get_recipe(selected_recipe_id, user_id=user_id, is_admin=is_admin)
    if not recipe:
        st.error("Selected recipe was not found.")
        return

    existing_image = bytes_to_image(recipe.get("image"))
    if existing_image:
        st.image(existing_image, caption="Current image", width=280)

    with st.form("edit_recipe_form"):
        payload = form_recipe_fields(recipe)
        keep_current_image = st.checkbox(
            "Keep existing image if no new file is uploaded",
            value=True,
        )
        submitted = st.form_submit_button("Update Recipe", type="primary")

    if submitted:
        ok, message = validate_recipe_input(payload)
        if not ok:
            st.error(message)
            return

        if payload["image"] is None and keep_current_image:
            payload["image"] = recipe.get("image")

        payload["is_favorite"] = int(recipe.get("is_favorite") or 0)
        payload["rating"] = recipe.get("rating")

        updated = update_recipe(selected_recipe_id, payload, user_id=user_id, is_admin=is_admin)
        if updated:
            st.success("Recipe updated successfully.")
        else:
            st.error("You are not allowed to update this recipe.")
        st.session_state["selected_recipe_id"] = selected_recipe_id


def page_data_tools() -> None:
    user_id = get_active_user_id()
    is_admin = is_active_user_admin()
    st.markdown("### 💾 Import / Export")
    st.markdown("Backup and migrate your recipes with Excel")
    st.markdown("---")

    export_rows = export_recipes_records(user_id=user_id, is_admin=is_admin)
    export_df = pd.DataFrame(export_rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="recipes")
    output.seek(0)

    st.download_button(
        "Export Recipes as Excel",
        data=output.getvalue(),
        file_name="recipesnap_recipes.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=False,
    )

    # Full dataset export includes all recipe columns for the active user scope.
    complete_rows = list_recipes(user_id=user_id, is_admin=is_admin)
    complete_dataset: list[dict] = []
    for row in complete_rows:
        item = dict(row)
        image_blob = item.get("image")
        item["image_base64"] = b64encode(bytes(image_blob)).decode("ascii") if image_blob else None
        item.pop("image", None)
        complete_dataset.append(item)

    export_payload = {
        "record_count": len(complete_dataset),
        "recipes": complete_dataset,
    }
    st.download_button(
        "Download Complete Recipe Dataset (JSON)",
        data=json.dumps(export_payload, ensure_ascii=True, indent=2).encode("utf-8"),
        file_name="recipesnap_complete_recipe_dataset.json",
        mime="application/json",
        use_container_width=False,
    )

    st.markdown("### Import Recipes")
    imported_file = st.file_uploader("Upload Excel export (.xlsx)", type=["xlsx"], key="import_excel_uploader")
    if imported_file is not None:
        try:
            imported_df = pd.read_excel(imported_file, sheet_name="recipes")
            imported_df = imported_df.where(pd.notnull(imported_df), None)
            payload = imported_df.to_dict(orient="records")
            created = import_recipes_records(payload, user_id=user_id)
            st.success(f"Imported {created} recipe(s) into SQLite.")
        except ValueError:
            st.error("The workbook must contain a sheet named 'recipes'.")
        except Exception:
            st.error("Could not import Excel file. Verify that it matches the exported format.")


def page_meal_planner() -> None:
    user_id = get_active_user_id()
    is_admin = is_active_user_admin()
    st.markdown("### 📅 Meal Planner")
    st.markdown("Plan your meals for the next two weeks")
    st.markdown("---")

    options = list_recipe_options(user_id=user_id, is_admin=is_admin)
    if not options:
        st.info("Add at least one recipe before creating meal plans.")
        return

    option_ids = [recipe_id for recipe_id, _ in options]
    with st.form("meal_plan_form", clear_on_submit=True):
        date_col, type_col, recipe_col = st.columns([1, 1, 2])
        with date_col:
            meal_date = st.date_input("Date", value=date.today())
        with type_col:
            meal_type = st.selectbox("Meal", ["Breakfast", "Lunch", "Dinner", "Snack"])
        with recipe_col:
            recipe_id = st.selectbox(
                "Recipe",
                options=option_ids,
                format_func=lambda rid: next((name for x, name in options if x == rid), str(rid)),
            )
        notes = st.text_input("Notes (optional)")
        submitted = st.form_submit_button("Add to Planner", type="primary")

    if submitted:
        created_id = add_meal_plan_entry(
            meal_date.isoformat(),
            meal_type,
            int(recipe_id),
            notes.strip() or None,
            user_id=user_id,
            is_admin=is_admin,
        )
        if created_id:
            st.success("Meal plan entry saved to SQLite.")
        else:
            st.error("You are not allowed to add meal plans for this recipe.")

    st.markdown("### Upcoming 14 Days")
    start = date.today()
    end = start + timedelta(days=13)
    entries = list_meal_plan_entries(start.isoformat(), end.isoformat(), user_id=user_id, is_admin=is_admin)

    if not entries:
        st.info("No entries in the upcoming schedule.")
        return

    for entry in entries:
        row_1, row_2 = st.columns([6, 1])
        with row_1:
            notes_text = f" | Notes: {entry['notes']}" if entry.get("notes") else ""
            st.write(
                f"{entry['meal_date']} | {entry['meal_type']} | {entry['recipe_title']}"
                f" ({entry.get('recipe_category') or 'Uncategorized'}){notes_text}"
            )
        with row_2:
            if st.button("Remove", key=f"meal_del_{entry['id']}"):
                delete_meal_plan_entry(int(entry["id"]), user_id=user_id, is_admin=is_admin)
                st.rerun()


def page_cook_mode() -> None:
    user_id = get_active_user_id()
    is_admin = is_active_user_admin()
    st.markdown("### 👨‍🍳 Cook Mode")
    st.markdown("Step-by-step guided cooking with ingredient scaling")
    st.markdown("---")

    options = list_recipe_options(user_id=user_id, is_admin=is_admin)
    if not options:
        st.info("Add at least one recipe before starting Cook Mode.")
        return

    option_ids = [recipe_id for recipe_id, _ in options]
    main_col, right_col = st.columns([2.8, 0.9], gap="large")

    with right_col:
        st.markdown("### Cook Controls")
        selected_ids = st.multiselect(
            "Select recipe(s)",
            options=option_ids,
            format_func=lambda rid: next((name for x, name in options if x == rid), str(rid)),
            default=st.session_state.get("cook_mode_selected_ids", []),
        )

        target_servings_map: dict[int, int] = {}
        if selected_ids:
            st.markdown("#### Ingredient Scaling")
            for recipe_id in selected_ids:
                recipe = get_recipe(int(recipe_id), user_id=user_id, is_admin=is_admin)
                if not recipe:
                    continue
                base_servings = max(1, int(recipe.get("servings", 1) or 1))
                target_servings_map[int(recipe_id)] = st.number_input(
                    f"{recipe.get('title') or recipe_id} servings",
                    min_value=1,
                    max_value=100,
                    value=int(st.session_state.get("cook_mode_target_servings", {}).get(int(recipe_id), base_servings)),
                    step=1,
                    key=f"cook_servings_{recipe_id}",
                )

        st.markdown("#### Tips for Best Result")
        with st.container(border=True):
            if selected_ids:
                for recipe_id in selected_ids:
                    recipe = get_recipe(int(recipe_id), user_id=user_id, is_admin=is_admin)
                    if not recipe:
                        continue
                    tip = (recipe.get("tips_for_best_result") or "").strip()
                    st.markdown(f"**{recipe.get('title') or recipe_id}**")
                    st.write(tip if tip else "No tips added for this recipe.")
            else:
                st.caption("Select recipe(s) to preview scaling and tips.")

        controls_1, controls_2 = st.columns([1, 1])
        with controls_1:
            if st.button("▶", type="primary", help="Start cooking"):
                if not selected_ids:
                    st.warning("Please select at least one recipe.")
                else:
                    st.session_state["cook_mode_selected_ids"] = selected_ids
                    st.session_state["cook_mode_target_servings"] = target_servings_map
                    st.session_state["cook_recipe_index"] = 0
                    st.session_state["cook_step_index"] = 0
                    st.session_state["scroll_to_cook"] = True
                    st.rerun()
        with controls_2:
            if st.button("↺", help="Reset cook mode"):
                st.session_state.pop("cook_mode_selected_ids", None)
                st.session_state.pop("cook_mode_target_servings", None)
                st.session_state.pop("cook_recipe_index", None)
                st.session_state.pop("cook_step_index", None)
                st.rerun()

    active_ids = st.session_state.get("cook_mode_selected_ids", [])
    if not active_ids:
        with main_col:
            st.info("Select recipe(s), adjust scaling in the right panel, then click Start Cooking.")
        return

    recipe_index = int(st.session_state.get("cook_recipe_index", 0))
    step_index = int(st.session_state.get("cook_step_index", 0))
    recipe_index = max(0, min(recipe_index, len(active_ids) - 1))

    current_recipe = get_recipe(int(active_ids[recipe_index]), user_id=user_id, is_admin=is_admin)
    if not current_recipe:
        st.error("A selected recipe could not be found. Reset and try again.")
        return

    target_servings = int(
        st.session_state.get("cook_mode_target_servings", {}).get(
            int(current_recipe["id"]),
            max(1, int(current_recipe.get("servings", 1) or 1)),
        )
    )
    base_servings = max(1, int(current_recipe.get("servings", 1) or 1))
    steps = parse_instruction_steps(current_recipe.get("instructions") or "")
    if not steps:
        st.warning("This recipe has no instruction steps.")
        return

    step_index = max(0, min(step_index, len(steps) - 1))
    st.session_state["cook_recipe_index"] = recipe_index
    st.session_state["cook_step_index"] = step_index

    recipe_name = current_recipe.get("title") or f"Recipe {current_recipe.get('id')}"

    # Anchor + scroll trigger: on mobile, "Start Cooking" scrolls here
    st.markdown('<div id="cv-cook-top"></div>', unsafe_allow_html=True)
    if st.session_state.pop("scroll_to_cook", False):
        import streamlit.components.v1 as _stc
        _stc.html(
            "<script>"
            "var el=window.parent.document.getElementById('cv-cook-top');"
            "if(el){el.scrollIntoView({behavior:'smooth',block:'start'});}"
            "</script>",
            height=0,
        )

    with main_col:
        st.markdown(f"### {recipe_name}")
        st.caption(
            f"Recipe {recipe_index + 1}/{len(active_ids)} | Step {step_index + 1}/{len(steps)} | Servings {base_servings} -> {target_servings}"
        )

        panel_1, panel_2 = st.columns([2.1, 0.9], gap="large")
        with panel_1:
            st.markdown("#### Scaled Ingredients")
            st.caption(f"Showing base and scaled measurements for {target_servings} persons")
            render_ingredients_table(
                current_recipe.get("ingredients") or "",
                current_recipe.get("ingredient_format", "quantity_item"),
                base_servings=base_servings,
                target_servings=target_servings,
            )
        with panel_2:
            step_nav_1, step_nav_2 = st.columns([1, 1])
            with step_nav_1:
                if st.button("⏮", help="Previous step"):
                    if step_index > 0:
                        st.session_state["cook_step_index"] = step_index - 1
                        set_cook_tip_popup(current_recipe)
                    elif recipe_index > 0:
                        prev_recipe = get_recipe(int(active_ids[recipe_index - 1]), user_id=user_id, is_admin=is_admin)
                        prev_steps = parse_instruction_steps((prev_recipe or {}).get("instructions") or "")
                        st.session_state["cook_recipe_index"] = recipe_index - 1
                        st.session_state["cook_step_index"] = max(0, len(prev_steps) - 1)
                        set_cook_tip_popup(prev_recipe)
                    st.rerun()
            with step_nav_2:
                if st.button("⏭", type="primary", help="Next step"):
                    if step_index < len(steps) - 1:
                        st.session_state["cook_step_index"] = step_index + 1
                        set_cook_tip_popup(current_recipe)
                    elif recipe_index < len(active_ids) - 1:
                        st.session_state["cook_recipe_index"] = recipe_index + 1
                        st.session_state["cook_step_index"] = 0
                        next_recipe = get_recipe(int(active_ids[recipe_index + 1]), user_id=user_id, is_admin=is_admin)
                        set_cook_tip_popup(next_recipe)
                    else:
                        st.success("You completed all selected recipes.")
                    st.rerun()
            st.markdown(f"#### Step {step_index + 1}")
            with st.container(border=True):
                st.write(steps[step_index])

        recipe_nav_1, recipe_nav_2 = st.columns([1, 1])
        with recipe_nav_1:
            if st.button("⏮", key="cook_prev_recipe_btn", help="Previous recipe"):
                if recipe_index > 0:
                    st.session_state["cook_recipe_index"] = recipe_index - 1
                    st.session_state["cook_step_index"] = 0
                    prev_recipe = get_recipe(int(active_ids[recipe_index - 1]), user_id=user_id, is_admin=is_admin)
                    set_cook_tip_popup(prev_recipe)
                    st.rerun()

        with recipe_nav_2:
            if st.button("⏭", key="cook_next_recipe_btn", help="Next recipe"):
                if recipe_index < len(active_ids) - 1:
                    st.session_state["cook_recipe_index"] = recipe_index + 1
                    st.session_state["cook_step_index"] = 0
                    next_recipe = get_recipe(int(active_ids[recipe_index + 1]), user_id=user_id, is_admin=is_admin)
                    set_cook_tip_popup(next_recipe)
                    st.rerun()


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🍽️", layout="wide")
    try:
        init_db()
    except (sqlite3.OperationalError, RuntimeError) as exc:
        st.error(
            "Database unavailable. Check the local SQLite file path or DATABASE_URL for deployment. "
            f"Details: {exc}"
        )
        st.stop()
    inject_styles()
    render_header()

    # --- Persistent session via cookie ---
    try:
        import extra_streamlit_components as stx
        cookie_manager = stx.CookieManager(key="cv_cookie_mgr")
        session_token = cookie_manager.get("cv_session")
    except Exception:
        cookie_manager = None
        session_token = None

    # Auto-restore session from cookie if not already logged in
    if not get_active_user() and session_token:
        restored_user = get_session_user(session_token)
        if restored_user:
            st.session_state["auth_user"] = restored_user
            st.session_state["cv_session_token"] = session_token
            saved_key = db_get_openai_api_key(restored_user["id"])
            if saved_key:
                st.session_state["openai_api_key"] = saved_key

    if not get_active_user():
        page_auth()
        return

    active_user = get_active_user() or {}
    fresh_user = get_user_by_id(int(active_user.get("id") or -1))
    if not fresh_user:
        st.session_state.pop("auth_user", None)
        st.warning("Your account was not found. Please login again.")
        st.rerun()
    if bool(int((fresh_user or {}).get("is_blocked") or 0)):
        st.session_state.pop("auth_user", None)
        st.error("Your account has been blocked by admin.")
        st.rerun()

    st.session_state["auth_user"] = {
        "id": int(fresh_user.get("id") or -1),
        "username": str(fresh_user.get("username") or ""),
        "email": str(fresh_user.get("email") or ""),
        "full_name": str(fresh_user.get("full_name") or ""),
        "phone": str(fresh_user.get("phone") or ""),
        "city": str(fresh_user.get("city") or ""),
        "country": str(fresh_user.get("country") or ""),
        "cooking_preference": str(fresh_user.get("cooking_preference") or ""),
        "is_admin": bool(int(fresh_user.get("is_admin") or 0)),
        "is_blocked": bool(int(fresh_user.get("is_blocked") or 0)),
        "created_at": str(fresh_user.get("created_at") or ""),
    }

    st.sidebar.title(APP_NAME)
    active_user = get_active_user() or {}
    role = "Admin" if bool(active_user.get("is_admin")) else "User"
    st.sidebar.caption(f"Logged in as {active_user.get('username')} ({role})")

    if st.sidebar.button("Logout", use_container_width=True):
        # Invalidate persistent session
        token = st.session_state.pop("cv_session_token", None)
        if token:
            delete_user_session(token)
        try:
            import extra_streamlit_components as stx
            _cm = stx.CookieManager(key="cv_cookie_mgr_logout")
            _cm.delete("cv_session")
        except Exception:
            pass
        for key in [
            "auth_user",
            "page",
            "_nav",
            "selected_recipe_id",
            "confirm_delete_id",
            "browse_recipe_details_id",
            "cook_mode_selected_ids",
            "cook_mode_target_servings",
            "cook_recipe_index",
            "cook_step_index",
        ]:
            st.session_state.pop(key, None)
        st.rerun()

    pages = ["Browse Recipes", "Add Recipe", "Edit Recipe", "Cook Mode", "Meal Planner", "Data Tools", "My Profile"]
    if bool(active_user.get("is_admin")):
        pages.append("Admin Users")

    if "page" not in st.session_state or st.session_state["page"] not in pages:
        st.session_state["page"] = "Browse Recipes"
    # Always sync radio key from page before the widget renders so programmatic
    # navigation (setting only `page`) is reflected in the sidebar without raising
    # StreamlitAPIException (widget-key must be set before render, not after).
    st.session_state["_nav"] = st.session_state["page"]

    def _on_nav_change() -> None:
        st.session_state["page"] = st.session_state["_nav"]

    st.sidebar.radio("Navigation", pages, key="_nav", on_change=_on_nav_change)
    
    # AI API Key configuration
    setup_ai_api_key_sidebar()
    
    page = st.session_state["page"]
    if page == "Browse Recipes":
        page_browse()
    elif page == "Add Recipe":
        page_add()
    elif page == "Edit Recipe":
        page_edit()
    elif page == "Cook Mode":
        page_cook_mode()
    elif page == "Meal Planner":
        page_meal_planner()
    elif page == "My Profile":
        page_my_profile()
    elif page == "Admin Users":
        page_admin_users()
    else:
        page_data_tools()


if __name__ == "__main__":
    main()
