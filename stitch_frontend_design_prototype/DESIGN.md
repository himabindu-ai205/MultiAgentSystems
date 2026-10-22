---
name: Fintech Support & Advisory System
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#3c4a43'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#6b7b72'
  outline-variant: '#bacac1'
  surface-tint: '#006c4f'
  primary: '#006c4f'
  on-primary: '#ffffff'
  primary-container: '#00d09c'
  on-primary-container: '#00533c'
  inverse-primary: '#2fe0aa'
  secondary: '#545f73'
  on-secondary: '#ffffff'
  secondary-container: '#d5e0f8'
  on-secondary-container: '#586377'
  tertiary: '#006a63'
  on-tertiary: '#ffffff'
  tertiary-container: '#70c6bc'
  on-tertiary-container: '#00524c'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#59fdc5'
  primary-fixed-dim: '#2fe0aa'
  on-primary-fixed: '#002116'
  on-primary-fixed-variant: '#00513b'
  secondary-fixed: '#d8e3fb'
  secondary-fixed-dim: '#bcc7de'
  on-secondary-fixed: '#111c2d'
  on-secondary-fixed-variant: '#3c475a'
  tertiary-fixed: '#9cf2e8'
  tertiary-fixed-dim: '#80d5cb'
  on-tertiary-fixed: '#00201d'
  on-tertiary-fixed-variant: '#00504a'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
  headline-xl-mobile:
    fontFamily: Inter
    fontSize: 26px
    fontWeight: '700'
    lineHeight: 34px
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  headline-sm:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1.5rem
  gutter-sm: 1rem
  margin: 2rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 1rem
  space-lg: 1.5rem
  space-xl: 2rem
---

## Brand & Style

The brand personality centers on financial clarity, approachable intelligence, and steady reassurance. Geared toward retail investors, active traders, and wealth-management seekers navigating high-stakes inquiries, the interface must project transparency, effortless precision, and immediate accessibility. 

The aesthetic adheres to **Corporate Modern Minimalism** tailored for contemporary financial technology. It emphasizes expansive white surfaces, precise hairline borders, crisp typography, and purposeful emerald-teal accents that instill confidence. By avoiding dense visual clutter and distracting decorative trends, the UI establishes an environment where automated advisory dialogues and complex ticket resolution flows feel calm, structured, and friction-free.

## Colors

The color palette anchors on purposeful contrast between high-utility neutrals and energetic financial accents:

- **Primary (`#00D09C`)**: The signature growth teal. Used for key interactive drivers, primary actions, active conversation triggers, and resolved or positive state indicators.
- **Secondary (`#1E293B`)**: Clean slate navy. Provides structural authority across global navigation, sidebars, dense data headers, and high-emphasis labels.
- **Tertiary (`#0F766E`)**: Deep emerald teal. Balances accessibility across smaller text elements, interactive focus states, hover borders, and advisory status pills.
- **Neutral (`#64748B`)**: Slate neutral. Drives secondary labels, inactive tab states, placeholder icons, and structural line dividers.

### Contextual & Surface Tones
- **Canvas (`#F8FAFC`)**: A calm, crisp, cool-slate foundation that minimizes eye strain during extended portal usage.
- **Surface Layer (`#FFFFFF`)**: Pure white containers, ticket cards, and conversation panels delivering distinct spatial separation.
- **Subtle Borders (`#E2E8F0`)**: Hairline delineation framing conversation threads, table rows, and form controls.
- **Status & Feedback**:
  - `Open / Active`: Emerald Teal (`#00D09C`) surface with deep forest text (`#065F46`).
  - `Pending / In-Review`: Amber accent (`#F59E0B`) with warm bronze text (`#78350F`).
  - `Escalated / Critical`: Crimson accent (`#EF4444`) with dark rose text (`#7F1D1D`).
  - `Closed / Solved`: Slate accent (`#E2E8F0`) with cool slate text (`#475569`).

## Typography

Typography relies entirely on **Inter** to ensure maximum legibility for financial figures, rapid scanability across ticketing tables, and structured rendering within conversational message bubbles.

- **Headlines**: Set in bold and semi-bold weights with tight letter tracking (`-0.015em` on headlines above 20px) to maintain a modern, authoritative tone across dashboard modules and modal viewports.
- **Body**: Uses regular weight at 14px for general application density and ticket logs, stepping up to 16px within advisory bot response bubbles for conversational readability.
- **Labels & Status Badges**: Set in medium or semi-bold weights with slight letter spacing (`+0.02em`) on uppercase tags to preserve clarity at smaller font sizes (11px–12px).
- **Tabular Figures**: Data-heavy tables and monetary values utilize `font-variant-numeric: tabular-nums` to guarantee aligned columns and frictionless scanning across financial balance cards and transaction histories.

## Layout & Spacing

The layout is built upon an 8pt base grid with a hybrid structure: a fixed-width left navigation rail combined with a fluid, multi-column dashboard and conversational workspace.

### Grid & Breakpoints
- **Desktop (≥ 1280px)**: 
  - Sleek collapsible sidebar fixed at `260px` width (or `72px` collapsed).
  - Main workspace: 12-column fluid grid, `1.5rem` (`24px`) gutters, `2rem` (`32px`) canvas margins.
  - Advisory split-view: 60% ticket/analytical surface and 40% persistent conversation drawer.
- **Tablet (768px – 1279px)**:
  - Sidebar collapses into an icon-only rail (`72px`) or off-canvas drawer.
  - 8-column layout, `1rem` (`16px`) gutters, `1.5rem` (`24px`) canvas margins.
  - Advisory panel switches to a full-width bottom sheet or toggled modal.
- **Mobile (< 768px)**:
  - 4-column layout with `1rem` (`16px`) gutters and margins.
  - Top header (`#1E293B`) anchors app controls; tickets and advisory bot operate in dedicated full-screen tab views.

### Spacing Rhythm
Component padding uses consistent increments: `space-xs` (4px) for badge and chip internal padding, `space-sm` (8px) for input and button horizontal margins, `space-md` (16px) for interior card pads, and `space-xl` (32px) for structural container boundaries.

## Elevation & Depth

Visual hierarchy relies on **crisp low-contrast outlines paired with ambient low-opacity shadows**, emphasizing clean boundaries rather than deep dimensional drops.

- **Level 0 (Base Canvas - `#F8FAFC`)**: Flat canvas layer.
- **Level 1 (Structural Surfaces - `#FFFFFF`)**: Used for workspace cards, data tables, and advisory logs. Outlined with a 1px border of `#E2E8F0`. Shadow: `0px 1px 3px rgba(15, 23, 42, 0.04), 0px 1px 2px rgba(15, 23, 42, 0.02)`.
- **Level 2 (Interactive Floating / Active Tickets)**: Hover states for support cards, search suggestion overlays, and chat quick-reply triggers. Outlined with `#CBD5E1`. Shadow: `0px 4px 6px -1px rgba(15, 23, 42, 0.06), 0px 2px 4px -2px rgba(15, 23, 42, 0.04)`.
- **Level 3 (Modals, Advisory Drawers & Flyouts)**: Elevated chat flyouts and critical action modals. Shadow: `0px 20px 25px -5px rgba(15, 23, 42, 0.08), 0px 8px 10px -6px rgba(15, 23, 42, 0.04)`.
- **Level 4 (Global Popovers & Tooltips)**: Solid slate backdrop (`#1E293B`) with no border and a sharp ambient drop: `0px 10px 15px -3px rgba(15, 23, 42, 0.12)`.

## Shapes

The design system implements a refined, geometric curvature profile (`roundedness: 2`):

- **Standard Elements (0.5rem / 8px)**: Input fields, conversational message bubbles, ticket summary list items, code snippets, and standard buttons.
- **Larger Containers (1rem / 16px - `rounded-lg`)**: Content cards, modal viewports, advisory chat surfaces, and account metric cards.
- **Overlay Sheets (1.5rem / 24px - `rounded-xl`)**: Slide-over drawer panels and mobile interaction sheets.
- **Capsule / Pill Geometry**: Filter chips, ticket status badges, and quick-reply recommendation tags use full capsule rounding (`9999px`) to create clear differentiation against structured tabular cards.

## Components

### Buttons
- **Primary Button**: Solid `#00D09C` background, `#0F172A` text (ensuring optimal contrast over light teal), 8px radius, medium weight (`label-lg`), height `40px` (desktop) or `44px` (mobile). Hover state: `#00B789`. Focus: 2px offset ring in `#00D09C`.
- **Secondary Button**: Crisp `#FFFFFF` surface with 1px `#E2E8F0` border, `#1E293B` text. Hover state: `#F8FAFC` background with `#CBD5E1` border.
- **Ghost / Tertiary Button**: Transparent background, `#64748B` text, hover: `#F1F5F9` background, `#1E293B` text.
- **Destructive Button**: `#FEF2F2` background, `#DC2626` text, `#FCA5A5` border.

### Status Badges & Ticket Tags
- Height `24px`, horizontal padding `8px`, typography `label-sm`, pill-shaped (`9999px` radius).
- **Resolved / Active**: `#ECFDF5` background, `#047857` text, with a 6px solid `#10B981` leading indicator dot.
- **In Progress / Investigation**: `#EFF6FF` background, `#1D4ED8` text, with a 6px pulsing `#3B82F6` dot.
- **Pending User Reply**: `#FFFBEB` background, `#B45309` text.
- **Escalated**: `#FEF2F2` background, `#B91C1C` text.

### Advisory Bot Chat Bubbles
- **User Message**: Aligned right, `#1E293B` background, `#FFFFFF` text, `14px` body typography, `8px` corner radius with an asymmetrical `2px` bottom-right radius.
- **Bot Advisory Message**: Aligned left, `#FFFFFF` background, 1px `#E2E8F0` border, `#0F172A` text, `8px` radius with an asymmetrical `2px` bottom-left radius. Features an integrated header with bot icon and `#00D09C` indicator.
- **Quick-Reply Suggestions**: Pill-shaped `#FFFFFF` buttons with 1px `#00D09C` border, `#0F766E` text, transitioning on hover to `#ECFDF5`.

### Input Fields & Search Bars
- Pure white (`#FFFFFF`) background, 1px `#E2E8F0` outline, `8px` radius, `40px` height (`48px` for conversational prompt box).
- Placeholder text in `#94A3B8`.
- Focus state: Outline switches to `#00D09C` with a soft `3px` aura (`rgba(0, 208, 156, 0.15)`), eliminating harsh native browser rings.

### Ticket Cards & Lists
- Surface `#FFFFFF`, 1px `#E2E8F0` border, `16px` padding, `8px` gap between stacked items.
- Hover state raises card subtly with Level 2 elevation and turns the left border into a 3px accent bar (`#00D09C`).
- Meta layout: Ticket ID (`#64748B`, 12px), title (`#1E293B`, 14px bold), right-aligned timestamp and priority badge.

### Navigation Sidebar
- Deep slate navy (`#1E293B`) or ultra-clean light mode (`#FFFFFF` with `#E2E8F0` right divider border). 
- Active menu item: `#00D09C` left indicator bar (3px), `#F1F5F9` background, `#0F766E` icon and bold text. Inactive items: `#64748B` with transition to `#0F172A` on hover.