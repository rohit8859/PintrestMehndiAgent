import argparse
import sys
import os
import logging
import uuid
from pathlib import Path
from config.settings import settings

# Configure logging directory and root logger before importing submodules that log
def setup_logging():
    log_dir = settings.base_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "mehndi_agent.log"
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    
    # File handler
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

from scheduler.task_scheduler import run_sync_for_all, install_windows_task, uninstall_windows_task, _scheduler_loop, setup_schedule_jobs
from gdrive.uploader import sync_pending_uploads

def main():
    setup_logging()
    
    parser = argparse.ArgumentParser(description="Pinterest to Google Drive Mehndi Downloader Agent")
    parser.add_argument("--sync-all", action="store_true", help="Run full scrape, download, and upload pipeline")
    parser.add_argument("--upload-only", action="store_true", help="Upload already downloaded pending files to Google Drive")
    parser.add_argument("--install-task", action="store_true", help="Register Windows Task Scheduler task based on config.json settings")
    parser.add_argument("--uninstall-task", action="store_true", help="Remove registered Windows Task Scheduler task")
    parser.add_argument("--run-scheduler", action="store_true", help="Run persistent scheduler in console")
    
    args = parser.parse_args()
    
    if args.sync_all:
        logging.info("CLI option --sync-all selected. Starting sync.")
        run_sync_for_all()
    elif args.upload_only:
        logging.info("CLI option --upload-only selected. Syncing pending images.")
        run_id = str(uuid.uuid4())[:8]
        sync_pending_uploads(run_id)
    elif args.install_task:
        logging.info("CLI option --install-task selected. Creating task...")
        success, msg = install_windows_task()
        print(msg)
    elif args.uninstall_task:
        logging.info("CLI option --uninstall-task selected. Deleting task...")
        success, msg = uninstall_windows_task()
        print(msg)
    elif args.run_scheduler:
        logging.info("CLI option --run-scheduler selected. Running persistent scheduler daemon.")
        print("Starting persistent scheduler loop (Ctrl+C to exit)...")
        try:
            import scheduler.task_scheduler as ts
            ts._scheduler_running = True
            ts._scheduler_loop()
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
