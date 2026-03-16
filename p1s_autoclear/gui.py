"""
GUI for P1S Auto-Clear: load 3MF, configure cooldown/push heights, export.
"""

import os
import platform
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path

from . import __version__
from .injector import DEFAULT_TEMPLATE
from .preview import (
    compute_sweep_z,
    draw_preview_on_canvas,
    get_max_z_from_3mf,
)
from .processor import get_autoclear_settings, process_3mf
from .settings import (
    BUILTIN_PROFILES,
    apply_settings_to_gui,
    autoclear_to_gui_settings,
    delete_profile,
    load_app_config,
    load_last_settings,
    load_profile,
    list_profiles,
    save_app_config,
    save_last_settings,
    save_profile,
    settings_to_dict,
)


def _create_tooltip(widget, text: str) -> None:
    """Create a hover tooltip for a widget.
    Binds Enter/Leave to show/hide a small popup with the given text.
    """
    tip = [None]

    def on_enter(event):
        tip[0] = tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")
        tk.Label(
            tw, text=text, justify=tk.LEFT,
            background="#3C3C42", foreground="#E0E0E0",
            relief=tk.SOLID, borderwidth=1, font=("", 9),
        ).pack(padx=4, pady=2)

    def on_leave(event):
        if tip[0]:
            tip[0].destroy()
            tip[0] = None

    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


# Bambu Lab dark theme colors (branded)
_BAMBU_BG = "#2D2D31"           # Main background (Bambu Studio dark)
_BAMBU_BG2 = "#3C3C42"          # Secondary / raised panels
_BAMBU_FG = "#E0E0E0"           # Primary text
_BAMBU_FG_MUTED = "#A6A9AA"     # Muted text
_BAMBU_ACCENT = "#FF6A13"       # Bambu brand orange
_BAMBU_ACCENT_HOVER = "#FF8533"
_BAMBU_ENTRY_BG = "#404040"
_BAMBU_CANVAS_BG = "#252526"
_BAMBU_BORDER = "#505050"


def _open_folder_in_explorer(folder_path: Path) -> None:
    """Open the given folder in the system file manager (Windows Explorer, Finder, etc.)."""
    try:
        path_str = str(folder_path.resolve())
        if os.name == "nt":
            os.startfile(path_str)
        elif platform.system() == "Darwin":
            subprocess.run(["open", path_str], check=False)
        else:
            subprocess.run(["xdg-open", path_str], check=False)
    except OSError:
        pass


def create_gui() -> tk.Tk:
    """Create and return the main P1S Auto-Clear GUI window.
    Builds the layout with file load, cooldown/loop/purge settings, push height,
    bending mode, profiles, G-code template, and Export/Reset/Help buttons.
    Loads last-used settings on startup; saves them on close.
    """
    root = tk.Tk()
    root.title(f"P1S Auto-Clear v{__version__} - NHDFARM-Style G-code Injector")
    root.minsize(580, 680)
    root.geometry("640x750")
    root.resizable(True, False)  # Allow horizontal resize only; prevent vertical shrinking
    root.configure(bg=_BAMBU_BG)

    # Bambu dark theme: ttk styles
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(".", background=_BAMBU_BG, foreground=_BAMBU_FG)
    style.configure("TFrame", background=_BAMBU_BG)
    style.configure("TLabel", background=_BAMBU_BG, foreground=_BAMBU_FG)
    style.configure("TLabelFrame", background=_BAMBU_BG, foreground=_BAMBU_FG)
    style.configure("TLabelFrame.Label", background=_BAMBU_BG, foreground=_BAMBU_FG)
    style.configure("TButton", background=_BAMBU_BG2, foreground=_BAMBU_FG)
    style.map("TButton", background=[("active", _BAMBU_ACCENT), ("pressed", _BAMBU_ACCENT_HOVER)])
    style.configure("TRadiobutton", background=_BAMBU_BG, foreground=_BAMBU_FG)
    style.map("TRadiobutton", background=[("active", _BAMBU_BG)])
    style.configure("TCheckbutton", background=_BAMBU_BG, foreground=_BAMBU_FG)
    style.map("TCheckbutton", background=[("active", _BAMBU_BG)])
    style.configure("TEntry", fieldbackground=_BAMBU_ENTRY_BG, foreground=_BAMBU_FG, insertcolor=_BAMBU_FG)
    style.configure("TCombobox", fieldbackground=_BAMBU_ENTRY_BG, foreground=_BAMBU_FG, background=_BAMBU_BG2)
    style.map("TCombobox", fieldbackground=[("readonly", _BAMBU_ENTRY_BG)])
    style.configure("TNotebook", background=_BAMBU_BG)
    style.configure("TNotebook.Tab", background=_BAMBU_BG2, foreground=_BAMBU_FG)
    style.map("TNotebook.Tab", background=[("selected", _BAMBU_ACCENT)])

    # State
    input_path_var = tk.StringVar()
    cooldown_mode_var = tk.StringVar(value="temp")
    cooldown_time_var = tk.StringVar(value="180")
    cooldown_temp_var = tk.StringVar(value="35")
    loop_count_var = tk.StringVar(value="1")
    remove_purge_var = tk.BooleanVar(value=False)
    fans_during_cooldown_var = tk.BooleanVar(value=False)
    skip_retraction_between_loops_var = tk.BooleanVar(value=True)
    reheat_between_loops_var = tk.BooleanVar(value=False)
    preheat_bed_temp_var = tk.StringVar(value="70")
    preheat_nozzle_temp_var = tk.StringVar(value="150")
    push_height_mode_var = tk.StringVar(value="auto")
    push_height_mm_var = tk.StringVar(value="5")
    push_height_offset_var = tk.StringVar(value="20")
    auto_sweep_z_var = tk.DoubleVar(value=30)  # Slider: sweep Z (mm from bed); default ~50-20
    last_max_z: list[float | None] = [None]  # Mutable holder for max part height (for slider range)
    bending_mode_var = tk.StringVar(value="on")
    push_mode_var = tk.StringVar(value="center_and_sweep")
    recommended_label_var = tk.StringVar(value="mm (recommended ≥5)")
    # App config (Settings tab)
    default_export_path_var = tk.StringVar(value="")
    open_export_folder_var = tk.BooleanVar(value=False)
    default_import_path_var = tk.StringVar(value="")

    # Main layout
    main = ttk.Frame(root, padding=(10, 10, 10, 10))
    main.pack(fill=tk.BOTH, expand=True)

    # Bottom buttons: pack first with side=BOTTOM so they always stay visible
    btn_frame = ttk.Frame(main, padding=(0, 12, 0, 0))
    btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))

    notebook = ttk.Notebook(main)
    notebook.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

    main_tab = ttk.Frame(notebook, padding=(4, 8))
    notebook.add(main_tab, text="Main")
    template_tab = ttk.Frame(notebook, padding=(4, 8, 4, 0))
    notebook.add(template_tab, text="G-code Template")
    settings_tab = ttk.Frame(notebook, padding=(4, 8))
    notebook.add(settings_tab, text="Settings")

    # --- File section ---
    file_frame = ttk.LabelFrame(main_tab, text="3MF File", padding=8)
    file_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
    main_tab.columnconfigure(0, weight=1)

    ttk.Entry(file_frame, textvariable=input_path_var, width=42).pack(
        side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5)
    )

    def load_file():
        path = filedialog.askopenfilename(
            title="Load 3MF",
            filetypes=[("3MF files", "*.3mf"), ("All files", "*.*")],
        )
        if path:
            input_path_var.set(path)
            try:
                autoclear = get_autoclear_settings(Path(path))
                if autoclear:
                    gui_data = autoclear_to_gui_settings(autoclear)
                    if gui_data:
                        apply_settings_to_gui(
                            gui_data,
                            cooldown_mode_var=cooldown_mode_var,
                            cooldown_time_var=cooldown_time_var,
                            cooldown_temp_var=cooldown_temp_var,
                            push_height_mode_var=push_height_mode_var,
                            push_height_mm_var=push_height_mm_var,
                            push_height_offset_var=push_height_offset_var,
                            bending_mode_var=bending_mode_var,
                            push_mode_var=push_mode_var,
                            loop_count_var=loop_count_var,
                            remove_purge_var=remove_purge_var,
                            skip_retraction_between_loops_var=skip_retraction_between_loops_var,
                            fans_during_cooldown_var=fans_during_cooldown_var,
                            reheat_between_loops_var=reheat_between_loops_var,
                            preheat_bed_temp_var=preheat_bed_temp_var,
                            preheat_nozzle_temp_var=preheat_nozzle_temp_var,
                            template_text=template_text,
                            default_template=DEFAULT_TEMPLATE.strip(),
                        )
            except Exception as e:
                messagebox.showwarning("Load 3MF", f"Could not load autoclear settings from file:\n{e}")
            # Set Auto push height to recommended (5 mm from bed) when file is loaded
            path_obj = Path(path)
            max_z = get_max_z_from_3mf(path_obj)
            if max_z is not None and max_z > 0:
                recommended_sweep_z = 5.0 if max_z >= 5 else max(1.0, max_z)
                last_max_z[0] = max_z
                push_height_mode_var.set("auto")
                auto_sweep_z_var.set(recommended_sweep_z)
                push_height_offset_var.set(str(max(1, int(max_z - recommended_sweep_z))))
                auto_slider.config(to=max(1, int(max_z)))
            refresh_preview()

    ttk.Button(file_frame, text="Load 3MF", command=load_file).pack(side=tk.LEFT, padx=(0, 4))

    # --- Top row: Cooldown | Settings ---
    top_row = ttk.Frame(main_tab)
    top_row.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 4))

    # Cooldown (narrower)
    cool_frame = ttk.LabelFrame(top_row, text="Cooldown", padding=2)
    cool_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
    ttk.Radiobutton(cool_frame, text="Time (sec)", variable=cooldown_mode_var, value="time").pack(anchor=tk.W)
    time_row = ttk.Frame(cool_frame)
    time_row.pack(fill=tk.X, padx=(16, 0))
    ttk.Label(time_row, text="Wait:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Entry(time_row, textvariable=cooldown_time_var, width=6).pack(side=tk.LEFT)
    ttk.Radiobutton(cool_frame, text="Temp (°C)", variable=cooldown_mode_var, value="temp").pack(anchor=tk.W, pady=(4, 0))
    temp_row = ttk.Frame(cool_frame)
    temp_row.pack(fill=tk.X, padx=(16, 0))
    ttk.Label(temp_row, text="Bed:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Entry(temp_row, textvariable=cooldown_temp_var, width=6).pack(side=tk.LEFT)
    fans_cb = ttk.Checkbutton(
        cool_frame,
        text="Fans 100% during cooldown",
        variable=fans_during_cooldown_var,
    )
    fans_cb.pack(anchor=tk.W, pady=(4, 0))
    _create_tooltip(fans_cb, "Runs all fans (part cooling, auxiliary, chamber) at 100% during cooldown to speed up bed cooling.")

    # Settings (Loop count + Skip purge + Skip bed leveling)
    settings_frame = ttk.LabelFrame(top_row, text="Settings", padding=2)
    settings_frame.pack(side=tk.LEFT, fill=tk.Y, expand=True)
    loop_row = ttk.Frame(settings_frame)
    loop_row.pack(fill=tk.X)
    ttk.Label(loop_row, text="Loops:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Entry(loop_row, textvariable=loop_count_var, width=4).pack(side=tk.LEFT)
    ttk.Label(settings_frame, text="(1 = single print)", font=("", 8)).pack(anchor=tk.W)
    purge_cb = ttk.Checkbutton(
        settings_frame,
        text="Skip nozzle load line",
        variable=remove_purge_var,
    )
    purge_cb.pack(anchor=tk.W)
    _create_tooltip(purge_cb, "Removes the nozzle load line (Bambu's filament purge at the front of the bed) from the start G-code. Saves time and filament by skipping this step before each print.")
    skip_retract_cb = ttk.Checkbutton(
        settings_frame,
        text="Skip retraction between loops",
        variable=skip_retraction_between_loops_var,
    )
    skip_retract_cb.pack(anchor=tk.W)
    _create_tooltip(skip_retract_cb, "When enabled with multiple loops: filament stays loaded between loops; retraction only runs after the last loop. Saves time and avoids re-load/purge each loop.")
    reheat_cb = ttk.Checkbutton(
        settings_frame,
        text="Reheat bed/nozzle between loops",
        variable=reheat_between_loops_var,
    )
    reheat_cb.pack(anchor=tk.W)
    _create_tooltip(reheat_cb, "Starts heating bed and nozzle during the sweep when using multiple loops, so the next print begins sooner. Only applies when Loops > 1.")
    reheat_row = ttk.Frame(settings_frame)
    reheat_row.pack(fill=tk.X, padx=(16, 0))
    ttk.Label(reheat_row, text="Bed °C:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Entry(reheat_row, textvariable=preheat_bed_temp_var, width=4).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Label(reheat_row, text="Nozzle °C:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Entry(reheat_row, textvariable=preheat_nozzle_temp_var, width=4).pack(side=tk.LEFT)

    # Push options (Bending + Push mode) - stacked, before Push Height
    push_opts_frame = ttk.LabelFrame(top_row, text="Push options", padding=2)
    push_opts_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
    bend_row = ttk.Frame(push_opts_frame)
    bend_row.pack(anchor=tk.W)
    ttk.Label(bend_row, text="Bending:").pack(side=tk.LEFT, padx=(0, 4))
    bending_combo = ttk.Combobox(
        bend_row,
        textvariable=bending_mode_var,
        values=("on", "off"),
        width=6,
        state="readonly",
    )
    bending_combo.pack(side=tk.LEFT)
    _create_tooltip(bending_combo, "On: NHDFARM Z flex (Z235↔Z200 ×6). Off: no bending.")
    push_mode_row = ttk.Frame(push_opts_frame)
    push_mode_row.pack(anchor=tk.W, pady=(4, 0))
    ttk.Label(push_mode_row, text="Push:").pack(side=tk.LEFT, padx=(0, 4))
    push_mode_combo = ttk.Combobox(
        push_mode_row,
        textvariable=push_mode_var,
        values=("center_only", "center_and_sweep"),
        width=18,
        state="readonly",
    )
    push_mode_combo.pack(side=tk.LEFT)
    _create_tooltip(push_mode_combo, "Center only: two pushes at center X. Center + sweep: center pushes plus right-to-left rake passes.")

    # --- Push height section (NHDFARM-style) ---
    heights_frame = ttk.LabelFrame(main_tab, text="Push Height", padding=6)
    heights_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N), pady=(0, 6))
    main_tab.rowconfigure(2, weight=1)  # Push height expands; profile row stays fixed

    # Two columns: controls (left) | preview (right)
    push_columns = ttk.Frame(heights_frame)
    push_columns.pack(fill=tk.BOTH, expand=True)

    # Left column: push height controls (Bending + Push are in top row Push options)
    controls_col = ttk.Frame(push_columns)
    controls_col.pack(side=tk.LEFT, fill=tk.Y)

    recommended_label_var = tk.StringVar(value="mm (recommended ≥5)")

    # Auto slider
    auto_row = ttk.Frame(controls_col)
    auto_row.pack(anchor=tk.W, pady=(6, 0))
    ttk.Radiobutton(
        auto_row,
        text="Auto",
        variable=push_height_mode_var,
        value="auto",
    ).pack(side=tk.LEFT, padx=(0, 6))
    auto_slider = tk.Scale(
        auto_row,
        from_=1,
        to=50,
        orient=tk.HORIZONTAL,
        variable=auto_sweep_z_var,
        resolution=1,
        length=140,
        showvalue=True,
        bg=_BAMBU_BG,
        fg=_BAMBU_FG,
        troughcolor=_BAMBU_BG2,
        activebackground=_BAMBU_ACCENT,
        highlightthickness=0,
    )
    auto_slider.pack(side=tk.LEFT, padx=(0, 12))

    # Manual entry (below Auto, with space)
    manual_row = ttk.Frame(controls_col)
    manual_row.pack(anchor=tk.W, pady=(14, 0))
    ttk.Radiobutton(
        manual_row,
        text="Manual",
        variable=push_height_mode_var,
        value="manual",
    ).pack(side=tk.LEFT, padx=(0, 4))
    manual_entry = ttk.Entry(manual_row, textvariable=push_height_mm_var, width=5)
    manual_entry.pack(side=tk.LEFT, padx=(0, 2))
    ttk.Label(manual_row, text="mm").pack(side=tk.LEFT)

    push_ht_tip = ttk.Label(
        controls_col,
        text="Larger offset\n= push closer to bed\nUse Manual for fixed Z height.",
        font=("", 8),
        justify=tk.LEFT,
    )
    push_ht_tip.pack(anchor=tk.W, pady=(2, 0))
    _create_tooltip(push_ht_tip, "Auto: sweep at (max part height - offset) mm. Larger offset = lower Z = closer to bed. If push misses the part, try 25–30 mm.")

    # Right column: side view preview
    preview_col = ttk.LabelFrame(push_columns, text="Side view", padding=(6, 4))
    preview_col.pack(side=tk.LEFT, padx=(12, 0), fill=tk.BOTH, expand=True)
    preview_canvas = tk.Canvas(
        preview_col,
        width=180,
        height=120,
        bg=_BAMBU_CANVAS_BG,
        highlightthickness=1,
        highlightbackground=_BAMBU_BORDER,
    )
    preview_canvas.pack(anchor=tk.W, pady=(4, 6))
    preview_text = tk.Text(
        preview_col,
        height=4,
        width=24,
        wrap=tk.WORD,
        state=tk.DISABLED,
        font=("", 10),
        relief=tk.FLAT,
        bg=_BAMBU_CANVAS_BG,
        fg=_BAMBU_FG,
        cursor="arrow",
    )
    preview_text.pack(anchor=tk.W, fill=tk.BOTH, expand=False)
    # Placeholder so widget reserves full height (4 lines) before refresh_preview runs
    preview_text.config(state=tk.NORMAL)
    preview_text.insert("1.0", "Load a 3MF to see\nsweep preview.\n\n\n")
    preview_text.config(state=tk.DISABLED)

    def sync_auto_slider_from_offset():
        """Update slider range and value from max_z and stored offset."""
        path = input_path_var.get().strip() or None
        max_z_val = get_max_z_from_3mf(Path(path)) if path else None
        max_z_val = max_z_val if max_z_val is not None and max_z_val > 0 else 50.0
        last_max_z[0] = max_z_val
        auto_slider.config(to=max(1, int(max_z_val)))
        if push_height_mode_var.get() == "auto":
            try:
                offset = int(push_height_offset_var.get().strip() or 20)
                sweep_z = max(1.0, min(max_z_val, max_z_val - offset))
                auto_sweep_z_var.set(sweep_z)
            except (ValueError, AttributeError):
                auto_sweep_z_var.set(max(1, min(30, max_z_val - 20)))

    def on_auto_slider_change(val):
        max_z_val = last_max_z[0] or 50.0
        try:
            sweep_z = max(1.0, min(max_z_val, float(val)))
            offset = max(1, int(max_z_val - sweep_z))
            push_height_offset_var.set(str(offset))
        except (ValueError, TypeError):
            pass

    auto_slider.config(command=on_auto_slider_change)

    def refresh_preview(*_args):
        path = input_path_var.get().strip() or None
        path_obj = Path(path) if path else None
        max_z_val = get_max_z_from_3mf(path_obj) if path_obj else None
        last_max_z[0] = max_z_val
        # Sync slider range and value when path or settings change
        if push_height_mode_var.get() == "auto":
            max_for_range = max_z_val if max_z_val and max_z_val > 0 else 50.0
            auto_slider.config(to=max(1, int(max_for_range)))
            try:
                offset = int(push_height_offset_var.get().strip() or 20)
                sweep_z = max(1.0, min(max_for_range, max_for_range - offset))
                if abs(auto_sweep_z_var.get() - sweep_z) > 0.5:
                    auto_sweep_z_var.set(sweep_z)
            except (ValueError, AttributeError):
                pass
        offset_for_preview = push_height_offset_var.get()
        if push_height_mode_var.get() == "auto" and max_z_val is not None and max_z_val > 0:
            offset_for_preview = str(int(max(1, min(max_z_val - 1, max_z_val - auto_sweep_z_var.get()))))
        info = draw_preview_on_canvas(
            preview_canvas,
            path=path_obj,
            push_height_mode=push_height_mode_var.get(),
            push_height_mm=push_height_mm_var.get(),
            push_height_offset_mm=offset_for_preview,
            width=180,
            height=120,
        )
        preview_text.config(state=tk.NORMAL)
        preview_text.delete("1.0", tk.END)
        preview_text.insert("1.0", info or "Load a 3MF to see sweep preview")
        preview_text.config(state=tk.DISABLED)

    def _schedule_refresh(*_a):
        root.after(50, refresh_preview)

    for var in (push_height_mode_var, push_height_mm_var, push_height_offset_var, auto_sweep_z_var):
        var.trace_add("write", _schedule_refresh)
    def clamp_manual_height(*_):
        try:
            v = float(push_height_mm_var.get().strip())
            if v < 1:
                push_height_mm_var.set("1")
        except (ValueError, AttributeError):
            pass

    manual_entry.bind("<KeyRelease>", lambda e: root.after(50, refresh_preview))
    manual_entry.bind("<FocusOut>", lambda e: (clamp_manual_height(), refresh_preview()))
    input_path_var.trace_add("write", _schedule_refresh)

    # --- Profile section ---
    profile_frame = ttk.LabelFrame(main_tab, text="Profile (filament type)", padding=8)
    profile_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
    main_tab.rowconfigure(3, minsize=52)  # Profile row never squishes

    def refresh_profile_combo():
        custom = list_profiles()
        # Custom profiles override built-ins with same name (no duplicate entries)
        builtins_to_show = [b for b in BUILTIN_PROFILES if b not in custom]
        combo["values"] = ["(Last used)", *builtins_to_show, *custom]

    profile_var = tk.StringVar(value="(Last used)")
    combo = ttk.Combobox(profile_frame, textvariable=profile_var, width=25, state="readonly")
    refresh_profile_combo()
    combo.pack(side=tk.LEFT, padx=(0, 5))

    def load_selected_profile():
        sel = profile_var.get().strip()
        if not sel or sel == "(Last used)":
            data = load_last_settings()
        elif sel in list_profiles():
            # Custom profile (file on disk) - use it even if name matches built-in
            data = load_profile(sel)
        elif sel in BUILTIN_PROFILES:
            data = dict(BUILTIN_PROFILES[sel])
        else:
            data = load_profile(sel)
        if data:
            apply_settings_to_gui(
                data,
                cooldown_mode_var=cooldown_mode_var,
                cooldown_time_var=cooldown_time_var,
                cooldown_temp_var=cooldown_temp_var,
                push_height_mode_var=push_height_mode_var,
                push_height_mm_var=push_height_mm_var,
                push_height_offset_var=push_height_offset_var,
                bending_mode_var=bending_mode_var,
                push_mode_var=push_mode_var,
                loop_count_var=loop_count_var,
                remove_purge_var=remove_purge_var,
                fans_during_cooldown_var=fans_during_cooldown_var,
                skip_retraction_between_loops_var=skip_retraction_between_loops_var,
                reheat_between_loops_var=reheat_between_loops_var,
                preheat_bed_temp_var=preheat_bed_temp_var,
                preheat_nozzle_temp_var=preheat_nozzle_temp_var,
                template_text=template_text,
                default_template=DEFAULT_TEMPLATE.strip(),
            )
            refresh_preview()

    def _current_settings_dict():
        return settings_to_dict(
            cooldown_mode=cooldown_mode_var.get(),
            cooldown_time=cooldown_time_var.get().strip(),
            cooldown_temp=cooldown_temp_var.get().strip(),
            push_height_mode=push_height_mode_var.get(),
            push_height_mm=push_height_mm_var.get().strip(),
            push_height_offset_mm=push_height_offset_var.get().strip(),
            push_mode=push_mode_var.get(),
            loop_count=loop_count_var.get().strip(),
            remove_purge=remove_purge_var.get(),
            fans_during_cooldown=fans_during_cooldown_var.get(),
            skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
            reheat_between_loops=reheat_between_loops_var.get(),
            preheat_bed_temp=preheat_bed_temp_var.get().strip(),
            preheat_nozzle_temp=preheat_nozzle_temp_var.get().strip(),
            bending_mode=bending_mode_var.get(),
            template=template_text.get("1.0", tk.END).strip(),
        )

    def save_profile_to_selected():
        """Save current settings to the selected profile (updates built-in or custom)."""
        sel = profile_var.get().strip()
        if not sel or sel == "(Last used)":
            messagebox.showinfo("Profile", "Select a named profile to update, or use 'Save as Profile' to create a new one.")
            return
        save_profile(sel, _current_settings_dict())
        refresh_profile_combo()
        profile_var.set(sel)
        messagebox.showinfo("Profile", f"Saved '{sel}'.")

    def save_as_profile():
        name = simpledialog.askstring("Save Profile", "Profile name (e.g. PLA, PETG, My Custom):", parent=root)
        if not name or not name.strip():
            return
        name = name.strip()
        save_profile(name, _current_settings_dict())
        refresh_profile_combo()
        profile_var.set(name)
        messagebox.showinfo("Profile", f"Saved as '{name}'.")

    def delete_selected_profile():
        sel = profile_var.get().strip()
        if not sel or sel == "(Last used)":
            messagebox.showinfo("Profile", "Cannot delete '(Last used)'.")
            return
        # Allow delete if it's a custom profile (file on disk), even if name matches a built-in
        if sel in BUILTIN_PROFILES and sel not in list_profiles():
            messagebox.showinfo("Profile", "Built-in profiles cannot be deleted. (Custom profiles with the same name can be deleted.)")
            return
        if messagebox.askyesno("Delete Profile", f"Delete profile '{sel}'?"):
            if delete_profile(sel):
                refresh_profile_combo()
                profile_var.set("(Last used)")
                messagebox.showinfo("Profile", "Deleted.")
            else:
                messagebox.showerror("Profile", "Could not delete profile (file not found).")

    ttk.Button(profile_frame, text="Load Profile", command=load_selected_profile).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(profile_frame, text="Save Profile", command=save_profile_to_selected).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(profile_frame, text="Save as Profile", command=save_as_profile).pack(side=tk.LEFT, padx=(0, 8))
    ttk.Button(profile_frame, text="Delete Profile", command=delete_selected_profile).pack(side=tk.LEFT)

    # --- G-code template (in separate tab) ---
    template_frame = ttk.LabelFrame(template_tab, text="G-code Template (placeholders: {cooldown}, {bending}, {sweeps})", padding=8)
    template_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 0))

    template_text = tk.Text(
        template_frame, height=12, width=55, wrap=tk.NONE,
        bg=_BAMBU_ENTRY_BG, fg=_BAMBU_FG, insertbackground=_BAMBU_FG,
        selectbackground=_BAMBU_ACCENT, selectforeground=_BAMBU_FG,
    )
    template_text.pack(fill=tk.BOTH, expand=True)
    template_text.insert("1.0", DEFAULT_TEMPLATE.strip())
    ttk.Button(template_frame, text="Reset Template", command=lambda: (
        template_text.delete("1.0", tk.END),
        template_text.insert("1.0", DEFAULT_TEMPLATE.strip()),
    )).pack(anchor=tk.W, pady=(4, 0))

    # --- Settings tab ---
    def _browse_export_dir():
        d = filedialog.askdirectory(title="Default export location", parent=root)
        if d:
            default_export_path_var.set(d)

    def _browse_import_dir():
        d = filedialog.askdirectory(title="Import/watch location (for future automation)", parent=root)
        if d:
            default_import_path_var.set(d)

    settings_scroll = ttk.Frame(settings_tab, padding=(4, 0))
    settings_scroll.pack(fill=tk.BOTH, expand=True)
    exp_frame = ttk.LabelFrame(settings_scroll, text="Export", padding=8)
    exp_frame.pack(fill=tk.X, pady=(0, 8))
    exp_row = ttk.Frame(exp_frame)
    exp_row.pack(fill=tk.X)
    ttk.Entry(exp_row, textvariable=default_export_path_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
    ttk.Button(exp_row, text="Browse...", command=_browse_export_dir).pack(side=tk.LEFT)
    ttk.Label(exp_frame, text="Default folder for Export 3MF dialog. Leave empty to use the loaded file's folder.", font=("", 8)).pack(anchor=tk.W)
    open_folder_cb = ttk.Checkbutton(
        exp_frame,
        text="Open export folder after exporting",
        variable=open_export_folder_var,
    )
    open_folder_cb.pack(anchor=tk.W, pady=(8, 0))
    _create_tooltip(open_folder_cb, "Opens the folder containing the exported file in your file manager after a successful export.")

    imp_frame = ttk.LabelFrame(settings_scroll, text="Import (for future automation)", padding=8)
    imp_frame.pack(fill=tk.X, pady=(0, 8))
    imp_row = ttk.Frame(imp_frame)
    imp_row.pack(fill=tk.X)
    ttk.Entry(imp_row, textvariable=default_import_path_var, width=50).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
    ttk.Button(imp_row, text="Browse...", command=_browse_import_dir).pack(side=tk.LEFT)
    ttk.Label(imp_frame, text="Watch folder for future auto-open: when a 3MF file is detected here, it will automatically load.", font=("", 8)).pack(anchor=tk.W)

    # Load last-used settings on startup
    last = load_last_settings()
    if last:
        apply_settings_to_gui(
            last,
            cooldown_mode_var=cooldown_mode_var,
            cooldown_time_var=cooldown_time_var,
            cooldown_temp_var=cooldown_temp_var,
            push_height_mode_var=push_height_mode_var,
            push_height_mm_var=push_height_mm_var,
            push_height_offset_var=push_height_offset_var,
            bending_mode_var=bending_mode_var,
            push_mode_var=push_mode_var,
            loop_count_var=loop_count_var,
            remove_purge_var=remove_purge_var,
            fans_during_cooldown_var=fans_during_cooldown_var,
            skip_retraction_between_loops_var=skip_retraction_between_loops_var,
            reheat_between_loops_var=reheat_between_loops_var,
            preheat_bed_temp_var=preheat_bed_temp_var,
            preheat_nozzle_temp_var=preheat_nozzle_temp_var,
            template_text=template_text,
            default_template=DEFAULT_TEMPLATE.strip(),
        )
    refresh_preview()

    # Load app config (Settings tab)
    app_cfg = load_app_config()
    if app_cfg:
        if "default_export_path" in app_cfg and app_cfg["default_export_path"]:
            default_export_path_var.set(str(app_cfg["default_export_path"]))
        if "open_export_folder_after_export" in app_cfg:
            open_export_folder_var.set(bool(app_cfg["open_export_folder_after_export"]))
        if "default_import_path" in app_cfg and app_cfg["default_import_path"]:
            default_import_path_var.set(str(app_cfg["default_import_path"]))

    # --- Buttons (btn_frame already created and packed at bottom, above) ---
    def _open_folder_in_explorer(folder_path: Path) -> None:
        """Open the given folder in the system file manager."""
        try:
            if os.name == "nt":
                os.startfile(str(folder_path))
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(folder_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder_path)], check=False)
        except OSError:
            pass

    def do_export():
        inp = input_path_var.get().strip()
        if not inp:
            messagebox.showerror("Error", "Please load a 3MF file first.")
            return
        path = Path(inp)
        if not path.exists():
            messagebox.showerror("Error", f"File not found: {path}")
            return

        push_height_mode = push_height_mode_var.get()
        max_z = get_max_z_from_3mf(path) or 10.0
        if push_height_mode == "manual":
            try:
                push_height_mm = float(push_height_mm_var.get().strip())
                push_height_mm = max(1, min(250, push_height_mm))
            except (ValueError, AttributeError):
                messagebox.showerror("Error", "Manual push height must be a number between 1 and 250 mm.")
                return
            push_height_offset_mm = 20  # unused when manual
        else:
            push_height_mm = 5
            sweep_z = max(1.0, min(max_z, auto_sweep_z_var.get()))
            push_height_offset_mm = max(1, int(max_z - sweep_z))

        cooldown_mode = cooldown_mode_var.get()
        if cooldown_mode == "time":
            try:
                cooldown_value = float(cooldown_time_var.get().strip())
            except (ValueError, AttributeError):
                cooldown_value = 180.0
        else:
            try:
                cooldown_value = float(cooldown_temp_var.get().strip())
            except (ValueError, AttributeError):
                cooldown_value = 35.0

        template = template_text.get("1.0", tk.END).strip() or None

        try:
            loop_count = int(loop_count_var.get().strip())
            loop_count = max(1, min(999, loop_count))
        except (ValueError, AttributeError):
            loop_count = 1

        stem = path.stem
        if stem.endswith(".gcode"):
            stem = Path(stem).stem
        default_name = f"{stem}_autoclear.3mf"
        exp_dir = default_export_path_var.get().strip()
        initial_dir = exp_dir if exp_dir and Path(exp_dir).is_dir() else str(path.parent)
        out_path = filedialog.asksaveasfilename(
            title="Export 3MF",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".3mf",
            filetypes=[("3MF files", "*.3mf"), ("All files", "*.*")],
        )
        if not out_path:
            return

        sweep_z = compute_sweep_z(max_z, push_height_mode, push_height_mm, push_height_offset_mm)
        if sweep_z < 5:
            if not messagebox.askyesno(
                "Warning",
                f"Sweep height will be {sweep_z:.1f} mm from bed (below 5 mm).\n\n"
                "This may hit the build plate. Continue?",
                icon="warning",
            ):
                return

        try:
            try:
                preheat_bed = int(preheat_bed_temp_var.get().strip())
                preheat_bed = max(0, min(150, preheat_bed))
            except (ValueError, AttributeError):
                preheat_bed = 70
            try:
                preheat_nozzle = int(preheat_nozzle_temp_var.get().strip())
                preheat_nozzle = max(0, min(300, preheat_nozzle))
            except (ValueError, AttributeError):
                preheat_nozzle = 150
            result = process_3mf(
                input_path=path,
                output_path=out_path,
                cooldown_mode=cooldown_mode,
                cooldown_value=cooldown_value,
                push_height_mode=push_height_mode,
                push_height_mm=push_height_mm,
                push_height_offset_mm=push_height_offset_mm,
                bending_mode="nhdfarm" if bending_mode_var.get() == "on" else "none",
                push_mode=push_mode_var.get(),
                template=template,
                loop_count=loop_count,
                remove_purge_line=remove_purge_var.get(),
                fans_during_cooldown=fans_during_cooldown_var.get(),
                skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
                reheat_between_loops=reheat_between_loops_var.get(),
                preheat_bed_temp=preheat_bed,
                preheat_nozzle_temp=preheat_nozzle,
            )
            messagebox.showinfo("Success", f"Exported to:\n{result}")
            if open_export_folder_var.get():
                _open_folder_in_explorer(Path(result).parent)
            save_last_settings(settings_to_dict(
                cooldown_mode=cooldown_mode,
                cooldown_time=cooldown_time_var.get().strip(),
                cooldown_temp=cooldown_temp_var.get().strip(),
                push_height_mode=push_height_mode,
                push_height_mm=push_height_mm_var.get().strip(),
                push_height_offset_mm=push_height_offset_var.get().strip(),
                bending_mode="nhdfarm" if bending_mode_var.get() == "on" else "none",
                push_mode=push_mode_var.get(),
                loop_count=str(loop_count),
                remove_purge=remove_purge_var.get(),
                fans_during_cooldown=fans_during_cooldown_var.get(),
                skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
                reheat_between_loops=reheat_between_loops_var.get(),
                preheat_bed_temp=preheat_bed_temp_var.get().strip(),
                preheat_nozzle_temp=preheat_nozzle_temp_var.get().strip(),
                template=template or "",
            ))
        except FileNotFoundError as e:
            messagebox.showerror("Error", str(e))
        except ValueError as e:
            messagebox.showerror("Error", str(e))
        except Exception as e:
            messagebox.showerror("Error", f"Unexpected error:\n{e}")

    def show_help():
        """Open Help window with full explanation of all settings and functions."""
        win = tk.Toplevel(root)
        win.title("P1S Auto-Clear – Help")
        win.minsize(480, 400)
        win.geometry("520x550")
        win.configure(bg=_BAMBU_BG)
        text_frame = ttk.Frame(win, padding=8)
        text_frame.pack(fill=tk.BOTH, expand=True)
        txt = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            padx=8,
            pady=8,
            bg=_BAMBU_ENTRY_BG,
            fg=_BAMBU_FG,
            insertbackground=_BAMBU_FG,
        )
        scrl = ttk.Scrollbar(text_frame, command=txt.yview)
        txt.configure(yscrollcommand=scrl.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrl.pack(side=tk.RIGHT, fill=tk.Y)
        help_text = """
P1S Auto-Clear – Help
=====================

OVERVIEW
--------
P1S Auto-Clear injects G-code into your 3MF project so that after each print,
the bed cools and a pusher/rake sweeps the part off automatically (NHDFARM-style).
Load a 3MF → configure → Export → open in Bambu Studio, slice, and print.

COOLDOWN
--------
• Time (sec): Wait a fixed number of seconds before pushing (G4).
• Temp (°C): Wait until bed cools to the given temperature (M190).
  Use temp-based cooldown (e.g. 40°C) for more reliable detachment.
• Fans 100% during cooldown: Runs all fans (part cooling, auxiliary, chamber)
  at 100% (M106 S255, P2, P3) during cooldown to speed up bed cooling.

LOOP COUNT
---------
Number of times to run the same print job (1–999). When > 1 and the 3MF
contains pre-sliced plate G-code (.gcode.3mf), the plate is wrapped in
Auto-Clear-style loop structure:
  ; === LOOP 1 OF N ===
  [full print + auto-clear]
  ; === END OF LOOP 1 ===
  ; Preparing for next loop...
  G4 S2 ; brief pause
  ; === LOOP 2 OF N === ...
Requires pre-sliced 3MF: slice in Bambu Studio, save as .gcode.3mf, then
load and Export from P1S Auto-Clear. Alternative: p1s-run-loop to send the
job repeatedly via command line.

SKIP NOZZLE LOAD LINE
---------------------
Removes the nozzle load line (Bambu G-code section ;===== nozzle load line =====)
from the start G-code. Saves time and filament. Also removes it from sliced
plate G-code in .gcode.3mf files.

SKIP RETRACTION BETWEEN LOOPS
-----------------------------
When enabled with multiple loops: filament stays loaded between loops (no
retraction after loops 1..N-1); retraction only runs after the last loop.
Saves time and avoids re-load/purge each loop. Use with "Skip nozzle load line"
for best results.
-----------
• Auto (max height - X mm): Sweep at (max part height - offset) mm. Bambu
  placeholder {max(5, max_layer_z - offset)} ensures minimum 5 mm clearance to
  avoid bed damage. Larger offset = lower Z = push closer to bed. If the sweep
  misses the part, try 25–30 mm offset.
• Manual: Fixed Z height in mm (1–250). Use for known part heights.

PUSH MODE
---------
• center_only: Two pushes forward at center X (125 mm) only. No rake passes.
• center_and_sweep: Center pushes + right-to-left rake passes (full Auto-Clear style).

BENDING
-------------------
Movement to flex the plate and help break adhesion before sweeps:
• On: NHDFARM Z235↔Z200 repeated 6 times.
• Off: No bending.

PROFILES
--------
• Load Profile: Apply saved settings (filament type or custom).
• Save Profile: Update the selected profile with current settings. For
  built-in profiles (PLA, PETG, ABS/ASA), this saves a custom override.
• Save as Profile: Create a new profile with a different name.
• Delete Profile: Remove a custom profile. Built-in profiles cannot be
  deleted; custom profiles (including ones named the same as built-ins)
  can be deleted.

SETTINGS TAB
------------
• Default export location: Folder used when the Export 3MF dialog opens.
  Leave empty to use the loaded file's folder.
• Open export folder after exporting: Opens the folder containing the
  exported file in your file manager after a successful export.
• Import location: Watch folder for future automation—when a 3MF file
  is detected here, it will automatically load.

G-CODE TEMPLATE
---------------
Placeholders: {cooldown}, {bending}, {sweeps}
• {cooldown}: G4 or M190 line
• {bending}: Bending block or empty
• {sweeps}: Pusher sweep block. center_only: two central pushes. center_and_sweep:
  central sweeps + double rake (pass 1 at F3000, pass 2 at F12000).
Reset Template restores the default. Do not remove placeholders.

END SECTION (matches Auto-Clear)
-------------------------------
After sweeps, the sequence moves to safe corner (X65 Y265), turns off fans,
then hands off to Bambu's machine end gcode (M17 S, etc.). No G28 Z—Bambu
handles homing at the start of each loop.

CAUTION – Z-height and roof collision:
• Bambu P1S/P1P/X1C default printable height is 250 mm (not 256 mm). A ~6 mm
  buffer is reserved for z-hop and debris clearance.
• NHDFARM bending uses Z235↔Z200 – Z235 is only ~15 mm below the 250 mm
  limit. Debris in the chamber, dust caps on Z screws, or a dirty bottom
  can cause the heatbed/chassis to collide with the roof.
• Z park (200 mm) is safer; the bending lift to 235 mm is the risky part.
• If you hear grinding or see collision, turn Bending Off,
  or edit the template to use lower Z values (e.g. Z220).

Reference (Bambu Lab official):
https://wiki.bambulab.com/en/knowledge-sharing/print-volume-limitations

BUTTONS & FUNCTIONS
------------------
• Load 3MF: Pick a 3MF file and load its autoclear settings into the form.
• Export 3MF: Save a new 3MF with injected G-code and current settings.
• Reset Template: Restore the default G-code template.
• Help: Open this help window.
• Load Profile: Apply the selected profile’s settings.
• Save Profile: Update the selected profile with current settings. For built-ins (PLA, PETG, ABS/ASA), saves a custom override.
• Save as Profile: Create a new profile with a different name (e.g. PLA, PETG).
• Delete Profile: Remove a custom profile. Custom profiles can be deleted even if named the same as a built-in.

EXPORT 3MF
----------
Saves a new 3MF with injected auto-clear block and stored settings.
Open in Bambu Studio, slice, and print. For looping, use:
  p1s-run-loop path/to/file.gcode.3mf
"""
        txt.insert("1.0", help_text.strip())
        txt.config(state=tk.DISABLED)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)

    ttk.Button(btn_frame, text="Export 3MF", command=do_export).pack(side=tk.LEFT, padx=(0, 10))
    def reset_template():
        template_text.delete("1.0", tk.END)
        template_text.insert("1.0", DEFAULT_TEMPLATE.strip())

    ttk.Button(btn_frame, text="Reset Template", command=reset_template).pack(side=tk.LEFT, padx=(0, 10))
    ttk.Button(btn_frame, text="Help", command=show_help).pack(side=tk.LEFT, padx=(0, 4))

    def on_closing():
        save_last_settings(settings_to_dict(
            cooldown_mode=cooldown_mode_var.get(),
            cooldown_time=cooldown_time_var.get().strip(),
            cooldown_temp=cooldown_temp_var.get().strip(),
            push_height_mode=push_height_mode_var.get(),
            push_height_mm=push_height_mm_var.get().strip(),
            push_height_offset_mm=push_height_offset_var.get().strip(),
            bending_mode="nhdfarm" if bending_mode_var.get() == "on" else "none",
            push_mode=push_mode_var.get(),
            loop_count=loop_count_var.get().strip(),
            remove_purge=remove_purge_var.get(),
            fans_during_cooldown=fans_during_cooldown_var.get(),
            skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
            reheat_between_loops=reheat_between_loops_var.get(),
            preheat_bed_temp=preheat_bed_temp_var.get().strip(),
            preheat_nozzle_temp=preheat_nozzle_temp_var.get().strip(),
            template=template_text.get("1.0", tk.END).strip(),
        ))
        save_app_config({
            "default_export_path": default_export_path_var.get().strip(),
            "open_export_folder_after_export": open_export_folder_var.get(),
            "default_import_path": default_import_path_var.get().strip(),
        })
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_closing)

    root.update_idletasks()  # Force layout so window height is correct on first show
    return root


def main() -> None:
    """Entry point: create GUI and start the event loop."""
    root = create_gui()
    root.mainloop()


if __name__ == "__main__":
    main()
