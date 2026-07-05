import asyncio
import logging
import re
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional, Callable
from playwright.async_api import async_playwright, Page
from config.settings import settings
from database.db_helper import db

logger = logging.getLogger("mehndi_agent.scraper")

def get_original_url(thumb_url: str) -> str:
    """
    Convert a Pinterest thumbnail or medium-res image URL to its original high-res URL.
    Example:
    https://i.pinimg.com/236x/ab/cd/ef/abcdef.jpg -> https://i.pinimg.com/originals/ab/cd/ef/abcdef.jpg
    """
    pattern = r'/(\d+x)/'
    if re.search(pattern, thumb_url):
        return re.sub(pattern, '/originals/', thumb_url)
    return thumb_url

def extract_pin_id_from_url(url: str) -> Optional[str]:
    """Extract numeric pin ID from a pin detail URL /pin/123456789/"""
    match = re.search(r'/pin/(\d+)/?', url)
    if match:
        return match.group(1)
    return None

def extract_pin_id_from_img_url(img_url: str) -> str:
    """Fallback: extract filename hash as pin ID if link cannot be resolved"""
    # e.g., https://i.pinimg.com/originals/8e/5c/df/8e5cdf2f5fbcda217a1a3617b73de4b9.jpg
    parsed = urllib.parse.urlparse(img_url)
    filename = Path(parsed.path).stem
    return filename

async def extract_pins_from_page(page: Page, category: str) -> List[dict]:
    """Extract pin details from the current state of the page"""
    extracted = []
    
    # Try using the standard pinWrapper selector first
    try:
        wrappers = await page.query_selector_all("div[data-test-id='pinWrapper']")
        for wrapper in wrappers:
            img_elem = await wrapper.query_selector("img")
            if not img_elem:
                continue
            img_src = await img_elem.get_attribute("src")
            if not img_src or not img_src.startswith("https://"):
                continue
                
            # Try to find the link
            link_elem = await wrapper.query_selector("a")
            pin_id = None
            if link_elem:
                href = await link_elem.get_attribute("href")
                if href:
                    pin_id = extract_pin_id_from_url(href)
            
            if not pin_id:
                pin_id = extract_pin_id_from_img_url(img_src)
                
            original_url = get_original_url(img_src)
            extracted.append({
                "pin_id": pin_id,
                "original_url": original_url,
                "category": category
            })
    except Exception as e:
        logger.debug(f"Error extracting with pinWrapper selector: {e}")

    # Fallback if wrappers are empty or query failed
    if not extracted:
        try:
            img_elems = await page.query_selector_all("img")
            for img_elem in img_elems:
                img_src = await img_elem.get_attribute("src")
                if not img_src or not img_src.startswith("https://") or "pinimg.com" not in img_src:
                    continue
                
                # Walk up parent elements to find an anchor with a pin link
                parent = img_elem
                pin_id = None
                for _ in range(5):  # Traverse up to 5 levels
                    parent_node = await parent.evaluate_handle("node => node.parentElement")
                    if not parent_node or parent_node.is_element() is False:
                        break
                    
                    # Cast parent_node back to element handle to run query selector
                    # Note: We can evaluate if it has an anchor inside it or if it is an anchor
                    tag_name = await parent_node.evaluate("node => node.tagName.toLowerCase()")
                    if tag_name == "a":
                        href = await parent_node.evaluate("node => node.getAttribute('href')")
                        if href and "/pin/" in href:
                            pin_id = extract_pin_id_from_url(href)
                            break
                    else:
                        anchor = await parent_node.as_element().query_selector("a[href^='/pin/']")
                        if anchor:
                            href = await anchor.get_attribute("href")
                            if href:
                                pin_id = extract_pin_id_from_url(href)
                                break
                    parent = parent_node.as_element()
                
                if not pin_id:
                    pin_id = extract_pin_id_from_img_url(img_src)
                    
                original_url = get_original_url(img_src)
                extracted.append({
                    "pin_id": pin_id,
                    "original_url": original_url,
                    "category": category
                })
        except Exception as e:
            logger.error(f"Error in fallback pin extraction: {e}")

    # Deduplicate within this batch
    unique_extracted = {}
    for pin in extracted:
        unique_extracted[pin["pin_id"]] = pin
        
    return list(unique_extracted.values())

async def scrape_pinterest(
    keyword: str,
    category: str,
    target_count: int,
    run_id: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> List[dict]:
    """
    Search Pinterest for pins and return metadata of found pins.
    Saves metadata as PENDING in the SQLite database.
    """
    logger.info(f"Starting Pinterest scraper for '{category}' (Keyword: '{keyword}') aiming for {target_count} images.")
    
    query_encoded = urllib.parse.quote(keyword)
    search_url = f"https://www.pinterest.com/search/pins/?q={query_encoded}"
    
    pins_found = {}
    
    async with async_playwright() as p:
        # Launch browser (with auto-install fallback for cloud deployments)
        try:
            browser = await p.chromium.launch(
                headless=settings.pinterest_headless,
                args=["--disable-dev-shm-usage", "--no-sandbox"]
            )
        except Exception as launch_error:
            error_str = str(launch_error)
            if "Executable doesn't exist" in error_str or "playwright install" in error_str.lower():
                logger.info("Playwright chromium browser not found. Attempting auto-installation...")
                try:
                    import sys
                    import subprocess
                    # Run playwright install chromium
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                    # Attempt launch again
                    browser = await p.chromium.launch(
                        headless=settings.pinterest_headless,
                        args=["--disable-dev-shm-usage", "--no-sandbox"]
                    )
                except Exception as install_error:
                    logger.error(f"Failed to auto-install Playwright browser: {install_error}")
                    raise launch_error
            else:
                raise launch_error
        
        # Setup persistent context options (custom user agent to avoid bot detection)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        page = await context.new_page()
        
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            # Wait for content
            await page.wait_for_timeout(settings.pinterest_delay * 1000)
            
            scroll_attempts = 0
            max_scroll_no_new = 15  # Limit scrolls if we hit the bottom or rate limits
            no_new_count = 0
            
            while len(pins_found) < target_count and no_new_count < max_scroll_no_new:
                # Extract pins currently in DOM
                batch = await extract_pins_from_page(page, category)
                
                new_pins_in_batch = 0
                for pin in batch:
                    pin_id = pin["pin_id"]
                    
                    # Check database status to skip already processed pins
                    status = db.get_pin_status(pin_id)
                    if status in ("DOWNLOADED", "DUPLICATE"):
                        continue
                        
                    if pin_id not in pins_found:
                        pins_found[pin_id] = pin
                        new_pins_in_batch += 1
                        
                        # Add to SQLite DB as pending
                        db.insert_pending_image(pin_id, pin["original_url"], category)
                
                # Log progress
                logger.info(f"Scrape progress for '{category}': Found {len(pins_found)} / {target_count} pins.")
                db.update_sync_progress(run_id, images_found=new_pins_in_batch)
                
                if progress_callback:
                    progress_callback(len(pins_found), target_count, f"Scraped {len(pins_found)} pin URLs...")
                
                if new_pins_in_batch == 0:
                    no_new_count += 1
                else:
                    no_new_count = 0
                    
                if len(pins_found) >= target_count:
                    break
                    
                # Scroll down
                scroll_distance = 1000
                await page.evaluate(f"window.scrollBy(0, {scroll_distance})")
                await page.wait_for_timeout(settings.pinterest_delay * 1000)
                scroll_attempts += 1
                
            logger.info(f"Finished scraping '{category}'. Total unique pins found: {len(pins_found)}")
            if progress_callback:
                progress_callback(len(pins_found), target_count, f"Scraping complete. Found {len(pins_found)} pins.")
                
        except Exception as e:
            logger.error(f"Error during scraping '{category}': {e}", exc_info=True)
            if progress_callback:
                progress_callback(len(pins_found), target_count, f"Error encountered during scrape: {e}")
        finally:
            await context.close()
            await browser.close()
            
    return list(pins_found.values())
