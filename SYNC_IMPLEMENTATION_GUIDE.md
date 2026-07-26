# QuickStock JA - Bidirectional Sync Implementation Guide

## Overview

This document describes the hardened bidirectional sync system implemented between the offline desktop version and the online web version of QuickStock JA. The sync system ensures that inventory data and sales transactions are properly synchronized between all clients and the central server.

## Commercial Pilot Sale Replay (QS-001)

The commercial-pilot desktop sale path is implemented by the durable JSON queue in
`Desktop_UI/Inventory_gui.py`. It sends queued sales to `POST /api/sales/`, which is
handled by `_record_desktop_sale()` in `Website/BackEnd/quickstock/api/views.py`.
The separate `Desktop_UI/src/services/sync_service.py` service is not the active sale
replay path and must not be used as evidence for QS-001.

### Exactly-Once Contract

1. The desktop creates one `offline_client_ref` before a sale is first persisted to the queue.
2. Legacy queued sales without a reference receive one during their first in-place queue upgrade.
3. Every attempt, including attempts after a timeout or restart, transmits that same reference.
4. The server requires the reference and enforces uniqueness on `(owner, sync_token)`.
5. A duplicate request from the same tenant and cashier returns the original sale acknowledgement.
6. A reference already used by another cashier in the tenant returns `409 Conflict`.
7. The desktop removes the queue action only after a 2xx response containing `ok: true`, a `sale_id`, and the exact matching `client_reference`.

This contract makes the sale, receipt number, tender/financial total, sale lines, and
stock deductions one atomic server effect. A lost response can cause another request,
but it cannot create another business transaction.

### Queue Record and Lifecycle

Business payload fields remain at the top level for backward compatibility. Durable
delivery metadata is stored under `_sync`:

```json
{
  "action": "RECORD_SALE",
  "offline_client_ref": "DESKTOP-...",
  "items": [],
  "_sync": {
    "schema_version": 1,
    "payload_version": 1,
    "action_id": "QUEUE-...",
    "client_id": "desktop-42",
    "operator_user_id": 42,
    "location_id": 7,
    "state": "pending",
    "attempts": 0,
    "created_at": "2026-07-22T12:00:00+00:00",
    "last_attempt_at": null,
    "next_attempt_at": null,
    "last_error": null
  }
}
```

Valid state transitions are:

| Current state | Event | Next state | Queue record |
| --- | --- | --- | --- |
| `pending` / `retry` | Attempt starts | `in_flight` | Persisted before HTTP transmission |
| `in_flight` | Verified acknowledgement | Removed | Acknowledged sale only |
| `in_flight` | Timeout, network error, 408, 425, 429, or 5xx | `retry` | Retained with exponential backoff |
| `in_flight` | Authentication failure | `paused_auth` | Retained without consuming retry budget |
| `in_flight` | Invalid/mismatched acknowledgement or permanent 4xx | `dead_letter` | Retained for review |
| `retry` | Eighth failed delivery | `dead_letter` | Retained for review |
| `paused_auth` | Valid login and replay | `in_flight` | Same action and sale reference |
| `dead_letter` | Explicit `retry_pending_action(action_id)` | `pending` | Attempts and error reset |

Retry delay starts at 5 seconds, doubles for each delivery attempt, and is capped at
300 seconds. A dead letter is never automatically deleted.

### Failure Recovery

- Queue writes use a temporary file, file `fsync`, atomic replace, and directory `fsync` where supported.
- Actions are updated and removed by `action_id`; an enqueue that occurs during replay is not lost.
- `in_flight` actions not owned by the running process are changed to `retry` on read, so restart recovery is immediate.
- A malformed queue is copied to `pending_sync.json.corrupt-*`, marked blocked, and never overwritten automatically.
- Repair or restore a corrupt queue from the preserved copy before allowing new queued writes.
- A protocol mismatch remains a dead letter with `last_error`; use the explicit retry operation only after correcting the cause.

### Backward Compatibility

Existing flat list queue files remain valid. The first read adds `_sync` metadata and
missing sale references, writes the upgraded queue atomically, and keeps the original
as `*.v0.bak`. Existing business payload fields are not renamed or removed. Queue files
remain scoped by desktop user, while server idempotency remains scoped by effective
tenant owner.

QS-002 adds an integrity gate to replay. A legacy sale that does not contain enough
trusted checkout evidence is preserved as a `dead_letter`; physical queue
compatibility does not mean an unverifiable sale is safe to transmit.

### QS-001 Acceptance and Verification

QS-001 passes only when all of the following are true:

- A legacy or new queued sale keeps one client reference across every attempt and restart.
- Duplicate delivery creates one sale, one receipt number, one set of sale lines, and one stock deduction.
- A timeout after server commit leaves the action queued and the retry receives the original acknowledgement.
- Queue corruption is preserved and blocks overwrite.
- Enqueue during replay cannot be erased by acknowledgement of another action.
- Missing, malformed, or mismatched acknowledgements cannot remove an action.
- Authentication interruption pauses rather than discards an action.
- Tenant isolation permits the same reference in separate businesses and prevents cross-cashier reuse inside one business.
- Failed multi-line sale validation leaves no sale lines or inventory changes.

Verification commands:

```bash
cd Desktop_UI
python3 run_tests.py

cd ../Website/BackEnd/quickstock
python3 manage.py check
python3 manage.py test api.tests.SyncPullInventoryTests
python3 manage.py test
```

## Validated Checkout Replay (QS-002)

QS-001 guarantees that one queued sale produces at most one server effect. QS-002
guarantees that only a valid, authorized checkout can reach that effect. The complete
payload, validation map, transaction boundary, and evidence are version controlled in
`QS_002_CHECKOUT_INTEGRITY.md`.

### Replay Gate

1. The desktop builds checkout contract version 1 before submission or queue storage.
2. Desktop preflight validates operator, tenant, location, register, cart, item state,
   canonical cached values, tax, totals, payment, customer credit, and queue metadata.
3. A failed preflight is neither transmitted nor queued; local inventory, receipts,
   and customer credit remain unchanged.
4. Replay validates the persisted contract again before transmission.
5. The backend parses the contract and repeats all checks against locked,
   tenant-owned server records.
6. Sale, receipt number, lines, stock, payment fields, shift total, customer credit,
   and audit commit in one outer database transaction.
7. The queue entry is removed only after the QS-001 acknowledgement matches the
   contract version and `offline_client_ref`.

### Legacy Sale Handling

Legacy flat sale entries remain readable and are upgraded in place with queue metadata
and a stable reference. If current trusted state cannot prove every required
contract-v1 field, replay moves the entry to `dead_letter` with the validation error.
The queue record and `*.v0.bak` source remain available for manual review.

### QS-002 Verification

```bash
venv/bin/python -m pytest -q \
  Desktop_UI/tests/test_checkout_integrity.py \
  Desktop_UI/tests/test_offline_sale_queue.py

cd Website/BackEnd/quickstock
python3 manage.py test api.tests.SyncPullInventoryTests --verbosity 2
python3 manage.py test --verbosity 1
```

Verified on 2026-07-22: 35 focused desktop tests, 23 focused API tests, and all 52
desktop tests passed. The backend suite passed 200 of 201 tests; the sole failure was
the pre-existing WiPay billing-cycle date-boundary assertion, unrelated to checkout.

## Architecture

### Components

1. **Desktop Client (Offline-First)**
   - Local SQLite/JSON storage
   - Active durable JSON action queue in `Inventory_gui.py`
   - Separate `SyncService` for non-QS-001 synchronization work
   - Automatic conflict resolution

2. **Web Server (Django Backend)**
   - MySQL database with sync metadata fields
   - RESTful API endpoints for sync operations
   - `SyncLog` model for tracking all sync operations
   - Transaction-based atomic operations

3. **Sync Protocol**
   - JSON-based data exchange
   - Sync tokens for change detection
   - Timestamp-based conflict resolution
   - Incremental sync support

## Key Features

### 1. Bidirectional Sync

The sync system supports true bidirectional synchronization:

- **Push**: Local changes (inventory updates, sales) are pushed to the server
- **Pull**: Remote changes from other clients are pulled and merged locally
- **Conflict Resolution**: Automatic resolution using "last-write-wins" strategy

### 2. Offline-First Design

- All operations work offline with local storage
- Changes are queued when offline and synced when connection is restored
- Priority-based queue ensures critical operations (sales) are synced first

### 3. Conflict Resolution

Multiple strategies available:
- **Last-Write-Wins** (default): Most recent modification takes precedence
- **Local-Wins**: Local changes always override remote
- **Remote-Wins**: Remote changes always override local
- **Manual**: Conflicts flagged for user resolution

### 4. Atomic Operations

- All sync operations use database transactions
- Failed syncs don't leave data in inconsistent state
- Rollback capability on errors

### 5. Comprehensive Logging

- Every sync operation is logged in `SyncLog` model
- Tracks: items pushed/pulled, conflicts, errors
- Useful for debugging and auditing

## Data Models

### Sync Metadata Fields (Added to Item and Sale models)

```python
# Sync Metadata Fields
sync_token = models.CharField(max_length=64, blank=True, null=True)
last_modified = models.DateTimeField(auto_now=True)
is_deleted = models.BooleanField(default=False)  # Soft delete for Items
sync_source = models.CharField(max_length=20, default="web")
is_synced = models.BooleanField(default=False)  # For Sales only
```

### SyncLog Model

```python
class SyncLog(models.Model):
    sync_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    sync_type = models.CharField(max_length=20)  # inventory, sales, full
    direction = models.CharField(max_length=20)  # push, pull, bidirectional
    status = models.CharField(max_length=20)  # pending, in_progress, completed, failed
    
    # Statistics
    items_pushed = models.PositiveIntegerField(default=0)
    items_pulled = models.PositiveIntegerField(default=0)
    items_failed = models.PositiveIntegerField(default=0)
    conflicts_detected = models.PositiveIntegerField(default=0)
    conflicts_resolved = models.PositiveIntegerField(default=0)
    
    # Timestamps
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
```

## API Endpoints

### Inventory Sync

#### Push Inventory
```
POST /api/sync/inventory/push/
Content-Type: application/json
Authorization: Token <token>

{
    "items": [
        {
            "sku": "ITEM-001",
            "name": "Item Name",
            "price": "100.00",
            "quantity": 10,
            "cost_price": "80.00",
            "category": "Category Name",
            "sync_token": "abc123",
            "last_modified": "2024-01-01T00:00:00+00:00",
            "is_deleted": false
        }
    ],
    "client_id": "desktop-uuid"
}
```

#### Pull Inventory
```
GET /api/sync/inventory/pull/?last_sync=2024-01-01T00:00:00Z&client_id=desktop-uuid
Authorization: Token <token>
```

### Sales Sync

The endpoints below are the bulk synchronization API. Commercial-pilot queued sales
use the idempotent direct endpoint documented after them.

#### Push Sales
```
POST /api/sync/sales/push/
Content-Type: application/json
Authorization: Token <token>

{
    "sales": [
        {
            "receipt_no": 1001,
            "timestamp": "2024-01-01T12:00:00+00:00",
            "tender": "cash",
            "total_price": "115.00",
            "items": [
                {
                    "item_sku": "ITEM-001",
                    "quantity": 2,
                    "unit_price": "50.00"
                }
            ]
        }
    ],
    "client_id": "desktop-uuid"
}
```

#### Pull Sales
```
GET /api/sync/sales/pull/?last_sync=2024-01-01T00:00:00Z&client_id=desktop-uuid
Authorization: Token <token>
```

#### Replay One Queued Desktop Sale

```http
POST /api/sales/
Content-Type: application/json
Authorization: Token <token>

{
  "offline_client_ref": "DESKTOP-0e33e33c-45b0-4e7c-836d-66fe9ed61f85",
  "location_id": 7,
  "tender": "cash",
  "discount": "0.00",
  "items": [
    {"sku": "ITEM-001", "quantity": 2}
  ]
}
```

The client-provided reference is mandatory. Unit prices and totals are derived from
validated server data; client totals are not trusted as accounting inputs.

### Bidirectional Sync

```
POST /api/sync/bidirectional/
Content-Type: application/json
Authorization: Token <token>

{
    "inventory": [...],  // Local inventory items
    "sales": [...],      // Local sales
    "last_sync": "2024-01-01T00:00:00Z",
    "client_id": "desktop-uuid"
}
```

### Sync Status

```
GET /api/sync/status/
Authorization: Token <token>
```

### Reference Data

```
GET /api/sync/reference/
Authorization: Token <token>
```

## Sync Flow

### 1. Initial Setup

```python
# Desktop client initialization
sync_service = SyncService(local_data_path)
sync_service.set_api_config(base_url, api_token)
sync_service.set_conflict_resolution(SyncConflictResolution.LAST_WRITE_WINS)
```

### 2. Making Changes (Offline)

```python
# All changes are queued for later sync
inventory_service.sell_item(sku="ITEM-001", quantity=2)
# This automatically:
# 1. Updates local inventory
# 2. Saves to local storage
# 3. Queues the action for sync
```

### 3. Syncing Changes

```python
# When connection is available
success, message, results = sync_service.bidirectional_sync(
    local_inventory=inventory_service.get_all_items(),
    user_id=user_id
)

if success:
    print(f"Sync completed: {results}")
else:
    print(f"Sync had issues: {message}")
```

### 4. Background Sync

```python
# Start automatic background sync
sync_service.start_background_sync(
    inventory=inventory_service.get_all_items(),
    user_id=user_id,
    callback=on_sync_complete,
    interval=300  # 5 minutes
)
```

## Conflict Resolution

### How Conflicts are Detected

A conflict occurs when:
1. An item is modified locally after the last sync
2. The same item is modified on the server after the last sync
3. Both modifications have different sync tokens

### Resolution Strategy (Last-Write-Wins)

```python
def resolve_conflict(local_item, remote_item):
    local_time = parse_timestamp(local_item.get("last_modified"))
    remote_time = parse_timestamp(remote_item.get("last_modified"))
    
    if remote_time >= local_time:
        return remote_item  # Remote wins
    else:
        return local_item   # Local wins
```

## Error Handling

### Retry Logic

The sync service implements exponential backoff for failed operations:

```python
class PendingSyncAction:
    def record_failure(self, error: str):
        self.retries += 1
        # Exponential backoff: 2^retries seconds (max 5 minutes)
        backoff_seconds = min(2 ** self.retries, 300)
        self.next_retry_time = now() + timedelta(seconds=backoff_seconds)
```

### Failed Sync Handling

1. Failed items are tracked in the sync log
2. Items are retried with exponential backoff
3. After eight failed deliveries, the item becomes a retained `dead_letter`
4. Errors remain on the queue record for manual review and explicit retry

## Best Practices

### 1. Always Use Sync Tokens

```python
# Generate sync token after successful sync
item.mark_synced(remote_token)
```

### 2. Handle Offline Scenarios

```python
# Queue actions when offline
if not is_online():
    sync_service.queue_action("sell_item", {
        "sku": sku,
        "quantity": quantity
    }, priority=1, item_id=sku)
```

### 3. Monitor Sync Status

```python
# Check sync status regularly
status = sync_service.get_sync_status()
if status["pending_count"] > 100:
    alert_admin("High pending sync count")
```

### 4. Use Atomic Operations

```python
# Wrap sync operations in transactions
with transaction.atomic():
    # Perform sync operations
    pass
```

## Database Migration

After implementing this sync system, run migrations:

```bash
python manage.py makemigrations
python manage.py migrate
```

The following fields are added to existing models:
- `Item`: sync_token, last_modified, is_deleted, sync_source
- `Sale`: sync_token, last_modified, is_synced, sync_source
- New model: `SyncLog`

## Testing

### Manual Testing

1. **Test Push Sync**:
   - Modify inventory on desktop
   - Run sync
   - Verify changes appear on web

2. **Test Pull Sync**:
   - Modify inventory on web
   - Run sync on desktop
   - Verify changes appear on desktop

3. **Test Conflict Resolution**:
   - Modify same item on both desktop and web
   - Run sync
   - Verify last-write-wins resolution

4. **Test Offline Mode**:
   - Disconnect network
   - Make changes on desktop
   - Reconnect network
   - Verify changes sync correctly

### Automated Testing

```python
# Example test case
def test_bidirectional_sync():
    # Setup
    desktop_items = [{"sku": "TEST-001", "name": "Test Item", "quantity": 10}]
    server_items = [{"sku": "TEST-002", "name": "Server Item", "quantity": 5}]
    
    # Run sync
    success, msg, results = sync_service.bidirectional_sync(desktop_items, user_id)
    
    # Verify
    assert success
    assert results["pushed"] == 1
    assert results["pulled"] == 1
```

## Troubleshooting

### Common Issues

1. **Sync Not Working**:
   - Check API token is valid
   - Verify network connectivity
   - Check sync log for errors

2. **Conflicts Not Resolving**:
   - Verify sync tokens are being updated
   - Check timestamp synchronization
   - Review conflict resolution strategy

3. **High Pending Queue**:
   - Check for repeated failures
   - Review error logs
   - Consider increasing retry limit

### Viewing Sync Logs

```python
# Get recent sync logs
sync_logs = SyncLog.objects.filter(
    user=user,
    status="failed"
).order_by("-started_at")[:10]

for log in sync_logs:
    print(f"Sync {log.sync_id}: {log.error_message}")
```

## Security Considerations

1. **Authentication**: All sync endpoints require valid authentication
2. **Authorization**: Users can only sync their own data (based on owner)
3. **Data Validation**: All incoming data is validated before processing
4. **Rate Limiting**: Consider implementing rate limits on sync endpoints
5. **Encryption**: Use HTTPS for all sync communications

## Performance Optimization

1. **Incremental Sync**: Only sync changes since last sync
2. **Batch Operations**: Process multiple items in single transaction
3. **Indexing**: Proper database indexes on sync-related fields
4. **Pagination**: Limit results for pull operations
5. **Compression**: Consider compressing large sync payloads

## Future Enhancements

1. **Real-time Sync**: WebSocket-based real-time synchronization
2. **Selective Sync**: Allow users to choose what to sync
3. **Sync Scheduling**: Configurable sync schedules
4. **Bandwidth Optimization**: Delta sync for large inventories
5. **Multi-device Support**: Better handling of multiple desktop clients

## Support

For issues or questions about the sync implementation:
1. Check the sync logs first
2. Review this documentation
3. Contact the development team with sync_id from failed operations
