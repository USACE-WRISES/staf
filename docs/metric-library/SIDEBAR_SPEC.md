# Metric Library Workbench spec

## Left sidebar (Metric Library)
Columns in the library list:
- Discipline: Stream discipline (Hydrology, Geomorphology, etc.).
- Function: Stream function label.
- Metric: Metric name (click selects the metric). Includes Add button.
- Minimum Tier: Lowest tier where the metric is available.
- Scoring: Shows "X Curves" plus an expander glyph.
- Details: Button to open Details tab in the inspector.

### X Curves behavior
- Text "X Curves" selects the metric and opens the Curves tab.
- The glyph toggles expansion in-row only.
- Expanded state lists curve names.
- Clicking a curve name opens Curves tab and selects that curve.
- When tier filter is Screening/Rapid/Detailed: X uses that tier's profile curves.
- When tier filter is All: X is total curves across profiles.

## Right sidebar (Metric Inspector)
Tabs:
- Details: Method/Context, How to measure, curves summary + button to open Curves tab.
- Scoring: Renders rubric based on scoring type (categorical/thresholds/curve/formula).
- Curves: Embedded builder for selecting and editing curves.

### Profile selection rules
Default profile selection:
1. If current tier filter is Screening/Rapid/Detailed and that profile exists, use it.
2. Else if a profile is marked `recommended=true`, use it.
3. Else use the first profile.

## QA checklist
- Open Screening page -> open library -> filter to Screening -> expand curves -> click curve name -> Curves tab opens.
- Repeat on Rapid and Detailed pages.
- Add a Detailed profile metric while on Screening page (profile picker).
- If a profile has 0 curves, Curves tab shows "Create curve" state and Add is disabled if curves are required.
- Added metrics show "Added" state in the library list and inspector header.

