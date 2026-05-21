# Design Theme Analysis - Electron GUI Pages

## Executive Summary
The analyzed pages follow a **consistent corporate blue/dark theme with enterprise application design patterns**. However, `quick_guide.html` deviates significantly from this system-wide theme and should be updated to match.

---

## Core Design System (CSS Variables)

All pages inherit these color variables from `assets/style.css`:

```css
:root {
  --color-primary: #1f3b73;              /* Deep blue */
  --color-primary-dark: #132a56;         /* Darker blue for hover */
  --color-accent: #0a5cbb;               /* Bright blue accent */
  --color-bg: #eef1f7;                   /* Light blue-gray background */
  --color-bg-soft: #f6f7fb;              /* Softer background */
  --color-surface: #ffffff;              /* Card/input backgrounds */
  --color-panel: #f9fafc;                /* Panel backgrounds */
  --color-border: #d7dae3;               /* Border color */
  --color-muted: #5f6575;                /* Muted text */
  --color-heading: #1b2646;              /* Dark heading text */
  --color-danger: #c53e3e;               /* Danger/error color */
  --color-sidebar: #f4f5f8;              /* Sidebar background */
  --shadow-card: 0 12px 28px rgba(15, 23, 46, 0.08);  /* Card shadow */
}
```

---

## Common Layout Structure

### Standard Page Layout (All pages except homepage & quick_guide)

```html
<body>
  <div id="navbarContainer"></div>  <!-- Left sidebar navbar -->
  
  <div class="page-shell">          <!-- Main content container -->
    <header class="page-header">    <!-- Page title & subtitle -->
      <h1 class="page-title">Title</h1>
      <p class="page-subtitle">Subtitle</p>
    </header>
    
    <!-- Page-specific content here -->
  </div>
</body>
```

### Key CSS Classes for Layout

| Class | Purpose | Usage |
|-------|---------|-------|
| `.page-shell` | Main content wrapper | Width: `min(1400px, 100%)`, centered, padding `48px 56px 64px` |
| `.page-header` | Page title section | Border-bottom, padding-bottom 18px |
| `.page-card` | Content cards | Background: `var(--color-surface)`, border, shadow |
| `.page-title` | Main heading | Font-size: 2rem, color: `var(--color-primary)` |
| `.page-subtitle` | Subheading | Font-size: 1rem, color: `var(--color-muted)` |

---

## Sidebar Navigation System

### Sidebar Structure (navbar.css)

```css
.app-navbar {
  position: fixed;
  top: 0;
  left: 0;
  width: 240px;              /* Standard sidebar width */
  background: #0f172a;       /* Dark navy background */
  padding: 32px 0 24px;
  box-shadow: 4px 0 20px rgba(0, 0, 0, 0.25);
}
```

### Sidebar Features
- **Fixed left sidebar**: 240px wide, collapses to 88px
- **Dark navy theme**: `#0f172a` background with `#f8fafc` text
- **Navigation links**: `#94a3b8` text, hover → `#1e293b` background
- **Active state**: Blue (`#2563eb`) with shadow
- **Brand section**: White title (`#f8fafc`), 28px margin-bottom

### Body Padding Adjustment
```css
body:not(.welcome-body) {
  padding-left: 240px;       /* Content pushed right for sidebar */
}

body.sidebar-collapsed:not(.welcome-body) {
  padding-left: 88px;        /* Collapsed state */
}
```

---

## Component Design Patterns

### Buttons

**Primary Button**
```css
button {
  background: var(--color-primary);      /* #1f3b73 */
  color: #fff;
  padding: 11px 22px;
  border-radius: 0;                      /* Sharp corners! */
  font-weight: 600;
  font-size: 0.95rem;
}

button:hover:not(:disabled) {
  background: var(--color-primary-dark); /* #132a56 */
}
```

**Secondary Button**
```css
button.secondary {
  background: #e5e8f2;                   /* Light gray-blue */
  color: var(--color-heading);
  border-color: #cfd4e3;
}
```

**Danger Button**
```css
button.danger {
  background: var(--color-danger);       /* #c53e3e */
  color: #ffffff;
}
```

**Link Button** (Subtle)
```css
button.link-btn {
  background: transparent;
  border: none;
  color: var(--color-primary);
  text-decoration: underline;
  padding: 0;
}
```

### Form Controls

**Input/Select/Textarea Styling**
```css
input, select, textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--color-border);  /* #d7dae3 */
  border-radius: 4px;                     /* Subtle rounded corners */
  background: var(--color-surface);       /* #ffffff */
  color: var(--color-heading);
  font-size: 0.95rem;
}

input:focus, select:focus, textarea:focus {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(31, 59, 115, 0.15);
}
```

**Form Groups**
```css
.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;                              /* Label to input spacing */
  margin-bottom: 16px;
}

label {
  font-weight: 600;
  color: var(--color-heading);
  font-size: 0.95rem;
}
```

### Cards

**Page Card**
```css
.page-card {
  background: var(--color-surface);      /* #ffffff */
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 32px 36px;
  box-shadow: var(--shadow-card);        /* Subtle elevation */
  display: flex;
  flex-direction: column;
  gap: 20px;
}
```

---

## Page-Specific Patterns

### Connection & Execution (connection.html)

**Layout**: Two-column grid
```css
.connection-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
```

**Status Panel**
```css
.status-panel.modern {
  /* Progress bar, log output, status indicators */
  progress { /* Standard HTML5 progress element */ }
  pre.log { /* Monospace log output */ }
}
```

### Device Setup (device_setup.html)

**Inline Styles with CSS Variables**
- Max-width: 860px (narrower than standard 1400px)
- Card headers with uppercase titles
- Grid forms with 2-column layout

```css
.ds-form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px;
}

.ds-card-title {
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.07em;
}
```

### Grading System (grading.html)

**Sidebar + Content Layout**
```css
.grading-container {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 20px;
}

.grading-sidebar {
  background: var(--color-bg-card);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.grading-sidebar button.active {
  background-color: var(--color-primary);
  color: #ffffff;
}
```

### Results (results.html)

**Two-Column Layout with Sticky Elements**
```css
.results-layout {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 20px;
}

.results-list {
  position: sticky;
  top: 16px;
  max-height: calc(100vh - 120px);
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
```

**Status Badges with Gradients**
```css
.status-pass {
  background: linear-gradient(135deg, #dff6e6 0%, #ccefd7 100%);
  color: #13361f;
}

.status-fail {
  background: linear-gradient(135deg, #fde2e2 0%, #f6c9c9 100%);
  color: #5b1f1f;
}
```

### System Admin (system_admin.html)

**Command Grid Layout**
```css
.command-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  max-height: 220px;
  overflow-y: auto;
}

@media (max-width: 1400px) { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 900px) { grid-template-columns: repeat(2, 1fr); }
```

---

## Typography & Spacing

### Font Stack
```css
body {
  font-family: "Segoe UI", "Roboto", Arial, sans-serif;
}
```

### Heading Hierarchy
- **H1 (Page Title)**: 2rem, color: `var(--color-primary)`, weight: 600
- **H2 (Section Title)**: 1.35rem, color: `var(--color-heading)`, weight: 600
- **H3/H4**: 1.3rem / 1rem, color: `var(--color-heading)`, weight: 600-700

### Spacing Standards
- **Gap/Gap between sections**: 20px, 24px, 40px (progressive hierarchy)
- **Padding in cards**: 32px 36px (horizontal × vertical)
- **Form group gap**: 6px (label to input)
- **Margin-bottom**: 16px (form groups)

---

## ⚠️ Quick Guide Issue - DOES NOT MATCH SYSTEM THEME

### Current quick_guide.html Problems:

1. **Different color scheme**: Uses `#667eea`, `#764ba2` instead of `#1f3b73`, `#0a5cbb`
2. **Gradient step numbers**: `linear-gradient(135deg, #667eea 0%, #764ba2 100%)` (not system colors)
3. **Missing sidebar integration**: Content doesn't account for 240px sidebar padding
4. **Custom background**: `#f5f7fa` instead of `var(--color-bg)`
5. **Different shadows**: Custom shadows instead of `var(--shadow-card)`
6. **Inconsistent button styling**: No primary/secondary button classes
7. **Custom spacing values**: Hardcoded `60px 40px` instead of using system padding

### What quick_guide.html SHOULD have:

✓ Include `<link rel="stylesheet" href="assets/nav.css" />` (already has it)
✓ Use `.page-shell` wrapper
✓ Use `var(--color-primary)`, `var(--color-heading)`, etc.
✓ Use `.page-card`, `.section-title`, `.page-title` classes
✓ Use system button classes (`.primary`, `.secondary`, `.danger`)
✓ Adopt page header structure with `.page-header`, `.page-title`, `.page-subtitle`

---

## Implementation Checklist for quick_guide.html

- [ ] Replace custom colors with CSS variables
- [ ] Wrap content in `.page-shell` container
- [ ] Add `.page-header` with `.page-title` and `.page-subtitle`
- [ ] Use `.page-card` for card components instead of inline styles
- [ ] Replace gradient with system `var(--color-primary)` (or create new accent if desired)
- [ ] Update button classes to use `.primary`, `.secondary`, etc.
- [ ] Use consistent padding: `48px 56px 64px` instead of `60px 40px`
- [ ] Replace hardcoded colors in step numbers with system design
- [ ] Verify sidebar padding works correctly (body gets 240px left padding automatically)

---

## File References

- **Main CSS**: `assets/style.css` (Color system, buttons, forms, cards)
- **Navigation CSS**: `assets/nav.css` (Sidebar styling, responsive behavior)
- **Page-Specific CSS**: 
  - `assets/grading.css`
  - `assets/results.css`
  - `assets/system_admin.css`
  - `assets/device_setup.css`

