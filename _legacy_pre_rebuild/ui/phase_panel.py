"""Phase panel widget for the 5-phase workflow.

This widget contains the phase buttons (Scan, Scrape, ATL, PAI, Review, Upload),
stop button, and progress indicator. It manages button enable/disable states
based on the current selection's stage.
"""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QProgressBar,
    QLabel,
    QGroupBox,
    QSizePolicy,
)


# Phase definitions (2.5 is ATL Diesel enrichment)
PHASES = {
    1: {"name": "🔍 Scan", "emoji": "🔍", "tooltip": "Scan HHP listings for new products"},
    2: {"name": "🧱 Scrape", "emoji": "🧱", "tooltip": "Full scrape with images and descriptions"},
    2.5: {"name": "🏷️ ATL", "emoji": "🏷️", "tooltip": "ATL Diesel competitor pricing & images"},
    3: {"name": "🧪 PAI", "emoji": "🧪", "tooltip": "Enrich with PAI cost and cross-references"},
    4: {"name": "📤 Sync", "emoji": "📤", "tooltip": "Review pricing and sync to Shopify/QBO"},
    5: {"name": "🚀 Upload", "emoji": "🚀", "tooltip": "Upload to Shopify"},
}


class PhaseButton(QPushButton):
    """Styled button for a workflow phase."""
    
    def __init__(self, phase: float, parent=None):
        info = PHASES.get(phase, {})
        # Use just the name (emoji + action) without Phase prefix
        text = info.get('name', '?')
        super().__init__(text, parent)
        
        self.phase = phase
        self.setToolTip(info.get("tooltip", ""))
        self.setMinimumWidth(90)
        
        # Store original tooltip for restoration
        self._base_tooltip = info.get("tooltip", "")
    
    def set_phase_state(self, enabled: bool, reason: str = "") -> None:
        """Set button enabled state with optional reason tooltip."""
        self.setEnabled(enabled)
        if enabled:
            self.setToolTip(self._base_tooltip)
        else:
            tooltip = reason if reason else f"Requires products at Stage {int(self.phase) - 1}"
            self.setToolTip(tooltip)


class PhasePanel(QWidget):
    """Panel containing the 5-phase workflow buttons and progress."""
    
    # Signals
    phase_triggered = Signal(float)  # phase number (1, 1.5, 2, 2.5, 3, 4, 5)
    stop_requested = Signal()
    analyze_families_triggered = Signal()  # Phase 1.5 family analysis
    propagate_families_triggered = Signal()  # Propagate data to family members
    run_all_triggered = Signal()  # Run all phases 1-3
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self) -> None:
        """Create and layout child widgets."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)
        
        # Phase buttons row
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(8)
        
        # Create buttons for all phases including 2.5
        self.phase_buttons: dict[float, PhaseButton] = {}
        for phase in [1, 2, 2.5, 3, 4, 5]:
            btn = PhaseButton(phase)
            btn.clicked.connect(lambda checked, p=phase: self._on_phase_clicked(p))
            self.phase_buttons[phase] = btn
            buttons_layout.addWidget(btn)
        
        # Analyze Families button (Phase 1.5) - special optional analysis
        self.analyze_families_btn = QPushButton("🔗 Analyze Families")
        self.analyze_families_btn.setToolTip(
            "Phase 1.5: Quick analysis to group products by shared cross-references\n"
            "(HTTP-only, much faster than full scrape)"
        )
        self.analyze_families_btn.setMinimumWidth(120)
        self.analyze_families_btn.setStyleSheet("""
            QPushButton {
                background-color: #E8F5E9;
                border: 1px solid #81C784;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #C8E6C9;
            }
            QPushButton:disabled {
                background-color: #f0f0f0;
                border-color: #ccc;
                color: #999;
            }
        """)
        self.analyze_families_btn.clicked.connect(self.analyze_families_triggered.emit)
        buttons_layout.addWidget(self.analyze_families_btn)
        
        # Propagate to Families button - copy data from enriched products to family members
        self.propagate_families_btn = QPushButton("📤 Propagate Families")
        self.propagate_families_btn.setToolTip(
            "Auto-propagate PAI/ATL data from enriched products to all family members\n"
            "(For each family, finds the best-enriched product and copies its data)"
        )
        self.propagate_families_btn.setMinimumWidth(140)
        self.propagate_families_btn.setStyleSheet("""
            QPushButton {
                background-color: #E3F2FD;
                border: 1px solid #64B5F6;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QPushButton:hover {
                background-color: #BBDEFB;
            }
            QPushButton:disabled {
                background-color: #f0f0f0;
                border-color: #ccc;
                color: #999;
            }
        """)
        self.propagate_families_btn.clicked.connect(self.propagate_families_triggered.emit)
        buttons_layout.addWidget(self.propagate_families_btn)
        
        # Run All Phases 1-3 button - combined workflow for convenience
        self.run_all_btn = QPushButton("🚀 Run All (1→3)")
        self.run_all_btn.setToolTip(
            "Run Phase 1 (Scan) + Phase 2 (Scrape) + Phase 3 (PAI) automatically\n"
            "Processes all discovered products through the complete workflow."
        )
        self.run_all_btn.setMinimumWidth(120)
        self.run_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #FFF3E0;
                border: 1px solid #FFB74D;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #FFE0B2;
            }
            QPushButton:disabled {
                background-color: #f0f0f0;
                border-color: #ccc;
                color: #999;
            }
        """)
        self.run_all_btn.clicked.connect(self.run_all_triggered.emit)
        buttons_layout.addWidget(self.run_all_btn)
        
        # Stop button
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("Stop current operation (Escape)")
        self.stop_btn.setMinimumWidth(80)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        buttons_layout.addWidget(self.stop_btn)
        
        buttons_layout.addStretch(1)
        
        # Progress row - clean two-line display
        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(8)
        
        # Left side: Phase status label
        self.phase_label = QLabel("Ready")
        self.phase_label.setMinimumWidth(120)
        self.phase_label.setMaximumWidth(180)
        self.phase_label.setStyleSheet("font-weight: bold;")
        progress_layout.addWidget(self.phase_label)
        
        # Current item display (replaces annoying rolling bar)
        # Shows: "📥 Product Name" with URL in tooltip
        self.current_product_label = QLabel("")
        self.current_product_label.setStyleSheet("""
            QLabel {
                color: #2196F3; 
                font-size: 11px; 
                min-width: 200px;
                max-width: 350px;
                padding: 2px 8px;
                background-color: #E3F2FD;
                border-radius: 4px;
            }
        """)
        self.current_product_label.setWordWrap(False)
        progress_layout.addWidget(self.current_product_label)
        
        # Spacer
        progress_layout.addSpacing(12)
        
        # Right side: Overall progress
        self.stats_label = QLabel("Added: 0 | Failed: 0")
        self.stats_label.setStyleSheet("color: #666; font-size: 11px;")
        progress_layout.addWidget(self.stats_label)
        
        # Overall progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m")
        self.progress_bar.setMinimumWidth(150)
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 4px;
                text-align: center;
                background-color: #f0f0f0;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 3px;
            }
        """)
        progress_layout.addWidget(self.progress_bar)
        
        # ETA label
        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("color: #999; font-size: 10px;")
        progress_layout.addWidget(self.eta_label)
        
        progress_layout.addStretch(1)
        
        # Add to main layout
        main_layout.addLayout(buttons_layout)
        main_layout.addLayout(progress_layout)
    
    def _connect_signals(self) -> None:
        """Wire up internal signals."""
        pass  # Signals connected in _setup_ui
    
    def _on_phase_clicked(self, phase: float) -> None:
        """Handle phase button click."""
        self.phase_triggered.emit(phase)
    
    # Public API
    
    def set_running(self, running: bool, phase: float = 0) -> None:
        """Set running state - disables phase buttons, enables stop."""
        self._running = running
        
        for btn in self.phase_buttons.values():
            btn.setEnabled(not running)
        
        # Also disable analyze families, propagate, and run all buttons when running
        self.analyze_families_btn.setEnabled(not running)
        self.propagate_families_btn.setEnabled(not running)
        self.run_all_btn.setEnabled(not running)
        
        self.stop_btn.setEnabled(running)
        
        if running and phase > 0:
            # Handle Phase 1.5 specially
            if phase == 1.5:
                self.phase_label.setText("Phase 1.5: Analyzing Families...")
            # Handle combined run
            elif phase == 0.5:  # Special indicator for "Run All"
                self.phase_label.setText("Running All Phases 1→3...")
            else:
                info = PHASES.get(phase, {})
                self.phase_label.setText(f"Phase {int(phase) if phase == int(phase) else phase}: {info.get('name', 'Running')}...")
        elif not running:
            self.phase_label.setText("Ready")
    
    def update_phase_states(
        self,
        stages_in_selection: set[int],
        has_products: bool = False,
    ) -> None:
        """Update which phase buttons are enabled based on selection.
        
        Args:
            stages_in_selection: Set of stage numbers present in selected rows
            has_products: Whether any products are loaded
        """
        if self._running:
            return
        
        # Phase 1 (Scan) - always enabled
        self.phase_buttons[1].set_phase_state(True)
        
        # Phase 2 (Scrape) - requires Stage 1 OR Stage 2 selection (rescrape)
        has_stage_1 = 1 in stages_in_selection
        has_stage_2 = 2 in stages_in_selection
        self.phase_buttons[2].set_phase_state(
            has_stage_1 or has_stage_2,
            "Select Stage 🔍 or 🧱 rows first" if not (has_stage_1 or has_stage_2) else ""
        )
        
        # Phase 2.5 (ATL) - requires Stage 2+ selection (any scraped products)
        has_stage_2_plus = any(s >= 2 for s in stages_in_selection)
        self.phase_buttons[2.5].set_phase_state(
            has_stage_2_plus,
            "Select Stage 🧱+ rows first (after scrape)" if not has_stage_2_plus else ""
        )
        
        # Phase 3 (PAI) - requires Stage 2 or Stage 3 selection (allow refresh)
        has_stage_3 = 3 in stages_in_selection
        enable_phase3 = has_stage_2 or has_stage_3
        self.phase_buttons[3].set_phase_state(
            enable_phase3,
            "Select Stage 🧱 or 📦 rows first" if not enable_phase3 else ""
        )
        
        # Phase 4 (Review) - requires Stage 3 selection
        self.phase_buttons[4].set_phase_state(
            has_stage_3,
            "Select Stage 📦 rows first" if not has_stage_3 else ""
        )
        
        # Phase 5 (Upload) - requires Stage 4 selection
        has_stage_4 = 4 in stages_in_selection
        self.phase_buttons[5].set_phase_state(
            has_stage_4,
            "Select Stage 🧠 rows first" if not has_stage_4 else ""
        )
        
        # Analyze Families button - enabled when any products exist
        self.analyze_families_btn.setEnabled(has_products)
        
        # Propagate Families button - enabled when products have families assigned
        has_families = any(
            str(getattr(p, "family_id", "") or "").startswith("FAM-")
            for p in products.values()
        ) if products else False
        self.propagate_families_btn.setEnabled(has_families)
    
    def update_progress(self, current: int, total: int, url: str = "", stats: dict = None) -> None:
        """Update progress bars and current item display.
        
        Args:
            current: Current item index (1-based)
            total: Total number of items
            url: Current URL or product being processed
            stats: Optional dict with 'added', 'skipped', 'failed' counts
        """
        # Overall progress bar
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            self.progress_bar.setFormat(f"{current} / {total}")
        else:
            # Indeterminate for scanning - but still show count
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(min(current, 99))  # Show some progress
            self.progress_bar.setFormat(f"{current} found...")
        
        # Current product display (replaced the annoying rolling bar)
        if url:
            # Extract product name from URL for cleaner display
            display_name = url
            if "/" in url:
                # Try to get last meaningful part of URL
                parts = url.rstrip("/").split("/")
                for part in reversed(parts):
                    if part and part not in ("product", "products", ""):
                        display_name = part.replace("-", " ").title()[:50]
                        break
            
            # Show current item with progress context
            if total > 0:
                self.current_product_label.setText(f"📥 [{current}/{total}] {display_name}")
            else:
                self.current_product_label.setText(f"📥 {display_name}")
            self.current_product_label.setToolTip(f"URL: {url}")
            self.current_product_label.setVisible(True)
        else:
            if total > 0 and current >= total:
                self.current_product_label.setText("✓ Complete")
                self.current_product_label.setStyleSheet("""
                    QLabel {
                        color: #228B22; 
                        font-size: 11px; 
                        min-width: 200px;
                        max-width: 350px;
                        padding: 2px 8px;
                        background-color: #E8F5E9;
                        border-radius: 4px;
                    }
                """)
            else:
                self.current_product_label.setText("")
            self.current_product_label.setToolTip("")
        
        # Stats display
        if stats:
            added = stats.get("added", 0)
            failed = stats.get("failed", 0)
            skipped = stats.get("skipped", 0)
            
            parts = [f"✓ {added}"]
            if skipped > 0:
                parts.append(f"⊘ {skipped}")
            if failed > 0:
                parts.append(f"✗ {failed}")
            
            self.stats_label.setText(" | ".join(parts))
            
            # Color based on status
            if failed > 0:
                self.stats_label.setStyleSheet("color: #cc0000; font-size: 11px;")
            elif added > 0:
                self.stats_label.setStyleSheet("color: #228B22; font-size: 11px;")
            else:
                self.stats_label.setStyleSheet("color: #666; font-size: 11px;")
    
    def update_eta(self, eta_text: str) -> None:
        """Update ETA display."""
        if eta_text:
            self.eta_label.setText(f"⏱ {eta_text}")
        else:
            self.eta_label.setText("")
    
    def set_phase_label(self, text: str) -> None:
        """Set the phase label text."""
        self.phase_label.setText(text)
    
    def reset_progress(self) -> None:
        """Reset progress bars and labels."""
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%v / %m")
        
        # Reset current product label styling
        self.current_product_label.setText("")
        self.current_product_label.setToolTip("")
        self.current_product_label.setStyleSheet("""
            QLabel {
                color: #2196F3; 
                font-size: 11px; 
                min-width: 200px;
                max-width: 350px;
                padding: 2px 8px;
                background-color: #E3F2FD;
                border-radius: 4px;
            }
        """)
        
        self.stats_label.setText("✓ 0")
        self.stats_label.setStyleSheet("color: #666; font-size: 11px;")
        
        self.eta_label.setText("")
        
        self.phase_label.setText("Ready")
