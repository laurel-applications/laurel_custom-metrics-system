# Confirmation modal for destructive actions

## Objective

Ask for a confirmation through a modal dialog before a widget or a dashboard is
deleted, instead of the native `confirm()` of the browser.

## Problem

The dashboard delete action uses the native `confirm()` of the browser. That
dialog cannot be styled, it looks different on every platform, and it blocks
the whole page. Worse, the widget delete action has no confirmation at all: a
single click destroys the widget and its configuration with no way back.

## Scope

- One modal dialog, shared by every destructive action of the application.
- The dashboard delete and the widget delete ask for a confirmation.
- The modal states what is about to be deleted.

## Out of scope

- No confirmation for actions that are not destructive.
- No undo.

## Constraints

- The dialog is the native `<dialog>` element shown with `showModal()`: it
  brings the backdrop, the focus trap and the Escape key without a library.
- The front end has no build step and no framework, so the behaviour is plain
  JavaScript in `app/static/app.js`.
- The dialog lives in `base.html`, so every page of the application can use it.

## Tasks

- [x] T1 -- Add the dialog to the shared layout.
- [x] T2 -- Wire the dialog in the front end.
- [x] T3 -- Mark the destructive forms and remove the native `confirm()`.
- [x] T4 -- Style the dialog.
- [x] T5 -- Verify the behaviour and add the tests.

## Authorized scope

- `app/templates/base.html`
- `app/templates/dashboard.html`
- `app/static/app.js`
- `app/static/style.css`

## Acceptance criteria

1. Deleting a dashboard opens the modal, names the dashboard and does not
   delete anything until the user confirms.
2. Deleting a widget opens the modal and names the widget. It had no
   confirmation at all before.
3. Cancelling, pressing Escape or clicking outside closes the modal and
   deletes nothing.
4. No template uses the native `confirm()` any more.

## Verification

- `pytest tests/ -q` covers the markup and the routes.
- `node --check app/static/app.js` passes.
- A node test exercises the dialog logic against the shipped script.

## Route

Delegated direct (one writer), together with the Colombia time and the chart
gaps. All three touch `app/static/app.js`, so one bounded writer does all of
them.
