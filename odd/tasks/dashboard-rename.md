# Dashboard rename

## Objective

Allow the name of a dashboard to be edited from the dashboard page.

## Problem

The name of a dashboard can only be set at creation time. Once created, a
dashboard cannot be renamed, so a name that was mistyped or that no longer
describes the dashboard has to be lived with. There is also a name in
production that was stored URL-encoded (`DHT11+Temperature+%26+Humidity+Dashboard`)
and the rename is the way to fix it.

## Scope

- A rename form on the dashboard page, next to the "Add widget" and
  "Delete dashboard" actions.
- A route that validates the new name and stores it.
- The new name is shown in the page title and in the dashboard list right
  after the rename.

## Out of scope

- No renaming from the dashboard list page.
- No history of previous names.

## Constraints

- The name is validated with the existing `validation.validate_name` helper, so
  a rename can never store a name the create form would have rejected.
- The form must work without JavaScript: the page is server rendered and the
  failure is reported with the standard error block.

## Tasks

- [x] T1 -- Add the rename route and make the dashboard page renderable with
      an error.
- [x] T2 -- Add the rename form to the dashboard page.
- [x] T3 -- Style the rename form.
- [x] T4 -- Verify the route, the validation and the redirect.

## Authorized scope

- `app/web/routes.py`
- `app/templates/dashboard.html`
- `app/static/style.css`

## Acceptance criteria

1. The dashboard page offers a rename form.
2. Submitting a valid name stores it and returns to the dashboard page, which
   shows the new name.
3. Submitting an empty or blank name re-renders the page with the explicit
   `INVALID_NAME` error and does not change the stored name.
4. The dashboard list shows the new name.
5. A name longer than the column allows is rejected with the same error
   contract.

## Verification

- `pytest tests/test_web.py` covers the new route.
- `pytest tests/` runs green.
- Browser: rename the dashboard and confirm the title and the list update.

## Route

Delegated direct (one writer), together with the dashboard global time range
picker. Both features touch the same files, so one bounded writer does both.
