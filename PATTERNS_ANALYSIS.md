# RecipeSnap Codebase Patterns & Conventions Guide

## Executive Summary
RecipeSnap is a **single-file Streamlit app** (`app.py`) backed by a simple SQLite layer (`db.py`). No ORM, no external frameworks. The codebase prioritizes **graceful degradation** (optional dependencies), **defensive input handling**, and **session-state-driven navigation**. This guide captures implicit conventions to help agents understand intent and avoid common mistakes.

---

## 1. Common Development Patterns

### 1.1 Input Cleaning & Coercion Pattern
**Pattern:** Always strip, lower-case, and type-cast user input before use. Never trust `.get()` defaults.

```python
# ✅ CORRECT
clean_username = (username or "").strip()
clean_email = (email or "").strip().lower()
safe_user_id = int(user_id or -1)

# ❌ AVOID
email = email.lower()  # Fails if email is None
user_id = user_id  # Type mismatch if it comes from DB row
```

**Where it's used:**
- `create_user()`, `reset_user_password()`, `update_user_profile()` in `db.py`
- Form inputs in `form_recipe_fields()` use `.strip()` and `safe_int()` helper

**Why:** Streamlit form inputs, DB rows, and user prompts are all potentially `None`, empty, or wrong type.

---

### 1.2 Tuple Return for Status + Message
**Pattern:** All user-facing DB writes return `tuple[bool, str]` for (success, message). All reads return dicts.

```python
# ✅ CORRECT
def create_user(...) -> tuple[bool, str]:
    try:
        with get_connection() as conn:
            conn.execute(...)
        return True, "Registration successful."
    except sqlite3.IntegrityError:
        return False, "Username or email already exists."

# Then in app.py:
ok, message = create_user(...)
if ok:
    st.success(message)
else:
    st.error(message)
```

**Where it's used:**
- `create_user()`, `reset_user_password()`, `update_user_profile()`, `set_user_blocked()`

**Why:** Centralizes error messaging in db.py; UI layer only decides how to display, not what to say.

---

### 1.3 Try-Except-st.error Pattern for User Actions
**Pattern:** Wrap user-triggered operations (web fetch, image upload, API call) in try-except; always convert exceptions to `st.error()` or fallback gracefully.

```python
# ✅ CORRECT (Web extraction)
if st.button("Extract Recipe from Web Link", disabled=not (web_url or "").strip()):
    try:
        parsed_web, web_text = parse_recipe_from_web_url(web_url)
        st.session_state["add_recipe_defaults"] = parsed_web
        st.success("Recipe extracted from web link.")
        st.rerun()
    except ValueError as exc:
        st.error(str(exc))
    except RuntimeError as exc:
        st.error(str(exc))
    except Exception:
        st.error("Could not parse this web page. Try another link.")

# ✅ CORRECT (OCR)
if st.button("Extract Text from Photo", disabled=selected_photo is None):
    try:
        ocr_text = extract_text_from_recipe_photo(selected_photo)
        if not ocr_text:
            st.warning("No readable text found in the uploaded image.")
        else:
            st.session_state["ocr_text"] = ocr_text
            st.success("Text extracted. Review and save below.")
            st.rerun()
    except RuntimeError as exc:
        st.error(str(exc))
    except Exception:
        st.error("Could not process image for OCR. Try a clearer photo.")
```

**Where it's used:**
- `page_add()` for OCR, web extraction, chatbot draft validation
- `call_chatgpt_for_recipe()` wraps HTTP errors
- `validate_recipe_input()` wraps field checks

**Why:** Prevents one user error from crashing the app. Messages guide the user to retry or use an alternative path.

---

### 1.4 Optional Dependency Graceful Fallback
**Pattern:** Wrap optional imports in try-except; expose via module-level variable; check before use; provide local fallback if available.

```python
# At top of app.py
try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

try:
    import pytesseract
except ImportError:
    pytesseract = None

# In extract_text_from_recipe_photo():
if RapidOCR is not None:
    ocr = get_rapidocr_engine()
    # ... use RapidOCR
    return result
if pytesseract is not None:
    # ... use pytesseract
    return result
raise RuntimeError("OCR engine is unavailable. Install project requirements...")
```

**Where it's used:**
- OCR engines (RapidOCR, pytesseract) in `extract_text_from_recipe_photo()`
- GitHub Copilot token in `call_chatgpt_for_recipe()` (local fallback template)

**Why:** App runs without OCR/AI but offers them if installed. No hard dependency on external services.

---

### 1.5 Dict Payload Pattern for DB Inserts/Updates
**Pattern:** Collect form data into a single `dict` (payload), pass to DB layer, DB layer extracts fields explicitly.

```python
# In app.py form handler:
payload = form_recipe_fields(defaults)  # Returns dict with all fields
if submitted and payload:
    ok, message = validate_recipe_input(payload)
    create_recipe(payload, user_id=user_id)

# In db.py:
def create_recipe(payload: dict, user_id: int) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO recipes (...) VALUES (?, ..., ?)""",
            (
                payload["title"],
                payload.get("description"),
                payload["ingredients"],
                # ... each field extracted explicitly
                int(user_id),
            ),
        )
```

**Where it's used:**
- `create_recipe(payload, user_id)`, `update_recipe(recipe_id, payload, ...)`
- Any multi-field operation

**Why:** Separates UI form assembly from DB schema; payload is loosely typed dict, DB function is responsible for extraction and defaults.

---

### 1.6 Scope Filtering Helper (`_recipe_scope_clause`)
**Pattern:** Non-admin users only see their own recipes. Helper function builds conditional WHERE clause.

```python
def _recipe_scope_clause(user_id: Optional[int], is_admin: bool, table_alias: str = "recipes") -> tuple[str, list[object]]:
    if is_admin:
        return "", []  # Admins see all
    safe_user_id = int(user_id or -1)
    return f" AND {table_alias}.user_id = ?", [safe_user_id]

# Usage:
def list_recipes(..., user_id: Optional[int] = None, is_admin: bool = False) -> list[dict]:
    query = "SELECT * FROM recipes WHERE 1=1"
    params: list[object] = []
    scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
    query += scope_clause
    params.extend(scope_params)
    # ...
```

**Where it's used:**
- All recipe read/write queries: `list_recipes()`, `get_recipe()`, `update_recipe()`, `delete_recipe()`, `set_favorite()`, `set_rating()`, etc.
- Meal plan entries queries

**Why:** Centralizes permission logic; ensures consistency; makes auditing easy.

---

### 1.7 SQLite Row → Dict Conversion
**Pattern:** Use `sqlite3.Row` with `row_factory` to get dict-like access; convert to `dict()` for Streamlit/JSON serialization.

```python
# In db.py setup:
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Enable dict-like access
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

# In query result:
with get_connection() as conn:
    rows = conn.execute(...).fetchall()
return [dict(row) for row in rows]  # Convert to list of dicts
```

**Where it's used:**
- Every query in `db.py` that returns data

**Why:** `sqlite3.Row` is memory-efficient and dict-like; converting to `dict()` makes Streamlit and JSON serialization work seamlessly.

---

## 2. State Management Patterns

### 2.1 Session State Key Conventions
**Pattern:** Use descriptive, hierarchical keys. Prefix page-specific state with page name or feature.

```python
# Navigation
st.session_state["page"]  # Current page: "Browse Recipes", "Add Recipe", etc.

# Auth
st.session_state["auth_user"]  # Dict: {id, username, email, ...}

# Form defaults
st.session_state["add_recipe_defaults"]  # Dict from OCR, web, or chatbot
st.session_state["ocr_text"]  # Raw text extracted from photo
st.session_state["web_extracted_text"]  # Truncated HTML text from web

# Cook mode
st.session_state["cook_mode_selected_ids"]  # List[int] of recipes being cooked
st.session_state["cook_mode_target_servings"]  # Dict[int, int]: recipe_id -> target_servings
st.session_state["cook_recipe_index"]  # int: current recipe in selected list
st.session_state["cook_step_index"]  # int: current step in current recipe

# Chatbot
st.session_state["recipe_chat_messages"]  # List[dict]: role + content for chat UI
st.session_state["chatbot_recipe_draft"]  # Dict: generated recipe
st.session_state["chatbot_recipe_source"]  # str: "copilot" or "fallback"

# Modals
st.session_state["confirm_delete_id"]  # int or None: recipe ID to confirm delete
st.session_state["cook_tip_popup_visible"]  # bool
st.session_state["cook_tip_popup_recipe_id"]  # int
st.session_state["cook_tip_popup_title"]  # str
st.session_state["cook_tip_popup_message"]  # str

# Theme
st.session_state["dark_mode"]  # bool

# Radio/Select widget internals
st.session_state["_nav"]  # str: synced with sidebar radio for page nav
st.session_state["add_recipe_source"]  # str: "Manual Entry", "Photo to Text", "Web Link", "Chatbot"
st.session_state["show_camera_input"]  # bool: show camera widget
```

**Where it's used:**
- Every page function uses `st.session_state["page"]` to store current page
- `page_cook_mode()` uses `cook_mode_*` keys to persist recipe selection and servings across reruns
- `page_add()` uses `add_recipe_defaults`, `ocr_text`, `web_extracted_text` to prefill form after extraction

**Why:** Streamlit reruns on every interaction; session_state persists across reruns within a single browser session.

---

### 2.2 Widget Key Naming & Collision Avoidance
**Pattern:** Use unique keys for interactive widgets, especially in loops. Prefix with recipe/entry ID.

```python
# ✅ CORRECT: Unique key per recipe
for recipe in recipes:
    if st.button("Edit", key=f"edit_btn_{recipe['id']}"):
        st.session_state["page"] = "Edit Recipe"
        st.session_state["selected_recipe_id"] = recipe["id"]
        st.rerun()
    if st.button("Delete", key=f"delete_btn_{recipe['id']}"):
        st.session_state["confirm_delete_id"] = recipe["id"]
        st.rerun()
    rating_value = st.select_slider(..., key=f"rating_slider_{recipe['id']}")

# ✅ CORRECT: Sync widget state before rendering (for programmatic nav)
st.session_state["_nav"] = st.session_state["page"]  # Set before radio renders
def _on_nav_change() -> None:
    st.session_state["page"] = st.session_state["_nav"]  # Callback syncs back
st.sidebar.radio("Navigation", pages, key="_nav", on_change=_on_nav_change)

# ❌ AVOID: Hardcoded keys in loops (causes collisions)
for recipe in recipes:
    st.button("Edit", key="edit_btn")  # All buttons have same key!
```

**Where it's used:**
- Recipe card actions in `render_recipe_card()` and `page_browse()`
- Cook mode ingredient scaling: `key=f"cook_servings_{recipe_id}"`
- Meal plan entry delete buttons: `key=f"meal_del_{entry['id']}"`

**Why:** Streamlit uses widget keys to persist state. Duplicate keys cause unpredictable behavior.

---

### 2.3 Form Submission & Rerun Pattern
**Pattern:** Never modify state directly from widget callbacks. Use `st.form_submit_button()`, check submission flag, modify state, then `st.rerun()`.

```python
# ✅ CORRECT
with st.form("add_recipe_form", clear_on_submit=True):
    payload = form_recipe_fields(defaults)
    submitted = st.form_submit_button("Save Recipe", type="primary")

if submitted and payload:
    ok, message = validate_recipe_input(payload)
    if not ok:
        st.error(message)
        return
    create_recipe(payload, user_id=user_id)
    st.session_state.pop("add_recipe_defaults", None)
    st.success("Recipe added successfully.")
    # Implicit rerun because function ends and Streamlit reruns on form submission

# ✅ CORRECT (for button actions)
if st.button("Next Step", type="primary"):
    if step_index < len(steps) - 1:
        st.session_state["cook_step_index"] = step_index + 1
    st.rerun()

# ❌ AVOID: Modifying state from callback
st.button("Next Step", on_click=lambda: st.session_state.update({...}))  # Race condition
```

**Where it's used:**
- Recipe forms (`page_add()`, `page_edit()`)
- Meal planner form (`page_meal_planner()`)
- Cook mode navigation buttons (`page_cook_mode()`)

**Why:** Forms auto-clear on submit; explicit `st.rerun()` ensures state is reflected in next render.

---

### 2.4 Auth State Refresh
**Pattern:** On every app load (in `main()`), refresh auth_user from DB to detect blocks/deletions.

```python
# In main():
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

# Sync fresh data back to session
st.session_state["auth_user"] = {
    "id": int(fresh_user.get("id") or -1),
    "username": str(fresh_user.get("username") or ""),
    # ... all fields
}
```

**Where it's used:**
- `main()` function (top-level auth check)
- `page_my_profile()` after successful profile update

**Why:** Admin may block a user; user may be deleted; session_state cache can be stale.

---

## 3. Error Handling Approach

### 3.1 Errors Never Raised to User
**Pattern:** Catch all exceptions. Convert to user-friendly messages. Never let stack traces reach Streamlit UI.

```python
# ✅ CORRECT
def parse_recipe_from_web_url(url_text: str) -> tuple[dict, str]:
    try:
        with urlopen(request, timeout=12) as response:
            raw_bytes = response.read()
            html_content = raw_bytes.decode("utf-8", errors="ignore")
    except HTTPError as exc:
        raise RuntimeError(f"Could not fetch URL (HTTP {exc.code}).") from exc
    except URLError as exc:
        raise RuntimeError("Could not reach the URL. Check your internet connection and link.") from exc

# In app.py handler:
except ValueError as exc:
    st.error(str(exc))
except RuntimeError as exc:
    st.error(str(exc))
except Exception:
    st.error("Could not parse this web page. Try another link.")

# ❌ AVOID
raise Exception("Something broke")  # User sees ugly stack trace
```

**Where it's used:**
- `page_add()` handlers for OCR, web, chatbot
- `call_chatgpt_for_recipe()` for HTTP errors
- `build_chatbot_recipe_draft()` exception handling

**Why:** Streamlit shows unhandled exceptions as red boxes with full trace. Confuses non-technical users.

---

### 3.2 Validation Before DB Write
**Pattern:** Validate user input **before** DB call. Return early with st.error() if validation fails.

```python
# ✅ CORRECT
if submitted and payload:
    ok, message = validate_recipe_input(payload)
    if not ok:
        st.error(message)
        return  # Stop here, don't call DB
    create_recipe(payload, user_id=user_id)
    st.success("Recipe added successfully.")

# Validation function:
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
```

**Where it's used:**
- `validate_recipe_input()` for recipes
- Input cleaning in `create_user()`, `update_user_profile()` etc.

**Why:** Prevents invalid state in DB; catches bugs early; provides clear feedback to user.

---

### 3.3 Type Coercion with Defaults
**Pattern:** Always coerce user input to expected type. Provide sensible fallback if coercion fails.

```python
# ✅ CORRECT
def safe_int(val, fallback: int) -> int:
    try:
        return int(val) if val is not None else fallback
    except (ValueError, TypeError):
        return fallback

# Usage:
servings = safe_int(payload.get("servings"), 1)
prep_time = safe_int(payload.get("prep_time"), 15)

# For floats:
try:
    draft["servings"] = max(1, int(raw.get("servings") or 1))
except (TypeError, ValueError):
    draft["servings"] = 1

# For ratings:
sanitized = None if rating is None else max(0.0, min(5.0, float(rating)))
```

**Where it's used:**
- `form_recipe_fields()` for numeric inputs
- `normalize_generated_recipe()` for AI-generated data
- `set_rating()` for clamping to 0-5 range

**Why:** AI, web extraction, or form widgets can produce unexpected types. Defensive coding prevents crashes.

---

## 4. Testing & Validation Patterns

### 4.1 Input Sanitization (Strip & Case Normalization)
**Pattern:** All text inputs strip whitespace and normalize case (lower) for comparison.

```python
# ✅ CORRECT
def authenticate_user(identifier: str, password: str) -> Optional[dict]:
    login = (identifier or "").strip()
    if not login or not password:
        return None
    
    # Compare with LOWER() in SQL
    row = conn.execute(
        "SELECT ... FROM users WHERE lower(username) = lower(?) OR lower(email) = lower(?)",
        (login, login),
    ).fetchone()

# ✅ CORRECT
clean_email = (email or "").strip().lower()
if "@" not in clean_email or "." not in clean_email:
    return False, "Please provide a valid email address."
```

**Where it's used:**
- `authenticate_user()` for username/email login
- `create_user()`, `update_user_profile()` for email/username
- `reset_user_password()` for lookup

**Why:** Prevents case-sensitive email/username collisions; consistent normalization.

---

### 4.2 Regex Validation Patterns
**Pattern:** Use `re.fullmatch()` for strict validation; `re.match()` for prefix matching; flags for case-insensitive.

```python
# ✅ CORRECT (strict phone format)
if clean_phone and not re.fullmatch(r"[0-9+\-()\s]{7,20}", clean_phone):
    return False, "Phone number format is invalid."

# ✅ CORRECT (strict fraction format)
if re.fullmatch(r"\d+\s+\d+/\d+", cleaned):  # "1 1/2"
    whole, frac = cleaned.split()
    return float(int(whole) + Fraction(frac))

# ✅ CORRECT (URL prefix)
if not re.match(r"^https?://", normalized_url, flags=re.IGNORECASE):
    return text_if_normalized

# ✅ CORRECT (JSON-LD script extraction, case-insensitive)
scripts = re.findall(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>",
    html_content,
    flags=re.IGNORECASE,
)
```

**Where it's used:**
- Phone validation in `create_user()`, `update_user_profile()`
- Ingredient quantity parsing in `scale_ingredient_line()`, `parse_leading_quantity()`
- URL validation in `normalize_reference_url()`, `validate_recipe_input()`
- Web scraping in `extract_recipe_from_json_ld()`, `parse_recipe_from_web_url()`

**Why:** Prevents injection; ensures data format; early rejection of invalid input.

---

### 4.3 Schema Validation via `PRAGMA table_info`
**Pattern:** Check for column existence before adding; don't assume schema on upgrade.

```python
# In init_db() migration path:
recipe_columns = {
    row[1] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()
}
if "is_favorite" not in recipe_columns:
    conn.execute("ALTER TABLE recipes ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
if "rating" not in recipe_columns:
    conn.execute("ALTER TABLE recipes ADD COLUMN rating REAL")
# ... repeat for all new columns
```

**Where it's used:**
- `init_db()` migration section for recipes, users, meal_plan_entries tables

**Why:** Idempotent; safe to run on old DBs; upgrades happen automatically on app start.

---

## 5. Performance Considerations

### 5.1 Query Patterns
**Pattern:** Use indexed columns for common filters; avoid SELECT * except for small result sets.

```python
# ✅ GOOD (indexed on meal_date, category/difficulty/search columns are selective)
query = """
    SELECT * FROM recipes
    WHERE 1=1
    AND user_id = ?
    AND category = ?
    AND difficulty = ?
    ORDER BY updated_at DESC, title ASC
"""

# ✅ GOOD (indexed on meal_plan_entries.meal_date, recipe_id)
query = """
    SELECT m.id, m.meal_date, m.meal_type, m.recipe_id, r.title
    FROM meal_plan_entries m
    JOIN recipes r ON r.id = m.recipe_id
    WHERE m.meal_date BETWEEN ? AND ?
    ORDER BY m.meal_date ASC
"""

# Indexes defined:
# CREATE INDEX IF NOT EXISTS idx_meal_plan_date ON meal_plan_entries(meal_date)
# CREATE INDEX IF NOT EXISTS idx_meal_plan_recipe ON meal_plan_entries(recipe_id)
```

**Where it's used:**
- `list_recipes()` with filtering (category, difficulty, search)
- `list_meal_plan_entries()` with date range
- Recipe queries in `page_browse()`, `page_cook_mode()`

**Why:** Small dataset (< 10K recipes for hobby app), but filters are interactive; indexes speed up sorted queries.

---

### 5.2 Caching (Limited Use)
**Pattern:** Use `@st.cache_resource` only for expensive setup (OCR engine); avoid caching DB queries.

```python
# ✅ GOOD (expensive resource)
@st.cache_resource
def get_rapidocr_engine():
    if RapidOCR is None:
        return None
    return RapidOCR()  # Lazy init of ONNX model

# ❌ AVOID (DB queries should never be cached—they change)
@st.cache_data
def list_recipes(...):
    # This would return stale data if recipe is added in another session!
    return conn.execute(...).fetchall()
```

**Where it's used:**
- `get_rapidocr_engine()` only

**Why:** OCR model loading is slow; recipes change frequently; single-user session, so cache doesn't help much.

---

### 5.3 String Interpolation Patterns
**Pattern:** Use f-strings for display; never interpolate SQL (use parameterized queries).

```python
# ✅ CORRECT (f-string for display)
st.markdown(f"**{draft.get('title') or 'Draft Recipe'}**\n\nCategory: {draft.get('category')}")

# ✅ CORRECT (parameterized SQL)
query = "SELECT * FROM recipes WHERE category = ? AND user_id = ?"
conn.execute(query, (category, user_id))

# ❌ AVOID (SQL injection)
query = f"SELECT * FROM recipes WHERE category = '{category}'"
```

**Where it's used:**
- All SQL queries in `db.py`
- All Streamlit markdown/text in `app.py`

**Why:** Prevents SQL injection; cleaner f-strings for display; separation of concerns.

---

## 6. Integration Points (Optional Dependencies)

### 6.1 OCR Integration (RapidOCR / Tesseract)
**Pattern:** Try RapidOCR first (faster, no additional system deps); fall back to pytesseract (slower, needs tesseract binary).

```python
def extract_text_from_recipe_photo(uploaded_file) -> str:
    image = Image.open(io.BytesIO(uploaded_file.getvalue())).convert("L")
    image = ImageOps.autocontrast(image)  # Enhance contrast

    if RapidOCR is not None:
        ocr = get_rapidocr_engine()
        image_array = np.array(image.convert("RGB"))
        result, _ = ocr(image_array)
        if result:
            return "\n".join(str(item[1]).strip() for item in result if len(item) > 1)

    if pytesseract is not None:
        thresholded = image.point(lambda px: 0 if px < 145 else 255, mode="1")
        return pytesseract.image_to_string(thresholded).strip()

    raise RuntimeError("OCR engine is unavailable. Install project requirements...")
```

**Where it's used:**
- `page_add()` when user selects "Photo to Text (OCR)" and uploads image

**Why:** Graceful fallback; RapidOCR is bundled in requirements.txt; tesseract is optional local install.

---

### 6.2 Web Extraction (JSON-LD, then Keyword Fallback)
**Pattern:** Try JSON-LD first (structured data), then HTML text extraction (keyword search) if JSON-LD fails.

```python
def parse_recipe_from_web_url(url_text: str) -> tuple[dict, str]:
    # ... fetch URL with urllib ...
    
    parsed = extract_recipe_from_json_ld(html_content)  # Try JSON-LD
    if not parsed:
        text_content = strip_html_to_text(html_content)
        parsed = parse_recipe_from_text(text_content)  # Fall back to text
    
    if not parsed:
        raise RuntimeError("Could not extract recipe fields from this page.")
    
    parsed["reference_url"] = normalized_url  # Always set source
    return parsed, strip_html_to_text(html_content)

# Helper to extract JSON-LD:
def extract_recipe_from_json_ld(html_content: str) -> dict:
    scripts = re.findall(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>([\s\S]*?)</script>", ...)
    for script in scripts:
        payload = json.loads(html.unescape(script).strip())
        nodes = [payload] + (payload.get("@graph", []) if isinstance(payload, dict) else [])
        for node in nodes:
            if "recipe" in str(node.get("@type", "")).lower():
                # Extract title, ingredients, instructions, times, etc.
                return {...}
    return {}
```

**Where it's used:**
- `page_add()` when user selects "Web Link" and pastes recipe URL

**Why:** Structured data (JSON-LD) is reliable if present; keyword fallback works for less-structured sites.

---

### 6.3 GitHub Copilot / Chatbot Recipe Generation
**Pattern:** Try remote API; fall back to local template-based generator; always validate output.

```python
def build_chatbot_recipe_draft(user_prompt: str) -> tuple[dict, str]:
    prompt = (user_prompt or "").strip()
    if not prompt:
        return {}, "empty"

    if not _is_recipe_prompt(prompt):  # Quick keyword check
        return {}, "off_topic"

    try:
        remote_raw = call_chatgpt_for_recipe(prompt)
        if isinstance(remote_raw, dict) and remote_raw.get("error") == "off_topic":
            return {}, "off_topic"
        remote_draft = normalize_generated_recipe(remote_raw, prompt)
        if remote_draft.get("title") and remote_draft.get("ingredients") and remote_draft.get("instructions"):
            return remote_draft, "copilot"  # Remote succeeded
    except Exception:
        pass  # API failed, fall through

    local_draft = build_local_chatbot_recipe_draft(prompt)  # Local fallback
    if local_draft:
        return local_draft, "fallback"
    return {}, "empty"

# Remote API call:
def call_chatgpt_for_recipe(user_prompt: str) -> dict:
    token = get_github_token()  # From st.secrets or env
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not configured.")
    
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a recipe-only assistant. ..."},
            {"role": "user", "content": user_prompt},
        ],
    }
    req = Request("https://models.inference.ai.azure.com/chat/completions", ...)
    with urlopen(req, timeout=35) as response:
        result = json.loads(response.read().decode("utf-8", errors="ignore"))
    # Parse and return JSON response
```

**Where it's used:**
- `page_add()` when user selects "Chatbot Assistant" and types a request

**Why:** API provides better quality; local fallback ensures app works offline; normalization validates output before saving.

---

### 6.4 Token Management Pattern
**Pattern:** Check for token in `st.secrets` first; fall back to environment variable. Graceful if missing.

```python
def get_github_token() -> str:
    env_value = (os.getenv("GITHUB_TOKEN") or "").strip()
    secret_value = ""
    try:
        secret_value = str(st.secrets["GITHUB_TOKEN"]).strip()
    except Exception:
        secret_value = ""
    return secret_value or env_value

# Usage in UI:
copilot_configured = bool(get_github_token())
if copilot_configured:
    st.success("Live source: GitHub Copilot (token detected).")
else:
    st.warning("Live source: Local fallback (GITHUB_TOKEN not found).")
```

**Where it's used:**
- `call_chatgpt_for_recipe()` to authenticate HTTP request
- `page_add()` to display token status to user

**Why:** Secrets are preferred in Streamlit (secure); env var is fallback for Docker/CLI. App doesn't crash if missing.

---

## 7. Common Maintenance Tasks

### 7.1 Schema Migrations
**Process:**
1. Add new column check in `init_db()` inside the migration section
2. Use `ALTER TABLE` with `IF NOT` pattern
3. Provide safe default value for existing rows
4. Test on old DB to ensure idempotency

```python
# Example: Adding a new column
recipe_columns = {
    row[1] for row in conn.execute("PRAGMA table_info(recipes)").fetchall()
}
if "ingredient_format" not in recipe_columns:
    conn.execute("ALTER TABLE recipes ADD COLUMN ingredient_format TEXT NOT NULL DEFAULT 'quantity_item'")

# Example: Adding user_id to existing table + backfill
if "user_id" not in recipe_columns:
    conn.execute("ALTER TABLE recipes ADD COLUMN user_id INTEGER")

# Backfill to admin user:
admin_user = conn.execute("SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC LIMIT 1").fetchone()
if admin_user:
    admin_id = int(admin_user["id"])
    conn.execute("UPDATE recipes SET user_id = ? WHERE user_id IS NULL", (admin_id,))
```

**Where it's used:**
- `init_db()` function in `db.py`

**Why:** No separate migration files; schema version auto-syncs on app startup; safe for multi-user scenarios.

---

### 7.2 Data Backups (XLSX Export/Import)
**Pattern:** Export recipes as Excel (with images as base64); import via `openpyxl`.

```python
# Export:
def page_data_tools() -> None:
    export_rows = export_recipes_records(user_id=user_id, is_admin=is_admin)
    export_df = pd.DataFrame(export_rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="recipes")
    output.seek(0)
    st.download_button("Export Recipes as Excel", data=output.getvalue(), ...)

# Import:
imported_file = st.file_uploader("Upload Excel export (.xlsx)", type=["xlsx"])
if imported_file:
    imported_df = pd.read_excel(imported_file, sheet_name="recipes")
    payload = imported_df.to_dict(orient="records")
    created = import_recipes_records(payload, user_id=user_id)
    st.success(f"Imported {created} recipe(s)...")

# Export function (images as base64):
def export_recipes_records(user_id, is_admin):
    rows = conn.execute("SELECT * FROM recipes WHERE ...").fetchall()
    exported = []
    for row in rows:
        item = dict(row)
        image_blob = item.get("image")
        item["image_base64"] = b64encode(image_blob).decode("ascii") if image_blob else None
        item.pop("image", None)
        exported.append(item)
    return exported

# Import function (base64 back to blob):
def import_recipes_records(recipes, user_id):
    created = 0
    for recipe in recipes:
        image_base64 = recipe.get("image_base64")
        image_blob = b64decode(image_base64) if image_base64 else None
        conn.execute("INSERT INTO recipes (..., image, ...) VALUES (..., ?, ...)", (image_blob,))
        created += 1
    return created
```

**Where it's used:**
- `page_data_tools()` for backup/restore workflow

**Why:** Excel is readable by non-technical users; base64 encoding preserves binary image data; easy migration path.

---

### 7.3 Debugging Common Issues

**Issue: "Widget key not unique"**
- Likely cause: Loop rendering widgets with hardcoded keys (not ID-prefixed)
- Fix: Use `key=f"widget_name_{id}"` inside loops

**Issue: Form doesn't clear after submission**
- Likely cause: Missing `clear_on_submit=True` or state not cleared
- Fix: Add `st.session_state.pop("add_recipe_defaults", None)` after form submission

**Issue: User sees stale data after update**
- Likely cause: Missing `st.rerun()` or cache returning old data
- Fix: Always call `st.rerun()` after state-modifying action; avoid `@st.cache_data` for DB queries

**Issue: OCR returns empty string**
- Likely cause: Image too dark/blurry or OCR not installed
- Fix: Suggest user try a clearer photo; check that `RapidOCR` or `pytesseract` is installed

**Issue: Web extraction fails**
- Likely cause: URL not accessible, no JSON-LD, malformed HTML
- Fix: Try another recipe site; many recipe sites have JSON-LD; fallback to text parsing tries keywords like "ingredient", "instruction"

---

## 8. Key Idioms & Anti-Patterns

### 8.1 Recommended Patterns

| Pattern | Where | Why |
|---------|-------|-----|
| `(value or "").strip()` | All string inputs | Handle None, empty, whitespace |
| `tuple[bool, str]` returns | DB write functions | Centralize error messaging |
| `dict` payloads | Multi-field operations | Decouple UI from schema |
| `_recipe_scope_clause()` | All recipe queries | Enforce user isolation consistently |
| `try-except` around user actions | UI button handlers | Never crash on bad input |
| Optional dependency guard | OCR, Copilot integration | Graceful degradation |
| `st.rerun()` after state mutation | Interactive buttons | Ensure UI reflects state |
| Widget key with ID | Loops | Avoid collisions |
| `PRAGMA table_info` migration | Schema updates | Safe, idempotent upgrades |
| `sqlite3.Row` + `dict()` conversion | All queries | Efficient, serializable |

### 8.2 Anti-Patterns to Avoid

| Anti-Pattern | Why It's Bad | Use Instead |
|--------------|-------------|-------------|
| Direct `sqlite3` import in `app.py` | Couples UI to DB schema | Import from `db.py` only |
| Caching DB query results | Stale data in multi-user | Always query fresh |
| Hardcoded widget keys in loops | Key collisions, lost state | Use `key=f".._{id}"` |
| Unhandled exceptions in UI handlers | Stack traces confuse users | Wrap in try-except, use `st.error()` |
| `st.write()` for validation messages | Goes to output, not alerts | Use `st.error()`, `st.warning()`, `st.success()` |
| Modifying nested lists in session_state | Shallow copy issues | Reconstruct dict, then assign |
| `@st.cache_data` on DB queries | Returns stale data | Never cache DB; only cache resources |
| SQL string interpolation | SQL injection risk | Use parameterized queries |
| Assuming form input is never None | It can be! | Always `.get()` with default + strip |
| Not validating AI/web-extracted data | Garbage in, garbage out | Normalize and validate before save |

---

## 9. Quick Reference: Common Code Snippets

### Get Active User
```python
def get_active_user() -> Optional[dict]:
    user = st.session_state.get("auth_user")
    return user if isinstance(user, dict) else None

user_id = int((get_active_user() or {}).get("id") or -1)
is_admin = bool((get_active_user() or {}).get("is_admin"))
```

### Build Query with Optional Filters
```python
query = "SELECT * FROM recipes WHERE 1=1"
params: list[object] = []

# Scope
scope_clause, scope_params = _recipe_scope_clause(user_id=user_id, is_admin=is_admin)
query += scope_clause
params.extend(scope_params)

# Optional category filter
if category:
    query += " AND category = ?"
    params.append(category)

# Order
query += " ORDER BY updated_at DESC, title ASC"

with get_connection() as conn:
    rows = conn.execute(query, tuple(params)).fetchall()
return [dict(row) for row in rows]
```

### Validate & Safe Numeric Coerce
```python
def safe_int(val, fallback: int) -> int:
    try:
        return int(val) if val is not None else fallback
    except (ValueError, TypeError):
        return fallback

servings = safe_int(payload.get("servings"), 1)
```

### Graceful API Call with Fallback
```python
try:
    remote_draft = call_chatgpt_for_recipe(prompt)
    if remote_draft.get("title"):
        return remote_draft, "copilot"
except Exception:
    pass

# Fall back to local
local_draft = build_local_chatbot_recipe_draft(prompt)
return local_draft, "fallback"
```

### Handle Optional Dependency
```python
try:
    from optional_lib import ExpensiveClass
except ImportError:
    ExpensiveClass = None

# Later:
if ExpensiveClass is not None:
    obj = ExpensiveClass()
else:
    raise RuntimeError("optional_lib not installed.")
```

---

## Summary: Core Principles

1. **Defensive Input Handling** — Always strip, coerce, validate. Never trust user/API data.
2. **Graceful Degradation** — Optional features fail silently with fallbacks; app never crashes.
3. **Session-State-Driven UI** — State persists across reruns; forms and navigation managed via `st.session_state`.
4. **Scope Enforcement** — Non-admin users see only their own recipes; enforced via `_recipe_scope_clause()`.
5. **Centralized Error Messaging** — DB layer returns `tuple[bool, str]`; UI layer decides display.
6. **Immutable DB Schema** — Migrations in `init_db()`; no separate migration files.
7. **No Caching (of queries)** — DB calls always fresh; only cache expensive resources (OCR model).
8. **Parameterized SQL** — Never interpolate user input into queries.
9. **Simple is Better** — Raw SQL (not ORM), single-file UI (not multi-component framework), SQLite (not complex DB).

