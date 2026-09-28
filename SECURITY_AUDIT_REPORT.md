# QuickStock JA - Security Audit Report

## Executive Summary

**Status:** Security review refreshed; this report is not a production certification.

**Last Review Date:** 2026-09-28
**Scope:** Python dependency advisories, targeted source scanning, and production configuration review.

The refreshed dependency scan found vulnerable pinned releases in the backend requirements. Those pins have been updated. The source scan also flags the MD5 checksum used for WiPay callback verification; WiPay currently specifies that checksum format, so changing its algorithm locally would break verification. See the residual findings below.

## Refresh Findings

- Backend pins updated: Django 5.2.17, Django REST framework 3.17.2, Requests 2.34.2, python-dotenv 1.2.2, and Pillow 12.3.0.
- Desktop minimums for Requests, python-dotenv, and Pillow raised to those patched releases.
- Removed `mark_safe()` from theme attributes; the values now use Django's escaping HTML formatter.
- No tracked `.env` or credential files were found during this review.
- Bandit still reports the WiPay MD5 response checksum. WiPay documents this response hash as `md5(transaction_id + total + api_key)`. Keep the API key server-side and continue using constant-time comparison. Prefer a provider-supported stronger signature if WiPay offers one for this flow.
- The live Render health endpoint returned HTTP 503 because the database and database-backed cache were unreachable; email sender configuration was also rejected. These deployment-side issues prevent a production-readiness conclusion and require Render account access to repair.

This source review is not a penetration test or an assessment against PCI DSS, GDPR, SOX, or ISO 27001.

## Remediation Summary

### ✅ RESOLVED - Critical Issues

#### 1. Direct Database Access Removed (WAS: CRITICAL)
**Status:** ✅ **RESOLVED**

**Resolution:** The `_get_db_connection()` method now raises a clear error directing users to use API methods. All database operations have been migrated to the REST API architecture.

```python
def _get_db_connection(self):
    """DEPRECATED: Direct database access has been removed for security."""
    logger.error("Direct database access is no longer supported. Use API methods instead.")
    raise RuntimeError(
        "Direct database access has been removed for security. "
        "Please configure API_BASE_URL and authenticate to use the system."
    )
```

#### 2. HTTPS Enforcement (WAS: HIGH)
**Status:** ✅ **RESOLVED**

**Resolution:** Non-local production API URLs now require HTTPS, while localhost remains allowed for development:

```python
API_BASE_URL_DEFAULT = "http://localhost:8000"

# HTTPS enforcement in production
is_local = any(x in url for x in ["localhost", "127.0.0.1"])
if os.getenv("QUICKSTOCK_ENV") == "production" and not url.startswith("https") and not is_local:
    logger.error(f"Blocked insecure connection attempt to {url}")
    return None
```

#### 3. Encrypted Credential Storage (WAS: HIGH)
**Status:** ✅ **RESOLVED**

**Resolution:** API tokens now stored in OS keyring; local cache uses machine-specific encryption:

- **OS Keyring:** `keyring.set_password("QuickStockJA", username, token)`
- **SecureCache:** PBKDF2 key derivation with 100,000 iterations
- **Machine-specific encryption:** Uses hardware identifiers for key derivation

### ✅ RESOLVED - High Severity Issues

#### 4. Input Validation (WAS: HIGH)
**Status:** ✅ **RESOLVED**

**Resolution:** Comprehensive input validation implemented:
- TRN validation (9-digit Jamaican format)
- Quantity validation (0 to MAX_DB_QUANTITY)
- Price validation (non-negative decimals)
- SKU validation and sanitization

#### 5. Authentication Validation (WAS: HIGH)
**Status:** ✅ **RESOLVED**

**Resolution:** Token management implemented:
- Token expiration checking via `_parse_token()` with max_age
- Automatic token refresh on 401 responses
- Session timeout handling
- Secure token storage in OS keyring

#### 6. Data Encryption at Rest (WAS: HIGH)
**Status:** ✅ **RESOLVED**

**Resolution:** All sensitive local data encrypted:
- `SecureCache` module for encrypted cache files
- Machine-specific encryption keys
- Automatic encryption of sensitive fields (api_token, password, hash, etc.)

### ✅ RESOLVED - Medium Severity Issues

#### 7. Error Handling (WAS: MEDIUM)
**Status:** ✅ **RESOLVED**

**Resolution:** Proper error handling implemented:
- User-friendly error messages (no internal details exposed)
- Detailed error logging for debugging
- Structured exception handling throughout

#### 8. Audit Logging (WAS: MEDIUM)
**Status:** ✅ **RESOLVED**

**Resolution:** Comprehensive logging system implemented:
- `log_security_event()` for security events
- `log_api_event()` for API calls
- `log_user_event()` for user actions
- `log_sync_event()` for sync operations
- Rotating file handlers (10MB max, 5 backups)

#### 9. Rate Limiting (WAS: MEDIUM)
**Status:** ✅ **RESOLVED**

**Resolution:** Rate limiting implemented on backend:
- Login rate limiting (configurable attempts/window)
- API rate limiting via cache-based counters
- Graceful degradation with 429 responses

## Production Security Features

### Authentication & Authorization
- ✅ API token authentication with expiration
- ✅ Role-based access control (Admin, Manager, Cashier)
- ✅ Secure token storage in OS keyring
- ✅ Session timeout handling

### Data Protection
- ✅ HTTPS enforcement for all API communications
- ✅ Encrypted local cache with machine-specific keys
- ✅ No plaintext credentials stored
- ✅ Input validation and sanitization

### Monitoring & Logging
- ✅ Structured logging with rotating file handlers
- ✅ Security event logging
- ✅ Health check endpoints
- ✅ Audit trail for critical operations

### Infrastructure Security
- ✅ Django security middleware enabled
- ✅ CSRF protection
- ✅ HSTS headers
- ✅ Secure cookie settings
- ✅ Fail-fast configuration validation

## Compliance Notes

The controls described in this report may support security work, but no compliance assessment or certification was performed.

## Recommendations

### Ongoing Security Practices

1. **Regular Updates**
   - Keep dependencies updated
   - Monitor security advisories
   - Apply security patches promptly

2. **Monitoring**
   - Review security logs weekly
   - Monitor for unusual API activity
   - Set up alerts for security events

3. **Access Control**
   - Rotate API tokens monthly
   - Review user permissions quarterly
   - Audit admin access annually

4. **Backup & Recovery**
   - Test restore procedures monthly
   - Verify backup integrity
   - Store backups securely off-site

## Conclusion

The identified Python dependency advisories and unsafe theme HTML construction have been addressed in source. The WiPay checksum remains constrained by its provider protocol. Production availability and email configuration still need repair in Render, and must be rechecked after that service is restored.
