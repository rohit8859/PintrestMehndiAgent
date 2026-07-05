import os
import sys
import time
import logging
from pathlib import Path
from datetime import datetime
import streamlit as st

# Add base directory to path so imports work correctly
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

def setup_logging():
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "mehndi_agent.log"
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.handlers = []
    
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

setup_logging()

from config.settings import settings
from database.db_helper import db
from scheduler import task_scheduler
from gdrive import uploader
from pinterest.scraper import scrape_pinterest
from pinterest.downloader import download_batch_pins

# Configure page settings
st.set_page_config(
    page_title="Mehndi Pinterest Agent Dashboard",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS -- "Henna Ledger" theme: warm paper background, henna-ink and
# turmeric-gold palette, paisley motifs as the recurring signature element.
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Marcellus&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
    :root {
        --henna-ink: #0F3D2E;
        --henna-maroon: #1F5C45;
        --henna-gold: #C68A2E;
        --henna-gold-light: #E8B85C;
        --henna-paper: #FBF1E1;
        --henna-paper-deep: #F3E4C8;
        --henna-sage: #2E7D5B;
        --henna-rose: #9C6B1F;
        --henna-border: #DCC08A;
    }

    /* ---- Page canvas ---- */
    [data-testid="stAppViewContainer"], .main {
        background-color: var(--henna-paper);
        background-image:
            radial-gradient(circle at 1px 1px, rgba(166, 124, 46, 0.07) 1px, transparent 0);
        background-size: 22px 22px;
    }
    [data-testid="stHeader"] {
        background-color: transparent;
    }

    /* ---- Typography ---- */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
        color: var(--henna-ink);
    }
    h1, h2, h3 {
        font-family: 'Marcellus', serif !important;
        color: var(--henna-maroon) !important;
        letter-spacing: 0.01em;
    }
    h1 {
        border-bottom: 2px solid var(--henna-gold);
        padding-bottom: 0.5rem;
        margin-bottom: 1.2rem !important;
    }

    /* ---- Paisley signature divider, under every h1 title block ---- */
    h1::after {
        content: "";
        display: block;
        margin-top: 10px;
        height: 14px;
        width: 100%;
        background-image: repeating-linear-gradient(
            90deg,
            transparent 0px,
            transparent 18px,
            var(--henna-gold-light) 18px,
            var(--henna-gold-light) 20px
        );
        background-size: 20px 2px;
        background-repeat: repeat-x;
        background-position: bottom;
        opacity: 0.6;
    }

    /* ---- Sidebar ---- */
    [data-testid="stSidebar"] {
        background-color: var(--henna-ink);
        border-right: 3px solid var(--henna-gold);
    }
    [data-testid="stSidebar"] * {
        color: var(--henna-paper) !important;
    }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
        color: var(--henna-gold-light) !important;
    }
    [data-testid="stSidebar"] hr {
        border-color: rgba(232, 184, 92, 0.3);
    }
    [data-testid="stSidebar"] [data-baseweb="radio"] label {
        padding: 6px 4px;
        border-radius: 6px;
    }
    [data-testid="stSidebar"] [data-baseweb="radio"] label:hover {
        background-color: rgba(232, 184, 92, 0.12);
    }

    /* ---- Metric cards ---- */
    .metric-card {
        background-color: #FFFDF8;
        border: 1px solid var(--henna-border);
        border-top: 3px solid var(--henna-gold);
        border-radius: 8px;
        padding: 16px 10px;
        text-align: center;
        box-shadow: 3px 3px 0px rgba(74, 36, 18, 0.06);
    }
    .metric-value {
        font-family: 'Marcellus', serif;
        font-size: 2.1rem;
        font-weight: 400;
        color: var(--henna-maroon);
        margin: 4px 0;
    }
    .metric-label {
        font-size: 0.78rem;
        color: var(--henna-ink);
        opacity: 0.75;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }

    /* ---- Gallery ---- */
    .gallery-img-container {
        display: inline-block;
        margin: 10px;
        border: 1px solid var(--henna-border);
        border-radius: 6px;
        padding: 6px;
        background-color: #FFFDF8;
    }

    /* ---- Log console ---- */
    .log-box {
        font-family: 'SFMono-Regular', Consolas, monospace;
        font-size: 0.8rem;
        background-color: #0A2A20;
        color: #E8B85C;
        padding: 15px;
        border-radius: 6px;
        border: 1px solid var(--henna-gold);
        height: 300px;
        overflow-y: scroll;
        white-space: pre-wrap;
    }

    /* ---- Buttons ---- */
    .stButton button {
        background-color: var(--henna-maroon);
        color: var(--henna-paper);
        border: 1px solid var(--henna-ink);
        border-radius: 6px;
        font-weight: 500;
        transition: all 0.15s ease;
    }
    .stButton button:hover {
        background-color: var(--henna-gold);
        color: var(--henna-ink);
        border-color: var(--henna-gold);
    }
    .stButton button:disabled {
        background-color: #D9CBAE;
        color: #8A8270;
        border-color: #D9CBAE;
    }

    /* ---- Inputs, selects, multiselect tags ---- */
    [data-baseweb="select"] > div, .stTextInput input, .stNumberInput input {
        border-color: var(--henna-border) !important;
        border-radius: 6px !important;
    }
    [data-baseweb="tag"] {
        background-color: var(--henna-gold) !important;
    }

    /* ---- Alert boxes (success / info / warning / error) ---- */
    [data-testid="stAlertContainer"] {
        border-radius: 6px;
        border-left: 4px solid;
    }
    div[data-baseweb="notification"] {
        border-radius: 6px;
    }

    /* ---- Dataframe ---- */
    [data-testid="stDataFrame"] {
        border: 1px solid var(--henna-border);
        border-radius: 6px;
    }

    /* ---- Info boxes used for category breakdown ---- */
    .stAlert {
        border-radius: 6px;
    }
</style>
""", unsafe_allow_html=True)

# Helper function to read log contents
def get_log_contents(lines_count: int = 150) -> str:
    log_file = settings.base_dir / "logs" / "mehndi_agent.log"
    if not log_file.exists():
        return "No log logs generated yet."
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "".join(lines[-lines_count:])
    except Exception as e:
        return f"Error reading log file: {e}"

# Initialize session state variables for progress tracking
if "running_sync" not in st.session_state:
    st.session_state.running_sync = False
if "sync_logs" not in st.session_state:
    st.session_state.sync_logs = []

# Try to download the database from Google Drive on startup
if "db_downloaded_from_drive" not in st.session_state:
    st.session_state.db_downloaded_from_drive = False
    
if not st.session_state.db_downloaded_from_drive:
    try:
        service = uploader.get_drive_service()
        if uploader.download_db_from_drive(service):
            st.session_state.db_downloaded_from_drive = True
            db._init_db()
            st.rerun()
    except Exception as e:
        pass

def run_sync_gui(target_count: int, selected_categories: list):
    st.session_state.running_sync = True
    st.session_state.sync_logs = []
    
    def log_gui(message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] {message}"
        st.session_state.sync_logs.append(formatted)
        
    run_id = st.session_state.current_run_id = str(time.time())[:8]
    log_gui(f"Initiating GUI manual sync run {run_id}")
    
    db._init_db()
        
    db.start_sync_run(run_id, ", ".join(selected_categories))
    
    keywords = settings.get_keywords()
    
    # 1. Scrape & Download phase
    for category in selected_categories:
        keyword = keywords.get(category)
        if not keyword:
            continue
            
        log_gui(f"Processing Category: '{category}' with search: '{keyword}'")
        
        try:
            # Scrape
            log_gui(f"Launching Playwright browser to scrape '{category}'...")
            
            # Helper to run async scraper synchronously
            pins = task_scheduler.asyncio_run_helper(
                scrape_pinterest(
                    keyword, 
                    category, 
                    target_count, 
                    run_id, 
                    progress_callback=lambda current, total, msg: log_gui(f"Scraper: {msg}")
                )
            )
            
            log_gui(f"Scrape completed. Found {len(pins)} matching pins.")
            
            # Download
            if pins:
                log_gui(f"Downloading files for '{category}'...")
                downloaded, duplicates, failed = download_batch_pins(
                    pins, 
                    run_id, 
                    progress_callback=lambda current, total, msg: log_gui(f"Downloader: {msg}")
                )
                log_gui(f"Completed '{category}': Saved {downloaded}, duplicates blocked: {duplicates}, failed: {failed}.")
            else:
                log_gui(f"No new pins found on Pinterest for '{category}'.")
                
        except Exception as e:
            log_gui(f"Error scraping/downloading category '{category}': {e}")
            logging.error(f"Error in GUI sync for {category}: {e}", exc_info=True)
            
    # 2. Upload phase
    log_gui("Beginning upload sync to Google Drive...")
    try:
        uploaded, failed = uploader.sync_pending_uploads(
            run_id, 
            progress_callback=lambda current, total, msg: log_gui(f"GDrive: {msg}")
        )
        log_gui(f"Google Drive sync complete. Successful uploads: {uploaded}, Failed: {failed}.")
    except Exception as e:
        log_gui(f"Google Drive upload process failed: {e}")
        logging.error(f"Error in GUI GDrive upload: {e}", exc_info=True)
        
    db.end_sync_run(run_id, "COMPLETED")
    
    # Upload updated database to Google Drive
    try:
        log_gui("Uploading updated database to Google Drive...")
        service = uploader.get_drive_service()
        if uploader.upload_db_to_drive(service):
            log_gui("Database uploaded and synced to Google Drive successfully.")
        else:
            log_gui("Failed to upload database to Google Drive.")
    except Exception as e:
        log_gui(f"Could not upload database to Google Drive: {e}")
        
    log_gui("Manual sync pipeline execution finished.")
    st.session_state.running_sync = False


# --- SIDEBAR NAV ---
st.sidebar.title("🎨 Mehndi Agent")
st.sidebar.caption("Pinterest to Google Drive Downloader")

menu = st.sidebar.radio(
    "Navigation",
    ["📊 Overview", "🚀 Run Agent", "☁️ Google Drive Sync", "🕒 Scheduler", "🖼️ Gallery", "📋 Logs"]
)

# Render background thread status in sidebar
scheduler_enabled = settings.get_scheduler_config().get("enabled", False)
st.sidebar.markdown("---")
st.sidebar.markdown(f"**Local Scheduler Daemon**: {'🟢 Active' if task_scheduler.is_scheduler_running() else '🔴 Stopped'}")
st.sidebar.markdown(f"**Scheduler Configuration**: {'🟢 Enabled' if scheduler_enabled else '🔴 Disabled'}")

# --- MAIN INTERFACE ---
if menu == "📊 Overview":
    st.title("📊 System Overview & Statistics")
    
    # Grab Stats from DB
    stats = db.get_statistics()
    
    # Metric rows
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>Total Tracked Pins</div><div class='metric-value'>{stats['total']}</div></div>", unsafe_allow_html=True)
    with col2:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>Downloaded Locally</div><div class='metric-value'>{stats['downloaded']}</div></div>", unsafe_allow_html=True)
    with col3:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>Duplicates Blocked</div><div class='metric-value'>{stats['duplicates']}</div></div>", unsafe_allow_html=True)
    with col4:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>Uploaded to Drive</div><div class='metric-value'>{stats['uploaded']}</div></div>", unsafe_allow_html=True)
    with col5:
        st.markdown(f"<div class='metric-card'><div class='metric-label'>Pending Uploads</div><div class='metric-value'>{stats['pending_upload']}</div></div>", unsafe_allow_html=True)
        
    st.markdown("### Category Breakdown")
    if stats["categories"]:
        cols = st.columns(3)
        for idx, (cat, cat_stats) in enumerate(stats["categories"].items()):
            col_idx = idx % 3
            with cols[col_idx]:
                st.info(f"**{cat}**\n* Total: {cat_stats['total']} pins\n* Downloaded: {cat_stats['downloaded']} unique images")
    else:
        st.write("No categories synchronized yet.")
        
    st.markdown("### Recent Synchronization Runs")
    history = db.get_sync_history(limit=10)
    if history:
        st.dataframe(history, use_container_width=True)
    else:
        st.info("No sync history found in database.")

elif menu == "🚀 Run Agent":
    st.title("🚀 Run Downloader Agent")
    st.write("Trigger manual scraping and uploading. You can select specific keywords and the number of images.")
    
    keywords = settings.get_keywords()
    
    # --- Manage Categories ---
    with st.expander("✏️ Manage Categories", expanded=False):
        st.caption("Add, edit, or remove the mehndi categories searched on Pinterest. Changes save to config.json immediately.")
        
        st.markdown("**Current categories**")
        if keywords:
            for cat_name, search_term in list(keywords.items()):
                ec1, ec2, ec3 = st.columns([2, 3, 1])
                with ec1:
                    st.text_input("Label", value=cat_name, disabled=True, key=f"label_{cat_name}", label_visibility="collapsed")
                with ec2:
                    new_term = st.text_input(
                        "Search term",
                        value=search_term,
                        key=f"term_{cat_name}",
                        label_visibility="collapsed"
                    )
                    if new_term != search_term:
                        updated = settings.get_keywords()
                        updated[cat_name] = new_term
                        settings.update_keywords(updated)
                        st.rerun()
                with ec3:
                    if st.button("Delete", key=f"del_{cat_name}"):
                        updated = settings.get_keywords()
                        updated.pop(cat_name, None)
                        settings.update_keywords(updated)
                        st.rerun()
        else:
            st.info("No categories configured yet. Add one below.")
        
        st.markdown("**Add a new category**")
        nc1, nc2, nc3 = st.columns([2, 3, 1])
        with nc1:
            new_label = st.text_input("New label", placeholder="e.g. Feet", key="new_cat_label", label_visibility="collapsed")
        with nc2:
            new_search = st.text_input("New search term", placeholder="e.g. Feet Mehndi Design", key="new_cat_search", label_visibility="collapsed")
        with nc3:
            if st.button("Add", key="add_cat_btn"):
                label_clean = new_label.strip()
                search_clean = new_search.strip()
                if not label_clean or not search_clean:
                    st.warning("Enter both a label and a search term.")
                elif label_clean in keywords:
                    st.warning(f"Category '{label_clean}' already exists.")
                else:
                    updated = settings.get_keywords()
                    updated[label_clean] = search_clean
                    settings.update_keywords(updated)
                    st.success(f"Added category '{label_clean}'.")
                    st.rerun()
        
        # Refresh local copy after any edits made in this render
        keywords = settings.get_keywords()
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("Run Configuration")
        
        # Multiselect for categories
        selected_cats = st.multiselect(
            "Select Categories to Sync",
            options=list(keywords.keys()),
            default=list(keywords.keys())
        )
        
        target_count = st.number_input(
            "Images to Download per Category",
            min_value=5,
            max_value=1000,
            value=settings.get_default_image_count(),
            step=5
        )
        
        # Start button
        run_btn = st.button("Start Sync Pipeline", disabled=st.session_state.running_sync)
        
        if run_btn and selected_cats:
            st.session_state.running_sync = True
            st.rerun()
            
    with col2:
        st.subheader("Agent Execution Log")
        
        if st.session_state.running_sync:
            with st.spinner("Agent running. Please wait..."):
                # Execute sync and print output
                log_container = st.empty()
                
                # Execute actual sync function
                # (Since streamlit reruns on click, running directly here works, but we should make sure we toggle running state)
                try:
                    run_sync_gui(target_count, selected_cats)
                    st.success("Synchronization process completed successfully!")
                except Exception as e:
                    st.error(f"Sync process encountered an error: {e}")
                    st.session_state.running_sync = False
                st.rerun()
                
        elif st.session_state.sync_logs:
            st.write("Logs from last manual run:")
            logs_str = "\n".join(st.session_state.sync_logs)
            st.markdown(f"<div class='log-box'>{logs_str}</div>", unsafe_allow_html=True)
        else:
            st.info("No active agent run. Configure options and click 'Start Sync Pipeline'.")

elif menu == "☁️ Google Drive Sync":
    st.title("☁️ Google Drive Sync Manager")
    
    st.subheader("Credential Status")
    
    token_file = settings.base_dir / "token.json"
    cred_file = settings.google_credentials_path
    
    col1, col2 = st.columns(2)
    
    with col1:
        if cred_file.exists():
            st.success(f"✅ `credentials.json` found in project root.")
        else:
            st.error(f"❌ `credentials.json` is missing.")
            st.warning("You must download client secrets from Google Cloud Console and save them to the project folder.")
            
        if token_file.exists():
            st.success("✅ `token.json` found. Agent is authenticated.")
        else:
            st.info("ℹ️ `token.json` not found. Authentication will be prompted during first run.")
            
    with col2:
        st.markdown(f"**Parent Folder ID**: `{settings.gdrive_parent_folder_id or 'Root of Google Drive'}`")
        st.markdown(f"**GCP Credentials Path**: `{settings.google_credentials_path}`")
        
    st.subheader("Manual Upload Sync")
    st.write("Upload any downloaded files that are currently pending sync on Google Drive.")
    
    pending_uploads = db.get_pending_uploads()
    st.markdown(f"**Pending uploads currently in database**: `{len(pending_uploads)}` images")
    
    sync_btn = st.button("Sync Pending Uploads Now", disabled=(len(pending_uploads) == 0))
    if sync_btn:
        with st.spinner("Uploading pending images to Google Drive..."):
            run_id = "GUI_SYNC_" + str(time.time())[:6]
            db.start_sync_run(run_id, "UPLOAD_ONLY")
            try:
                uploaded, failed = uploader.sync_pending_uploads(run_id)
                st.success(f"Sync complete. Uploaded {uploaded} images, failed {failed}.")
            except Exception as e:
                st.error(f"Sync failed: {e}")
            db.end_sync_run(run_id, "COMPLETED")
            st.rerun()

    st.markdown("---")
    st.subheader("💾 Database Sync Management")
    st.write("Manually fetch the latest database file from Google Drive to synchronize runs performed in the cloud, or backup your local database.")
    
    col_sync1, col_sync2 = st.columns(2)
    with col_sync1:
        if st.button("Download latest database from Drive"):
            with st.spinner("Downloading database..."):
                try:
                    service = uploader.get_drive_service()
                    if uploader.download_db_from_drive(service):
                        db._init_db()
                        st.success("Successfully synchronized and loaded database from Google Drive!")
                        st.session_state.db_downloaded_from_drive = True
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("No database file found on Google Drive to download.")
                except Exception as e:
                    st.error(f"Download failed: {e}")
                    
    with col_sync2:
        if st.button("Upload local database to Drive"):
            with st.spinner("Uploading database..."):
                try:
                    service = uploader.get_drive_service()
                    if uploader.upload_db_to_drive(service):
                        st.success("Successfully backed up local database to Google Drive!")
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error("Failed to upload database to Google Drive.")
                except Exception as e:
                    st.error(f"Upload failed: {e}")

    st.markdown("---")
    st.subheader("🗑️ Reset Sync History Database")
    st.warning("⚠️ WARNING: Resetting the database will delete your entire download/upload history log. The agent will start downloading all Pinterest images from scratch (which could create duplicates if you don't clear your Google Drive folders as well).")
    
    confirm_reset = st.checkbox("I understand that this deletes all sync history logs and cannot be undone.")
    reset_btn = st.button("Reset & Overwrite Sync Database", type="secondary", disabled=not confirm_reset)
    
    if reset_btn and confirm_reset:
        with st.spinner("Resetting sync history..."):
            try:
                db_path = settings.db_path
                # Try truncating tables first (safest, prevents Windows file lock issues)
                try:
                    with db._get_connection() as conn:
                        conn.execute("DELETE FROM images")
                        conn.execute("DELETE FROM sync_history")
                        conn.commit()
                except Exception:
                    # Fallback to file deletion if table clean fails
                    if db_path.exists():
                        try:
                            os.remove(db_path)
                        except Exception:
                            pass
                    db._init_db()
                
                # Overwrite the Google Drive database with the reset empty database
                service = uploader.get_drive_service()
                if uploader.upload_db_to_drive(service):
                    st.success("🎉 Sync database has been successfully reset locally and on Google Drive! The agent will now download new images starting from scratch.")
                    st.session_state.db_downloaded_from_drive = True
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error("Failed to upload the reset database to Google Drive. Please check your credentials.")
            except Exception as e:
                st.error(f"Error resetting database: {e}")


elif menu == "🕒 Scheduler":
    st.title("🕒 Automation Scheduler")
    
    st.subheader("Schedule Settings")
    sched_cfg = settings.get_scheduler_config()
    
    col1, col2 = st.columns(2)
    
    with col1:
        enabled = st.checkbox("Enable Automated Sync Schedule", value=sched_cfg.get("enabled", False))
        
        interval = st.selectbox(
            "Synchronization Interval",
            options=["daily", "weekly", "monthly"],
            index=["daily", "weekly", "monthly"].index(sched_cfg.get("interval", "daily").lower())
        )
        
        time_str = st.text_input(
            "Execution Time (24h Format HH:MM)",
            value=sched_cfg.get("time", "02:00")
        )
        
    with col2:
        day_of_week = "monday"
        day_of_month = 1
        
        if interval == "weekly":
            day_of_week = st.selectbox(
                "Day of Week",
                options=["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
                index=["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(sched_cfg.get("day_of_week", "monday").lower())
            )
        elif interval == "monthly":
            day_of_month = st.slider(
                "Day of Month",
                min_value=1,
                max_value=28,
                value=int(sched_cfg.get("day_of_month", 1))
            )
            
    # Save config button
    if st.button("Save Schedule Settings"):
        settings.update_scheduler_config(
            enabled=enabled,
            interval=interval,
            time_str=time_str,
            day_of_week=day_of_week,
            day_of_month=day_of_month
        )
        st.success("Scheduler settings updated in config.json.")
        
        # If in-memory scheduler thread is running, restart/setup jobs
        if task_scheduler.is_scheduler_running():
            task_scheduler.setup_schedule_jobs()
            st.info("In-memory scheduler reloaded with new configuration.")
            
    st.markdown("---")
    st.subheader("Background Scheduling Daemon")
    st.write("Run the scheduler as a background thread inside this web server instance.")
    
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        if task_scheduler.is_scheduler_running():
            st.success("🟢 Scheduler Thread is currently running in background.")
            if st.button("Stop Scheduler Thread"):
                task_scheduler.stop_scheduler_thread()
                st.rerun()
        else:
            st.warning("🔴 Scheduler Thread is stopped.")
            if st.button("Start Scheduler Thread"):
                task_scheduler.start_scheduler_thread()
                st.rerun()
                
    st.markdown("---")
    st.subheader("Windows Native Task Scheduler")
    st.write("Register this agent as a native Windows Task Scheduler background task. This allows the script to run even when the Streamlit server is closed.")
    
    col_w1, col_w2 = st.columns(2)
    with col_w1:
        if st.button("Register Windows Task"):
            success, msg = task_scheduler.install_windows_task()
            if success:
                st.success(msg)
            else:
                st.error(msg)
    with col_w2:
        if st.button("Remove Registered Windows Task"):
            success, msg = task_scheduler.uninstall_windows_task()
            if success:
                st.success(msg)
            else:
                st.error(msg)

elif menu == "🖼️ Gallery":
    st.title("🖼️ Image Gallery")
    
    keywords = settings.get_keywords()
    selected_cat = st.selectbox("Select Category to View", options=["All"] + list(keywords.keys()))
    
    cat_filter = None if selected_cat == "All" else selected_cat
    images = db.get_all_images(limit=100, category=cat_filter)
    
    if not images:
        st.info("No images found in database for the selected category.")
    else:
        st.write(f"Showing last {len(images)} downloaded images:")
        
        # Grid format (4 columns)
        cols = st.columns(4)
        for idx, img in enumerate(images):
            col_idx = idx % 4
            with cols[col_idx]:
                local_path = img["local_path"]
                if local_path and os.path.exists(local_path):
                    st.image(local_path, use_column_width=True)
                    st.caption(
                        f"**ID**: {img['pin_id']}\n\n"
                        f"**Category**: {img['category']}\n\n"
                        f"**Hash**: `{img['image_hash']}`\n\n"
                        f"**Drive**: {'Uploaded ✅' if img['upload_status'] == 'UPLOADED' else 'Pending 🔴'}"
                    )
                else:
                    st.error(f"Image file missing: {local_path}")
                    st.caption(f"**ID**: {img['pin_id']} | **Drive**: {img['upload_status']}")

elif menu == "📋 Logs":
    st.title("📋 Live System Logs")
    st.write("Inspect latest debug and info logs written by the downloader agent.")
    
    lines_to_read = st.slider("Number of log lines to show", min_value=50, max_value=500, value=150, step=50)
    
    log_content = get_log_contents(lines_to_read)
    st.markdown(f"<div class='log-box'>{log_content}</div>", unsafe_allow_html=True)
    
    if st.button("Refresh Logs"):
        st.rerun()
