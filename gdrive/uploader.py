import os
import sys
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from config.settings import settings
from database.db_helper import db

logger = logging.getLogger("mehndi_agent.gdrive")

# Shared full Google Drive scope for seamless scanner integration
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Lazy-loaded scanner imports
SCANNER_DIR = Path(__file__).resolve().parent.parent.parent / "gdrive_image_scanner"
if str(SCANNER_DIR) not in sys.path:
    sys.path.append(str(SCANNER_DIR))

try:
    from scanner.core import MehndiScanner
    from database.db_helper import db as scanner_db
    from gdrive.client import GDriveClient as ScannerGDriveClient
    from notifier.alerts import AlertNotifier
    _scanner_available = True
    logger.info("Scanner Agent integration loaded successfully in Pinterest agent.")
except ImportError as e:
    _scanner_available = False
    logger.warning(f"Scanner Agent is not available. Normal uploads will continue. Error: {e}")

# Cache for folder IDs to minimize API calls
# Key: (parent_id, folder_name) | Value: drive_folder_id
_folder_id_cache: Dict[Tuple[Optional[str], str], str] = {}

def restore_secrets_from_env():
    """
    Checks if Google credentials or tokens are present in Streamlit secrets
    and writes them to disk so Google API libraries can read them natively.
    """
    token_path = settings.base_dir / "token.json"
    cred_path = settings.google_credentials_path

    try:
        import streamlit as st
        import json
        

        # Helper to convert secret to json string whether it is a string or parsed dict
        def parse_secret_to_json_str(content, name: str) -> Optional[str]:
            if not content:
                return None
            if isinstance(content, str):
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        logger.info(f"Loaded {name} string secret with keys: {list(parsed.keys())}")
                except Exception as e:
                    logger.warning(f"Failed to parse {name} secret string as JSON: {e}")
                return content.strip()
            try:
                def to_dict(obj):
                    if isinstance(obj, dict):
                        return {k: to_dict(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [to_dict(x) for x in obj]
                    return obj
                parsed_dict = to_dict(content)
                logger.info(f"Loaded {name} dict secret with keys: {list(parsed_dict.keys())}")
                return json.dumps(parsed_dict, indent=2)
            except Exception as e:
                logger.error(f"Error serializing secret dictionary: {e}")
                return str(content)

        # 1. Restore credentials.json
        cred_str = None
        if "GOOGLE_CREDENTIALS_JSON" in st.secrets:
            cred_str = parse_secret_to_json_str(st.secrets["GOOGLE_CREDENTIALS_JSON"], "GOOGLE_CREDENTIALS_JSON")
            if cred_str:
                with open(cred_path, "w", encoding="utf-8") as f:
                    f.write(cred_str)
                logger.info("Restored credentials.json from Streamlit Secrets.")

        # 2. Restore token.json
        if "GOOGLE_TOKEN_JSON" in st.secrets:
            token_str = parse_secret_to_json_str(st.secrets["GOOGLE_TOKEN_JSON"], "GOOGLE_TOKEN_JSON")
            if token_str:
                try:
                    token_data = json.loads(token_str)
                    modified = False
                    
                    # Normalize 'access_token' to 'token'
                    if "access_token" in token_data and "token" not in token_data:
                        token_data["token"] = token_data["access_token"]
                        modified = True
                        
                    # Normalize 'expiry_date' to 'expiry'
                    if "expiry_date" in token_data and "expiry" not in token_data:
                        val = token_data["expiry_date"]
                        if isinstance(val, (int, float)):
                            import datetime
                            if val > 1e11:
                                val = val / 1000.0
                            dt = datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc)
                            token_data["expiry"] = dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                        else:
                            token_data["expiry"] = str(val)
                        modified = True
                        
                    # Add default token_uri
                    if "token_uri" not in token_data:
                        token_data["token_uri"] = "https://oauth2.googleapis.com/token"
                        modified = True
                        
                    # Inject client_id and client_secret if missing
                    if ("client_id" not in token_data or "client_secret" not in token_data) and cred_str:
                        try:
                            cred_data = json.loads(cred_str)
                            client_id = None
                            client_secret = None
                            if "installed" in cred_data:
                                client_id = cred_data["installed"].get("client_id")
                                client_secret = cred_data["installed"].get("client_secret")
                            elif "web" in cred_data:
                                client_id = cred_data["web"].get("client_id")
                                client_secret = cred_data["web"].get("client_secret")
                                
                            if client_id and "client_id" not in token_data:
                                token_data["client_id"] = client_id
                                modified = True
                            if client_secret and "client_secret" not in token_data:
                                token_data["client_secret"] = client_secret
                                modified = True
                        except Exception as cred_err:
                            logger.warning(f"Could not extract client info from credentials for injection: {cred_err}")
                            
                    if modified:
                        token_str = json.dumps(token_data, indent=2)
                        logger.info(f"Normalized token.json. Resulting keys: {list(token_data.keys())}")
                except Exception as norm_err:
                    logger.warning(f"Error normalizing token.json: {norm_err}")

                with open(token_path, "w", encoding="utf-8") as f:
                    f.write(token_str)
                logger.info("Restored token.json from Streamlit Secrets.")
                
        # 3. Restore GDRIVE_PARENT_FOLDER_ID env variable
        if "GDRIVE_PARENT_FOLDER_ID" in st.secrets:
            os.environ["GDRIVE_PARENT_FOLDER_ID"] = st.secrets["GDRIVE_PARENT_FOLDER_ID"]
            
        # 4. Restore PINTEREST_HEADLESS env variable
        if "PINTEREST_HEADLESS" in st.secrets:
            os.environ["PINTEREST_HEADLESS"] = str(st.secrets["PINTEREST_HEADLESS"])

    except Exception as e:
        logger.debug(f"Streamlit secrets not available: {e}")

def get_gdrive_credentials() -> Tuple[Optional[Credentials], Optional[str]]:
    """
    Retrieves credentials from token.json or runs local OAuth server using credentials.json.
    Returns: (credentials, error_message)
    """
    # Restore credentials from Streamlit secrets if running in cloud environment
    restore_secrets_from_env()
    
    token_path = settings.base_dir / "token.json"
    creds = None
    
    # 1. Try loading existing token.json
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            logger.warning(f"Error reading existing token.json: {e}")
            
    # 2. If token is invalid or expired, refresh or authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                logger.info("Google OAuth token expired. Attempting to refresh...")
                creds.refresh(Request())
                # Save refreshed token
                with open(token_path, "w") as token_file:
                    token_file.write(creds.to_json())
                logger.info("Google OAuth token refreshed successfully.")
            except Exception as e:
                logger.error(f"Failed to refresh Google token: {e}")
                creds = None  # Force re-authentication
                
        if not creds:
            # Check if credentials.json exists
            cred_json_path = settings.google_credentials_path
            if not cred_json_path.exists():
                err_msg = (
                    f"Google client secrets file '{cred_json_path.name}' is missing in the project folder. "
                    "Please follow the Google Drive Setup Guide to obtain and place it there."
                )
                logger.error(err_msg)
                return None, err_msg
                
            try:
                logger.info("Initiating Google OAuth flow...")
                flow = InstalledAppFlow.from_client_secrets_file(str(cred_json_path), SCOPES)
                creds = flow.run_local_server(port=8090)
                # Save credentials for future use
                with open(token_path, "w") as token_file:
                    token_file.write(creds.to_json())
                logger.info("Google OAuth token generated and saved to token.json.")
            except Exception as e:
                err_msg = f"Failed to complete OAuth flow: {e}"
                logger.error(err_msg)
                return None, err_msg
                
    return creds, None

def get_drive_service():
    """Builds and returns the Google Drive API service"""
    creds, err = get_gdrive_credentials()
    if not creds:
        raise ValueError(err or "Google Drive authentication failed.")
    return build("drive", "v3", credentials=creds)

def get_or_create_folder(service, folder_name: str, parent_id: Optional[str] = None) -> str:
    """
    Finds a folder in Google Drive by name and parent. Creates it if not found.
    Uses local cache to avoid redundant API queries.
    """
    cache_key = (parent_id, folder_name)
    if cache_key in _folder_id_cache:
        return _folder_id_cache[cache_key]
        
    # Build search query
    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    else:
        query += " and 'root' in parents"
        
    try:
        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            orderBy="createdTime desc",
            pageSize=1
        ).execute()
        
        files = results.get("files", [])
        if files:
            folder_id = files[0]["id"]
            logger.debug(f"Found existing Google Drive folder: '{folder_name}' (ID: {folder_id})")
            _folder_id_cache[cache_key] = folder_id
            return folder_id
            
        # Not found, create new folder
        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder"
        }
        if parent_id:
            folder_metadata["parents"] = [parent_id]
            
        folder = service.files().create(body=folder_metadata, fields="id").execute()
        folder_id = folder.get("id")
        logger.info(f"Created new Google Drive folder: '{folder_name}' (ID: {folder_id})")
        _folder_id_cache[cache_key] = folder_id
        return folder_id
        
    except HttpError as e:
        logger.error(f"Google Drive API error in get_or_create_folder for '{folder_name}': {e}")
        raise

def upload_file_to_drive(service, local_path: str, category: str) -> str:
    """
    Uploads a local file to the categorized subdirectory inside "Mehndi Collection".
    Returns the uploaded file ID.
    """
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(f"Local file not found: {local_path}")
        
    # 1. Resolve parent directory hierarchy in Google Drive
    # Root "Mehndi Collection"
    parent_id = settings.gdrive_parent_folder_id
    root_folder_id = get_or_create_folder(service, "Mehndi Collection", parent_id=parent_id or None)
    
    # Category folder (e.g. "Bridal") under "Mehndi Collection"
    category_folder_id = get_or_create_folder(service, category, parent_id=root_folder_id)
    
    # 2. Prepare upload metadata
    file_metadata = {
        "name": path.name,
        "parents": [category_folder_id]
    }
    
    # Simple mime detection
    mimetype = "image/jpeg"
    if path.suffix.lower() == ".png":
        mimetype = "image/png"
    elif path.suffix.lower() == ".webp":
        mimetype = "image/webp"
        
    media = MediaFileUpload(str(path), mimetype=mimetype, resumable=True)
    
    # 3. Perform upload with retries
    max_retries = settings.max_retries
    for attempt in range(1, max_retries + 1):
        try:
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id"
            ).execute()
            
            file_id = file.get("id")
            logger.info(f"Successfully uploaded {path.name} to Drive folder '{category}' (File ID: {file_id})")
            return file_id
        except (HttpError, IOError) as e:
            logger.warning(f"Upload attempt {attempt} failed for {path.name}: {e}")
            if attempt < max_retries:
                time.sleep(attempt * 2.0)  # Backoff
            else:
                raise
                
    raise RuntimeError(f"Failed to upload {path.name} after {max_retries} attempts.")

def sync_pending_uploads(
    run_id: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Tuple[int, int]:
    """
    Queries database for downloaded but un-uploaded images, scans them for copyright/branding,
    and uploads or flags them accordingly.
    Returns: (successful_uploads, failed_uploads)
    """
    pending = db.get_pending_uploads()
    if not pending:
        logger.info("No pending uploads found.")
        return 0, 0
        
    total = len(pending)
    uploaded = 0
    failed = 0
    
    logger.info(f"Syncing {total} pending uploads to Google Drive.")
    
    try:
        service = get_drive_service()
    except Exception as e:
        logger.error(f"Google Drive service initialization failed: {e}")
        if progress_callback:
            progress_callback(0, total, f"Authentication failed: {e}")
        return 0, total

    # Instantiate scanner if integration is available
    scanner = None
    scanner_gdrive = None
    if _scanner_available:
        try:
            logger.info("Initializing Mehndi Scanner Agent for upload validation...")
            scanner = MehndiScanner()
            scanner_gdrive = ScannerGDriveClient()
        except Exception as se:
            logger.warning(f"Could not initialize scanner objects, continuing without scanning: {se}")

    for i, img in enumerate(pending):
        pin_id = img["pin_id"]
        local_path = img["local_path"]
        category = img["category"]
        
        path_obj = Path(local_path)
        if not path_obj.exists():
            logger.warning(f"Local file does not exist, skipping: {local_path}")
            continue
            
        try:
            # Run copyright scanning if available
            if _scanner_available and scanner and scanner_gdrive:
                logger.info(f"Scanning '{path_obj.name}' for copyright/branding...")
                result = scanner.scan_image(path_obj)
                
                if result.get("is_duplicate", False):
                    logger.info(f"Pin {pin_id} is a duplicate. Skipping upload.")
                    db.update_download_status(pin_id, "DUPLICATE")
                    db.update_upload_status(pin_id, "DUPLICATE")
                    db.update_sync_progress(run_id, duplicates_skipped=1)
                    continue
                    
                decision = result.get("decision", "APPROVED")
                confidence = result.get("confidence", 0.0)
                reason = result.get("reason", "No details")
                
                if decision == "APPROVED":
                    # Safe to upload to normal folder
                    file_id = upload_file_to_drive(service, local_path, category)
                    db.update_upload_status(pin_id, "UPLOADED", gdrive_file_id=file_id)
                    db.update_sync_progress(run_id, uploads_successful=1)
                    
                    # Record approval in scanner database
                    md5_val, phash_val = scanner.get_image_hashes(path_obj)
                    scanner_db.add_scanned_image(
                        gdrive_file_id=file_id,
                        filename=path_obj.name,
                        phash=phash_val,
                        md5=md5_val,
                        status="APPROVED",
                        confidence_score=confidence,
                        detected_text=reason,
                        detected_brands=result.get("detected_brands", {}),
                        decision="APPROVED",
                        reason=reason,
                        action_taken="MOVED_TO_APPROVED"
                    )
                    uploaded += 1
                    logger.info(f"Pin {pin_id} approved. Uploaded normally.")
                    
                elif decision == "REVIEW":
                    # Upload original/annotated to Review folder in Drive
                    upload_path = Path(result["annotated_path"]) if result.get("annotated_path") else path_obj
                    file_id = scanner_gdrive.upload_file_to_folder(upload_path, "review")
                    
                    if file_id:
                        db.update_upload_status(pin_id, "REVIEW", gdrive_file_id=file_id)
                        
                        # Record in scanner database
                        md5_val, phash_val = scanner.get_image_hashes(path_obj)
                        scanner_db.add_scanned_image(
                            gdrive_file_id=file_id,
                            filename=path_obj.name,
                            phash=phash_val,
                            md5=md5_val,
                            status="REVIEW",
                            confidence_score=confidence,
                            detected_text=reason,
                            detected_brands=result.get("detected_brands", {}),
                            decision="REVIEW",
                            reason=reason,
                            annotated_path=result.get("annotated_path"),
                            action_taken="MOVED_TO_REVIEW"
                        )
                        
                        # Send alert notification
                        msg = f"⚠️ <b>Branding Review Alert (Pinterest Upload)</b>\nImage: <code>{path_obj.name}</code>\nCategory: <b>{category}</b>\nConfidence: <b>{int(confidence)}%</b>\nReason: {reason}"
                        AlertNotifier.send_alert(msg, subject=f"Mehndi Scanner Alert: {path_obj.name}")
                        logger.info(f"Pin {pin_id} flagged for REVIEW and moved to GDrive review folder.")
                    else:
                        raise RuntimeError("Failed to upload flagged image to Google Drive review folder.")
                        
                elif decision == "REJECTED":
                    # Upload annotated/original to Rejected folder in Drive
                    upload_path = Path(result["annotated_path"]) if result.get("annotated_path") else path_obj
                    file_id = scanner_gdrive.upload_file_to_folder(upload_path, "rejected")
                    
                    if file_id:
                        db.update_upload_status(pin_id, "REJECTED", gdrive_file_id=file_id)
                        
                        # Record in scanner database
                        md5_val, phash_val = scanner.get_image_hashes(path_obj)
                        scanner_db.add_scanned_image(
                            gdrive_file_id=file_id,
                            filename=path_obj.name,
                            phash=phash_val,
                            md5=md5_val,
                            status="REJECTED",
                            confidence_score=confidence,
                            detected_text=reason,
                            detected_brands=result.get("detected_brands", {}),
                            decision="REJECTED",
                            reason=reason,
                            annotated_path=result.get("annotated_path"),
                            action_taken="MOVED_TO_REJECTED"
                        )
                        
                        # Send alert notification
                        msg = f"🚨 <b>Image Copyright Rejected (Pinterest Upload)</b>\nImage: <code>{path_obj.name}</code>\nCategory: <b>{category}</b>\nConfidence: <b>{int(confidence)}%</b>\nReason: {reason}"
                        AlertNotifier.send_alert(msg, subject=f"Mehndi Copyright Rejected: {path_obj.name}")
                        logger.info(f"Pin {pin_id} REJECTED and moved to GDrive rejected folder.")
                    else:
                        raise RuntimeError("Failed to upload flagged image to Google Drive rejected folder.")
            else:
                # Fallback to standard upload
                file_id = upload_file_to_drive(service, local_path, category)
                db.update_upload_status(pin_id, "UPLOADED", gdrive_file_id=file_id)
                db.update_sync_progress(run_id, uploads_successful=1)
                uploaded += 1
                logger.info(f"Pin {pin_id} uploaded normally (no scanner check).")
                
        except Exception as e:
            err_msg = str(e)
            logger.error(f"Failed to upload pin {pin_id}: {err_msg}")
            db.update_upload_status(pin_id, "FAILED", error_message=err_msg)
            db.update_sync_progress(run_id, uploads_failed=1)
            failed += 1
            
        if progress_callback:
            progress_callback(i + 1, total, f"Processed {i+1}/{total} uploads...")
            
    logger.info(f"Sync complete. Uploaded/Processed: {uploaded}, Failed: {failed}")
    return uploaded, failed

def download_db_from_drive(service) -> bool:
    """
    Downloads mehndi_agent.db from Mehndi Collection root folder on Google Drive
    and overwrites the local database.
    """
    try:
        parent_id = settings.gdrive_parent_folder_id
        root_folder_id = get_or_create_folder(service, "Mehndi Collection", parent_id=parent_id or None)
        
        # Search for mehndi_agent.db in the root folder
        query = f"name = 'mehndi_agent.db' and '{root_folder_id}' in parents and trashed = false"
        results = service.files().list(
            q=query, 
            spaces="drive", 
            fields="files(id, name)",
            orderBy="modifiedTime desc"
        ).execute()
        files = results.get("files", [])
        
        if not files:
            logger.info("Database file 'mehndi_agent.db' not found on Google Drive. Will create a new one upon upload.")
            return False
            
        file_id = files[0]["id"]
        db_path = settings.db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Download file content
        from googleapiclient.http import MediaIoBaseDownload
        import io
        
        request = service.files().get_media(fileId=file_id)
        with io.FileIO(db_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
                
        logger.info("Successfully downloaded database 'mehndi_agent.db' from Google Drive.")
        return True
    except Exception as e:
        logger.error(f"Error downloading database from Google Drive: {e}")
        return False

def upload_db_to_drive(service) -> bool:
    """
    Uploads the local mehndi_agent.db file to Mehndi Collection root folder on Google Drive,
    overwriting the existing one if present.
    """
    try:
        db_path = settings.db_path
        if not db_path.exists():
            logger.warning("Local database file does not exist. Cannot upload.")
            return False
            
        parent_id = settings.gdrive_parent_folder_id
        root_folder_id = get_or_create_folder(service, "Mehndi Collection", parent_id=parent_id or None)
        
        # Search if the file already exists on Drive
        query = f"name = 'mehndi_agent.db' and '{root_folder_id}' in parents and trashed = false"
        results = service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
        files = results.get("files", [])
        
        media = MediaFileUpload(str(db_path), mimetype="application/x-sqlite3", resumable=True)
        
        if files:
            # Update existing file
            file_id = files[0]["id"]
            file = service.files().update(fileId=file_id, media_body=media).execute()
            logger.info(f"Successfully updated database 'mehndi_agent.db' on Google Drive (ID: {file_id}).")
        else:
            # Create new file
            file_metadata = {
                "name": "mehndi_agent.db",
                "parents": [root_folder_id]
            }
            file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
            file_id = file.get("id")
            logger.info(f"Successfully created database 'mehndi_agent.db' on Google Drive (ID: {file_id}).")
            
        return True
    except Exception as e:
        logger.error(f"Error uploading database to Google Drive: {e}")
        return False


