import streamlit as st
import time
import uuid
import sqlite3
import hashlib
from datetime import datetime, timedelta, date
import pymongo
import pandas as pd # NEW: Required for Analytics and Data Export

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="DAILY STEPS - FOR STUDENT ROUTINE", page_icon="📚", layout="wide")


# ==========================================
# 1. DATABASE SETUP (Polyglot Persistence)
# ==========================================

# --- A. SQLite Setup (Relational Data: Users & Tasks) ---
def init_sqlite_db():
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password TEXT, email TEXT, notifications_enabled INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, username TEXT, task TEXT, time TEXT, priority TEXT, completed INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_sqlite_db()

# --- B. MongoDB Setup (Document Data: Focus Logs, Goals, Reflections) ---
try:
    MONGO_URI = st.secrets["MONGO_URI"] 
    mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    mongo_client.server_info() 
    mongo_db = mongo_client["daily_steps_db"]
    focus_col = mongo_db["focus_logs"]
    goals_col = mongo_db["weekly_goals"]
    reflections_col = mongo_db["reflections"]
    history_col = mongo_db["daily_history"] 
    gamification_col = mongo_db["gamification"]
    MONGO_AVAILABLE = True
except (pymongo.errors.ServerSelectionTimeoutError, KeyError):
    MONGO_AVAILABLE = False
    st.sidebar.warning("⚠️ Could not connect to MongoDB. Goals, Reflections, and Focus Time will not be saved permanently.")


# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================

# --- Authentication Helpers ---
def make_hashes(password): return hashlib.sha256(str.encode(password)).hexdigest()
def check_hashes(password, hashed_text): return make_hashes(password) == hashed_text

def add_user(username, password, email):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('INSERT INTO users(username, password, email, notifications_enabled) VALUES (?,?,?,?)', 
              (username, make_hashes(password), email, 0))
    conn.commit()
    conn.close()

def login_user(username, password):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('SELECT password FROM users WHERE username = ?', (username,))
    data = c.fetchone()
    conn.close()
    if data: return check_hashes(password, data[0])
    return False

# --- SQLite Task Operations ---
def add_task(task_id, username, task, time_str, priority, completed=0):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)', (task_id, username, task, time_str, priority, completed))
    conn.commit()
    conn.close()

def get_tasks(username):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('SELECT id, task, time, priority, completed FROM tasks WHERE username = ?', (username,))
    data = c.fetchall()
    conn.close()
    return [{"id": row[0], "task": row[1], "time": row[2], "priority": row[3], "completed": bool(row[4])} for row in data]

def update_task_status(task_id, completed):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('UPDATE tasks SET completed = ? WHERE id = ?', (int(completed), task_id))
    conn.commit()
    conn.close()

def edit_task_details(task_id, new_task, new_time_str, new_priority):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('UPDATE tasks SET task = ?, time = ?, priority = ? WHERE id = ?', 
              (new_task, new_time_str, new_priority, task_id))
    conn.commit()
    conn.close()

def delete_task(task_id):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()

# --- MongoDB Operations ---
def log_focus_time_mongo(username, minutes):
    if not MONGO_AVAILABLE: 
        st.session_state.focus_time_logged = st.session_state.get('focus_time_logged', 0) + minutes
        return
    today = str(date.today())
    focus_col.update_one({"username": username, "date": today}, {"$inc": {"minutes_logged": minutes}}, upsert=True)

def get_today_focus_mongo(username):
    if not MONGO_AVAILABLE: return st.session_state.get('focus_time_logged', 0)
    doc = focus_col.find_one({"username": username, "date": str(date.today())})
    return doc["minutes_logged"] if doc else 0

def save_weekly_goals_mongo(username, goals_data):
    if not MONGO_AVAILABLE: return
    goals_col.update_one({"username": username}, {"$set": goals_data}, upsert=True)

def save_reflection_mongo(username, reflection_data):
    if not MONGO_AVAILABLE: return
    reflection_data["date"] = str(date.today())
    reflections_col.update_one({"username": username, "date": str(date.today())}, {"$set": reflection_data}, upsert=True)

# --- Gamification Operations ---
def get_gamification(username):
    if not MONGO_AVAILABLE: return {"xp": 0, "level": 1, "streak": 0}
    profile = gamification_col.find_one({"username": username})
    if not profile:
        profile = {"username": username, "xp": 0, "level": 1, "streak": 0, "last_checkout": None}
        gamification_col.insert_one(profile)
    return profile

def add_xp(username, xp_amount):
    if not MONGO_AVAILABLE: return False
    profile = get_gamification(username)
    new_xp = profile["xp"] + xp_amount
    new_level = (new_xp // 100) + 1 
    
    gamification_col.update_one(
        {"username": username},
        {"$set": {"xp": new_xp, "level": new_level}}
    )
    return new_level > profile["level"] 

def update_streak_on_checkout(username):
    if not MONGO_AVAILABLE: return
    profile = get_gamification(username)
    today_date = date.today()
    
    last_checkout_str = profile.get("last_checkout")
    
    if last_checkout_str:
        last_checkout_date = datetime.strptime(last_checkout_str, "%Y-%m-%d").date()
        delta = (today_date - last_checkout_date).days
        
        if delta == 1:
            new_streak = profile["streak"] + 1 
        elif delta == 0:
            new_streak = profile["streak"] 
        else:
            new_streak = 1 
    else:
        new_streak = 1 
        
    gamification_col.update_one(
        {"username": username},
        {"$set": {"streak": new_streak, "last_checkout": str(today_date)}}
    )

def get_rank_title(level):
    if level < 5: return "Novice Planner 🥉"
    elif level < 10: return "Focused Scholar 🥈"
    elif level < 25: return "Discipline Master 🥇"
    elif level < 50: return "Productivity Ninja 🥷"
    else: return "Grandmaster 👑"

# --- Level Up Celebration Popup ---
@st.dialog("🌟 LEVEL UP!")
def level_up_popup(level, title):
    st.balloons()
    st.markdown(f"<h1 style='text-align: center; color: #FFD700;'>Congratulations!</h1>", unsafe_allow_html=True)
    st.markdown(f"<h3 style='text-align: center;'>You've reached Level {level}</h3>", unsafe_allow_html=True)
    st.markdown(f"<p style='text-align: center; font-size: 20px;'>New Rank: <b>{title}</b></p>", unsafe_allow_html=True)
    st.write("---")
    st.write("Your dedication to your routine is paying off. Keep building those habits!")
    if st.button("Awesome! Let's keep going", use_container_width=True):
        st.rerun()

# --- Account Management Helpers ---
def update_user_password(username, new_password):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    c.execute('UPDATE users SET password = ? WHERE username = ?', (make_hashes(new_password), username))
    conn.commit()
    conn.close()

def update_user_username(old_username, new_username):
    conn = sqlite3.connect('daily_steps.db')
    c = conn.cursor()
    
    # 1. Check if the new username is already taken
    c.execute('SELECT username FROM users WHERE username = ?', (new_username,))
    if c.fetchone():
        conn.close()
        return False # Username exists
    
    # 2. Update SQLite (Auth & Tasks)
    c.execute('UPDATE users SET username = ? WHERE username = ?', (new_username, old_username))
    c.execute('UPDATE tasks SET username = ? WHERE username = ?', (new_username, old_username))
    conn.commit()
    conn.close()

    # 3. Update MongoDB (Migrate all personal data)
    if MONGO_AVAILABLE:
        collections = [focus_col, goals_col, reflections_col, history_col, gamification_col]
        for col in collections:
            col.update_many({"username": old_username}, {"$set": {"username": new_username}})
    
    return True

# ==========================================
# 3. SESSION STATE INITIALIZATION
# ==========================================
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'username' not in st.session_state: st.session_state.username = ""


# ==========================================
# 4. AUTHENTICATION UI
# ==========================================
if not st.session_state.logged_in:
    st.markdown("<h1 style='text-align: center;'>🔒 Welcome to Daily Steps</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #888;'>Build your routine, master self-discipline, and track your progress.</p>", unsafe_allow_html=True)
    st.write("") 
    
    col_spacer1, col_auth, col_spacer2 = st.columns([1, 1.5, 1])
    with col_auth:
        with st.container(border=True):
            tab_login, tab_signup = st.tabs(["🔑 Login", "📝 Sign Up"])
            
            with tab_login:
                st.subheader("Welcome Back")
                login_username = st.text_input("Username", key="login_user")
                login_password = st.text_input("Password", type='password', key="login_pass")
                
                if st.button("Login", use_container_width=True, type="primary"):
                    if login_user(login_username, login_password):
                        st.session_state.logged_in = True
                        st.session_state.username = login_username
                        st.rerun()
                    else:
                        st.error("Incorrect Username or Password")
                        
            with tab_signup:
                st.subheader("Create an Account")
                new_user = st.text_input("Choose a Username", key="reg_user")
                new_email = st.text_input("Email Address", key="reg_email")
                new_password = st.text_input("Choose a Password", type='password', key="reg_pass")
                
                if st.button("Sign Up", use_container_width=True):
                    if not new_user or not new_password:
                        st.warning("Please fill out all fields.")
                    else:
                        conn = sqlite3.connect('daily_steps.db')
                        c = conn.cursor()
                        c.execute('SELECT * FROM users WHERE username = ?', (new_user,))
                        if c.fetchone():
                            st.error("Username already exists.")
                        else:
                            add_user(new_user, new_password, new_email)
                            st.success("Account created successfully! Switch to the Login tab.")
                            st.balloons()
                        conn.close()


# ==========================================
# 5. MAIN APPLICATION (Logged In)
# ==========================================
else:
    # --- SIDEBAR NAVIGATION ---
    st.sidebar.title(f"👤 {st.session_state.username}")

    user_stats = get_gamification(st.session_state.username)
    rank_title = get_rank_title(user_stats['level'])
    
    st.sidebar.markdown(f"### {rank_title}")
    st.sidebar.markdown(f"**Level {user_stats['level']}** | 🔥 **{user_stats['streak']} Day Streak**")
    
    progress_to_next = (user_stats['xp'] % 100)
    st.sidebar.progress(progress_to_next / 100.0, text=f"XP: {user_stats['xp']} / {(user_stats['level']) * 100}")
    st.sidebar.divider()
    
    # NEW: Added Analytics Dashboard to the menu
    page = st.sidebar.radio("Go to:", [
        "Home", "Daily Routine Checklist", "Tips for Self-Discipline", 
        "Weekly Goals", "Daily Reflection", "Analytics Dashboard", "Notifications & Settings"
    ])

    # --- PAGE 1: HOME ---
    if page == "Home":
        st.title("📚 Daily Steps for Students")
        st.subheader("Build a strong routine and master self-discipline.")
        st.write("""
        Welcome to your personal growth tracker! As a student, building a solid daily routine 
        is the foundation of academic success and reduced stress. 
        """)
        st.image("https://images.unsplash.com/photo-1434030216411-0b793f4b4173?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80", caption="Focus and build your future.")

    # --- PAGE 2: DAILY ROUTINE ---
    elif page == "Daily Routine Checklist":
        
        if 'timer_active' not in st.session_state: st.session_state.timer_active = False
        if 'end_time' not in st.session_state: st.session_state.end_time = None
        if 'timer_minutes' not in st.session_state: st.session_state.timer_minutes = 0

        today_focus_time = get_today_focus_mongo(st.session_state.username)

        def get_time_window(start_time, duration_mins):
            start_dt = datetime.combine(date.today(), start_time)
            end_dt = start_dt + timedelta(minutes=duration_mins)
            return f"{start_dt.strftime('%I:%M %p').lstrip('0').replace(':00', '')} - {end_dt.strftime('%I:%M %p').lstrip('0').replace(':00', '')}"

        def start_timer_cb(mins):
            st.session_state.timer_active = True
            st.session_state.timer_minutes = mins
            st.session_state.end_time = datetime.now() + timedelta(minutes=mins)

        def stop_timer_cb():
            if st.session_state.timer_active and st.session_state.end_time:
                remaining_sec = (st.session_state.end_time - datetime.now()).total_seconds()
                elapsed_sec = (st.session_state.timer_minutes * 60) - remaining_sec
                if elapsed_sec > 60: 
                    log_focus_time_mongo(st.session_state.username, int(elapsed_sec // 60))
            st.session_state.timer_active = False
            st.session_state.end_time = None

        @st.dialog("➕ Schedule Custom Task")
        def custom_task_modal():
            new_task_name = st.text_input("Task Description:")
            col1, col2, col3 = st.columns([1.5, 1, 1])
            with col1: new_time = st.time_input("Start Time:", value=datetime.now().time().replace(second=0, microsecond=0))
            with col2: new_duration = st.number_input("Mins:", min_value=5, step=5, value=30)
            with col3: new_priority = st.selectbox("Priority:", ["🔴 High", "🟡 Med", "🟢 Low"])
                
            if st.button("Add to Schedule", use_container_width=True):
                if new_task_name.strip():
                    t_window = get_time_window(new_time, new_duration)
                    add_task(str(uuid.uuid4()), st.session_state.username, new_task_name, t_window, new_priority)
                    st.toast(f"Added: {new_task_name}", icon="✅")
                    st.rerun()
                else:
                    st.error("Please enter a description.")

        @st.dialog("✏️ Edit Task")
        def edit_task_modal(task_id, current_name, current_priority):
            new_task_name = st.text_input("New Description:", value=current_name)
            col1, col2 = st.columns(2)
            with col1: new_time = st.time_input("New Start Time:", value=datetime.now().time().replace(second=0, microsecond=0))
            with col2: new_duration = st.number_input("Duration (Mins):", min_value=5, step=5, value=30)
            new_priority = st.selectbox("Priority:", ["🔴 High", "🟡 Med", "🟢 Low"], index=["🔴 High", "🟡 Med", "🟢 Low"].index(current_priority))
            
            if st.button("Save Changes", use_container_width=True):
                if new_task_name.strip():
                    t_window = get_time_window(new_time, new_duration)
                    edit_task_details(task_id, new_task_name, t_window, new_priority)
                    st.toast("Task updated!", icon="✏️")
                    st.rerun()

        PRESETS = {
            "📚 Study": [
                {"task": "Self Study (1 hr)", "priority": "🔴 High", "duration": 60},
                {"task": "Revise Session", "priority": "🟡 Med", "duration": 30},
                {"task": "Daily Lectures", "priority": "🟢 Low", "duration": 30},
                {"task": "Complete Practice Set", "priority": "🔴 High", "duration": 30}
            ],
            "💪 Fitness": [
                {"task": "30 Min Cardio", "priority": "🟡 Med", "duration": 30},
                {"task": "Strength Training", "priority": "🔴 High", "duration": 30},
                {"task": "15 Min Yoga/Stretch", "priority": "🟢 Low", "duration": 15},
                {"task": "Hit 10k Steps", "priority": "🟡 Med", "duration": 30}
            ],
            "🧠 Life": [
                {"task": "Meal Prep", "priority": "🟡 Med", "duration": 30},
                {"task": "Tidy Workspace", "priority": "🟢 Low", "duration": 30},
                {"task": "Read 10 Pages", "priority": "🟢 Low", "duration": 30},
                {"task": "Checking Inboxes", "priority": "🟡 Med", "duration": 30}
            ],
            "💻 Project": [
                {"task": "Code for 1 Hour", "priority": "🔴 High", "duration": 60},
                {"task": "Youtube Lectures", "priority": "🟡 Med", "duration": 30},
                {"task": "Brainstorming/ Leetcode", "priority": "🔴 High", "duration": 30},
                {"task": "Update GitHub", "priority": "🟢 Low", "duration": 30}
            ]
        }

        st.markdown("""
            <style>
            .big-font { font-size:30px !important; font-weight: bold; color: #4CAF50;}
            .stButton button { width: 100%; text-align: left; }
            </style>
        """, unsafe_allow_html=True)

        st.markdown('<p class="big-font">⚡ Daily Routine</p>', unsafe_allow_html=True)

        current_tasks = get_tasks(st.session_state.username)
        total_tasks = len(current_tasks)
        completed_tasks = sum(1 for t in current_tasks if t["completed"])
        completion_rate = int((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 0

        col1, col2, col3 = st.columns(3)
        with col1: st.metric("Tasks Completed", f"{completed_tasks} / {total_tasks}", f"{completion_rate}%")
        with col2: st.metric("Pending Tasks", total_tasks - completed_tasks)
        with col3: st.metric("Focus Time Logged", f"{today_focus_time} mins", "Today")

        st.progress(completion_rate)
        st.divider()

        left_col, right_col = st.columns([1, 2.5])

        with left_col:
            with st.container(border=True):
                st.subheader("⏱️ Focus Timer")
                focus_minutes = st.number_input("Minutes", min_value=1, max_value=120, value=25)
                
                col_start, col_stop = st.columns(2)
                with col_start: st.button("🚀 Start", use_container_width=True, on_click=start_timer_cb, args=(focus_minutes,))
                with col_stop: st.button("⏹️ Stop", use_container_width=True, on_click=stop_timer_cb)

                timer_placeholder = st.empty()
                
                if st.session_state.timer_active and st.session_state.end_time:
                    remaining_sec = int((st.session_state.end_time - datetime.now()).total_seconds())
                    
                    if remaining_sec > 0:
                        for rem in range(remaining_sec, -1, -1):
                            mins, secs = divmod(rem, 60)
                            timer_placeholder.markdown(f"<h1 style='text-align: center; color: #ff4b4b;'>{mins:02d}:{secs:02d}</h1>", unsafe_allow_html=True)
                            time.sleep(1)
                    
                    st.session_state.timer_active = False
                    log_focus_time_mongo(st.session_state.username, st.session_state.timer_minutes)
                    st.session_state.end_time = None
                    st.toast("⏰ Time's up! Take a break.", icon="🔥")
                    st.balloons()
                    st.rerun()
                else:
                    timer_placeholder.markdown(f"<h1 style='text-align: center; color: #555;'>{focus_minutes:02d}:00</h1>", unsafe_allow_html=True)

        with right_col:
            c_header, c_sort = st.columns([2, 1])
            with c_header:
                st.subheader("📋 Build Your Routine")
            with c_sort:
                sort_pref = st.selectbox("Sort By:", ["Time", "Priority"], label_visibility="collapsed")
            
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1:
                if st.button("➕ Custom Task", use_container_width=True):
                    custom_task_modal()
            
            with st.expander("⚡ Add Regular Presets", expanded=False):
                tabs = st.tabs(list(PRESETS.keys()))
                for i, (category, items) in enumerate(PRESETS.items()):
                    with tabs[i]:
                        cols = st.columns(2) 
                        for j, item in enumerate(items):
                            with cols[j % 2]:
                                if st.button(f"➕ {item['task']}", key=f"preset_{category}_{j}"):
                                    clean_now = datetime.now().replace(second=0, microsecond=0).time()
                                    t_window = get_time_window(clean_now, item["duration"])
                                    add_task(str(uuid.uuid4()), st.session_state.username, item["task"], t_window, item["priority"])
                                    st.toast(f"Added: {item['task']}", icon="✅")
                                    st.rerun()

            st.divider()
            st.subheader("🗓️ Today's Action Plan")
            
            if not current_tasks:
                st.info("Your schedule is empty! Use the menus above to plan your day.")
            else:
                tab_pending, tab_completed = st.tabs(["⏳ Pending Tasks", "✅ Completed Tasks"])
                
                with tab_pending:
                    pending_tasks = [t for t in current_tasks if not t['completed']]
                    
                    if "Priority" in sort_pref:
                        w = {"🔴 High": 1, "🟡 Med": 2, "🟢 Low": 3}
                        pending_tasks = sorted(pending_tasks, key=lambda x: w.get(x['priority'], 4))

                    if not pending_tasks:
                        st.success("You are all caught up for today! 🎉")
                    else:
                        for task_dict in pending_tasks:
                            with st.container(border=True):
                                col_chk, col_time, col_task, col_pri, col_edit, col_del = st.columns([0.5, 1.5, 2.5, 1, 0.5, 0.5])
                                with col_chk:
                                    is_checked = st.checkbox("", value=task_dict["completed"], key=f"chk_p_{task_dict['id']}")
                                    if is_checked:
                                        update_task_status(task_dict["id"], True)
                                        xp_reward = {"🔴 High": 20, "🟡 Med": 10, "🟢 Low": 5}.get(task_dict['priority'], 10)
                                        leveled_up = add_xp(st.session_state.username, xp_reward) 
                                        
                                        if leveled_up:
                                            new_stats = get_gamification(st.session_state.username)
                                            new_title = get_rank_title(new_stats['level'])
                                            level_up_popup(new_stats['level'], new_title)
                                        else:
                                            st.toast(f"Task Completed! (+{xp_reward} XP)", icon="🎮")
                                            time.sleep(0.5) 
                                            st.rerun()

                                with col_time: st.write(f"🕰️ **{task_dict['time']}**")
                                with col_task: st.write(task_dict["task"])
                                with col_pri: st.write(task_dict['priority'])
                                with col_edit:
                                    if st.button("✏️", key=f"edit_p_{task_dict['id']}", help="Edit Task"):
                                        edit_task_modal(task_dict["id"], task_dict["task"], task_dict["priority"])
                                with col_del:
                                    if st.button("✖", key=f"del_p_{task_dict['id']}", help="Delete"):
                                        delete_task(task_dict["id"])
                                        st.rerun()

                with tab_completed:
                    done_tasks = [t for t in current_tasks if t['completed']]
                    if not done_tasks:
                        st.info("No tasks completed yet. Let's get to work! 💪")
                    else:
                        for task_dict in done_tasks:
                            with st.container(border=True):
                                col_chk, col_time, col_task, col_pri, col_del = st.columns([0.5, 1.5, 3, 1, 0.5])
                                with col_chk:
                                    is_checked = st.checkbox("", value=task_dict["completed"], key=f"chk_c_{task_dict['id']}")
                                    if not is_checked:
                                        update_task_status(task_dict["id"], False)
                                        st.rerun()
                                with col_time: st.write(f"🕰️ **{task_dict['time']}**")
                                with col_task: st.markdown(f"<span style='text-decoration: line-through; color: #888;'>{task_dict['task']}</span>", unsafe_allow_html=True)
                                with col_pri: st.write(task_dict['priority'])
                                with col_del:
                                    if st.button("✖", key=f"del_c_{task_dict['id']}", help="Delete"):
                                        delete_task(task_dict["id"])
                                        st.rerun()
                    
                st.divider()
                st.markdown("### 🌙 End of Day Checkout")
                st.write("Done for the day? Log your final progress to history and clear your board for tomorrow.")
                
                if st.button("✅ Complete My Day & Checkout", use_container_width=True, type="primary"):
                    if total_tasks > 0:
                        if MONGO_AVAILABLE:
                            history_data = {
                                "completion_rate": completion_rate,
                                "tasks_completed": completed_tasks,
                                "total_tasks": total_tasks,
                                "focus_minutes": today_focus_time,
                                "logged_at": datetime.now()
                            }
                            history_col.update_one(
                                {"username": st.session_state.username, "date": str(date.today())},
                                {"$set": history_data},
                                upsert=True
                            )
                        
                        conn = sqlite3.connect('daily_steps.db')
                        c = conn.cursor()
                        c.execute('DELETE FROM tasks WHERE username = ?', (st.session_state.username,))
                        conn.commit()
                        conn.close()
                        
                        update_streak_on_checkout(st.session_state.username)
                        
                        # --- MODAL SUMMARY WITH STREAK CHECK ---
                        @st.dialog("🌙 Day Completed!")
                        def checkout_summary(rate, focus):
                            st.balloons()
                            final_stats = get_gamification(st.session_state.username)
                            streak = final_stats['streak']
                            
                            st.markdown(f"<h2 style='text-align: center; color: #4CAF50;'>Great work, {st.session_state.username}!</h2>", unsafe_allow_html=True)
                            
                            # Streak Milestone Logic
                            if streak in [3, 7, 14, 30, 100]:
                                st.success(f"🔥 INCREDIBLE! You hit a {streak}-Day Streak!")
                            else:
                                st.write(f"Current Streak: 🔥 **{streak} Days**")

                            st.write(f"📊 **Completion Rate:** {rate}%")
                            st.write(f"⏱️ **Focus Time:** {focus} minutes")
                            st.divider()
                            st.info("Your progress is logged. See you tomorrow!")
                            if st.button("Finish", use_container_width=True):
                                st.rerun()
                        
                        checkout_summary(completion_rate, today_focus_time)
                        
                    else:
                        st.warning("You don't have any tasks on your board to check out!")


    # --- PAGE 3: DISCIPLINE TIPS ---
    elif page == "Tips for Self-Discipline":
        st.title("🧠 Improving Self-Discipline")
        st.write("Self-discipline is a muscle. The more you use it, the stronger it gets.")
        with st.expander("1. The 5-Minute Rule"):
            st.write("If a task takes less than 5 minutes to do, do it immediately.")
        with st.expander("2. Put Your Phone in Another Room"):
            st.write("Out of sight, out of mind. Eliminate the temptation to scroll.")
        with st.expander("3. Use the Pomodoro Technique"):
            st.write("Study for 25 minutes, then take a 5-minute break.")

    # --- PAGE 4: WEEKLY GOALS ---
    elif page == "Weekly Goals":
        st.title("🎯 Weekly Goals")
        st.markdown("Set your macro targets for the week. Cross them off as you go to fill your progress bars!")
        
        existing_goals = {}
        if MONGO_AVAILABLE:
            existing_goals = goals_col.find_one({"username": st.session_state.username}) or {}
        
        # Helper function to upgrade old string data to the new dictionary format
        def format_goals(goal_list):
            if not goal_list:
                return [{"text": "", "done": False}]
            
            formatted = []
            for g in goal_list:
                if isinstance(g, str):   
                    formatted.append({"text": g, "done": False})
                elif isinstance(g, dict): 
                    formatted.append(g)
                else:                     
                    formatted.append({"text": str(g) if g else "", "done": False})
                    
            return formatted
        
        # AGGRESSIVE OVERRIDE: Clear stuck session state memory if it holds old strings
        if 'acad_goals' in st.session_state and len(st.session_state.acad_goals) > 0 and isinstance(st.session_state.acad_goals[0], str):
            del st.session_state['acad_goals']
        if 'pers_goals' in st.session_state and len(st.session_state.pers_goals) > 0 and isinstance(st.session_state.pers_goals[0], str):
            del st.session_state['pers_goals']

        # Initialize session state safely
        if 'acad_goals' not in st.session_state:
            st.session_state.acad_goals = format_goals(existing_goals.get("academic", []))
        if 'pers_goals' not in st.session_state:
            st.session_state.pers_goals = format_goals(existing_goals.get("personal", []))

        col1, col2 = st.columns(2)
        
        # --- ACADEMIC GOALS ---
        with col1:
            with st.container(border=True):
                st.subheader("📚 Academic Goals")
                
                # Progress Bar Math
                total_acad = len(st.session_state.acad_goals)
                done_acad = sum(1 for g in st.session_state.acad_goals if g["done"])
                acad_prog = done_acad / total_acad if total_acad > 0 else 0.0
                st.progress(acad_prog, text=f"Progress: {done_acad} / {total_acad} Completed")
                st.write("") # Spacer
                
                # Render Goals
                for i in range(len(st.session_state.acad_goals)):
                    c1, c2, c3 = st.columns([0.15, 0.7, 0.15])
                    
                    # Checkbox
                    st.session_state.acad_goals[i]["done"] = c1.checkbox("", value=st.session_state.acad_goals[i].get("done", False), key=f"ac_chk_{i}")
                    
                    # Text Input (Disables if checked!)
                    is_done = st.session_state.acad_goals[i]["done"]
                    st.session_state.acad_goals[i]["text"] = c2.text_input(
                        f"Goal {i+1}", 
                        value=st.session_state.acad_goals[i].get("text", ""), 
                        key=f"ac_txt_{i}", 
                        label_visibility="collapsed",
                        disabled=is_done
                    )
                    
                    # Instant Delete Button
                    if c3.button("✖", key=f"ac_del_{i}", help="Delete Goal"):
                        st.session_state.acad_goals.pop(i)
                        st.rerun()
                
                if st.button("➕ Add Academic Goal", use_container_width=True):
                    st.session_state.acad_goals.append({"text": "", "done": False})
                    st.rerun()

        # --- PERSONAL GOALS ---
        with col2:
            with st.container(border=True):
                st.subheader("🌱 Personal Goals")
                
                # Progress Bar Math
                total_pers = len(st.session_state.pers_goals)
                done_pers = sum(1 for g in st.session_state.pers_goals if g["done"])
                pers_prog = done_pers / total_pers if total_pers > 0 else 0.0
                st.progress(pers_prog, text=f"Progress: {done_pers} / {total_pers} Completed")
                st.write("") # Spacer
                
                # Render Goals
                for i in range(len(st.session_state.pers_goals)):
                    c1, c2, c3 = st.columns([0.15, 0.7, 0.15])
                    
                    # Checkbox
                    st.session_state.pers_goals[i]["done"] = c1.checkbox("", value=st.session_state.pers_goals[i].get("done", False), key=f"pe_chk_{i}")
                    
                    # Text Input (Disables if checked!)
                    is_done = st.session_state.pers_goals[i]["done"]
                    st.session_state.pers_goals[i]["text"] = c2.text_input(
                        f"Goal {i+1}", 
                        value=st.session_state.pers_goals[i].get("text", ""), 
                        key=f"pe_txt_{i}", 
                        label_visibility="collapsed",
                        disabled=is_done
                    )
                    
                    # Instant Delete Button
                    if c3.button("✖", key=f"pe_del_{i}", help="Delete Goal"):
                        st.session_state.pers_goals.pop(i)
                        st.rerun()
                
                if st.button("➕ Add Personal Goal", use_container_width=True):
                    st.session_state.pers_goals.append({"text": "", "done": False})
                    st.rerun()
            
        st.write("")
        if st.button("💾 Save Weekly Goals", type="primary", use_container_width=True):
            if MONGO_AVAILABLE:
                # Clean up: only save goals that actually have text typed into them
                clean_acad = [g for g in st.session_state.acad_goals if g["text"].strip()]
                clean_pers = [g for g in st.session_state.pers_goals if g["text"].strip()]
                
                save_weekly_goals_mongo(st.session_state.username, {
                    "academic": clean_acad, 
                    "personal": clean_pers, 
                    "updated_at": datetime.now()
                })
                
                # Force state update to clean arrays
                st.session_state.acad_goals = clean_acad if clean_acad else [{"text": "", "done": False}]
                st.session_state.pers_goals = clean_pers if clean_pers else [{"text": "", "done": False}]
                
                st.success("Weekly Goals locked in! Go crush them.")
                st.balloons()
                time.sleep(1)
                st.rerun()
            else:
                st.error("Cannot save: MongoDB is not connected.")

    # --- PAGE 5: DAILY REFLECTION ---
    elif page == "Daily Reflection":
        st.title("📝 Daily Reflection & Journal")
        
        tab_write, tab_history = st.tabs(["✍️ Write Today's Entry", "📖 Past Journals"])
        
        with tab_write:
            existing_ref = reflections_col.find_one({"username": st.session_state.username, "date": str(date.today())}) or {} if MONGO_AVAILABLE else {}

            st.write("Take a few minutes to evaluate your day and clear your mind.")
            
            # Structured Reflection
            score = st.slider("Productivity Score (1-10)", 1, 10, existing_ref.get("score", 5))
            
            c1, c2 = st.columns(2)
            with c1:
                went_well = st.text_area("What went well today?", existing_ref.get("went_well", ""), height=100)
            with c2:
                improve = st.text_area("What could be improved tomorrow?", existing_ref.get("improve", ""), height=100)
            
            st.divider()
            
            # NEW: Free-form Diary Section
            st.subheader("📓 Dear Diary...")
            diary_entry = st.text_area(
                "Write whatever is on your mind.", 
                existing_ref.get("diary_entry", ""), 
                height=200, 
                placeholder="Today I felt really good about..."
            )
            
            if st.button("Submit Reflection & Journal", type="primary"):
                if MONGO_AVAILABLE:
                    save_reflection_mongo(st.session_state.username, {
                        "score": score, 
                        "went_well": went_well, 
                        "improve": improve, 
                        "diary_entry": diary_entry, # <-- Saving the new diary entry
                        "submitted_at": datetime.now()
                    })
                    st.success("Entry saved! Self-awareness is the first step to continuous improvement.")
                    st.balloons()
                else:
                    st.error("Cannot save: MongoDB is not connected.")

        with tab_history:
            st.subheader("Your Past Journals")
            if MONGO_AVAILABLE:
                # Fetch all reflections, sort by date descending (newest first)
                past_refs = list(reflections_col.find({"username": st.session_state.username}).sort("date", -1).limit(10))
                
                if not past_refs:
                    st.info("No past journals found. Your journey starts today!")
                else:
                    for ref in past_refs:
                        with st.expander(f"📅 {ref['date']} - Productivity: {ref.get('score', 'N/A')}/10"):
                            # Display Structured Reflection
                            st.write("**What went well:**")
                            st.write(f"> {ref.get('went_well', 'Nothing entered.')}")
                            st.write("**What to improve:**")
                            st.write(f"> {ref.get('improve', 'Nothing entered.')}")
                            
                            # Display Free-form Diary (if it exists)
                            diary_text = ref.get('diary_entry', '').strip()
                            if diary_text:
                                st.divider()
                                st.write("**📓 Dear Diary:**")
                                st.write(f"> *{diary_text}*")
            else:
                st.warning("MongoDB not connected. History unavailable.")

    # --- PAGE 6: ANALYTICS DASHBOARD ---
    elif page == "Analytics Dashboard":
        st.title("📊 Insights & Analytics")
        st.markdown("Track your progress, focus time, and productivity trends over time.")
        
        if MONGO_AVAILABLE:
            # Fetch user history, sorted from oldest to newest
            history_cursor = history_col.find({"username": st.session_state.username}).sort("logged_at", 1)
            history_data = list(history_cursor)
            
            if not history_data:
                st.info("No data yet! Complete a day using the 'Checkout' button to see your insights.")
            else:
                # Convert MongoDB data to a Pandas DataFrame
                df = pd.DataFrame(history_data)
                
                # FIX: Handle mixed data types (strings vs datetime objects)
                df['logged_at'] = pd.to_datetime(df['logged_at'], utc=True, errors='coerce')
                
                # Drop any rows that failed to parse (optional safety)
                df = df.dropna(subset=['logged_at'])
                
                # Create a readable 'date' column
                df['display_date'] = df['logged_at'].dt.strftime('%b %d')
                df['day_of_week'] = df['logged_at'].dt.day_name()
                
                # --- TIME FILTER ---
                time_filter = st.radio("⏳ Time Range:", ["Last 7 Days", "Last 30 Days", "All Time"], horizontal=True)
                if time_filter == "Last 7 Days":
                    df = df.tail(7)
                elif time_filter == "Last 30 Days":
                    df = df.tail(30)

                if df.empty:
                    st.warning("No data found for this specific time range.")
                else:
                    # --- TOP METRICS ---
                    st.markdown("### 🏆 Performance Overview")
                    col1, col2, col3, col4 = st.columns(4)
                    
                    with col1:
                        with st.container(border=True):
                            st.metric("Total Focus Mins", f"{df['focus_minutes'].sum()}m")
                    with col2:
                        with st.container(border=True):
                            st.metric("Daily Avg Focus", f"{int(df['focus_minutes'].mean())}m")
                    with col3:
                        with st.container(border=True):
                            st.metric("Tasks Completed", df['tasks_completed'].sum())
                    with col4:
                        with st.container(border=True):
                            avg_completion = int((df['tasks_completed'].sum() / df['total_tasks'].sum()) * 100) if df['total_tasks'].sum() > 0 else 0
                            st.metric("Avg Completion Rate", f"{avg_completion}%")
                    
                    st.divider()
                    
                    # --- CHARTS ---
                    c_chart1, c_chart2 = st.columns(2)
                    
                    with c_chart1:
                        st.subheader("⏱️ Focus Time Trend")
                        focus_chart_data = df.set_index('display_date')['focus_minutes']
                        # Using an area chart with a sleek color looks much more premium than a basic bar chart
                        st.area_chart(focus_chart_data, color="#8B5CF6") 
                        
                    with c_chart2:
                        st.subheader("📋 Task Completion vs Assigned")
                        task_chart_data = df[['display_date', 'tasks_completed', 'total_tasks']].set_index('display_date')
                        # Rename columns for a cleaner chart legend
                        task_chart_data = task_chart_data.rename(columns={'tasks_completed': 'Completed', 'total_tasks': 'Assigned'})
                        st.line_chart(task_chart_data, color=["#10B981", "#EF4444"]) # Green for Done, Red for Assigned

                    st.divider()
                    
                    # --- DEEP INSIGHTS ---
                    st.subheader("🧠 Deep Insights")
                    
                    # Calculate best day of the week
                    best_day = df.groupby('day_of_week')['focus_minutes'].mean().idxmax()
                    # Calculate record focus day
                    highest_focus = df['focus_minutes'].max()
                    best_date = df.loc[df['focus_minutes'].idxmax()]['display_date']
                    
                    i1, i2 = st.columns(2)
                    with i1:
                        st.info(f"📅 **Best Focus Day:** You average the most focus time on **{best_day}s**.")
                    with i2:
                        st.success(f"🔥 **Record Focus:** Your highest focus time was **{highest_focus}m** on {best_date}.")
        else:
            st.error("Cannot load analytics: MongoDB is not connected.")

    # --- PAGE 7: NOTIFICATIONS & SETTINGS ---
    elif page == "Notifications & Settings":
        st.title("⚙️ Settings & Account Security")
        
        # --- SECTION 1: ACCOUNT SECURITY ---
        with st.container(border=True):
            st.subheader("🔐 Account Security")
            col_u, col_p = st.columns(2)
            
            with col_u:
                st.write("**Change Username**")
                new_u = st.text_input("New Username", placeholder="Enter new username")
                if st.button("Update Username", use_container_width=True):
                    if new_u.strip():
                        success = update_user_username(st.session_state.username, new_u)
                        if success:
                            st.session_state.username = new_u
                            st.success(f"Username changed to {new_u}!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("This username is already taken.")
                    else:
                        st.warning("Username cannot be empty.")

            with col_p:
                st.write("**Change Password**")
                new_p = st.text_input("New Password", type="password", placeholder="Enter new password")
                confirm_p = st.text_input("Confirm New Password", type="password")
                if st.button("Update Password", use_container_width=True, type="primary"):
                    if new_p == confirm_p and len(new_p) >= 4:
                        update_user_password(st.session_state.username, new_p)
                        st.success("Password updated successfully!")
                    elif len(new_p) < 4:
                        st.error("Password must be at least 4 characters.")
                    else:
                        st.error("Passwords do not match.")

        st.write("") # Spacer

        # --- SECTION 2: ALERTS & DATA ---
        col1, col2 = st.columns(2)
        
        with col1:
            with st.container(border=True):
                st.subheader("🔔 Automated Alerts")
                conn = sqlite3.connect('daily_steps.db')
                c = conn.cursor()
                c.execute('SELECT email, notifications_enabled FROM users WHERE username = ?', (st.session_state.username,))
                user_data = c.fetchone()
                
                email = user_data[0] if user_data else ""
                notif_on = bool(user_data[1]) if user_data else False
                
                new_email = st.text_input("Registered Email", value=email)
                enable_alerts = st.toggle("Enable Daily Summary Emails", value=notif_on)
                
                if st.button("Update Alert Settings", use_container_width=True):
                    c.execute('UPDATE users SET email = ?, notifications_enabled = ? WHERE username = ?', 
                              (new_email, int(enable_alerts), st.session_state.username))
                    conn.commit()
                    st.success("Alert settings updated!")
                conn.close()

        with col2:
            with st.container(border=True):
                st.subheader("💾 Data Management")
                st.write("Export your historical data for your own records.")
                
                if st.button("📥 Download My Data (CSV)", use_container_width=True):
                    if MONGO_AVAILABLE:
                        export_data = list(history_col.find({"username": st.session_state.username}, {"_id": 0}))
                        if export_data:
                            df_export = pd.DataFrame(export_data)
                            csv = df_export.to_csv(index=False).encode('utf-8')
                            st.download_button("Download CSV", data=csv, file_name=f"{st.session_state.username}_history.csv", mime='text/csv', use_container_width=True)
                        else:
                            st.info("No data found to export.")
                    else:
                        st.error("MongoDB not connected.")
