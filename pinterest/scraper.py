import asyncio
import json
import logging
import re
import urllib.parse
from pathlib import Path
from typing import List, Dict, Optional, Callable
from playwright.async_api import async_playwright, Page
from config.settings import settings
from database.db_helper import db

logger = logging.getLogger("mehndi_agent.scraper")

# --- Stealth Configuration ---
# Use a recent, realistic Chrome user agent
STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

STEALTH_INIT_SCRIPT = """() => {
    // Hide webdriver flag
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    // Fake plugins array
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
    // Fake languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
    // Add chrome object
    window.chrome = { runtime: {} };
    // Override permissions query
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters);
}"""

MODAL_DISMISS_SCRIPT = """() => {
    // Remove login/signup modals from DOM
    const selectors = [
        'div[role="dialog"]',
        'div[class*="SignupModal"]',
        'div[class*="UnauthBanner"]',
        'div[data-test-id="fullPageSignupModal"]',
        'div[data-test-id="loginModal"]',
    ];
    selectors.forEach(sel => {
        document.querySelectorAll(sel).forEach(el => el.remove());
    });
    
    // Remove fixed/absolute overlays that contain login text
    Array.from(document.querySelectorAll('div')).forEach(el => {
        const s = window.getComputedStyle(el);
        if (s.position === 'fixed' && parseInt(s.zIndex) > 5) {
            const text = el.innerText || '';
            if (text.includes('Log in') || text.includes('Sign up')) {
                el.remove();
            }
        }
    });
    
    // Force-enable scrolling
    document.body.style.setProperty('overflow', 'auto', 'important');
    document.documentElement.style.setProperty('overflow', 'auto', 'important');
    document.body.style.setProperty('position', 'static', 'important');
}"""


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


# --- Extraction Strategy 1: DOM Parsing (Primary) ---

async def extract_pins_from_dom(page: Page, category: str) -> List[dict]:
    """Extract pin details by querying the rendered DOM."""
    extracted = []
    
    # Method A: Use pinWrapper selector
    try:
        wrappers = await page.query_selector_all("div[data-test-id='pinWrapper']")
        for wrapper in wrappers:
            img_elem = await wrapper.query_selector("img")
            if not img_elem:
                continue
            img_src = await img_elem.get_attribute("src")
            if not img_src or not img_src.startswith("https://"):
                continue
                
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

    # Method B: Fallback to all img tags with pinimg.com
    if not extracted:
        try:
            img_elems = await page.query_selector_all("img")
            for img_elem in img_elems:
                img_src = await img_elem.get_attribute("src")
                if not img_src or not img_src.startswith("https://") or "pinimg.com" not in img_src:
                    continue
                
                # Try to find pin link via JavaScript (more reliable than parent traversal)
                pin_id = await page.evaluate("""(imgEl) => {
                    let node = imgEl;
                    for (let i = 0; i < 8; i++) {
                        node = node.parentElement;
                        if (!node) break;
                        // Check if this node is an anchor with /pin/
                        if (node.tagName === 'A' && node.href && node.href.includes('/pin/')) {
                            const match = node.href.match(/\\/pin\\/(\\d+)/);
                            return match ? match[1] : null;
                        }
                        // Check for anchor children
                        const anchor = node.querySelector('a[href*="/pin/"]');
                        if (anchor) {
                            const match = anchor.href.match(/\\/pin\\/(\\d+)/);
                            return match ? match[1] : null;
                        }
                    }
                    return null;
                }""", img_elem)
                
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

    # Deduplicate
    unique = {}
    for pin in extracted:
        unique[pin["pin_id"]] = pin
    return list(unique.values())


# --- Extraction Strategy 2: JavaScript Evaluation (Secondary) ---

async def extract_pins_via_js(page: Page, category: str) -> List[dict]:
    """Extract pins by running JavaScript directly in the page context.
    This is more reliable than Python-side DOM queries when the page structure varies."""
    try:
        pins_data = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();
            
            // Strategy A: Find all images from pinimg.com and walk up to find pin links
            const imgs = document.querySelectorAll('img[src*="pinimg.com"]');
            imgs.forEach(img => {
                const src = img.src;
                if (!src || !src.startsWith('https://')) return;
                
                let pinId = null;
                let node = img;
                for (let i = 0; i < 8; i++) {
                    node = node.parentElement;
                    if (!node) break;
                    
                    if (node.tagName === 'A' && node.href && node.href.includes('/pin/')) {
                        const m = node.href.match(/\\/pin\\/(\\d+)/);
                        if (m) { pinId = m[1]; break; }
                    }
                    const a = node.querySelector('a[href*="/pin/"]');
                    if (a) {
                        const m = a.href.match(/\\/pin\\/(\\d+)/);
                        if (m) { pinId = m[1]; break; }
                    }
                }
                
                // Fallback: use filename as ID
                if (!pinId) {
                    const parts = src.split('/');
                    const fname = parts[parts.length - 1];
                    pinId = fname.split('.')[0];
                }
                
                if (!seen.has(pinId)) {
                    seen.add(pinId);
                    // Upgrade to original quality
                    const origUrl = src.replace(/\\/\\d+x\\//, '/originals/');
                    results.push({ pin_id: pinId, original_url: origUrl });
                }
            });
            
            // Strategy B: Find all /pin/ links and look for images inside them
            if (results.length === 0) {
                const links = document.querySelectorAll('a[href*="/pin/"]');
                links.forEach(link => {
                    const m = link.href.match(/\\/pin\\/(\\d+)/);
                    if (!m) return;
                    const pinId = m[1];
                    if (seen.has(pinId)) return;
                    
                    const img = link.querySelector('img[src*="pinimg.com"]');
                    if (img) {
                        seen.add(pinId);
                        const origUrl = img.src.replace(/\\/\\d+x\\//, '/originals/');
                        results.push({ pin_id: pinId, original_url: origUrl });
                    }
                });
            }
            
            return results;
        }""")
        
        return [{"pin_id": p["pin_id"], "original_url": p["original_url"], "category": category} for p in pins_data]
    except Exception as e:
        logger.error(f"Error in JS pin extraction: {e}")
        return []


# --- Extraction Strategy 3: Network API Interception (Tertiary/Cloud Fallback) ---

def _extract_pins_from_json(data, pins_dict):
    """Recursively search API response JSON for pin objects."""
    if isinstance(data, dict):
        if "id" in data and "images" in data and isinstance(data["images"], dict):
            pin_id = str(data["id"])
            images = data["images"]
            for key in ["orig", "1200x", "736x", "564x", "474x", "236x"]:
                if key in images and isinstance(images[key], dict):
                    img_url = images[key].get("url", "")
                    if img_url and "pinimg.com" in img_url:
                        pins_dict[pin_id] = img_url
                        break
        for v in data.values():
            _extract_pins_from_json(v, pins_dict)
    elif isinstance(data, list):
        for item in data:
            _extract_pins_from_json(item, pins_dict)


# --- Main Scraper ---

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
    
    Uses a multi-strategy approach:
    1. DOM parsing (primary)
    2. JavaScript evaluation (secondary)
    3. Network API interception (cloud fallback)
    """
    logger.info(f"Starting Pinterest scraper for '{category}' (Keyword: '{keyword}') aiming for {target_count} images.")
    
    query_encoded = urllib.parse.quote(keyword)
    search_url = f"https://www.pinterest.com/search/pins/?q={query_encoded}"
    
    pins_found = {}
    api_captured_pins = {}  # Pins captured via network interception
    
    async def on_api_response(response):
        """Intercept Pinterest API responses to capture pin data."""
        try:
            url = response.url
            if "resource/BaseSearchResource" in url or "resource/BoardFeedResource" in url:
                if response.status == 200:
                    ct = response.headers.get("content-type", "")
                    if "json" in ct:
                        body = await response.text()
                        data = json.loads(body)
                        _extract_pins_from_json(data, api_captured_pins)
        except Exception:
            pass
    
    async with async_playwright() as p:
        # Launch browser with stealth flags
        try:
            browser = await p.chromium.launch(
                headless=settings.pinterest_headless,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ]
            )
        except Exception as launch_error:
            error_str = str(launch_error)
            if "Executable doesn't exist" in error_str or "playwright install" in error_str.lower():
                logger.info("Playwright chromium browser not found. Attempting auto-installation...")
                try:
                    import sys
                    import subprocess
                    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                    browser = await p.chromium.launch(
                        headless=settings.pinterest_headless,
                        args=[
                            "--disable-dev-shm-usage",
                            "--no-sandbox",
                            "--disable-blink-features=AutomationControlled",
                        ]
                    )
                except Exception as install_error:
                    logger.error(f"Failed to auto-install Playwright browser: {install_error}")
                    raise launch_error
            else:
                raise launch_error
        
        # Create context with stealth user agent and headers
        context = await browser.new_context(
            user_agent=STEALTH_USER_AGENT,
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Sec-CH-UA": '"Chromium";v="131", "Not_A Brand";v="24"',
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"',
            }
        )
        
        page = await context.new_page()
        
        # Inject stealth overrides (runs before any page script)
        try:
            await page.add_init_script(STEALTH_INIT_SCRIPT)
        except Exception as e:
            logger.warning(f"Could not register stealth init script: {e}")
        
        # Attach network interceptor for cloud fallback
        page.on("response", on_api_response)
            
        try:
            # Visit home page first to establish session cookies
            logger.info("Navigating to Pinterest home page first...")
            await page.goto("https://www.pinterest.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            
            # Dismiss any initial modal
            await page.evaluate(MODAL_DISMISS_SCRIPT)
            
            # Navigate to search page
            logger.info(f"Navigating to search page: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for the pin grid to appear
            try:
                await page.wait_for_selector(
                    "div[data-test-id='pinWrapper'], img[src*='pinimg.com']",
                    timeout=15000
                )
                logger.info("Pin grid detected in DOM.")
            except Exception:
                logger.warning("Pin grid not detected within timeout. Will try extraction anyway.")
            
            # Additional delay for dynamic rendering
            await page.wait_for_timeout(settings.pinterest_delay * 1000)
            
            scroll_attempts = 0
            max_scroll_no_new = 15
            no_new_count = 0
            
            while len(pins_found) < target_count and no_new_count < max_scroll_no_new:
                # Dismiss any login modal
                try:
                    await page.evaluate(MODAL_DISMISS_SCRIPT)
                except Exception:
                    pass
                
                # --- Multi-strategy extraction ---
                # Strategy 1: DOM parsing
                batch = await extract_pins_from_dom(page, category)
                
                # Strategy 2: JS evaluation (if DOM returned nothing)
                if not batch:
                    batch = await extract_pins_via_js(page, category)
                    if batch:
                        logger.info(f"JS extraction found {len(batch)} pins (DOM extraction returned 0).")
                
                # Strategy 3: Network API captured pins (if both above returned nothing)
                if not batch and api_captured_pins:
                    logger.info(f"Using {len(api_captured_pins)} pins from network API interception.")
                    for pin_id, img_url in api_captured_pins.items():
                        batch.append({
                            "pin_id": pin_id,
                            "original_url": get_original_url(img_url),
                            "category": category
                        })
                
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
                await page.evaluate("window.scrollBy(0, 1200)")
                await page.wait_for_timeout(settings.pinterest_delay * 1000)
                scroll_attempts += 1
                
            # If we found no pins, save a diagnostic screenshot and upload it to Google Drive
            if len(pins_found) == 0:
                try:
                    screenshot_path = settings.base_dir / "downloads" / f"scrape_error_{category}.png"
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(screenshot_path))
                    logger.info(f"Saved diagnostic screenshot of page to: {screenshot_path}")
                    
                    # Also save page HTML for debugging
                    try:
                        html_content = await page.content()
                        html_path = settings.base_dir / "downloads" / f"scrape_error_{category}.html"
                        html_path.write_text(html_content[:50000], encoding="utf-8")
                        logger.info(f"Saved diagnostic HTML ({len(html_content)} chars) to: {html_path}")
                    except Exception:
                        pass
                    
                    # Direct upload to Google Drive under 'Diagnostics' folder
                    try:
                        from gdrive import uploader
                        service = uploader.get_drive_service()
                        file_id = uploader.upload_file_to_drive(service, str(screenshot_path), "Diagnostics")
                        logger.info(f"Uploaded diagnostic screenshot to Google Drive. File ID: {file_id}")
                    except Exception as upload_err:
                        logger.warning(f"Could not upload diagnostic screenshot to Google Drive: {upload_err}")
                except Exception as ss_error:
                    logger.warning(f"Could not take/upload diagnostic screenshot: {ss_error}")

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
