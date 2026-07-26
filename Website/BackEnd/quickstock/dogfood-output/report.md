# Dogfood Report: QuickStock JA

| Field | Value |
|-------|-------|
| **Date** | 2026-07-18 |
| **App URL** | http://127.0.0.1:8765 |
| **Session** | quickstock-commercial-qa |
| **Scope** | Public pages, authenticated app navigation, responsive layouts, core workflows, console errors |

## Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 0 |
| Medium | 2 |
| Low | 0 |
| **Total** | **2** |

## Issues

### ISSUE-001: Mobile operator control overflows and is clipped at the viewport edge

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM |
| **Category** | Responsive layout |
| **Page** | `/dashboard/` |
| **Viewport** | 500 px browser viewport |
| **Evidence** | `dogfood-output/screenshots/dashboard-mobile-auth.png` |

**Observed:** The operator control in the authenticated header extends beyond the right edge, clipping the final avatar content and creating horizontal page overflow.

**Measured:** `window.innerWidth` is 500 px, `document.documentElement.scrollWidth` is 520 px, and the operator button bounds are `left: 268 px; right: 520 px; width: 252 px`.

**Reproduction:**

1. Sign in as an administrator.
2. Open `/dashboard/` at a mobile-width viewport.
3. Observe the authenticated header and compare the operator control's right edge with the viewport.

**Expected:** Header controls remain fully visible within the viewport and do not introduce horizontal overflow.

### ISSUE-002: Advanced Analysis charts fail to initialize when the chart library is unavailable

| Field | Value |
|-------|-------|
| **Severity** | MEDIUM |
| **Category** | Functional / runtime dependency |
| **Page** | `/advanced-reports/` |
| **Evidence** | `dogfood-output/screenshots/advanced-reports-mobile.png` |

**Observed:** The browser records `ReferenceError: Chart is not defined` from `advanced_reports.js:112`, so the Revenue Velocity chart cannot initialize even though the surrounding report page renders.

**Reproduction:**

1. Sign in with Professional-plan access.
2. Open `/advanced-reports/`.
3. Inspect the rendered Revenue Velocity section and browser errors.

**Expected:** The charting dependency loads reliably or the page supplies a graceful no-chart fallback without an uncaught runtime error.
