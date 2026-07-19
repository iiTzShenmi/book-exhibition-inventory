# EXIS UX Implementation Plan

**Scope:** Goal 3 only. This plan covers interface hierarchy, accessibility, and workflow feedback. It does not change authentication, authorization, session handling, database transactions, or the Goal 2 security controls.

## Product priorities

1. Find a book by title, author, ISBN, or keyword quickly.
2. Identify its location and stock state without unnecessary navigation.
3. Surface replenishment information to authorized staff without competing with public search.
4. Make inventory mutations deliberate, visible, and recoverable.

## Interface inventory

| Screen or workflow | Primary goal | Current hierarchy and issue | Mobile/accessibility/accidental-action risk | Recommended change | Status |
| --- | --- | --- | --- | --- | --- |
| Public homepage | Start a book search | Search shares the first experience with an auto-opening announcement and secondary presentation modules. | Announcement steals focus; secondary content delays the main task. | Search-first workspace; collapse optional event content while keeping the catalogue visible. | Implemented |
| Search workflow | Submit a query and retain context | Field persists query but has no clear action, active-filter summary, or submission state. | Filter state is not announced. | Add clear search, loading state, active filters, and result summary. | Implemented |
| Search results | Scan title, author, location, stock, and replenishment need | Results expose location/stock but do not show a result count or explicit query/filter summary. | Result-card focus moves the viewport unexpectedly; no live result status. | Add count/status, location/stock labels, and stable keyboard focus behavior. | Implemented |
| Filters | Narrow results safely | Booth selector is an ungrouped long native select. | Long options are difficult to scan and easy to mis-select on touch screens. | Searchable grouped location picker, display/reserve grouping, visible selected state, removable filter chips. | Implemented |
| Replenishment monitoring | Find high-priority stock work | Public page contains a decorative realtime modal with a permanent loading state until opened. | It competes with search and can look broken. | Keep operational status in a collapsed secondary section and use clear empty/loading/error states. | Implemented: the unsupported public modal was removed; staff alerts remain on the dashboard. |
| Pending actions | Understand what needs attention | Alerts are split between public modal and admin dashboard. | Priority is not clear to staff. | Keep staff-specific action queue on dashboard; public users only see search results. | Implemented |
| Problem report | Submit a useful issue once | Sending/success/error exists, but scope and limits are not visible. | No visible character limit or duplicate-submission explanation. | Add scope, limits, persistent status, and submission guard while preserving Goal 2 schema/honeypot/CSRF behavior. | Implemented |
| Login | Access administrator tools | Labels and focus styles are present. | No clear indication that login is staff-only, but no sensitive details should be added. | Preserve generic errors; add concise staff-only and retry guidance. | Implemented |
| Invitation acceptance | Redeem a valid staff invitation | Current copy calls it a security code and asks users to contact the owner. | The intended audience, password rule, and generic failure behavior are unclear. | Explain invited-staff-only flow without exposing validation details. | Implemented |
| Administrator dashboard | Search and safely manage inventory | Search/filter pattern is separate from public flow; quick actions are visually equal. | Long option lists and repeated-click mutations can be confusing. | Align search/filter controls, add action state, and clearly separate add, edit, move, archive, and delete. | Implemented |
| Inventory editing | Change book metadata or locations | Book and cabinet modals contain core actions. | Modal focus handling and submission feedback need strengthening. | Add modal semantics/focus restoration and disabled pending controls. | Implemented |
| Inventory movement | Move a book to another cabinet | Source title and target picker are present. | Source location and pending state are not made explicit enough. | Show source/target context, loading state, and clear success/failure feedback. | Implemented |
| Archive/remove | Remove a book from a cabinet | Browser confirmation exists for removal. | Destructive action wording does not explicitly say it archives the retained record. | Use archive language and retain confirmation; do not change transaction behavior. | Implemented |
| Audit history | Review sensitive operational history | Advanced-admin log starts collapsed, which prevents blank expansion. | Div grid remains less semantic than a table. | Preserve collapse behavior; add accessible labels and responsive reading support only where safe. | Partially implemented |
| Empty/loading/validation/error states | Recover from unavailable or invalid actions | States exist inconsistently across public and admin screens. | Some state messages are modal/toast only. | Normalize inline status regions and disable duplicate submissions. | Implemented for search, issue reports, cabinet assignment, add-book preview, and inventory movement; broader bulk-action coverage remains outside current behavior. |

## Implementation sequence

1. Preserve Goal 2 semantics and add template-level regression coverage before changing public search and form markup.
2. Establish the search-first public workspace: search field, filter disclosure, active filter summary, result summary, and secondary operational disclosure.
3. Replace public/admin long booth selectors with the shared no-framework searchable picker pattern; preserve standard form values and server routes.
4. Remove disabled map/announcement presentation from the public production experience; retain no fake map interaction.
5. Add form instructions, client-side length feedback, submission state, and retry feedback without changing server-side validation.
6. Improve admin mutation modal labels, confirmation language, pending states, keyboard behavior, and responsive layout without changing requests or transaction logic.
7. Apply focused responsive/accessibility CSS and JavaScript: landmarks, status regions, focus restoration, Escape, reduced motion, touch targets, and overflow.
8. Validate with existing tests, source-level frontend regressions, JavaScript syntax checks, and browser screenshots when tooling is available.

## Decision boundaries

- No recent-location persistence will be introduced; it would create stale shared-device state without a demonstrated operational benefit.
- No quantity controls will be introduced because EXIS currently models in-stock state, not numeric quantities.
- No CAPTCHA, moderation workflow, support mailbox, role matrix, or external-contact policy will be invented; those remain product decisions from Goal 2.
- The disabled map is removed from public presentation rather than restyled as a working feature.

## Final status

- **Implemented:** search-first homepage, result summary, active removable filters with clear-all, searchable cabinet pickers, public placeholder removal, explicit empty/loading/validation/error feedback for implemented workflows, invited-staff guidance, admin mutation context, archive wording, responsive layout adjustments, and modal focus management.
- **Partially implemented:** audit-history presentation remains intentionally unchanged except for the previously implemented collapsed default; converting it to a table was excluded from Goal 3 because it would be broader restructuring.
- **Requires product decision:** support mailbox, issue-report moderation workflow, persistent recent-location storage, quantity controls, CAPTCHA, and role-policy changes.
- **Requires real-user testing:** desktop and mobile browser screenshots, keyboard walkthroughs, touch selection in long cabinet lists, and staff confirmation-copy review. This environment has no Node.js, browser binary, or browser automation package.

## Manual acceptance sequence

1. On desktop and phone-width browsers, open `/`, verify that search is the first interactive task, and confirm no announcement, map, or permanent loading placeholder appears.
2. Search by title, then add, remove, and keyboard-select cabinet, stock, and author filters. Verify the width remains stable, active filters remove independently, and the result count matches the visible cards.
3. Use keyboard only: Tab through search, open the cabinet picker, select a cabinet with arrows and Enter, open a result card with Enter or Space, and close each modal with Escape.
4. Submit a problem report with valid input, then attempt an over-limit description and a repeat click. Verify the counter, pending state, generic response, and no duplicate request.
5. Open `/login` and `/register`; verify staff/invitation guidance, autocomplete behavior, generic errors, and that no validation detail is exposed.
6. As an authorized staff user, search inventory, open cabinet books, toggle availability, move a book, and archive a record. Verify source/target text, confirmation wording, pending state, success/error feedback, focus return, and that the existing audit trail receives the operation.
