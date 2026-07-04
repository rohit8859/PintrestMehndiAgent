import os
import logging
import time
import requests
from io import BytesIO
from pathlib import Path
from PIL import Image
import imagehash
from typing import List, Dict, Optional, Tuple, Callable
from config.settings import settings
from database.db_helper import db

logger = logging.getLogger("mehndi_agent.downloader")

def get_fallback_urls(original_url: str) -> List[str]:
    """Generate lower resolution fallbacks if the original high-resolution URL fails"""
    fallbacks = []
    if "/originals/" in original_url:
        # Fallback order: 736px width, 564px width
        fallbacks.append(original_url.replace("/originals/", "/736x/"))
        fallbacks.append(original_url.replace("/originals/", "/564x/"))
    return fallbacks

def download_image_file(url: str, max_retries: int = 3, delay: float = 1.0) -> Optional[bytes]:
    """Download image bytes with custom headers, retries, and rate limit delays"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.pinterest.com/"
    }
    
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                return response.content
            elif response.status_code == 404:
                logger.debug(f"404 error downloading from {url}")
                return None  # Direct to fallback or skip
            else:
                logger.warning(f"Failed download attempt {attempt} for {url}. Status: {response.status_code}")
        except requests.RequestException as e:
            logger.warning(f"Network error on attempt {attempt} for {url}: {e}")
            
        if attempt < max_retries:
            time.sleep(delay * attempt)  # Incremental backoff
            
    return None

def process_and_save_image(
    pin_id: str,
    original_url: str,
    category: str,
    image_bytes: bytes
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Validates image bytes, computes perceptual hash, checks for database duplicates,
    and saves to categorized disk folder if unique.
    Returns: (status, local_path, image_hash)
    """
    try:
        image = Image.open(BytesIO(image_bytes))
        image.verify()  # Verify image integrity
        
        # Must reopen because verify() closes the file pointer in PIL
        image = Image.open(BytesIO(image_bytes))
        
        # Calculate Perceptual Hash
        phash = str(imagehash.phash(image))
        
        # Check database for exact duplicate hash
        duplicate = db.check_hash_exists(phash)
        if duplicate:
            logger.info(f"Duplicate image detected via hash {phash} (Pin ID: {pin_id}, matches existing Pin ID: {duplicate['pin_id']})")
            return "DUPLICATE", None, phash
            
        # Determine output format and file extension
        ext = ".jpg"
        if image.format:
            ext = f".{image.format.lower()}"
            if ext == ".jpeg":
                ext = ".jpg"
                
        # Define saving directory: Mehndi Collection/<Category>/
        dest_dir = settings.downloads_dir / "Mehndi Collection" / category
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        file_name = f"{pin_id}{ext}"
        local_path = dest_dir / file_name
        
        # Save image to file
        with open(local_path, "wb") as f:
            f.write(image_bytes)
            
        logger.debug(f"Saved unique image locally: {local_path}")
        return "DOWNLOADED", str(local_path), phash
        
    except Exception as e:
        logger.error(f"Error processing image bytes for Pin ID {pin_id}: {e}")
        return "FAILED", None, None

def download_pin(pin_id: str, original_url: str, category: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Attempts to download a single pin image using original URL and fallbacks.
    Returns: (download_status, local_path, image_hash)
    """
    urls_to_try = [original_url] + get_fallback_urls(original_url)
    
    image_bytes = None
    last_tried_url = original_url
    
    for url in urls_to_try:
        last_tried_url = url
        image_bytes = download_image_file(url, max_retries=settings.max_retries, delay=settings.pinterest_delay)
        if image_bytes:
            break
            
    if not image_bytes:
        logger.error(f"All download options failed for Pin ID {pin_id}. Original URL: {original_url}")
        return "FAILED", None, None
        
    # Process image (verify, hash, save)
    status, local_path, phash = process_and_save_image(pin_id, last_tried_url, category, image_bytes)
    return status, local_path, phash

def download_batch_pins(
    pins: List[dict],
    run_id: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> Tuple[int, int, int]:
    """
    Downloads a batch of pins, deduplicates, and logs progress.
    Returns: (downloaded_count, duplicates_count, failed_count)
    """
    total = len(pins)
    downloaded = 0
    duplicates = 0
    failed = 0
    
    logger.info(f"Starting batch download of {total} pins.")
    
    for i, pin in enumerate(pins):
        pin_id = pin["pin_id"]
        original_url = pin["original_url"]
        category = pin["category"]
        
        # Double check status to avoid duplicate downloads
        db_status = db.get_pin_status(pin_id)
        if db_status in ("DOWNLOADED", "DUPLICATE"):
            logger.info(f"Pin {pin_id} already processed (status: {db_status}). Skipping download.")
            if db_status == "DOWNLOADED":
                downloaded += 1
            else:
                duplicates += 1
            
            if progress_callback:
                progress_callback(i + 1, total, f"Downloaded {downloaded}, duplicates {duplicates}, failed {failed}...")
            continue
            
        status, local_path, phash = download_pin(pin_id, original_url, category)
        
        # Update database with results
        if status == "DOWNLOADED":
            downloaded += 1
            db.update_download_status(pin_id, "DOWNLOADED", local_path=local_path, image_hash=phash)
            db.update_sync_progress(run_id, images_downloaded=1)
        elif status == "DUPLICATE":
            duplicates += 1
            db.update_download_status(pin_id, "DUPLICATE", image_hash=phash)
            db.update_sync_progress(run_id, duplicates_skipped=1)
        else:
            failed += 1
            db.update_download_status(pin_id, "FAILED", error_message="Failed downloading and parsing image bytes.")
            
        if progress_callback:
            progress_callback(i + 1, total, f"Downloaded {downloaded}, duplicates {duplicates}, failed {failed}...")
            
        # Small delay to prevent spamming Pinterest's CDN
        time.sleep(settings.pinterest_delay / 2.0)
        
    logger.info(f"Batch download complete. Saved: {downloaded}, Duplicates blocked: {duplicates}, Failed: {failed}")
    return downloaded, duplicates, failed
