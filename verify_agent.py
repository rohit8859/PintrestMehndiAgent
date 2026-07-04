import sys
import logging
from pathlib import Path
from PIL import Image
from io import BytesIO
import imagehash

# Configure simple logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("verify_agent")

def test_imports():
    logger.info("Testing library imports...")
    try:
        import playwright
        import googleapiclient
        import requests
        import streamlit
        import dotenv
        import schedule
        logger.info("✅ All core libraries imported successfully!")
        return True
    except ImportError as e:
        logger.error(f"❌ Library import failed: {e}")
        return False

def test_settings():
    logger.info("Testing configuration loading...")
    try:
        from config.settings import settings
        logger.info(f"Base Directory: {settings.base_dir}")
        logger.info(f"Database Path: {settings.db_path}")
        logger.info(f"Downloads Directory: {settings.downloads_dir}")
        logger.info(f"Pinterest Headless: {settings.pinterest_headless}")
        logger.info(f"Pinterest Delay: {settings.pinterest_delay}s")
        logger.info("Keywords configured:")
        for cat, kw in settings.get_keywords().items():
            logger.info(f"  - {cat} -> {kw}")
        logger.info("✅ Settings loaded successfully!")
        return True
    except Exception as e:
        logger.error(f"❌ Settings test failed: {e}")
        return False

def test_database():
    logger.info("Testing SQLite database operations...")
    try:
        from database.db_helper import db
        # 1. Test insertion
        test_pin = "test_pin_12345"
        test_url = "https://i.pinimg.com/originals/test_image.jpg"
        db.insert_pending_image(test_pin, test_url, "TestCategory")
        logger.info("  - Inserted test pending image successfully.")
        
        # 2. Test status check
        exists = db.check_pin_exists(test_pin)
        logger.info(f"  - Verified pin exists: {exists}")
        
        # 3. Test status update and hash deduplication
        dummy_hash = "f0f0f0f0f0f0f0f0"
        db.update_download_status(test_pin, "DOWNLOADED", local_path="downloads/test.jpg", image_hash=dummy_hash)
        logger.info("  - Updated download status to DOWNLOADED.")
        
        # 4. Check duplicate detection
        dup = db.check_hash_exists(dummy_hash)
        if dup and dup["pin_id"] == test_pin:
            logger.info("  - Duplicate check verified successfully!")
        else:
            raise ValueError(f"Duplicate check failed. Returned: {dup}")
            
        # 5. Get statistics
        stats = db.get_statistics()
        logger.info(f"  - Total records in DB: {stats['total']}")
        
        # Clean up test row
        with db._get_connection() as conn:
            conn.execute("DELETE FROM images WHERE pin_id = ?", (test_pin,))
            conn.commit()
        logger.info("  - Cleaned up test record.")
        logger.info("✅ SQLite Database verified successfully!")
        return True
    except Exception as e:
        logger.error(f"❌ Database test failed: {e}", exc_info=True)
        return False

def test_url_resolution():
    logger.info("Testing image URL resolution upgrades...")
    from pinterest.scraper import get_original_url
    
    test_urls = [
        ("https://i.pinimg.com/236x/8e/5c/df/8e5cdf2f5fbcda217a1a3617b73de4b9.jpg", 
         "https://i.pinimg.com/originals/8e/5c/df/8e5cdf2f5fbcda217a1a3617b73de4b9.jpg"),
        ("https://i.pinimg.com/736x/ab/cd/ef/abcdef.jpg", 
         "https://i.pinimg.com/originals/ab/cd/ef/abcdef.jpg"),
        ("https://i.pinimg.com/564x/11/22/33/112233.jpg", 
         "https://i.pinimg.com/originals/11/22/33/112233.jpg")
    ]
    
    success = True
    for thumb, expected in test_urls:
        actual = get_original_url(thumb)
        if actual == expected:
            logger.info(f"  - Upgraded {thumb.split('/')[-4]} size successfully.")
        else:
            logger.error(f"  - Upgrade failed for {thumb}. Expected {expected}, got {actual}")
            success = False
            
    if success:
        logger.info("✅ URL resolution upgrades verified successfully!")
    return success

def test_perceptual_hashing():
    logger.info("Testing Perceptual Hashing (pHash)...")
    try:
        from PIL import ImageDraw
        # Create a simple image with patterns/structures in memory
        img = Image.new('RGB', (100, 100), color='red')
        draw = ImageDraw.Draw(img)
        draw.rectangle([20, 20, 80, 80], fill='blue', outline='green')
        hash1 = imagehash.phash(img)
        
        # Create a slightly modified image (with one pixel changed)
        img2 = img.copy()
        img2.putpixel((50, 50), (255, 255, 0))  # Change one pixel to yellow
        hash2 = imagehash.phash(img2)
        
        # Checking hash distance (should be identical or very close)
        distance = hash1 - hash2
        logger.info(f"  - Hash 1: {hash1}")
        logger.info(f"  - Hash 2: {hash2}")
        logger.info(f"  - Hamming Distance: {distance}")
        
        if distance <= 4:
            logger.info("  - Hash similarity check passed!")
        else:
            raise ValueError(f"Hamming distance too large: {distance}")
            
        logger.info("✅ Perceptual Hashing verified successfully!")
        return True
    except Exception as e:
        logger.error(f"❌ Hashing test failed: {e}")
        return False

def run_all_tests():
    logger.info("=== STARTING AGENT VERIFICATION ===")
    
    t1 = test_imports()
    t2 = test_settings()
    t3 = test_database()
    t4 = test_url_resolution()
    t5 = test_perceptual_hashing()
    
    logger.markdown = False  # Avoid issue in normal console logs
    logger.info("=== VERIFICATION SUMMARY ===")
    logger.info(f"Core Imports:           {'PASS ✅' if t1 else 'FAIL ❌'}")
    logger.info(f"Configuration Settings: {'PASS ✅' if t2 else 'FAIL ❌'}")
    logger.info(f"SQLite Database:        {'PASS ✅' if t3 else 'FAIL ❌'}")
    logger.info(f"URL Resolution:         {'PASS ✅' if t4 else 'FAIL ❌'}")
    logger.info(f"Perceptual Hashing:     {'PASS ✅' if t5 else 'FAIL ❌'}")
    
    if all([t1, t2, t3, t4, t5]):
        logger.info("🎉 All verification tests PASSED! The agent is ready to run.")
        return True
    else:
        logger.error("❌ Some verification tests failed. Please check dependencies or errors above.")
        return False

if __name__ == "__main__":
    run_all_tests()
