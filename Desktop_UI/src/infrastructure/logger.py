"""
Production Logging Module for QuickStock JA
Provides rotating file logging with proper log levels and formatting.
"""

import logging
import os
import platform
from logging.handlers import RotatingFileHandler
from datetime import datetime


def get_log_dir():
    """Returns a writeable directory for log files across OS platforms."""
    if platform.system() == "Windows":
        path = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "QuickStockJA", "logs")
    else:
        path = os.path.expanduser("~/.local/share/QuickStockJA/logs")
    os.makedirs(path, exist_ok=True)
    return path


LOG_DIR = get_log_dir()
LOG_FILE = os.path.join(LOG_DIR, "quickstock.log")
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5  # Keep 5 rotated log files

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Set up a logger with rotating file handler and console output.

    Args:
        name: Name for the logger (typically __name__)
        level: Logging level (default: INFO)

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # Prevents warnings from bubbling up and printing twice

    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger

    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    simple_formatter = logging.Formatter(
        '%(levelname)s - %(message)s'
    )

    # Rotating file handler
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding='utf-8'
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(detailed_formatter)
        logger.addHandler(file_handler)
    except (PermissionError, OSError):
        # Fallback to current directory if log dir is not writable
        fallback_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"{name}.log")
        try:
            file_handler = RotatingFileHandler(
                fallback_file,
                maxBytes=MAX_LOG_SIZE,
                backupCount=BACKUP_COUNT,
                encoding='utf-8'
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(detailed_formatter)
            logger.addHandler(file_handler)
        except Exception:
            pass

    # Console handler (WARNING and above only)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(simple_formatter)
    logger.addHandler(console_handler)

    return logger


# Create module-level logger for easy import
logger = setup_logger("quickstock")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance by name.
    
    Args:
        name: Logger name
        
    Returns:
        Logger instance
    """
    return setup_logger(name)


def log_security_event(message: str, logger_instance: logging.Logger = None):
    """Log security-related events with SECURITY level."""
    if logger_instance is None:
        logger_instance = logger
    logger_instance.warning(f"[SECURITY] {message}")


def log_database_event(message: str, logger_instance: logging.Logger = None):
    """Log database-related events."""
    if logger_instance is None:
        logger_instance = logger
    logger_instance.info(f"[DATABASE] {message}")


def log_sync_event(message: str, logger_instance: logging.Logger = None):
    """Log sync-related events."""
    if logger_instance is None:
        logger_instance = logger
    logger_instance.info(f"[SYNC] {message}")


def log_api_event(message: str, logger_instance: logging.Logger = None):
    """Log API-related events."""
    if logger_instance is None:
        logger_instance = logger
    logger_instance.info(f"[API] {message}")


def log_user_event(message: str, logger_instance: logging.Logger = None):
    """Log user-related events."""
    if logger_instance is None:
        logger_instance = logger
    logger_instance.info(f"[USER] {message}")