# Pinterest to Google Drive Mehndi Downloader Agent 🎨🤖

An automated AI agent that searches Pinterest for high-quality mehndi design images, downloads them, uses Perceptual Hashing (pHash) to detect and skip duplicate images, organizes them into categorized folders, and uploads them to Google Drive with automated scheduling and a Streamlit dashboard.

---

## 🌟 Features

1. **Pinterest Crawler**: Uses Playwright browser automation to perform dynamic search queries, bypass lazy-loading, and extract pin images.
2. **Resolution Upgrader**: Automatically replaces thumbnail dimensions in image URLs (e.g., `236x`, `564x`, `736x`) with `originals` to fetch maximum image quality, falling back to lower resolutions if the original is removed.
3. **Perceptual Hash Deduplication**: Utilizes the `imagehash` library to generate a unique perceptual hash (`phash`) for each image, preventing duplicates even if their filenames, URLs, or sizes differ.
4. **SQLite Metadata Store**: Tracks local file paths, download statuses, Google Drive file IDs, and sync histories to avoid redundant downloads or uploads.
5. **Categorized Subfolders**: Automatically organizes mehndi designs into structures like `Mehndi Collection/Bridal/`, `Arabic/`, etc.
6. **Google Drive Integration**: Handles OAuth 2.0 flow, folder hierarchy replication, resumable media uploads, and exponential backoff retries.
7. **Task Scheduler**: Configures daily, weekly, or monthly runs. Supports background thread execution or integration with the native Windows Task Scheduler.
8. **Streamlit Web Dashboard**: Monitor system metrics, view local downloads in a grid gallery, configure keywords, trigger runs manually, configure scheduling, and view live debug logs.

---

## 📂 Project Structure

```
pinterest_gdrive_agent/
│
├── config/
│   ├── __init__.py
│   └── settings.py          # Configuration manager (.env and config.json)
│
├── database/
│   ├── __init__.py
│   └── db_helper.py         # SQLite database management (hashes, paths, status)
│
├── pinterest/
│   ├── __init__.py
│   ├── scraper.py           # Playwright automation to find pin URLs
│   └── downloader.py        # Image download, size fallback, and pHash deduplication
│
├── gdrive/
│   ├── __init__.py
│   └── uploader.py          # Google Drive OAuth & upload manager
│
├── scheduler/
│   ├── __init__.py
│   └── task_scheduler.py    # Daemon scheduler & Windows Task setup helpers
│
├── ui/
│   ├── __init__.py
│   └── dashboard.py         # Streamlit web dashboard
│
├── logs/
│   └── mehndi_agent.log     # Execution and error log file
│
├── downloads/               # Directory where downloaded images are saved
│
├── main.py                  # CLI & task executor coordinator
├── requirements.txt         # Python package dependencies
├── .env.example             # Template for API keys and paths
├── config.json              # Configurable Pinterest search and scheduling defaults
├── README.md                # System documentation
└── gdrive_setup_guide.md    # Guide for setting up Google Drive API credentials
```

---

## 🛠️ Installation & Setup

### 1. Clone or Move to the Workspace
Ensure all files are placed in a folder (e.g. `d:\MehSang\pinterest_gdrive_agent`).

### 2. Install Dependencies
Run the following command to install the required Python packages:
```bash
pip install -r requirements.txt
```

### 3. Install Playwright Browsers
Install Chromium, the browser engine used by Playwright for scraping:
```bash
playwright install chromium
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` in the same directory:
```bash
copy .env.example .env
```
Open `.env` and configure:
* `GDRIVE_PARENT_FOLDER_ID`: (Optional) If you want to nest the `Mehndi Collection` folder inside an existing Google Drive folder, paste its ID here. Otherwise, leave it blank to create it in your Drive root.

### 5. Obtain Google Drive Credentials
To enable uploading to Google Drive:
1. Create a project in the Google Cloud Console, enable the Google Drive API, and configure the OAuth consent screen.
2. Download your OAuth Client ID JSON credentials and save them as `credentials.json` in the `pinterest_gdrive_agent/` root folder.
3. For detailed instructions, refer to **[gdrive_setup_guide.md](file:///d:/MehSang/pinterest_gdrive_agent/gdrive_setup_guide.md)**.

---

## 🚀 How to Run the Agent

### A. Web Dashboard (Recommended)
Launch the Streamlit web dashboard:
```bash
streamlit run ui/dashboard.py
```
This will open `http://localhost:8501` in your browser. From here, you can:
* Start manual scraping crawls.
* Test Google Drive sync status.
* Browse and inspect downloaded images in the **Gallery**.
* Manage automated schedules and trigger Windows Task Scheduler setup.
* Monitor live logs.

### B. Command Line Interface (CLI)
You can run the agent directly from the terminal for automation:

* **Run Full Sync Pipeline**: Scrapes Pinterest, downloads unique images, and uploads them to Drive:
  ```bash
  python main.py --sync-all
  ```
* **Sync Pending Uploads Only**: Uploads downloaded images that failed or are pending:
  ```bash
  python main.py --upload-only
  ```
* **Register Windows Task**: Configures the agent to run automatically on Windows Task Scheduler based on the scheduler settings in `config.json`:
  ```bash
  python main.py --install-task
  ```
* **Remove Windows Task**:
  ```bash
  python main.py --uninstall-task
  ```
* **Run Console Scheduler Daemon**: Runs the scheduling loop persistently inside the terminal:
  ```bash
  python main.py --run-scheduler
  ```
"# PintrestMehndiAgent" 
