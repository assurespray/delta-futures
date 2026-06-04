# Setup Flow Navigation & Back Buttons

## TL;DR
> **Quick Summary**: Add true "Previous Question" back buttons to all steps of the setup flows (Algo, Screener, Paper), and add post-completion navigation buttons (Main Menu, Back to Menu, Add Another).
> 
> **Deliverables**: 
> - 4 completion screens updated with 3 navigation buttons
> - `algo_setup.py` FSM refactored to support backward state transitions
> - `screener_setup.py` FSM refactored to support backward state transitions
> - `paper_trading.py` FSMs refactored to support backward state transitions
> 
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 3 waves

---

## Context

### Original Request
"can you add back button to the setup flow individual, screener,paper setups and also after the completion of the setup flow add back to menu, mainmenu, add add setup to continue adding more setups"
"also add cancel button in addition to back button to cancel the flow and move back to menu"

### Interview Summary
**Key Discussions**:
- Clarified that "back button" means true "Previous Question" navigation through the setup flow, not just cancelling the setup.
- Added a requirement for a "Cancel" button on every step to immediately abort the flow and return to the menu.

---

## Work Objectives

### Core Objective
Implement full backward navigation in Telegram `ConversationHandler` setup flows and improve post-setup navigation.

### Concrete Deliverables
- `handlers/algo_setup.py` updated with backward transitions and completion buttons
- `handlers/screener_setup.py` updated with backward transitions and completion buttons
- `handlers/paper_trading.py` updated with backward transitions and completion buttons
- `bot.py` updated to register new FSM fallback/back handlers

### Must Have
- True state machine reversion (e.g. from Timeframe selection back to Direction selection)
- State data must be preserved when going back (don't lose the name if going back to edit it)
- Completion screens must have working callback buttons for Main Menu, Menu, and Add Another

### Must NOT Have (Guardrails)
- Do NOT use generic text messages like "Type 'back'" - must use `InlineKeyboardButton`
- Do NOT rewrite the entire bot architecture - work within the existing `python-telegram-bot` FSM framework.

---

## Verification Strategy

### Test Decision
- **Automated tests**: None + agent QA
- **Agent-Executed QA**: ALWAYS (mandatory for all tasks)

### QA Policy
Every task MUST include agent-executed QA scenarios using `interactive_bash` or appropriate testing tools to simulate Telegram callbacks.

---

## Execution Strategy

### Parallel Execution Waves

Wave 1 (Completion Buttons):
├── Task 1: Add completion buttons to Algo, Screener, and Paper setups [quick]

Wave 2 (FSM Refactoring):
├── Task 2: Implement Previous Question logic for Algo Setups [deep]
├── Task 3: Implement Previous Question logic for Screener Setups [deep]
├── Task 4: Implement Previous Question logic for Paper Setups [deep]

Wave FINAL:
├── Task F1: Plan compliance audit (oracle)
├── Task F2: Code quality review (unspecified-high)
├── Task F3: Real manual QA (unspecified-high)
└── Task F4: Scope fidelity check (deep)

---

## TODOs

- [ ] 1. Add post-completion navigation buttons

  **What to do**:
  - Update `algo_confirmed` in `algo_setup.py` to send a keyboard with "➕ Add Another Setup" (`algo_add_start`), "🔙 Back to Algo Menu" (`menu_algo_setups`), and "🏠 Main Menu" (`main_menu`). Remove the text "Use /start to return to main menu."
  - Update `screener_confirmed` in `screener_setup.py` similarly (use `screener_add_start` and `menu_screener_setups`).
  - Update `pscr_confirmed` and `paper_confirmed` in `paper_trading.py` similarly (use their respective add starts and `menu_paper_trading`).

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Just replacing text instructions with `InlineKeyboardMarkup` on completion screens.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1
  - **Blocked By**: None

  **Acceptance Criteria**:
  - [ ] 4 completion functions updated to return `InlineKeyboardMarkup` with the 3 buttons instead of plain text.

  **QA Scenarios**:
  ```
  Scenario: Completion screen shows buttons
    Tool: Bash (grep/cat)
    Preconditions: None
    Steps:
      1. grep for 'InlineKeyboardMarkup' inside algo_confirmed
    Expected Result: Finds the keyboard configuration with the 3 required buttons.
    Evidence: .omo/evidence/task-1-buttons.txt
  ```

- [ ] 2. Implement Previous Question logic for Algo Setups

  **What to do**:
  - In `handlers/algo_setup.py`, extract the prompt generation logic for each state into reusable functions (e.g., `prompt_setup_api(update, context)`).
  - Add a `[🔙 Back]` button AND a `[❌ Cancel]` button to every inline keyboard during the `algo_setup_conv` (e.g., `callback_data="algo_back_to_SETUP_DESC"` and `callback_data="algo_cancel"`).
  - Add a fallback/state handler for `algo_back_to_.*` that calls the appropriate prompt function and returns that state.
  - Ensure the cancel callback fully clears context and returns to the Algo menu.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Requires careful FSM state mapping and restructuring of prompt logic.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocked By**: Task 1

  **Acceptance Criteria**:
  - [ ] Every step in the Algo Setup flow has both a Back button and a Cancel button.
  - [ ] Clicking Back correctly renders the previous prompt and reverts FSM state.
  - [ ] Clicking Cancel aborts the flow and returns to the menu.

  **QA Scenarios**:
  ```
  Scenario: Algo FSM handles backward transition
    Tool: Bash (grep/cat)
    Preconditions: None
    Steps:
      1. grep for 'algo_back_to_' in handlers/algo_setup.py
    Expected Result: Finds back callback data and a handler for it.
    Evidence: .omo/evidence/task-2-algo-back.txt
  ```

- [ ] 3. Implement Previous Question logic for Screener Setups

  **What to do**:
  - Same as Task 2, but for `screener_setup.py` (`screener_setup_conv`).
  - Ensure both `[🔙 Back]` and `[❌ Cancel]` buttons are present on all inline keyboards.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Requires careful FSM state mapping.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocked By**: Task 1

  **Acceptance Criteria**:
  - [ ] Every step in the Screener Setup flow has both a Back button and a Cancel button.
  - [ ] Clicking Back correctly renders the previous prompt and reverts FSM state.
  - [ ] Clicking Cancel aborts the flow and returns to the menu.

  **QA Scenarios**:
  ```
  Scenario: Screener FSM handles backward transition
    Tool: Bash (grep/cat)
    Preconditions: None
    Steps:
      1. grep for 'screener_back_to_' in handlers/screener_setup.py
    Expected Result: Finds back callback data and a handler for it.
    Evidence: .omo/evidence/task-3-screener-back.txt
  ```

- [ ] 4. Implement Previous Question logic for Paper Setups

  **What to do**:
  - Same as Task 2, but for `paper_trading.py` (both `paper_setup_conv` and `pscr_setup_conv`).
  - Ensure both `[🔙 Back]` and `[❌ Cancel]` buttons are present on all inline keyboards for both FSMs.

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Requires careful FSM state mapping for two separate FSMs.

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2
  - **Blocked By**: Task 1

  **Acceptance Criteria**:
  - [ ] Every step in the Paper Setup flows has both a Back button and a Cancel button.
  - [ ] Clicking Back correctly renders the previous prompt and reverts FSM state.
  - [ ] Clicking Cancel aborts the flow and returns to the menu.

  **QA Scenarios**:
  ```
  Scenario: Paper FSM handles backward transition
    Tool: Bash (grep/cat)
    Preconditions: None
    Steps:
      1. grep for 'paper_back_to_' in handlers/paper_trading.py
    Expected Result: Finds back callback data and a handler for it.
    Evidence: .omo/evidence/task-4-paper-back.txt
  ```

---

## Final Verification Wave
- [ ] F1. Plan Compliance Audit — `oracle`
  Read the plan. Verify FSM logic handles backward state.
- [ ] F2. Code Quality Review — `unspecified-high`
  Run linter. Check for redundant code in FSM refactoring.
- [ ] F3. Real Manual QA — `unspecified-high`
  Manually verify button callbacks.
- [ ] F4. Scope Fidelity Check — `deep`
  Verify nothing outside the setup flows was altered.
