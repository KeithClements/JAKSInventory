"""Phase modules (discovery → scrape → enrich → review → upload).

Important: keep this package __init__ lightweight.
Eager importing submodules here can create circular imports (e.g. scraper ↔ description).
"""

__all__ = [
	"discovery",
	"scraper",
	"enrichment",
	"review",
	"upload",
	"description",
	"templates",
]
