"""Windows GUI entry point"""
import sys
import logging
from pathlib import Path


def setup_logging():
    """Configure logging for Windows GUI"""
    log_dir = Path.home() / ".meshtastic-bridge"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bridge.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()  # Also log to console
        ]
    )


def main():
    """Main entry point for Windows GUI"""
    setup_logging()

    logger = logging.getLogger(__name__)
    logger.info("=" * 60)
    logger.info("Meshtastic BLE Bridge - Windows GUI")
    logger.info("=" * 60)

    try:
        from gui.tray_app import TrayApplication

        app = TrayApplication()
        app.run()

    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        logger.error("Make sure all dependencies are installed:")
        logger.error("  pip install pystray pillow pywin32")
        sys.exit(1)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
