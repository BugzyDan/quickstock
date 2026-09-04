"""
Secure Cache Module for QuickStock JA
Provides encryption for sensitive local cache files using machine-specific keys.
"""

import os
import json
import platform
import uuid
from datetime import datetime

try:
    import base64
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    base64 = None
    Fernet = None
    PBKDF2HMAC = None
    hashes = None
    default_backend = None


def get_machine_id() -> str:
    """
    Get a unique identifier for the current machine.
    This is used to derive encryption keys that are machine-specific.
    """
    # Try multiple methods to get a stable machine ID
    machine_id = None
    
    # Method 1: Machine UUID (most reliable on most systems)
    try:
        machine_id = str(uuid.getnode())
    except Exception:
        pass
    
    # Method 2: Platform-specific identifiers
    if machine_id is None:
        try:
            if platform.system() == "Windows":
                # Use Windows machine GUID
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 
                                   r"SOFTWARE\Microsoft\Cryptography") as key:
                    machine_id = winreg.QueryValueEx(key, "MachineGuid")[0]
            elif platform.system() == "Darwin":
                # Use IOPlatformSerialNumber on macOS
                import subprocess
                result = subprocess.run(
                    ["ioreg", "-l", "-c", "IOPlatformExpertDevice"],
                    capture_output=True, text=True
                )
                for line in result.stdout.splitlines():
                    if "IOPlatformSerialNumber" in line:
                        machine_id = line.split('"')[-2]
                        break
            else:
                # Use machine-id on Linux
                for path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
                    if os.path.exists(path):
                        with open(path, "r") as f:
                            machine_id = f.read().strip()
                        break
        except Exception:
            pass
    
    # Fallback: Use hostname and username
    if machine_id is None:
        machine_id = f"{platform.node()}-{os.getlogin() if hasattr(os, 'getlogin') else os.getuid()}"
    
    return machine_id


def derive_key(machine_id: str, salt: bytes = None) -> tuple:
    """
    Derive an encryption key from the machine ID.
    Returns (key, salt) tuple.
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError(
            "SecureCache requires the 'cryptography' package. Install Desktop_UI requirements before storing credentials."
        )

    if salt is None:
        salt = os.urandom(16)

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend()
    )
    key = base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))
    return key, salt


class SecureCache:
    """
    A secure cache that encrypts sensitive data using machine-specific keys.
    """
    
    def __init__(self, cache_file: str):
        """
        Initialize the secure cache.
        
        Args:
            cache_file: Path to the cache file
        """
        self.cache_file = cache_file
        self.machine_id = get_machine_id()
        self._salt = None
        self._key = None
    
    def _get_key(self) -> bytes:
        """Get or derive the encryption key."""
        if self._key is None:
            # Try to load existing salt from cache file
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, "r") as f:
                        data = json.load(f)
                    if isinstance(data, dict) and "_salt" in data:
                        self._salt = bytes.fromhex(data["_salt"])
                except Exception:
                    pass
            
            self._key, self._salt = derive_key(self.machine_id, self._salt)
        
        return self._key
    
    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a plaintext string.
        
        Args:
            plaintext: The text to encrypt
            
        Returns:
            Encrypted text (base64 encoded)
        """
        key = self._get_key()

        f = Fernet(key)
        return f.encrypt(plaintext.encode()).decode()
    
    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt an encrypted string.
        
        Args:
            ciphertext: The encrypted text (base64 encoded)
            
        Returns:
            Decrypted plaintext
        """
        key = self._get_key()

        f = Fernet(key)
        return f.decrypt(ciphertext.encode()).decode()
    
    def save(self, data: dict) -> None:
        """
        Save data to the cache file with encryption for sensitive fields.
        
        Args:
            data: Dictionary containing data to save
        """
        # Fields that should be encrypted
        sensitive_fields = ["api_token", "password", "hash", "secret", "key"]
        
        encrypted_data = {}
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in sensitive_fields):
                if value:  # Only encrypt non-empty values
                    encrypted_data[key] = self.encrypt(str(value))
                else:
                    encrypted_data[key] = value
            else:
                encrypted_data[key] = value
        
        # Store salt for key derivation
        if self._salt:
            encrypted_data["_salt"] = self._salt.hex()
        encrypted_data["_encrypted"] = True
        encrypted_data["_timestamp"] = datetime.now().isoformat()
        
        # Write atomically
        temp_file = self.cache_file + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(encrypted_data, f, indent=2)
        os.replace(temp_file, self.cache_file)
    
    def load(self) -> dict:
        """
        Load and decrypt data from the cache file.
        
        Returns:
            Dictionary containing the decrypted data
        """
        if not os.path.exists(self.cache_file):
            return {}
        
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                encrypted_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
        
        if not isinstance(encrypted_data, dict):
            return {}
        
        # Check if data is encrypted
        if not encrypted_data.get("_encrypted", False):
            # Return as-is for backward compatibility
            return encrypted_data
        
        # Decrypt sensitive fields
        decrypted_data = {}
        sensitive_fields = ["api_token", "password", "hash", "secret", "key"]
        
        for key, value in encrypted_data.items():
            if key.startswith("_"):  # Skip metadata fields
                continue
            if any(sensitive in key.lower() for sensitive in sensitive_fields):
                if value:
                    try:
                        decrypted_data[key] = self.decrypt(value)
                    except Exception:
                        # If decryption fails, return None for this field
                        decrypted_data[key] = None
                else:
                    decrypted_data[key] = value
            else:
                decrypted_data[key] = value
        
        return decrypted_data
    
    def clear(self) -> None:
        """Remove the cache file."""
        if os.path.exists(self.cache_file):
            os.remove(self.cache_file)


# Convenience function for quick secure cache operations
def get_secure_cache(cache_file: str) -> SecureCache:
    """
    Get a SecureCache instance for the specified file.
    
    Args:
        cache_file: Path to the cache file
        
    Returns:
        SecureCache instance
    """
    return SecureCache(cache_file)
