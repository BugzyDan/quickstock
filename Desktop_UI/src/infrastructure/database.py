"""
Database infrastructure module for QuickStock JA.
Provides database connection management and utilities.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# Try to import mysql-connector
try:
    import mysql.connector
    from mysql.connector import Error, pooling
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False
    mysql = None
    Error = None
    pooling = None

load_dotenv()


class DatabaseManager:
    """
    Centralized database connection management.
    
    This class provides a singleton-style database manager that handles
    connection pooling, query execution, and error handling.
    """
    
    _instance = None
    _pool = None
    
    def __new__(cls):
        """Singleton pattern to ensure only one database manager exists."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the database manager."""
        if self._initialized:
            return
        
        self._pool_name = "quickstock_db_pool"
        self._pool_size = int(os.getenv("DB_POOL_SIZE", 5))
        self._is_connected = False
        
        # Configuration from environment
        self.host = os.getenv("DB_HOST", "localhost")
        self.port = int(os.getenv("DB_PORT", 3306))
        self.user = os.getenv("DB_USER", "root")
        self.password = os.getenv("DB_PASSWORD", "")
        self.database = os.getenv("DB_NAME", "quickstock")
        
        self._initialized = True
    
    def connect(self) -> Tuple[bool, str]:
        """
        Establish database connection pool.
        
        Returns:
            Tuple of (success, message)
        """
        if not MYSQL_AVAILABLE:
            return False, "MySQL connector not installed. Run: pip install mysql-connector-python"
        
        try:
            DatabaseManager._pool = pooling.MySQLConnectionPool(
                pool_name=self._pool_name,
                pool_size=self._pool_size,
                pool_reset_session=True,
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                connection_timeout=10,
            )
            
            # Test connection
            test_conn = DatabaseManager._pool.get_connection()
            if test_conn and test_conn.is_connected():
                test_conn.close()
                self._is_connected = True
                return True, "Connected successfully"
            
            return False, "Failed to establish connection"
            
        except Error as e:
            return False, f"MySQL Error: {e}"
        except Exception as e:
            return False, f"Unexpected error: {e}"
    
    def get_connection(self) -> Optional[Any]:
        """
        Get a connection from the pool.
        
        Returns:
            MySQL connection or None
        """
        if not self._is_connected:
            success, _ = self.connect()
            if not success:
                return None
        
        try:
            return DatabaseManager._pool.get_connection()
        except Error:
            return None
    
    def execute(
        self,
        query: str,
        params: Optional[Tuple] = None,
        fetch: bool = True,
        commit: bool = False,
    ) -> Tuple[bool, str, Optional[List[Dict]]]:
        """
        Execute a database query.
        
        Args:
            query: SQL query string
            params: Query parameters
            fetch: If True, fetch results
            commit: If True, commit transaction
            
        Returns:
            Tuple of (success, message, results)
        """
        conn = self.get_connection()
        if conn is None:
            return False, "Failed to get database connection", None
        
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(query, params or ())
            
            results = None
            if fetch:
                results = cursor.fetchall()
            
            if commit:
                conn.commit()
            
            cursor.close()
            conn.close()
            
            return True, "Query executed successfully", results
            
        except Error as e:
            return False, f"Database error: {e}", None
        except Exception as e:
            return False, f"Unexpected error: {e}", None
        finally:
            if conn.is_connected():
                conn.close()
    
    def execute_many(
        self,
        query: str,
        data_list: List[Tuple],
        commit: bool = True,
    ) -> Tuple[bool, str, int]:
        """
        Execute a query multiple times.
        
        Args:
            query: SQL query string
            data_list: List of parameter tuples
            commit: If True, commit after execution
            
        Returns:
            Tuple of (success, message, rows_affected)
        """
        conn = self.get_connection()
        if conn is None:
            return False, "Failed to get database connection", 0
        
        try:
            cursor = conn.cursor()
            rows = cursor.executemany(query, data_list)
            
            if commit:
                conn.commit()
            
            cursor.close()
            conn.close()
            
            return True, "Batch execution successful", rows
            
        except Error as e:
            if commit and conn.is_connected():
                conn.rollback()
            return False, f"Database error: {e}", 0
        except Exception as e:
            if commit and conn.is_connected():
                conn.rollback()
            return False, f"Unexpected error: {e}", 0
        finally:
            if conn.is_connected():
                conn.close()
    
    def is_connected(self) -> bool:
        """Check if database is connected."""
        if not self._is_connected:
            return False
        
        try:
            conn = self.get_connection()
            if conn:
                conn.close()
                return True
        except:
            pass
        
        return False
    
    def close(self):
        """Close all connections."""
        self._is_connected = False
        DatabaseManager._pool = None
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration (password masked)."""
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": "****" if self.password else "(empty)",
            "database": self.database,
            "pool_size": self._pool_size,
            "is_connected": self._is_connected,
        }


# Helper function to get singleton instance
def get_database_manager() -> DatabaseManager:
    """
    Get the singleton database manager instance.
    
    Returns:
        DatabaseManager instance
    """
    return DatabaseManager()