# EXIS UX Changelog

## 1. UX objectives

Goal 3 prioritizes fast book lookup, clear location and stock state, safe authorized inventory actions, predictable recovery from errors, and accessible mobile operation. It preserves the existing EXIS visual identity and Goal 2 security controls.

## 2. Screens changed

Updated the public homepage, search workflow, search results, cabinet filter, issue-report form, login, registration, administrator dashboard, cabinet-book actions, add-book preview, move-book dialog, and related responsive styles.

## 3. Search and filter improvements

Search retains its query, shows a pending state, exposes a clear-search action, and maintains a stable width after navigation. Results display a count, active filter summary, individually removable filters, a clear-all action, and a no-result state.

Public and administrator cabinet controls are filterable, grouped by cabinet type, keyboard-operable, and submit the existing `cabinet` value unchanged. Search cards show cover, title, location, and stock at the same time; operational information is not hidden behind a cover click.

## 4. Homepage hierarchy changes

The homepage presents EXIS identity and book search first. The automatic announcement, placeholder floor plan, public realtime modal, and disabled-feature footer presentation were removed. Event information remains optional; the catalogue is visible by default below the search workflow.

## 5. Administrator workflow changes

Core dialogs have dialog semantics, focus restoration, Escape behavior, and focus trapping. Add, cabinet-assignment, stock-state, archive, and move actions disable their initiating control while pending.

Move confirmation shows title, source, destination, duplicate-record behavior, and inline status. Archive language explains that public search will no longer show the archived cabinet record while the operational record remains retained. Existing routes, confirmations, audit events, transaction behavior, and undo paths were not changed.

## 6. Form-state improvements

The issue form provides scope guidance, server-matching limits (80-character name and 1200-character description), a live counter, sending state, success/failure feedback, and duplicate-click prevention. Its Goal 2 JSON schema, CSRF protection, honeypot, deduplication, and separate `IssueReport` storage remain unchanged.

Login and registration state their staff/invitation scope, use appropriate autocomplete hints, show a pending state, and preserve generic backend failures. No authentication, authorization, session, or invitation-security logic was redesigned.

## 7. Mobile changes

Search widths remain constrained across homepage and results. Cabinet options become in-flow at narrow widths, result cards retain readable cover/location/stock information, secondary sections use compact disclosures, and administrator dialogs use viewport-safe scrolling and full-width mobile actions.

## 8. Accessibility changes

Added explicit labels, search input types, live status regions, associated help text, visible focus states, keyboard cabinet selection, semantic dialogs, focus restoration, focus trapping, Escape behavior, and existing reduced-motion support.

## 9. Visual-system changes

The existing blue, white, and slate EXIS treatment remains. Search, filter, status, dialog, pending, archive, and active-filter states now share tighter borders, spacing, button hierarchy, and responsive dimensions without a visual rebrand.

## 10. Tests added

`tests/test_ux_regressions.py` covers the search-first homepage, removed map/announcement markup, result summary, active-filter clearing, issue-form bounds and live feedback, login/registration guidance, administrator empty/form states, move/archive context, and removal of periodic focus stealing.

## 11. Screenshots produced

None. This WSL environment has no Node.js, browser binary, or browser automation package. No screenshots were fabricated.

## 12. Known limitations

Browser-assisted desktop/mobile, screen-reader, console, zoom, touch, and responsive-overflow checks remain manual work. ISBN and numeric quantity fields are not part of the current data model, so the interface does not claim they are available. Audit history remains collapsed by default but was not restructured into a table.

## 13. Recommended future improvements

Run the documented manual acceptance sequence on desktop and phone-width browsers. Product decisions remain required for a support mailbox, issue-report moderation/retention, CAPTCHA policy, recent-location persistence, numeric quantities, and role capability policy. Do not deploy until the separate `DEPLOYMENT_CHECKLIST.md` has been completed and reviewed.
