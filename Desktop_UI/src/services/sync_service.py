"""
Enhanced Sync Service for data synchronization between local storage and API.

This module provides robust bidirectional synchronization using API-only communication:
- Conflict resolution (last-write-wins with timestamp comparison)
- Retry logic with exponential backoff
- Sync state tracking
- Offline queue management with priorities
- API-only communication (no direct database access)
"""

import json
import os
import threading
import time
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable
from enum import Enum

from ..infrastructure.network import (
    DEFAULT_API_BASE_URL,
    build_api_url,
    normalize_api_base_url,
    requires_https_in_production,
    validate_api_base_url,
)

logger = logging.getLogger(__name__)


class SyncStatus(Enum):
    """Enum for sync operation status."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CONFLICT = "conflict"
    PARTIAL = "partial"


class SyncDirection(Enum):
    """Enum for sync direction."""
    PUSH = "push"  # Local to remote
    PULL = "pull"  # Remote to local
    BIDIRECTIONAL = "bidirectional"


class SyncConflictResolution(Enum):
    """Enum for conflict resolution strategies."""
    LOCAL_WINS = "local_wins"
    REMOTE_WINS = "remote_wins"
    LAST_WRITE_WINS = "last_write_wins"
    MANUAL = "manual"


class SyncEntry:
    """Represents a single sync entry with metadata."""
    
    def __init__(self, data: Dict[str, Any], sync_token: Optional[str] = None):
        self.data = data
        self.sync_token = sync_token or self._generate_sync_token(data)
        self.last_modified = data.get("last_modified") or datetime.now(timezone.utc).isoformat()
        self.is_deleted = data.get("is_deleted", False)
    
    @staticmethod
    def _generate_sync_token(data: Dict[str, Any]) -> str:
        """Generate a unique sync token based on data content."""
        content = json.dumps(data, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "data": self.data,
            "sync_token": self.sync_token,
            "last_modified": self.last_modified,
            "is_deleted": self.is_deleted,
        }


class PendingSyncAction:
    """Represents a pending sync action with priority and retry tracking."""
    
    def __init__(
        self,
        action: str,
        data: Dict[str, Any],
        priority: int = 0,
        item_id: Optional[str] = None,
    ):
        self.action = action
        self.data = data
        self.priority = priority  # Higher = more urgent
        self.item_id = item_id  # SKU or unique identifier
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.retries = 0
        self.max_retries = 5
        self.last_error: Optional[str] = None
        self.next_retry_time: Optional[str] = None
    
    def should_retry(self) -> bool:
        """Check if this action should be retried."""
        if self.retries >= self.max_retries:
            return False
        if self.next_retry_time:
            retry_time = datetime.fromisoformat(self.next_retry_time)
            return datetime.now(timezone.utc) >= retry_time
        return True
    
    def record_failure(self, error: str):
        """Record a failed sync attempt and calculate next retry time."""
        self.retries += 1
        self.last_error = error
        # Exponential backoff: 2^retries seconds
        backoff_seconds = min(2 ** self.retries, 300)  # Max 5 minutes
        self.next_retry_time = (
            datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
        ).isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "action": self.action,
            "data": self.data,
            "priority": self.priority,
            "item_id": self.item_id,
            "timestamp": self.timestamp,
            "retries": self.retries,
            "max_retries": self.max_retries,
            "last_error": self.last_error,
            "next_retry_time": self.next_retry_time,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingSyncAction":
        """Create from dictionary representation."""
        action = cls(
            action=data.get("action", ""),
            data=data.get("data", {}),
            priority=data.get("priority", 0),
            item_id=data.get("item_id"),
        )
        action.timestamp = data.get("timestamp", action.timestamp)
        action.retries = data.get("retries", 0)
        action.max_retries = data.get("max_retries", 5)
        action.last_error = data.get("last_error")
        action.next_retry_time = data.get("next_retry_time")
        return action


class SyncStateManager:
    """Manages sync state persistence."""
    
    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.state_file = os.path.join(storage_path, "sync_state.json")
        self._state: Dict[str, Any] = {}
        self._load_state()
    
    def _load_state(self):
        """Load sync state from file."""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self._state = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load sync state: {e}")
            self._state = {}
    
    def _save_state(self):
        """Save sync state to file atomically."""
        try:
            temp_file = self.state_file + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(temp_file, self.state_file)
        except IOError as e:
            logger.error(f"Failed to save sync state: {e}")
    
    def get_last_sync_time(self, entity_type: str) -> Optional[datetime]:
        """Get last sync time for an entity type."""
        key = f"last_sync_{entity_type}"
        timestamp_str = self._state.get(key)
        if timestamp_str:
            return datetime.fromisoformat(timestamp_str)
        return None
    
    def set_last_sync_time(self, entity_type: str, timestamp: datetime):
        """Set last sync time for an entity type."""
        key = f"last_sync_{entity_type}"
        self._state[key] = timestamp.isoformat()
        self._save_state()
    
    def get_sync_token(self, entity_type: str, entity_id: str) -> Optional[str]:
        """Get sync token for a specific entity."""
        key = f"token_{entity_type}_{entity_id}"
        return self._state.get(key)
    
    def set_sync_token(self, entity_type: str, entity_id: str, token: str):
        """Set sync token for a specific entity."""
        key = f"token_{entity_type}_{entity_id}"
        self._state[key] = token
        self._save_state()
    
    def get_all_sync_tokens(self, entity_type: str) -> Dict[str, str]:
        """Get all sync tokens for an entity type."""
        tokens = {}
        prefix = f"token_{entity_type}_"
        for key, value in self._state.items():
            if key.startswith(prefix):
                entity_id = key[len(prefix):]
                tokens[entity_id] = value
        return tokens
    
    def clear_sync_state(self, entity_type: Optional[str] = None):
        """Clear sync state, optionally for a specific entity type."""
        if entity_type:
            prefix = f"token_{entity_type}_"
            keys_to_remove = [k for k in self._state if k.startswith(prefix)]
            for key in keys_to_remove:
                del self._state[key]
            # Also clear last sync time
            sync_key = f"last_sync_{entity_type}"
            if sync_key in self._state:
                del self._state[sync_key]
        else:
            self._state = {}
        self._save_state()


class SyncService:
    """
    Enhanced service for synchronizing data between local storage and API.
    
    This service uses API-only communication - no direct database access.
    
    Handles:
    - Bidirectional sync via API (push and pull)
    - Conflict resolution with multiple strategies
    - Retry logic with exponential backoff
    - Offline queue management with priorities
    - Sync state tracking
    """
    
    def __init__(self, local_data_path: Optional[str] = None):
        """
        Initialize sync service.
        
        Args:
            local_data_path: Path to local data storage
        """
        self.local_data_path = local_data_path or self._default_data_path()
        
        # API configuration
        self.api_base_url = normalize_api_base_url(os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL))
        self.api_token: Optional[str] = None
        
        # Sync state
        self.state_manager = SyncStateManager(self.local_data_path)
        
        # Sync status
        self.is_syncing = False
        self.last_sync_time: Optional[datetime] = None
        self.sync_errors: List[str] = []
        self.sync_status = SyncStatus.PENDING
        
        # Conflict resolution strategy
        self.conflict_resolution = SyncConflictResolution.LAST_WRITE_WINS
        
        # Offline queue with priority support
        self.pending_queue_file = os.path.join(self.local_data_path, "pending_sync.json")
        self.pending_queue: List[PendingSyncAction] = []
        self._load_pending_queue()
        
        # Sync callbacks
        self.on_sync_start: Optional[Callable] = None
        self.on_sync_progress: Optional[Callable] = None
        self.on_sync_complete: Optional[Callable] = None
        self.on_sync_error: Optional[Callable] = None
        
        # Thread safety
        self._sync_lock = threading.Lock()
    
    def _default_data_path(self) -> str:
        """Get default data path based on OS."""
        import platform
        if platform.system() == "Windows":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
        else:
            base = os.path.expanduser("~/.local/share")
        
        path = os.path.join(base, "QuickStockJA")
        os.makedirs(path, exist_ok=True)
        return path
    
    def _load_pending_queue(self):
        """Load pending sync queue from file."""
        try:
            if os.path.exists(self.pending_queue_file):
                with open(self.pending_queue_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.pending_queue = [
                        PendingSyncAction.from_dict(item) for item in data
                    ]
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load pending queue: {e}")
            self.pending_queue = []
    
    def _save_pending_queue(self):
        """Save pending queue to file atomically."""
        try:
            temp_file = self.pending_queue_file + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump([item.to_dict() for item in self.pending_queue], f, indent=2)
            os.replace(temp_file, self.pending_queue_file)
        except IOError as e:
            logger.error(f"Failed to save pending queue: {e}")
    
    def queue_action(
        self,
        action: str,
        data: Dict[str, Any],
        priority: int = 0,
        item_id: Optional[str] = None,
    ):
        """
        Queue an action for later sync with priority support.
        
        Args:
            action: Action type (create, update, delete, sell_item, receive_stock)
            data: Action data
            priority: Priority level (higher = more urgent)
            item_id: Unique identifier for the item (e.g., SKU)
        """
        # Check if there's already a pending action for this item
        existing_idx = None
        for i, pending in enumerate(self.pending_queue):
            if pending.item_id == item_id and pending.action == action:
                existing_idx = i
                break
        
        new_action = PendingSyncAction(action, data, priority, item_id)
        
        if existing_idx is not None:
            # Update existing action with newer data
            self.pending_queue[existing_idx] = new_action
            logger.info(f"Updated pending action for item {item_id}")
        else:
            self.pending_queue.append(new_action)
            logger.info(f"Queued new action: {action} for item {item_id}")
        
        # Sort by priority (higher priority first)
        self.pending_queue.sort(key=lambda x: (-x.priority, x.timestamp))
        
        # Keep queue size manageable
        if len(self.pending_queue) > 1000:
            # Remove oldest low-priority items
            self.pending_queue = self.pending_queue[:1000]
        
        self._save_pending_queue()
    
    def get_pending_count(self) -> int:
        """Get number of pending sync actions."""
        return len(self.pending_queue)
    
    def get_pending_actions(self, limit: int = 50) -> List[PendingSyncAction]:
        """Get pending actions ready for sync."""
        ready_actions = [a for a in self.pending_queue if a.should_retry()]
        return ready_actions[:limit]
    
    def clear_pending(self, successful_item_ids: Optional[List[str]] = None):
        """
        Clear pending actions.
        
        Args:
            successful_item_ids: If provided, only remove these specific actions
        """
        if successful_item_ids is None:
            self.pending_queue.clear()
        else:
            self.pending_queue = [
                a for a in self.pending_queue if a.item_id not in successful_item_ids
            ]
        self._save_pending_queue()
    
    def remove_failed_pending(self, max_retries_exceeded: bool = True):
        """Remove failed pending actions."""
        if max_retries_exceeded:
            self.pending_queue = [
                a for a in self.pending_queue 
                if a.retries < a.max_retries
            ]
        else:
            self.pending_queue = [
                a for a in self.pending_queue 
                if a.last_error is None
            ]
        self._save_pending_queue()
    
    def set_api_config(self, base_url: str, token: str):
        """Set API configuration."""
        self.api_base_url = normalize_api_base_url(base_url, DEFAULT_API_BASE_URL)
        self.api_token = token
    
    def set_conflict_resolution(self, strategy: SyncConflictResolution):
        """Set conflict resolution strategy."""
        self.conflict_resolution = strategy
    
    # ==================== API SYNC OPERATIONS ====================
    
    def _make_api_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Any]:
        """
        Make an API request with retry logic.
        
        Args:
            endpoint: API endpoint path
            method: HTTP method (GET, POST, PUT, PATCH)
            data: Data to send (for POST/PUT/PATCH)
            params: Query parameters (for GET)
            
        Returns:
            Tuple of (success, message, response)
        """
        if not self.api_token:
            return False, "API token not configured", None

        is_valid_url, error_message = validate_api_base_url(self.api_base_url)
        if not is_valid_url:
            return False, error_message, None
        if requires_https_in_production(self.api_base_url):
            return False, "Production API URLs must use HTTPS unless they point to localhost.", None
        
        max_retries = 3
        backoff = 1
        
        for attempt in range(max_retries):
            try:
                import requests
                
                url = build_api_url(self.api_base_url, endpoint)
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Token {self.api_token}",
                }
                
                if method == "GET":
                    response = requests.get(url, headers=headers, params=params, timeout=30, verify=True)
                elif method == "POST":
                    response = requests.post(url, json=data, headers=headers, timeout=30, verify=True)
                elif method == "PUT":
                    response = requests.put(url, json=data, headers=headers, timeout=30, verify=True)
                elif method == "PATCH":
                    response = requests.patch(url, json=data, headers=headers, timeout=30, verify=True)
                else:
                    return False, f"Unsupported HTTP method: {method}", None
                
                # Handle authentication errors
                if response.status_code == 401:
                    return False, "API authentication failed - token may be expired", None
                
                response.raise_for_status()
                return True, "API request successful", response.json()
                
            except ImportError:
                return False, "requests library not installed", None
            except requests.exceptions.Timeout:
                logger.warning(f"API timeout on attempt {attempt + 1}")
                time.sleep(backoff)
                backoff *= 2
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"API connection error on attempt {attempt + 1}: {e}")
                time.sleep(backoff)
                backoff *= 2
            except requests.exceptions.HTTPError as e:
                return False, f"API error: {str(e)}", None
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    return False, f"API error after {max_retries} attempts: {str(e)}", None
                time.sleep(backoff)
                backoff *= 2
        
        return False, f"API request failed after {max_retries} attempts", None
    
    def sync_push_inventory(
        self,
        inventory: List[Dict[str, Any]],
        client_id: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Push inventory changes to the server via API.
        
        Args:
            inventory: List of inventory items to push
            client_id: Client device identifier
            
        Returns:
            Tuple of (success, message, results)
        """
        if not inventory:
            return True, "No items to push", {"pushed": 0, "updated": 0, "failed": 0}
        
        # Prepare items for API
        items_data = []
        for item in inventory:
            last_modified = item.get("last_modified") or datetime.now(timezone.utc).isoformat()
            items_data.append({
                "sku": item.get("SKU") or item.get("sku"),
                "name": item.get("Name") or item.get("name"),
                "price": str(item.get("Price") or item.get("price", 0)),
                "cost_price": str(item.get("Cost") or item.get("cost_price", 0)),
                "quantity": item.get("Amount") or item.get("quantity", 0),
                "category": item.get("Category") or item.get("category"),
                "sync_token": item.get("sync_token") or SyncEntry._generate_sync_token(item),
                "last_modified": last_modified,
                "is_deleted": item.get("is_deleted", False),
            })
        
        payload = {
            "items": items_data,
            "client_id": client_id or "desktop-client",
        }
        
        success, message, response = self._make_api_request(
            "/api/inventory/push/",
            method="POST",
            data=payload,
        )
        
        if success and response:
            results = response.get("results", {})
            # Update sync state for successfully synced items
            for item_data in items_data:
                sku = item_data.get("sku")
                if sku:
                    self.state_manager.set_sync_token("inventory", sku, 
                        SyncEntry._generate_sync_token(item_data))
            
            self.last_sync_time = datetime.now(timezone.utc)
            self.state_manager.set_last_sync_time("inventory", self.last_sync_time)
            
            return True, response.get("message", "Inventory pushed successfully"), results
        
        return False, message, {"pushed": 0, "updated": 0, "failed": 0}
    
    def sync_pull_inventory(
        self,
        client_id: Optional[str] = None,
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Pull inventory changes from the server via API.
        
        Args:
            client_id: Client device identifier
            
        Returns:
            Tuple of (success, message, items)
        """
        last_sync = self.state_manager.get_last_sync_time("inventory")
        
        params = {
            "client_id": client_id or "desktop-client",
        }
        
        if last_sync:
            params["last_sync"] = last_sync.isoformat()
        
        success, message, response = self._make_api_request(
            "/api/inventory/",
            method="GET",
            params=params,
        )
        
        if success and response:
            items = response.get("items", [])
            count = response.get("count", len(items))
            
            # Update sync state for pulled items
            for item in items:
                sku = item.get("sku")
                sync_token = item.get("sync_token")
                if sku and sync_token:
                    self.state_manager.set_sync_token("inventory", sku, sync_token)
            
            if items:
                self.last_sync_time = datetime.now(timezone.utc)
                self.state_manager.set_last_sync_time("inventory", self.last_sync_time)
            
            return True, f"Pulled {count} items from server", items
        
        return False, message, []
    
    def sync_push_sales(
        self,
        sales: List[Dict[str, Any]],
        client_id: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Push sales to the server via API.
        
        Args:
            sales: List of sale records to push
            client_id: Client device identifier
            
        Returns:
            Tuple of (success, message, results)
        """
        if not sales:
            return True, "No sales to push", {"pushed": 0, "skipped": 0, "failed": 0}
        
        # Prepare sales for API
        sales_data = []
        for sale in sales:
            sale_items = []
            for item in sale.get("items", []):
                sale_items.append({
                    "item_sku": item.get("sku") or item.get("item_sku"),
                    "quantity": item.get("quantity", 1),
                    "unit_price": str(item.get("price") or item.get("unit_price", 0)),
                    "total_price": str(item.get("total_price", 0)),
                })
            
            sales_data.append({
                "receipt_no": sale.get("receipt_id") or sale.get("receipt_no"),
                "timestamp": sale.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "tender": sale.get("tender", "cash"),
                "subtotal": str(sale.get("subtotal", 0)),
                "gct_amount": str(sale.get("gct_amount", 0)),
                "total_price": str(sale.get("total_price", 0)),
                "discount": str(sale.get("discount", 0)),
                "items": sale_items,
            })
        
        payload = {
            "sales": sales_data,
            "client_id": client_id or "desktop-client",
        }
        
        success, message, response = self._make_api_request(
            "/api/sync/sales/push/",
            method="POST",
            data=payload,
        )
        
        if success and response:
            results = response.get("results", {})
            self.last_sync_time = datetime.now(timezone.utc)
            return True, response.get("message", "Sales pushed successfully"), results
        
        return False, message, {"pushed": 0, "skipped": 0, "failed": 0}
    
    def sync_pull_sales(
        self,
        client_id: Optional[str] = None,
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Pull sales from the server via API.
        
        Args:
            client_id: Client device identifier
            
        Returns:
            Tuple of (success, message, sales)
        """
        last_sync = self.state_manager.get_last_sync_time("sales")
        
        params = {
            "client_id": client_id or "desktop-client",
        }
        
        if last_sync:
            params["last_sync"] = last_sync.isoformat()
        
        success, message, response = self._make_api_request(
            "/api/sync/sales/pull/",
            method="GET",
            params=params,
        )
        
        if success and response:
            sales = response.get("sales", [])
            count = response.get("count", len(sales))
            
            if sales:
                self.last_sync_time = datetime.now(timezone.utc)
                self.state_manager.set_last_sync_time("sales", self.last_sync_time)
            
            return True, f"Pulled {count} sales from server", sales
        
        return False, message, []
    
    def sync_reference_data(
        self,
    ) -> Tuple[bool, str, Dict[str, List]]:
        """
        Sync reference data (locations, suppliers, categories) from server.
        
        Returns:
            Tuple of (success, message, data)
        """
        success, message, response = self._make_api_request(
            "/api/sync/reference/",
            method="GET",
        )
        
        if success and response:
            data = {
                "locations": response.get("locations", []),
                "suppliers": response.get("suppliers", []),
                "categories": response.get("categories", []),
            }
            return True, "Reference data synced", data
        
        return False, message, {"locations": [], "suppliers": [], "categories": []}
    
    def get_sync_status_info(self) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Get sync status from server.
        
        Returns:
            Tuple of (success, message, status)
        """
        success, message, response = self._make_api_request(
            "/api/sync/status/",
            method="GET",
        )
        
        if success and response:
            return True, "Status retrieved", response.get("status", {})
        
        return False, message, {}
    
    # ==================== BIDIRECTIONAL SYNC ====================
    
    def bidirectional_sync(
        self,
        local_inventory: List[Dict],
        local_sales: Optional[List[Dict]] = None,
        client_id: Optional[str] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Perform bidirectional sync between local and remote via API.
        
        This method:
        1. Pushes local inventory changes to the server
        2. Pulls remote inventory changes from the server
        3. Pushes local sales to the server
        4. Pulls remote sales from the server
        
        Args:
            local_inventory: Local inventory items
            local_sales: Local sales records (optional)
            client_id: Client device identifier
            
        Returns:
            Tuple of (success, message, results)
        """
        if self._sync_lock.locked():
            return False, "Sync already in progress", {}
        
        with self._sync_lock:
            self.is_syncing = True
            self.sync_status = SyncStatus.IN_PROGRESS
            
            if self.on_sync_start:
                self.on_sync_start()
            
            results = {
                "inventory_pushed": 0,
                "inventory_pulled": 0,
                "sales_pushed": 0,
                "sales_pulled": 0,
                "errors": [],
            }
            
            try:
                # Step 1: Push local inventory
                if self.on_sync_progress:
                    self.on_sync_progress("Pushing inventory...", 0)
                
                inv_push_success, inv_push_msg, inv_push_results = self.sync_push_inventory(
                    local_inventory, client_id
                )
                results["inventory_pushed"] = inv_push_results.get("pushed", 0) + inv_push_results.get("updated", 0)
                
                if not inv_push_success:
                    results["errors"].append(f"Inventory push error: {inv_push_msg}")
                
                # Step 2: Pull remote inventory
                if self.on_sync_progress:
                    self.on_sync_progress("Pulling inventory...", 25)
                
                inv_pull_success, inv_pull_msg, inv_pull_items = self.sync_pull_inventory(client_id)
                results["inventory_pulled"] = len(inv_pull_items)
                
                if not inv_pull_success:
                    results["errors"].append(f"Inventory pull error: {inv_pull_msg}")
                
                # Step 3: Push local sales
                if self.on_sync_progress:
                    self.on_sync_progress("Pushing sales...", 50)
                
                if local_sales:
                    sales_push_success, sales_push_msg, sales_push_results = self.sync_push_sales(
                        local_sales, client_id
                    )
                    results["sales_pushed"] = sales_push_results.get("pushed", 0)
                    
                    if not sales_push_success:
                        results["errors"].append(f"Sales push error: {sales_push_msg}")
                
                # Step 4: Pull remote sales
                if self.on_sync_progress:
                    self.on_sync_progress("Pulling sales...", 75)
                
                sales_pull_success, sales_pull_msg, sales_pull_items = self.sync_pull_sales(client_id)
                results["sales_pulled"] = len(sales_pull_items)
                
                if not sales_pull_success:
                    results["errors"].append(f"Sales pull error: {sales_pull_msg}")
                
                # Step 5: Final status
                total_errors = len(results["errors"])
                total_pushed = results["inventory_pushed"] + results["sales_pushed"]
                total_pulled = results["inventory_pulled"] + results["sales_pulled"]
                
                if total_errors == 0:
                    self.sync_status = SyncStatus.COMPLETED
                    message = f"Sync complete: {total_pushed} pushed, {total_pulled} pulled"
                elif total_pushed > 0 or total_pulled > 0:
                    self.sync_status = SyncStatus.PARTIAL
                    message = f"Partial sync: {total_pushed} pushed, {total_pulled} pulled"
                else:
                    self.sync_status = SyncStatus.FAILED
                    message = f"Sync failed: {'; '.join(results['errors'][:3])}"
                
                self.last_sync_time = datetime.now(timezone.utc)
                
                if self.on_sync_complete:
                    self.on_sync_complete(self.sync_status, message, results)
                
                return self.sync_status == SyncStatus.COMPLETED, message, results
                
            except Exception as e:
                error_msg = f"Sync failed with exception: {str(e)}"
                logger.error(error_msg)
                self.sync_status = SyncStatus.FAILED
                results["errors"].append(error_msg)
                
                if self.on_sync_error:
                    self.on_sync_error(e)
                
                return False, error_msg, results
            finally:
                self.is_syncing = False
    
    # ==================== BACKGROUND SYNC ====================
    
    def start_background_sync(
        self,
        inventory: List[Dict],
        sales: Optional[List[Dict]] = None,
        client_id: Optional[str] = None,
        callback: Optional[Callable] = None,
        interval: int = 300,  # 5 minutes default
    ):
        """
        Start background sync in a separate thread.
        
        Args:
            inventory: Inventory items to sync
            sales: Sales records to sync (optional)
            client_id: Client device identifier
            callback: Optional callback function(success, message, results)
            interval: Sync interval in seconds
        """
        def sync_worker():
            while True:
                self.is_syncing = True
                try:
                    success, message, results = self.bidirectional_sync(
                        inventory, sales, client_id
                    )
                    if callback:
                        callback(success, message, results)
                except Exception as e:
                    error_msg = f"Background sync failed: {str(e)}"
                    logger.error(error_msg)
                    if callback:
                        callback(False, error_msg, {})
                finally:
                    self.is_syncing = False
                
                time.sleep(interval)
        
        thread = threading.Thread(target=sync_worker, daemon=True)
        thread.start()
        return thread
    
    # ==================== STATUS ====================
    
    def get_sync_status(self) -> Dict[str, Any]:
        """Get current sync status."""
        return {
            "is_syncing": self.is_syncing,
            "sync_status": self.sync_status.value,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "pending_count": len(self.pending_queue),
            "pending_ready": len(self.get_pending_actions()),
            "api_configured": bool(self.api_token),
            "api_base_url": self.api_base_url,
            "conflict_resolution": self.conflict_resolution.value,
            "errors": self.sync_errors[-10:],  # Last 10 errors
            "state": {
                "last_sync_inventory": self.state_manager.get_last_sync_time("inventory")
                .isoformat() if self.state_manager.get_last_sync_time("inventory") else None,
            },
        }
    
    def get_sync_summary(self) -> Dict[str, Any]:
        """Get a summary of sync state for display."""
        pending_count = len(self.pending_queue)
        failed_count = sum(1 for a in self.pending_queue if a.retries > 0)
        
        return {
            "pending_actions": pending_count,
            "failed_actions": failed_count,
            "last_sync": self.last_sync_time,
            "api_configured": bool(self.api_token),
            "status": self.sync_status.value,
        }
    
    def clear_errors(self):
        """Clear sync error history."""
        self.sync_errors.clear()
    
    def reset_sync_state(self, entity_type: Optional[str] = None):
        """
        Reset sync state, forcing a full resync on next sync.
        
        Args:
            entity_type: If provided, only reset state for this entity type
        """
        self.state_manager.clear_sync_state(entity_type)
        if entity_type is None:
            self.last_sync_time = None
            self.sync_errors.clear()
    
    def force_sync(self, inventory: List[Dict], client_id: Optional[str] = None) -> Tuple[bool, str, Dict]:
        """
        Force a full sync regardless of sync state.
        
        Args:
            inventory: Local inventory items
            client_id: Client device identifier
            
        Returns:
            Tuple of (success, message, results)
        """
        # Reset sync state first
        self.reset_sync_state("inventory")
        
        # Perform sync
        return self.bidirectional_sync(inventory, client_id=client_id)
