import os
import sys
import subprocess
import logging
import threading
import time
import uuid
import schedule
from datetime import datetime
from typing import Dict, Optional, Callable, Tuple
from config.settings import settings
from database.db_helper import db
from pinterest.scraper import scrape_pinterest
from pinterest.downloader import download_batch_pins
from gdrive.uploader import sync_pending_uploads, download_db_from_drive, upload_db_to_drive, get_drive_service

logger = logging.getLogger("mehndi_agent.scheduler")

# Flag to control the background scheduler thread in memory
_scheduler_running = False
_scheduler_thread: Optional[threading.Thread] = None

def run_sync_for_all(
    target_count: Optional[int] = None, 
    progress_callback: Optional[Callable[[str], None]] = None
) -> str:
    """
    Runs the full sync pipeline:
    1. Scrapes Pinterest for all configured keywords.
    2. Downloads and deduplicates images.
    3. Uploads new images to Google Drive.
    """
    run_id = str(uuid.uuid4())[:8]
    keywords = settings.get_keywords()
    default_count = target_count or settings.get_default_image_count()
    
    msg = f"Starting full sync run {run_id} at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    logger.info(msg)
    if progress_callback:
        progress_callback(msg)
        
    # Sync database from Google Drive first
    try:
        service = get_drive_service()
        download_db_from_drive(service)
    except Exception as e:
        logger.warning(f"Could not download database from Google Drive at startup: {e}")
        
    db.start_sync_run(run_id, "ALL_CATEGORIES")
    
    # 1. Scrape and Download for each category
    for category, keyword in keywords.items():
        cat_msg = f"Processing category '{category}' (Query: '{keyword}')"
        logger.info(cat_msg)
        if progress_callback:
            progress_callback(cat_msg)
            
        try:
            # Scrape pins
            pins = asyncio_run_helper(scrape_pinterest(keyword, category, default_count, run_id))
            
            # Download pins
            if pins:
                downloaded, duplicates, failed = download_batch_pins(pins, run_id)
                status_msg = f"Category '{category}': Saved {downloaded}, Duplicates {duplicates}, Failed {failed}."
            else:
                status_msg = f"Category '{category}': No new pins found."
            logger.info(status_msg)
            if progress_callback:
                progress_callback(status_msg)
        except Exception as e:
            err_msg = f"Error processing category '{category}': {e}"
            logger.error(err_msg, exc_info=True)
            if progress_callback:
                progress_callback(err_msg)
                
    # 2. Upload pending to Google Drive
    upload_msg = "Starting upload of downloaded images to Google Drive..."
    logger.info(upload_msg)
    if progress_callback:
        progress_callback(upload_msg)
        
    try:
        uploaded, failed = sync_pending_uploads(run_id)
        fin_upload_msg = f"Google Drive sync complete. Uploaded {uploaded} images successfully. Failed {failed}."
        logger.info(fin_upload_msg)
        if progress_callback:
            progress_callback(fin_upload_msg)
    except Exception as e:
        err_msg = f"Error during Google Drive upload: {e}"
        logger.error(err_msg, exc_info=True)
        if progress_callback:
            progress_callback(err_msg)
            
    db.end_sync_run(run_id, "COMPLETED")
    
    # Upload updated database to Google Drive at the end of sync
    try:
        service = get_drive_service()
        upload_db_to_drive(service)
    except Exception as e:
        logger.warning(f"Could not upload database to Google Drive at end of sync: {e}")
        
    end_msg = f"Full sync run {run_id} completed."
    logger.info(end_msg)
    if progress_callback:
        progress_callback(end_msg)
        
    return run_id

def asyncio_run_helper(coro):
    """Helper to run async functions synchronously across threads"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(lambda: asyncio.run(coro))
            return future.result()
    else:
        return loop.run_until_complete(coro)

def start_scheduler_thread():
    global _scheduler_running, _scheduler_thread
    if _scheduler_running:
        return
        
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    logger.info("Background scheduler thread started.")

def stop_scheduler_thread():
    global _scheduler_running
    _scheduler_running = False
    logger.info("Background scheduler thread stop requested.")

def is_scheduler_running() -> bool:
    return _scheduler_running

def _scheduler_loop():
    setup_schedule_jobs()
    while _scheduler_running:
        schedule.run_pending()
        time.sleep(1)

def setup_schedule_jobs():
    schedule.clear()
    config = settings.get_scheduler_config()
    if not config.get("enabled", False):
        logger.info("Scheduler is disabled in configuration.")
        return
        
    interval = config.get("interval", "daily").lower()
    time_str = config.get("time", "02:00")
    
    job = schedule.every()
    
    if interval == "daily":
        job = job.day.at(time_str)
    elif interval == "weekly":
        day_of_week = config.get("day_of_week", "monday").lower()
        if day_of_week == "monday":
            job = job.monday.at(time_str)
        elif day_of_week == "tuesday":
            job = job.tuesday.at(time_str)
        elif day_of_week == "wednesday":
            job = job.wednesday.at(time_str)
        elif day_of_week == "thursday":
            job = job.thursday.at(time_str)
        elif day_of_week == "friday":
            job = job.friday.at(time_str)
        elif day_of_week == "saturday":
            job = job.saturday.at(time_str)
        elif day_of_week == "sunday":
            job = job.sunday.at(time_str)
    elif interval == "monthly":
        day_of_month = int(config.get("day_of_month", 1))
        job = job.day.at(time_str)
        
        def run_monthly_job():
            if datetime.now().day == day_of_month:
                run_sync_for_all()
                
        job.do(run_monthly_job)
        logger.info(f"Scheduled monthly run on day {day_of_month} at {time_str}")
        return
        
    job.do(run_sync_for_all)
    logger.info(f"Scheduled job: {interval} at {time_str}")

def install_windows_task() -> Tuple[bool, str]:
    """Creates a Windows Task Scheduler task to run the agent in the background"""
    if sys.platform != "win32":
        return False, "Windows Task Scheduler is only supported on Windows operating systems."
        
    config = settings.get_scheduler_config()
    interval = config.get("interval", "daily").upper()
    time_str = config.get("time", "02:00")
    
    task_name = "PinterestGDriveMehndiAgent"
    
    python_exe = sys.executable
    pythonw_exe = python_exe.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw_exe):
        pythonw_exe = python_exe  # Fallback
        
    script_path = settings.base_dir / "main.py"
    
    cmd = [
        "schtasks", "/create", "/tn", task_name,
        "/tr", f'"{pythonw_exe}" "{script_path}" --sync-all',
        "/f"
    ]
    
    if interval == "DAILY":
        cmd.extend(["/sc", "DAILY", "/st", time_str])
    elif interval == "WEEKLY":
        day = config.get("day_of_week", "MON")[:3].upper()
        cmd.extend(["/sc", "WEEKLY", "/d", day, "/st", time_str])
    elif interval == "MONTHLY":
        day_num = str(config.get("day_of_month", 1))
        cmd.extend(["/sc", "MONTHLY", "/d", day_num, "/st", time_str])
    else:
        cmd.extend(["/sc", "DAILY", "/st", time_str])
        
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True, f"Successfully created Windows task: {result.stdout.strip()}"
    except subprocess.CalledProcessError as e:
        return False, f"Failed to create Windows task: {e.stderr or e.output}"

def uninstall_windows_task() -> Tuple[bool, str]:
    """Removes the Windows Task Scheduler task"""
    if sys.platform != "win32":
        return False, "Windows Task Scheduler is only supported on Windows."
        
    task_name = "PinterestGDriveMehndiAgent"
    cmd = ["schtasks", "/delete", "/tn", task_name, "/f"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True, f"Successfully removed Windows task: {result.stdout.strip()}"
    except subprocess.CalledProcessError as e:
        return False, f"Failed to remove Windows task: {e.stderr or e.output}"
