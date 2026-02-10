## Freeze Point 1: Inferring Blog Structure from Inconsistent HTML

### What felt risky
Real-world blog posts often use inconsistent or non-semantic heading structures.
Some posts rely heavily on paragraphs, some misuse heading levels (e.g. H3 used
as main sections), and some include introductory content without any heading.
Automatically forcing structure in these cases risks inventing meaning and
misrepresenting the author’s intent.

### Decision taken
The system infers main sections only when a repeated, semantically meaningful
heading level is detected (H2 preferred, then H3). Content before the first main
section is treated as introductory context and excluded from section count.
Blogs without clear section boundaries are treated as single-section documents.

### Why this constraint exists
This prevents structural invention and preserves nuance, even when the resulting
structure is less “clean” than an idealized version.

---

## Freeze Point 2: Approval Before Content Modification

### What felt risky
Automatically rewriting or restructuring content based solely on model output
creates a high risk of altering author intent, introducing inaccuracies, or
over-optimizing content without user awareness.

### Decision taken
All proposed changes are surfaced as explicit, explainable suggestions and require
a clear yes/no approval before execution. Sections that are not approved remain
completely unchanged.

### Why this constraint exists
This enforces a human-in-the-loop workflow and ensures that automation augments
editorial judgment rather than replacing it.

---

## Freeze Point 3: Link Evaluation Without Semantic Fact-Checking

### What felt risky
Assessing the “trustworthiness” or “correctness” of external links via automated
semantic analysis risks false positives, subjective judgments, and overreach.
Fact-checking is context-sensitive and difficult to automate safely.

### Decision taken
The system evaluates links conservatively using programmatic accessibility checks
(HTTP reachability) and basic categorization (official, blog, pdf, unknown).
Links are flagged visually but never removed or rewritten automatically.

### Why this constraint exists
This avoids making unverifiable claims about content quality while still providing
useful signals to the user about broken or potentially weak links.

---

## Freeze Point 4: HTML Output Generation from Structured Metadata

### What felt risky
Directly rewriting raw HTML risks breaking layout, scripts, or embedded content.
Applying changes inline without structure makes it difficult to explain what was
changed and why.

### Decision taken
The system reconstructs the final output HTML from normalized structural metadata
(title, intro, sections, links), applying visual annotations to clearly indicate:
approved changes, rejected suggestions, and link status.

### Why this constraint exists
Generating HTML from structured data ensures deterministic output, preserves
explainability, and cleanly separates content reasoning from presentation logic.
