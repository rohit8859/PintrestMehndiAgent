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
STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

STEALTH_INIT_SCRIPT = """() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
    window.chrome = { runtime: {} };
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(parameters);
}"""

MODAL_DISMISS_SCRIPT = """() => {
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
    Array.from(document.querySelectorAll('div')).forEach(el => {
        const s = window.getComputedStyle(el);
        if (s.position === 'fixed' && parseInt(s.zIndex) > 5) {
            const text = el.innerText || '';
            if (text.includes('Log in') || text.includes('Sign up')) {
                el.remove();
            }
        }
    });
    document.body.style.setProperty('overflow', 'auto', 'important');
    document.documentElement.style.setProperty('overflow', 'auto', 'important');
    document.body.style.setProperty('position', 'static', 'important');
}"""


def get_original_url(thumb_url: str) -> str:
    """Convert a Pinterest thumbnail URL to its original high-res URL."""
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
    parsed = urllib.parse.urlparse(img_url)
    filename = Path(parsed.path).stem
    return filename


# =============================================================================
# EXTRACTION STRATEGY 1: Comprehensive JavaScript (Primary)
# Checks src, srcset, data-src, background-image for ALL elements.
# =============================================================================

COMPREHENSIVE_JS_EXTRACT = """() => {
    const results = [];
    const seen = new Set();
    
    // Helper: Extract a pinimg URL from any element attribute or style
    function getImageUrl(el) {
        const attrs = ['src', 'srcset', 'data-src', 'data-original', 'data-pin-media'];
        for (const attr of attrs) {
            const val = el.getAttribute(attr);
            if (val && val.includes('pinimg.com')) {
                const match = val.match(/(https?:\\/\\/[^\\s,"']+pinimg\\.com\\/[^\\s,"']+)/);
                if (match) return match[1];
            }
        }
        // Check background-image CSS
        try {
            const bg = window.getComputedStyle(el).backgroundImage;
            if (bg && bg.includes('pinimg.com')) {
                const match = bg.match(/(https?:\\/\\/[^\\s)"']+pinimg\\.com\\/[^\\s)"']+)/);
                if (match) return match[1];
            }
        } catch(e) {}
        return null;
    }
    
    // Helper: Walk up DOM from an element to find a /pin/ link
    function findPinId(el) {
        let node = el;
        for (let i = 0; i < 10; i++) {
            if (!node) break;
            if (node.tagName === 'A') {
                const href = node.getAttribute('href') || '';
                const m = href.match(/\\/pin\\/(\\d+)/);
                if (m) return m[1];
            }
            // Check child anchor links
            if (node.querySelectorAll) {
                const links = node.querySelectorAll('a[href*="/pin/"]');
                for (const link of links) {
                    const m = (link.getAttribute('href') || '').match(/\\/pin\\/(\\d+)/);
                    if (m) return m[1];
                }
            }
            node = node.parentElement;
        }
        return null;
    }
    
    // Approach A: Start from /pin/ links, find images inside or nearby
    const pinLinks = document.querySelectorAll('a[href*="/pin/"]');
    pinLinks.forEach(link => {
        const href = link.getAttribute('href') || '';
        const m = href.match(/\\/pin\\/(\\d+)/);
        if (!m) return;
        const pinId = m[1];
        if (seen.has(pinId)) return;
        
        // Search for images inside the link itself
        let imgUrl = null;
        const imgs = link.querySelectorAll('img, video, source, div');
        for (const img of imgs) {
            imgUrl = getImageUrl(img);
            if (imgUrl) break;
        }
        
        // If not found inside the link, search the parent container
        if (!imgUrl) {
            let parent = link.parentElement;
            for (let i = 0; i < 3 && parent; i++) {
                const childImgs = parent.querySelectorAll('img, div[style*="background"]');
                for (const img of childImgs) {
                    imgUrl = getImageUrl(img);
                    if (imgUrl) break;
                }
                if (imgUrl) break;
                parent = parent.parentElement;
            }
        }
        
        if (imgUrl) {
            // Skip tiny avatars/logos
            if (imgUrl.includes('/30x30/') || imgUrl.includes('/24x24/') || imgUrl.includes('/20x20/')) return;
            seen.add(pinId);
            results.push({
                pin_id: pinId,
                img_url: imgUrl.replace(/\\/\\d+x\\//, '/originals/')
            });
        }
    });
    
    // Approach B: Start from images, find pin links above
    if (results.length === 0) {
        const allImgLike = document.querySelectorAll('img, div[style*="background"], video, source');
        allImgLike.forEach(el => {
            const imgUrl = getImageUrl(el);
            if (!imgUrl) return;
            if (imgUrl.includes('/30x30/') || imgUrl.includes('/24x24/') || imgUrl.includes('/20x20/')) return;
            
            const pinId = findPinId(el);
            if (!pinId || seen.has(pinId)) return;
            
            seen.add(pinId);
            results.push({
                pin_id: pinId,
                img_url: imgUrl.replace(/\\/\\d+x\\//, '/originals/')
            });
        });
    }
    
    // Debug info
    const debug = {
        totalImgs: document.querySelectorAll('img').length,
        pinimgInSrc: document.querySelectorAll('img[src*="pinimg"]').length,
        pinimgInSrcset: document.querySelectorAll('img[srcset*="pinimg"]').length,
        pinLinks: document.querySelectorAll('a[href*="/pin/"]').length,
        pinWrappers: document.querySelectorAll('div[data-test-id="pinWrapper"]').length,
        bodyTextSnippet: (document.body.innerText || '').substring(0, 200),
    };
    
    return { results, debug };
}"""


# =============================================================================
# EXTRACTION STRATEGY 2: Page Source Regex (Fallback for broken DOM)
# =============================================================================

def extract_pins_from_html_source(html: str, category: str) -> List[dict]:
    """Extract pins from raw HTML source using regex.
    Works even when DOM queries fail because it parses the source text directly."""
    
    pins = {}
    
    # Pinterest embeds pin data as JSON in the page. Look for pin objects
    # Pattern: find pairs of pin_id and image URL near each other in the HTML
    
    # Find all unique pin IDs
    pin_ids = list(set(re.findall(r'/pin/(\d{10,})/', html)))
    
    # Find all pinimg image URLs (excluding tiny avatars)
    all_img_urls = re.findall(
        r'(https://i\.pinimg\.com/(?:originals|\d+x)/[a-f0-9/]+\.[a-z]{3,4})',
        html
    )
    # Filter out tiny thumbnails (avatars, logos)
    img_urls = [u for u in all_img_urls if '/30x30/' not in u and '/24x24/' not in u and '/20x20/' not in u]
    unique_img_urls = list(dict.fromkeys(img_urls))  # preserve order, remove dupes
    
    logger.info(f"HTML source regex: {len(pin_ids)} pin IDs, {len(unique_img_urls)} image URLs")
    
    if not pin_ids or not unique_img_urls:
        return []
    
    # Strategy: For each pin ID, find the closest image URL in the HTML
    for pin_id in pin_ids:
        if pin_id in pins:
            continue
            
        # Find position of this pin ID in the HTML
        pin_pattern = f'/pin/{pin_id}/'
        pin_pos = html.find(pin_pattern)
        if pin_pos == -1:
            continue
        
        # Find the closest pinimg URL to this position
        best_url = None
        best_dist = float('inf')
        for img_url in unique_img_urls:
            img_pos = html.find(img_url, max(0, pin_pos - 5000))
            if img_pos == -1:
                img_pos = html.rfind(img_url, 0, pin_pos + 5000)
            if img_pos == -1:
                continue
            dist = abs(img_pos - pin_pos)
            if dist < best_dist:
                best_dist = dist
                best_url = img_url
        
        if best_url and best_dist < 5000:  # Only pair if reasonably close
            pins[pin_id] = {
                "pin_id": pin_id,
                "original_url": get_original_url(best_url),
                "category": category,
            }
    
    return list(pins.values())


# =============================================================================
# EXTRACTION STRATEGY 3: Network API Interception
# =============================================================================

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


# =============================================================================
# MAIN SCRAPER
# =============================================================================

async def scrape_pinterest(
    keyword: str,
    category: str,
    target_count: int,
    run_id: str,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> List[dict]:
    """
    Search Pinterest for pins and return metadata of found pins.
    Uses a multi-strategy extraction approach for maximum reliability.
    """
    logger.info(f"Starting Pinterest scraper for '{category}' (Keyword: '{keyword}') aiming for {target_count} images.")
    
    query_encoded = urllib.parse.quote(keyword)
    search_url = f"https://www.pinterest.com/search/pins/?q={query_encoded}"
    
    pins_found = {}
    api_captured_pins = {}
    
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
        
        try:
            await page.add_init_script(STEALTH_INIT_SCRIPT)
        except Exception as e:
            logger.warning(f"Could not register stealth init script: {e}")
        
        page.on("response", on_api_response)
            
        try:
            logger.info("Navigating to Pinterest home page first...")
            await page.goto("https://www.pinterest.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)
            
            try:
                await page.evaluate(MODAL_DISMISS_SCRIPT)
            except Exception:
                pass
            
            logger.info(f"Navigating to search page: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            
            # Wait for content to load
            try:
                await page.wait_for_selector(
                    "div[data-test-id='pinWrapper'], img[src*='pinimg.com'], a[href*='/pin/']",
                    timeout=15000
                )
                logger.info("Pin grid detected in DOM.")
            except Exception:
                logger.warning("Pin grid not detected within timeout. Will try extraction anyway.")
            
            await page.wait_for_timeout(settings.pinterest_delay * 1000)
            
            scroll_attempts = 0
            max_scroll_no_new = 15
            no_new_count = 0
            html_source_tried = False
            
            while len(pins_found) < target_count and no_new_count < max_scroll_no_new:
                try:
                    await page.evaluate(MODAL_DISMISS_SCRIPT)
                except Exception:
                    pass
                
                # --- STRATEGY 1: Comprehensive JavaScript Extraction ---
                batch = []
                try:
                    js_result = await page.evaluate(COMPREHENSIVE_JS_EXTRACT)
                    debug = js_result.get("debug", {})
                    raw_pins = js_result.get("results", [])
                    
                    if scroll_attempts == 0:
                        logger.info(
                            f"Page DOM: imgs={debug.get('totalImgs', '?')}, "
                            f"pinimg_src={debug.get('pinimgInSrc', '?')}, "
                            f"pinimg_srcset={debug.get('pinimgInSrcset', '?')}, "
                            f"pin_links={debug.get('pinLinks', '?')}, "
                            f"wrappers={debug.get('pinWrappers', '?')}"
                        )
                    
                    batch = [
                        {"pin_id": p["pin_id"], "original_url": p["img_url"], "category": category}
                        for p in raw_pins
                    ]
                    if batch:
                        logger.debug(f"JS extraction found {len(batch)} pins in this batch.")
                except Exception as e:
                    logger.error(f"JS extraction error: {e}")
                
                # --- STRATEGY 2: Page Source Regex (if JS found nothing) ---
                if not batch and not html_source_tried:
                    html_source_tried = True
                    try:
                        html = await page.content()
                        batch = extract_pins_from_html_source(html, category)
                        if batch:
                            logger.info(f"HTML source regex found {len(batch)} pins.")
                    except Exception as e:
                        logger.error(f"HTML source extraction error: {e}")
                
                # --- STRATEGY 3: Network API Captured Pins ---
                if not batch and api_captured_pins:
                    logger.info(f"Using {len(api_captured_pins)} pins from network API interception.")
                    for pin_id, img_url in api_captured_pins.items():
                        batch.append({
                            "pin_id": pin_id,
                            "original_url": get_original_url(img_url),
                            "category": category
                        })
                
                # --- Process Batch ---
                new_pins_in_batch = 0
                skipped_existing = 0
                for pin in batch:
                    pin_id = pin["pin_id"]
                    
                    status = db.get_pin_status(pin_id)
                    if status in ("DOWNLOADED", "DUPLICATE"):
                        skipped_existing += 1
                        continue
                        
                    if pin_id not in pins_found:
                        pins_found[pin_id] = pin
                        new_pins_in_batch += 1
                        db.insert_pending_image(pin_id, pin["original_url"], category)
                
                logger.info(
                    f"Scrape progress for '{category}': Found {len(pins_found)} / {target_count} pins. "
                    f"(batch={len(batch)}, new={new_pins_in_batch}, skipped={skipped_existing})"
                )
                db.update_sync_progress(run_id, images_found=new_pins_in_batch)
                
                if progress_callback:
                    progress_callback(len(pins_found), target_count, f"Scraped {len(pins_found)} pin URLs...")
                
                if new_pins_in_batch == 0:
                    no_new_count += 1
                else:
                    no_new_count = 0
                    
                if len(pins_found) >= target_count:
                    break
                    
                await page.evaluate("window.scrollBy(0, 1200)")
                await page.wait_for_timeout(settings.pinterest_delay * 1000)
                scroll_attempts += 1
                
            # --- FALLBACK: Related Pins Traversal (if scroll limit hit before target_count) ---
            if len(pins_found) < target_count and len(pins_found) > 0:
                logger.info(f"Target count {target_count} not reached (currently at {len(pins_found)}). Initiating related pins traversal...")
                if progress_callback:
                    progress_callback(len(pins_found), target_count, f"Initiating related pins traversal (currently at {len(pins_found)})...")
                
                # Keep a list of seed pin IDs to visit
                seed_pin_ids = list(pins_found.keys())
                
                for seed_id in seed_pin_ids:
                    if len(pins_found) >= target_count:
                        break
                        
                    detail_url = f"https://www.pinterest.com/pin/{seed_id}/"
                    logger.info(f"Navigating to seed pin detail page: {detail_url}")
                    if progress_callback:
                        progress_callback(len(pins_found), target_count, f"Fetching related pins from pin {seed_id}...")
                    
                    try:
                        await page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
                        await page.wait_for_timeout(1000)
                        # Scroll down multiple times to trigger related pins lazy loading
                        for _ in range(3):
                            await page.evaluate("window.scrollBy(0, 1500)")
                            await page.wait_for_timeout(1500)
                        
                        # Dismiss modals if any
                        try:
                            await page.evaluate(MODAL_DISMISS_SCRIPT)
                        except Exception:
                            pass
                        
                        # Extract pins from this detail page
                        batch = []
                        # Try JS extraction first
                        try:
                            js_result = await page.evaluate(COMPREHENSIVE_JS_EXTRACT)
                            raw_pins = js_result.get("results", [])
                            batch = [
                                {"pin_id": p["pin_id"], "original_url": p["img_url"], "category": category}
                                for p in raw_pins
                            ]
                        except Exception as e:
                            logger.debug(f"JS extraction error on pin detail: {e}")
                            
                        # Try regex fallback if JS empty
                        if not batch:
                            try:
                                html = await page.content()
                                batch = extract_pins_from_html_source(html, category)
                            except Exception as e:
                                logger.debug(f"Source regex error on pin detail: {e}")
                                
                        new_pins_in_batch = 0
                        skipped_existing = 0
                        for pin in batch:
                            pin_id = pin["pin_id"]
                            
                            status = db.get_pin_status(pin_id)
                            if status in ("DOWNLOADED", "DUPLICATE"):
                                skipped_existing += 1
                                continue
                                
                            if pin_id not in pins_found:
                                pins_found[pin_id] = pin
                                new_pins_in_batch += 1
                                db.insert_pending_image(pin_id, pin["original_url"], category)
                                
                        logger.info(
                            f"Related pins from pin {seed_id}: Found {len(pins_found)} / {target_count} pins. "
                            f"(batch={len(batch)}, new={new_pins_in_batch}, skipped={skipped_existing})"
                        )
                        db.update_sync_progress(run_id, images_found=new_pins_in_batch)
                        
                        if progress_callback:
                            progress_callback(len(pins_found), target_count, f"Scraped {len(pins_found)} pin URLs...")
                            
                        await page.wait_for_timeout(1000)  # Human-like delay
                    except Exception as detail_err:
                        logger.warning(f"Failed to fetch related pins for {seed_id}: {detail_err}")

            # If no pins found, save diagnostics
            if len(pins_found) == 0:
                try:
                    screenshot_path = settings.base_dir / "downloads" / f"scrape_error_{category}.png"
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(screenshot_path))
                    logger.info(f"Saved diagnostic screenshot to: {screenshot_path}")
                    
                    try:
                        html_content = await page.content()
                        html_path = settings.base_dir / "downloads" / f"scrape_error_{category}.html"
                        html_path.write_text(html_content[:100000], encoding="utf-8")
                        logger.info(f"Saved diagnostic HTML ({len(html_content)} chars) to: {html_path}")
                    except Exception:
                        pass
                    
                    try:
                        from gdrive import uploader
                        service = uploader.get_drive_service()
                        file_id = uploader.upload_file_to_drive(service, str(screenshot_path), "Diagnostics")
                        logger.info(f"Uploaded diagnostic screenshot to Google Drive. File ID: {file_id}")
                        # Also upload the HTML for analysis
                        if html_path.exists():
                            html_file_id = uploader.upload_file_to_drive(service, str(html_path), "Diagnostics")
                            logger.info(f"Uploaded diagnostic HTML to Google Drive. File ID: {html_file_id}")
                    except Exception as upload_err:
                        logger.warning(f"Could not upload diagnostics to Google Drive: {upload_err}")
                except Exception as ss_error:
                    logger.warning(f"Could not save diagnostics: {ss_error}")

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
