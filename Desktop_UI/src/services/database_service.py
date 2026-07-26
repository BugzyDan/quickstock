"""
Database service for MySQL connections.
"""

import os
from typing import Any, Dict, List, Optional, Tuple
from dotenv import load_dotenv

# Try to import mysql-connector, handle if not installed
try:
    import mysql.connector
    from mysql.connector import Error, pooling
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False
    mysql = None
    Error = None
    pooling = None

# Load environment variables
load_dotenv()


class DatabaseService:
    """
    Service for managing MySQL database connections.
    
    This class handles all database connectivity, including connection
    pooling and error handling.
    """
    
    def __init__(self):
        """Initialize the database service."""
        self.connection_pool = None
        self._pool_name = "quickstock_pool"
        self._pool_size = 5
        self._is_connected = False
        
        # Database configuration from environment
        self.host = os.getenv("DB_HOST", "localhost")
        self.port = int(os.getenv("DB_PORT", 3306))
        self.user = os.getenv("DB_USER", "root")
        self.password = os.getenv("DB_PASSWORD", "")
        self.database = os.getenv("DB_NAME", "quickstock")
    
    def connect(self) -> Tuple[bool, str]:
        """
        Establish a database connection.
        
        Returns:
            Tuple of (success, message)
        """
        if not MYSQL_AVAILABLE:
            return False, "MySQL connector not installed. Run: pip install mysql-connector-python"
        
        try:
            # Create connection pool for better performance
            self.connection_pool = pooling.MySQLConnectionPool(
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
            
            # Test the connection
            test_conn = self.connection_pool.get_connection()
            if test_conn.is_connected():
                test_conn.close()
                self._is_connected = True
                return True, "Connected successfully"
            
            return False, "Failed to establish connection"
            
        except Error as e:
            return False, f"MySQL Connection Error: {e}"
        except Exception as e:
            return False, f"Unexpected error: {e}"
    
    def get_connection(self) -> Optional[Any]:
        """
        Get a connection from the pool.
        
        Returns:
            MySQL connection object or None
        """
        if not self._is_connected or self.connection_pool is None:
            success, msg = self.connect()
            if not success:
                return None
        
        try:
            return self.connection_pool.get_connection()
        except Error as e:
            print(f"Error getting connection from pool: {e}")
            return None
    
    def execute_query(
        self,
        query: str,
        params: Optional[Tuple] = None,
        fetch: bool = True,
        commit: bool = False
    ) -> Tuple[bool, str, Optional[List[Dict]]]:
        """
        Execute a database query.
        
        Args:
            query: SQL query string
            params: Query parameters
            fetch: If True, fetch and return results
            commit: If True, commit the transaction
            
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
        commit: bool = True
    ) -> Tuple[bool, str, int]:
        """
        Execute a query multiple times with different parameters.
        
        Args:
            query: SQL query string
            data_list: List of parameter tuples
            commit: If True, commit after all executions
            
        Returns:
            Tuple of (success, message, rows_affected)
        """
        conn = self.get_connection()
        if conn is None:
            return False, "Failed to get database connection", 0
        
        try:
            cursor = conn.cursor()
            rows_affected = cursor.executemany(query, data_list)
            
            if commit:
                conn.commit()
            
            cursor.close()
            conn.close()
            
            return True, "Batch execution successful", rows_affected
            
        except Error as e:
            if commit:
                conn.rollback()
            return False, f"Database error: {e}", 0
        except Exception as e:
            if commit:
                conn.rollback()
            return False, f"Unexpected error: {e}", 0
        finally:
            if conn.is_connected():
                conn.close()
    
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test the database connection.
        
        Returns:
            Tuple of (success, message)
        """
        return self.connect()
    
    def is_connected(self) -> bool:
        """
        Check if database is connected.
        
        Returns:
            True if connected, False otherwise
        """
        if not self._is_connected or self.connection_pool is None:
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
        """Close all connections in the pool."""
        self._is_connected = False
        self.connection_pool = None
    
    def get_config(self) -> Dict[str, Any]:
        """
        Get current database configuration.
        
        Returns:
            Dictionary with config values (password masked)
        """
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": "****" if self.password else "(empty)",
            "database": self.database,
            "pool_size": self._pool_size,
            "is_connected": self._is_connected,
        }


# Singleton instance for application-wide use
_db_service = None


def get_database_service() -> DatabaseService:
    """
    Get the singleton database service instance.
    
    Returns:
        DatabaseService instance
    """
    global _db_service
    if _db_service is None:
        _db_service = DatabaseService()
    return _db_service