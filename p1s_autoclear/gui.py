"""
GUI for P1S Auto-Clear: load 3MF, configure cooldown/push heights, export.
"""

import os
import platform
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from . import __version__
from .git_sync import current_branch, find_repo_root, update_from_github
from .injector import DEFAULT_TEMPLATE, PUSHER_MIN_HEIGHT_MM
from .preview import (
    compute_sweep_z,
    draw_preview_on_canvas,
    get_max_z_from_3mf,
)
from .merge_3mf import merge_3mf_chain_files, merge_3mf_files
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


_active_tooltips: set = set()
_tooltip_focus_bound = False


def _hide_all_tooltips(*args) -> None:
    """Destroy all active tooltips (e.g. when window loses focus)."""
    for tw in list(_active_tooltips):
        try:
            tw.destroy()
        except tk.TclError:
            pass
        _active_tooltips.discard(tw)


def _bind_tooltip_to_widget_and_children(widget, on_enter, on_leave) -> None:
    """Bind Enter/Leave to widget and all descendants so tooltips work on container widgets."""
    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)
    for child in widget.winfo_children():
        _bind_tooltip_to_widget_and_children(child, on_enter, on_leave)


def _create_tooltip(widget, text: str) -> None:
    """Create a hover tooltip for a widget.
    Binds Enter/Leave to show/hide a small popup with the given text.
    Also binds to children so tooltips work when hovering anywhere in a container (e.g. Frame).
    Tooltips are cleared when the window loses focus (prevents stuck tooltips).
    """
    tip = [None]
    hide_job = [None]  # after_id for delayed hide

    def hide_tooltip():
        if tip[0]:
            try:
                tip[0].destroy()
            except tk.TclError:
                pass
            _active_tooltips.discard(tip[0])
            tip[0] = None

    def cancel_pending_hide():
        if hide_job[0] is not None:
            try:
                widget.after_cancel(hide_job[0])
            except tk.TclError:
                pass
            hide_job[0] = None

    def on_enter(event):
        cancel_pending_hide()
        hide_tooltip()  # Clear any existing (prevents duplicates when Enter fires repeatedly)
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")
        tk.Label(
            tw, text=text, justify=tk.LEFT,
            background="#3C3C42", foreground="#E0E0E0",
            relief=tk.SOLID, borderwidth=1, font=("", 9),
        ).pack(padx=4, pady=2)
        tip[0] = tw
        _active_tooltips.add(tw)

    def on_leave(event):
        # Brief delay: avoid hiding when moving between widget and its children
        root = widget.winfo_toplevel()

        def do_hide():
            hide_job[0] = None
            # Only hide if mouse is actually outside the widget and its descendants
            try:
                x = root.winfo_pointerx()
                y = root.winfo_pointery()
                wx = widget.winfo_rootx()
                wy = widget.winfo_rooty()
                ww = widget.winfo_width()
                wh = widget.winfo_height()
                if x < wx or x >= wx + ww or y < wy or y >= wy + wh:
                    hide_tooltip()
            except tk.TclError:
                hide_tooltip()

        cancel_pending_hide()
        hide_job[0] = widget.after(100, do_hide)

    _bind_tooltip_to_widget_and_children(widget, on_enter, on_leave)

    # Clear tooltips when window loses focus or mouse leaves the window
    global _tooltip_focus_bound
    if not _tooltip_focus_bound:
        root = widget.winfo_toplevel()
        root.bind("<FocusOut>", _hide_all_tooltips)
        root.bind("<Leave>", lambda e: root.after(50, _hide_all_tooltips))
        _tooltip_focus_bound = True


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
    root.title(f"P1S Auto-Clear v{__version__}")
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
    cooldown_hold_seconds_var = tk.StringVar(value="60")
    loop_count_var = tk.StringVar(value="1")
    bed_level_interval_var = tk.StringVar(value="0")
    remove_purge_var = tk.BooleanVar(value=False)
    fans_during_cooldown_var = tk.BooleanVar(value=False)
    skip_retraction_between_loops_var = tk.BooleanVar(value=True)
    reheat_between_loops_var = tk.BooleanVar(value=False)
    preheat_bed_temp_var = tk.StringVar(value="70")
    preheat_nozzle_temp_var = tk.StringVar(value="150")
    push_height_mode_var = tk.StringVar(value="auto")
    push_height_mm_var = tk.StringVar(value="5")
    push_height_offset_var = tk.StringVar(value="20")
    bending_mode_var = tk.StringVar(value="on")
    push_mode_var = tk.StringVar(value="center_and_sweep")
    # App config (Settings tab)
    default_export_path_var = tk.StringVar(value="")
    open_export_folder_var = tk.BooleanVar(value=False)
    default_import_path_var = tk.StringVar(value="")
    version_override_var = tk.StringVar(value="(use detected)")

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

    # --- File section (supports single or multiple 3MF files) ---
    file_frame = ttk.LabelFrame(main_tab, text="3MF Files", padding=8)
    file_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 8))
    main_tab.columnconfigure(0, weight=1)

    file_list_data: list[dict] = []  # [{"path": str, "settings": dict}, ...]

    list_container = ttk.Frame(file_frame)
    list_container.pack(fill=tk.BOTH, expand=True)
    file_listbox = tk.Listbox(
        list_container,
        height=3,
        selectmode=tk.EXTENDED,
        font=("Segoe UI", 9),
        bg=_BAMBU_ENTRY_BG,
        fg=_BAMBU_FG,
        selectbackground=_BAMBU_ACCENT,
        selectforeground=_BAMBU_FG,
    )
    file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
    list_scroll = ttk.Scrollbar(list_container, command=file_listbox.yview)
    list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    file_listbox.configure(yscrollcommand=list_scroll.set)

    _last_selected_idx: list[int | None] = [None]  # mutable so closures can update

    def _update_selected_heading():
        sel = file_listbox.curselection()
        if len(sel) == 1 and file_list_data:
            name = Path(file_list_data[sel[0]]["path"]).name
            file_frame.configure(text=f"3MF Files — Selected: {name}")
        else:
            file_frame.configure(text="3MF Files")

    def _on_file_selection_changed():
        sel = file_listbox.curselection()
        prev = _last_selected_idx[0]
        if prev is not None and prev < len(file_list_data):
            file_list_data[prev]["settings"] = _dict_from_gui_for_file()
        _last_selected_idx[0] = sel[0] if len(sel) == 1 else None
        _update_selected_heading()
        if len(sel) == 1 and file_list_data:
            idx = sel[0]
            apply_settings_to_gui(
                file_list_data[idx]["settings"],
                cooldown_mode_var=cooldown_mode_var,
                cooldown_time_var=cooldown_time_var,
                cooldown_temp_var=cooldown_temp_var,
                cooldown_hold_seconds_var=cooldown_hold_seconds_var,
                push_height_mode_var=push_height_mode_var,
                push_height_mm_var=push_height_mm_var,
                push_height_offset_var=push_height_offset_var,
                bending_mode_var=bending_mode_var,
                push_mode_var=push_mode_var,
                loop_count_var=loop_count_var,
                bed_level_interval_var=bed_level_interval_var,
                remove_purge_var=remove_purge_var,
                skip_retraction_between_loops_var=skip_retraction_between_loops_var,
                fans_during_cooldown_var=fans_during_cooldown_var,
                reheat_between_loops_var=reheat_between_loops_var,
                preheat_bed_temp_var=preheat_bed_temp_var,
                preheat_nozzle_temp_var=preheat_nozzle_temp_var,
                template_text=template_text,
                default_template=DEFAULT_TEMPLATE.strip(),
            )
            input_path_var.set(file_list_data[idx]["path"])
            root.after(50, refresh_preview)

    file_listbox.bind("<<ListboxSelect>>", lambda e: _on_file_selection_changed())

    def _refresh_file_list_ui(select_index: int = 0):
        file_listbox.delete(0, tk.END)
        for item in file_list_data:
            p = Path(item["path"])
            file_listbox.insert(tk.END, p.name)
        if file_list_data:
            idx = min(select_index, len(file_list_data) - 1)
            file_listbox.selection_set(idx)
            file_listbox.see(idx)
            _on_file_selection_changed()
        else:
            file_listbox.selection_clear(0, tk.END)
            input_path_var.set("")
            _last_selected_idx[0] = None
            _update_selected_heading()

    def _dict_from_gui_for_file() -> dict:
        """Build settings dict from current main form vars (for single-file or defaults)."""
        cooldown_mode = cooldown_mode_var.get()
        return settings_to_dict(
            cooldown_mode=cooldown_mode,
            cooldown_time=cooldown_time_var.get().strip(),
            cooldown_temp=cooldown_temp_var.get().strip(),
            cooldown_hold_seconds=cooldown_hold_seconds_var.get().strip(),
            loop_count=loop_count_var.get().strip(),
            bed_level_interval=bed_level_interval_var.get().strip(),
            remove_purge=remove_purge_var.get(),
            fans_during_cooldown=fans_during_cooldown_var.get(),
            skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
            reheat_between_loops=reheat_between_loops_var.get(),
            preheat_bed_temp=preheat_bed_temp_var.get().strip(),
            preheat_nozzle_temp=preheat_nozzle_temp_var.get().strip(),
            template=template_text.get("1.0", tk.END).strip(),
            push_height_mode=push_height_mode_var.get(),
            push_height_mm=push_height_mm_var.get().strip(),
            push_height_offset_mm=push_height_offset_var.get().strip(),
            bending_mode=bending_mode_var.get(),
            push_mode=push_mode_var.get(),
        )

    def _show_file_settings_dialog(idx: int) -> None:
        """Open per-file settings dialog for the file at index."""
        if idx < 0 or idx >= len(file_list_data):
            return
        item = file_list_data[idx]
        dlg = tk.Toplevel(root)
        dlg.title(f"Settings for {Path(item['path']).name}")
        dlg.transient(root)
        dlg.grab_set()
        dlg.geometry("320x280")
        dlg.configure(bg=_BAMBU_BG)

        bm = item["settings"].get("bending_mode", "nhdfarm")
        if str(bm).lower() in ("nhdfarm", "farmloop"):
            bm = "on"
        elif str(bm).lower() in ("none", "off"):
            bm = "off"
        vars_d = {
            "cooldown_mode": tk.StringVar(value=item["settings"].get("cooldown_mode", "temp")),
            "cooldown_time": tk.StringVar(value=str(item["settings"].get("cooldown_time", "180"))),
            "cooldown_temp": tk.StringVar(value=str(item["settings"].get("cooldown_temp", "35"))),
            "cooldown_hold_seconds": tk.StringVar(value=str(item["settings"].get("cooldown_hold_seconds", "60"))),
            "loop_count": tk.StringVar(value=str(item["settings"].get("loop_count", "1"))),
            "bed_level_interval": tk.StringVar(value=str(item["settings"].get("bed_level_interval", "0"))),
            "remove_purge": tk.BooleanVar(value=item["settings"].get("remove_purge_line", False)),
            "fans_during_cooldown": tk.BooleanVar(value=item["settings"].get("fans_during_cooldown", False)),
            "skip_retraction_between_loops": tk.BooleanVar(value=item["settings"].get("skip_retraction_between_loops", True)),
            "reheat_between_loops": tk.BooleanVar(value=item["settings"].get("reheat_between_loops", False)),
            "preheat_bed_temp": tk.StringVar(value=str(item["settings"].get("preheat_bed_temp", "70"))),
            "preheat_nozzle_temp": tk.StringVar(value=str(item["settings"].get("preheat_nozzle_temp", "150"))),
            "push_height_mode": tk.StringVar(value=item["settings"].get("push_height_mode", "auto")),
            "push_height_mm": tk.StringVar(value=str(item["settings"].get("push_height_mm", "5"))),
            "push_height_offset_mm": tk.StringVar(value=str(item["settings"].get("push_height_offset_mm", "20"))),
            "bending_mode": tk.StringVar(value=bm),
            "push_mode": tk.StringVar(value=item["settings"].get("push_mode", "center_and_sweep")),
        }

        f = ttk.Frame(dlg, padding=8)
        f.pack(fill=tk.BOTH, expand=True)
        ttk.Label(f, text="Loops:").grid(row=0, column=0, sticky=tk.W, padx=(0, 4), pady=2)
        ttk.Entry(f, textvariable=vars_d["loop_count"], width=6).grid(row=0, column=1, sticky=tk.W, pady=2)
        ttk.Radiobutton(f, text="Time (sec)", variable=vars_d["cooldown_mode"], value="time").grid(row=1, column=0, columnspan=2, sticky=tk.W)
        ttk.Entry(f, textvariable=vars_d["cooldown_time"], width=6).grid(row=2, column=1, sticky=tk.W, padx=(20, 0), pady=2)
        ttk.Radiobutton(f, text="Temp (°C)", variable=vars_d["cooldown_mode"], value="temp").grid(row=3, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(f, text="Bed:").grid(row=4, column=0, sticky=tk.W, padx=(20, 4))
        ttk.Entry(f, textvariable=vars_d["cooldown_temp"], width=6).grid(row=4, column=1, sticky=tk.W, pady=2)
        ttk.Checkbutton(f, text="Skip nozzle load line", variable=vars_d["remove_purge"]).grid(row=5, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(f, text="Bed level every N loops:").grid(row=6, column=0, sticky=tk.W, padx=(0, 4))
        ttk.Entry(f, textvariable=vars_d["bed_level_interval"], width=4).grid(row=6, column=1, sticky=tk.W, pady=2)

        def on_ok():
            bm = vars_d["bending_mode"].get()
            item["settings"] = {
                "cooldown_mode": vars_d["cooldown_mode"].get(),
                "cooldown_time": vars_d["cooldown_time"].get(),
                "cooldown_temp": vars_d["cooldown_temp"].get(),
                "cooldown_hold_seconds": vars_d["cooldown_hold_seconds"].get(),
                "loop_count": vars_d["loop_count"].get(),
                "bed_level_interval": vars_d["bed_level_interval"].get(),
                "remove_purge_line": vars_d["remove_purge"].get(),
                "fans_during_cooldown": vars_d["fans_during_cooldown"].get(),
                "skip_retraction_between_loops": vars_d["skip_retraction_between_loops"].get(),
                "reheat_between_loops": vars_d["reheat_between_loops"].get(),
                "preheat_bed_temp": vars_d["preheat_bed_temp"].get(),
                "preheat_nozzle_temp": vars_d["preheat_nozzle_temp"].get(),
                "push_height_mode": vars_d["push_height_mode"].get(),
                "push_height_mm": vars_d["push_height_mm"].get(),
                "push_height_offset_mm": vars_d["push_height_offset_mm"].get(),
                "bending_mode": "nhdfarm" if bm == "on" else "none",
                "push_mode": vars_d["push_mode"].get(),
            }
            dlg.destroy()

        ttk.Button(f, text="OK", command=on_ok).grid(row=7, column=0, padx=(0, 4), pady=(12, 0))
        ttk.Button(f, text="Cancel", command=dlg.destroy).grid(row=7, column=1, pady=(12, 0))

    def add_file():
        imp_dir = default_import_path_var.get().strip()
        initial_dir = imp_dir if imp_dir and Path(imp_dir).is_dir() else None
        if initial_dir is None and file_list_data:
            first = Path(file_list_data[0]["path"])
            if first.parent.exists():
                initial_dir = str(first.parent)
        kwargs: dict = {"title": "Add 3MF", "filetypes": [("3MF files", "*.3mf"), ("All files", "*.*")]}
        if initial_dir:
            kwargs["initialdir"] = initial_dir
        path = filedialog.askopenfilename(**kwargs)
        if path:
            autoclear = get_autoclear_settings(Path(path))
            gui_data = autoclear_to_gui_settings(autoclear) if autoclear else {}
            if not gui_data:
                gui_data = _dict_from_gui_for_file()
            file_list_data.append({"path": path, "settings": gui_data})
            _refresh_file_list_ui(select_index=len(file_list_data) - 1)
            path_obj = Path(path)
            max_z = get_max_z_from_3mf(path_obj)
            if max_z is not None and max_z > 0:
                recommended_sweep_z = 5.0 if max_z >= 5 else max(1.0, max_z)
                push_height_mode_var.set("auto")
                push_height_offset_var.set(str(max(1, int(max_z - recommended_sweep_z))))
            refresh_preview()

    def remove_files():
        sel = list(file_listbox.curselection())
        for i in reversed(sel):
            file_list_data.pop(i)
        _refresh_file_list_ui()
        if file_list_data:
            refresh_preview()
        else:
            input_path_var.set("")

    def settings_for_selected():
        sel = file_listbox.curselection()
        if len(sel) == 1:
            _show_file_settings_dialog(sel[0])
        elif len(sel) > 1:
            messagebox.showinfo("Settings", "Edit one file at a time. Select a single file and click Settings.")
        else:
            messagebox.showinfo("Settings", "Select a file first.")

    btn_row = ttk.Frame(file_frame)
    btn_row.pack(fill=tk.X, pady=(6, 0))
    ttk.Button(btn_row, text="Add 3MF", command=add_file).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(btn_row, text="Remove", command=remove_files).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(btn_row, text="Settings", command=settings_for_selected).pack(side=tk.LEFT)

    chain_single_job_var = tk.BooleanVar(value=True)
    chain_cb = ttk.Checkbutton(
        file_frame,
        text="One continuous print (all files in one job — auto-clear between each)",
        variable=chain_single_job_var,
    )
    chain_cb.pack(anchor=tk.W, pady=(6, 0))
    _create_tooltip(
        chain_cb,
        "When adding multiple 3MFs: ON = single print job (job1 → push → job2 → …). "
        "OFF = separate Bambu plates (print each job manually).",
    )

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
    hold_row = ttk.Frame(cool_frame)
    hold_row.pack(fill=tk.X, padx=(16, 0), pady=(4, 0))
    ttk.Label(hold_row, text="Extra hold (sec):").pack(side=tk.LEFT, padx=(0, 4))
    hold_entry = ttk.Entry(hold_row, textvariable=cooldown_hold_seconds_var, width=6)
    hold_entry.pack(side=tk.LEFT)
    _create_tooltip(hold_entry, "When using temp-based cooldown: extra seconds to wait after bed reaches target temp before sweep. Default 60.")
    fans_cb = ttk.Checkbutton(
        cool_frame,
        text="Fans 100% during cooldown",
        variable=fans_during_cooldown_var,
    )
    fans_cb.pack(anchor=tk.W, pady=(4, 0))
    _create_tooltip(fans_cb, "Runs all fans at 100% during cooldown, bending, and first push; fans turn off after first push to maximize cooldown.")

    # Settings (Loop count + Bed level interval + Skip purge)
    settings_frame = ttk.LabelFrame(top_row, text="Settings", padding=2)
    settings_frame.pack(side=tk.LEFT, fill=tk.Y, expand=True)
    loop_row = ttk.Frame(settings_frame)
    loop_row.pack(fill=tk.X)
    ttk.Label(loop_row, text="Loops:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Entry(loop_row, textvariable=loop_count_var, width=4).pack(side=tk.LEFT)
    ttk.Label(settings_frame, text="(1 = single print)", font=("", 8)).pack(anchor=tk.W)
    bed_level_row = ttk.Frame(settings_frame)
    bed_level_row.pack(fill=tk.X)
    ttk.Label(bed_level_row, text="Bed level every:").pack(side=tk.LEFT, padx=(0, 4))
    ttk.Entry(bed_level_row, textvariable=bed_level_interval_var, width=4).pack(side=tk.LEFT)
    ttk.Label(bed_level_row, text="loops (0=first only)", font=("", 8)).pack(side=tk.LEFT, padx=(4, 0))
    _create_tooltip(bed_level_row, "For run_loop: bed leveling on loop 1 and every N loops. 0 = first loop only. Saves ~5 min per loop when skipped.")
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
        values=("center_only", "center_and_sweep", "part_center", "part_center_sweep"),
        width=18,
        state="readonly",
    )
    push_mode_combo.pack(side=tk.LEFT)
    _create_tooltip(
        push_mode_combo,
        "Center only / + sweep: fixed bed columns. Part center: two safe-X pushes across print "
        "footprint (from gcode). Part center + sweep: multiple columns, same safe X band (32–206 mm).",
    )

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

    auto_row = ttk.Frame(controls_col)
    auto_row.pack(anchor=tk.W, pady=(6, 0))
    ttk.Radiobutton(
        auto_row,
        text="Auto",
        variable=push_height_mode_var,
        value="auto",
    ).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Label(auto_row, text="Offset:").pack(side=tk.LEFT, padx=(0, 4))
    auto_offset_entry = ttk.Entry(auto_row, textvariable=push_height_offset_var, width=5)
    auto_offset_entry.pack(side=tk.LEFT, padx=(0, 4))
    ttk.Label(auto_row, text="mm from part top").pack(side=tk.LEFT)
    _create_tooltip(
        auto_offset_entry,
        "Sweep height ≈ max part height minus this offset. Larger offset = lower sweep (closer to bed). Try 20–30 if the pusher misses the part.",
    )

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
        text="Larger offset = sweep closer to bed.\nManual = fixed Z height in mm.",
        font=("", 8),
        justify=tk.LEFT,
    )
    push_ht_tip.pack(anchor=tk.W, pady=(2, 0))

    # Right column: side view preview (canvas left, text right)
    preview_col = ttk.LabelFrame(push_columns, text="Side view", padding=(6, 4))
    preview_col.pack(side=tk.LEFT, padx=(12, 0), fill=tk.BOTH, expand=True)
    preview_row = ttk.Frame(preview_col)
    preview_row.pack(pady=(4, 6))
    preview_canvas = tk.Canvas(
        preview_row,
        width=180,
        height=120,
        bg=_BAMBU_CANVAS_BG,
        highlightthickness=1,
        highlightbackground=_BAMBU_BORDER,
    )
    preview_canvas.pack(side=tk.LEFT, padx=(0, 8))
    preview_text = tk.Text(
        preview_row,
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
    preview_text.pack(side=tk.LEFT)
    # Placeholder so widget reserves full height (4 lines) before refresh_preview runs
    preview_text.config(state=tk.NORMAL)
    preview_text.insert("1.0", "Load a 3MF\nto see preview.\n\n\n")
    preview_text.config(state=tk.DISABLED)

    def refresh_preview(*_args):
        path = input_path_var.get().strip() or None
        path_obj = Path(path) if path else None
        offset_for_preview = push_height_offset_var.get()
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
        preview_text.insert("1.0", info or "Load a 3MF to see preview")
        preview_text.config(state=tk.DISABLED)

    def _schedule_refresh(*_a):
        root.after(50, refresh_preview)

    for var in (push_height_mode_var, push_height_mm_var, push_height_offset_var, push_mode_var):
        var.trace_add("write", _schedule_refresh)
    auto_offset_entry.bind("<KeyRelease>", lambda e: root.after(50, refresh_preview))
    auto_offset_entry.bind("<FocusOut>", lambda e: refresh_preview())
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
    combo = ttk.Combobox(profile_frame, textvariable=profile_var, width=25)
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
                cooldown_hold_seconds_var=cooldown_hold_seconds_var,
                push_height_mode_var=push_height_mode_var,
                push_height_mm_var=push_height_mm_var,
                push_height_offset_var=push_height_offset_var,
                bending_mode_var=bending_mode_var,
                push_mode_var=push_mode_var,
                loop_count_var=loop_count_var,
                bed_level_interval_var=bed_level_interval_var,
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
            cooldown_hold_seconds=cooldown_hold_seconds_var.get().strip(),
            push_height_mode=push_height_mode_var.get(),
            push_height_mm=push_height_mm_var.get().strip(),
            push_height_offset_mm=push_height_offset_var.get().strip(),
            push_mode=push_mode_var.get(),
            loop_count=loop_count_var.get().strip(),
            bed_level_interval=bed_level_interval_var.get().strip(),
            remove_purge=remove_purge_var.get(),
            fans_during_cooldown=fans_during_cooldown_var.get(),
            skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
            reheat_between_loops=reheat_between_loops_var.get(),
            preheat_bed_temp=preheat_bed_temp_var.get().strip(),
            preheat_nozzle_temp=preheat_nozzle_temp_var.get().strip(),
            bending_mode=bending_mode_var.get(),
            template=template_text.get("1.0", tk.END).strip(),
        )

    def save_profile_click():
        """Save current settings. Edit the profile name in the box: same name overwrites, new name creates a new profile."""
        name = profile_var.get().strip()
        if not name or name == "(Last used)":
            messagebox.showinfo("Profile", "Enter or select a profile name in the box, then click Save. Same name overwrites; new name creates a new profile.")
            return
        save_profile(name, _current_settings_dict())
        refresh_profile_combo()
        profile_var.set(name)
        messagebox.showinfo("Profile", f"Saved '{name}'.")

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
    ttk.Button(profile_frame, text="Save Profile", command=save_profile_click).pack(side=tk.LEFT, padx=(0, 8))
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

    # Version section
    version_frame = ttk.LabelFrame(settings_scroll, text="Version", padding=8)
    version_frame.pack(fill=tk.X, pady=(0, 8))
    ttk.Label(version_frame, text=f"Detected: v{__version__}").pack(anchor=tk.W)
    ver_row = ttk.Frame(version_frame)
    ver_row.pack(fill=tk.X, pady=(4, 0))
    ttk.Label(ver_row, text="Display as:").pack(side=tk.LEFT, padx=(0, 6))
    version_combo = ttk.Combobox(
        ver_row,
        textvariable=version_override_var,
        values=("(use detected)", "0.1.0", "0.2.0", "dev"),
        width=16,
        state="readonly",
    )
    version_combo.pack(side=tk.LEFT)
    version_combo.set("(use detected)")  # Default before app config load
    _create_tooltip(version_combo, "Override the version shown in the window title. Useful for testing or compatibility.")

    def _update_title(*_):
        v = version_override_var.get().strip()
        disp = v if v and v != "(use detected)" else __version__
        root.title(f"P1S Auto-Clear v{disp}")
    version_override_var.trace_add("write", _update_title)

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

    git_frame = ttk.LabelFrame(settings_scroll, text="Git / GitHub", padding=8)
    git_frame.pack(fill=tk.X, pady=(0, 8))
    _repo_root = find_repo_root(Path(__file__).resolve().parent)
    if _repo_root:
        _br = current_branch(_repo_root) or "?"
        git_info_var = tk.StringVar(value=f"Repository:\n{_repo_root}\nBranch: {_br}")
    else:
        git_info_var = tk.StringVar(value="No Git repository found next to the app.")
    ttk.Label(git_frame, textvariable=git_info_var, font=("", 8), justify=tk.LEFT).pack(anchor=tk.W)

    def _update_from_github():
        repo = find_repo_root(Path(__file__).resolve().parent)
        if not repo:
            messagebox.showerror("Git", "Could not find a Git repository.", parent=root)
            return
        root.config(cursor="watch")
        root.update_idletasks()
        try:
            ok, msg = update_from_github(repo)
        except FileNotFoundError:
            ok, msg = False, "Git executable not found. Install Git for Windows and ensure it is on PATH."
        except subprocess.TimeoutExpired:
            ok, msg = False, "Git command timed out."
        except OSError as e:
            ok, msg = False, str(e)
        finally:
            root.config(cursor="")
        msg = (msg or "")[:4000]
        if ok:
            messagebox.showinfo("Update from GitHub", msg or "Done.", parent=root)
        else:
            messagebox.showerror("Update from GitHub", msg, parent=root)
        r = find_repo_root(Path(__file__).resolve().parent)
        if r:
            br = current_branch(r) or "?"
            git_info_var.set(f"Repository:\n{r}\nBranch: {br}")

    ttk.Button(git_frame, text="Update from GitHub", command=_update_from_github).pack(anchor=tk.W, pady=(8, 0))
    ttk.Label(
        git_frame,
        text=(
            "Runs git fetch and git pull --ff-only (fast-forward only). "
            "Uncommitted local changes are left as-is; fix conflicts in Git if pull fails."
        ),
        font=("", 8),
        wraplength=520,
        justify=tk.LEFT,
    ).pack(anchor=tk.W, pady=(4, 0))

    # Load last-used settings on startup
    last = load_last_settings()
    if last:
        apply_settings_to_gui(
            last,
            cooldown_mode_var=cooldown_mode_var,
            cooldown_time_var=cooldown_time_var,
            cooldown_temp_var=cooldown_temp_var,
            cooldown_hold_seconds_var=cooldown_hold_seconds_var,
            push_height_mode_var=push_height_mode_var,
            push_height_mm_var=push_height_mm_var,
            push_height_offset_var=push_height_offset_var,
            bending_mode_var=bending_mode_var,
            push_mode_var=push_mode_var,
            loop_count_var=loop_count_var,
            bed_level_interval_var=bed_level_interval_var,
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
        if "version_override" in app_cfg and app_cfg["version_override"]:
            version_override_var.set(str(app_cfg["version_override"]))
    _update_title()  # Apply version to title after config load

    # --- Buttons (btn_frame already created and packed at bottom, above) ---
    def do_export():
        if not file_list_data:
            messagebox.showerror("Error", "Please add at least one 3MF file first.")
            return
        sel = file_listbox.curselection()
        if len(sel) == 1:
            file_list_data[sel[0]]["settings"] = _dict_from_gui_for_file()
        paths = [Path(item["path"]) for item in file_list_data]
        for p in paths:
            if not p.exists():
                messagebox.showerror("Error", f"File not found: {p}")
                return
        path = paths[0]  # primary path for preview/default name

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
            try:
                push_height_offset_mm = max(1, int(push_height_offset_var.get().strip() or 20))
            except (ValueError, AttributeError):
                push_height_offset_mm = 20

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

        try:
            cooldown_hold = float(cooldown_hold_seconds_var.get().strip())
            cooldown_hold = max(0, min(600, cooldown_hold))
        except (ValueError, AttributeError):
            cooldown_hold = 60.0

        stem = path.stem
        if stem.endswith(".gcode"):
            stem = Path(stem).stem
        default_name = f"{stem}_autoclear.3mf" if len(file_list_data) == 1 else "merged_autoclear.3mf"
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
        push_mode = push_mode_var.get()
        if push_mode in (
            "part_center",
            "part_center_sweep",
        ) and max_z is not None and max_z < PUSHER_MIN_HEIGHT_MM:
            if not messagebox.askyesno(
                "Warning",
                f"Part height is {max_z:.1f} mm (below {PUSHER_MIN_HEIGHT_MM:.0f} mm).\n\n"
                "Pusher cannot contact part at this height – you may need to remove it manually.",
                icon="warning",
            ):
                return
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

            input_for_process = path
            if len(file_list_data) > 1 and chain_single_job_var.get():
                default_params = {
                    "cooldown_mode": cooldown_mode,
                    "cooldown_value": cooldown_value,
                    "cooldown_hold_seconds": cooldown_hold,
                    "loop_count": 1,
                    "remove_purge_line": remove_purge_var.get(),
                    "fans_during_cooldown": fans_during_cooldown_var.get(),
                    "skip_retraction_between_loops": skip_retraction_between_loops_var.get(),
                    "reheat_between_loops": reheat_between_loops_var.get(),
                    "preheat_bed_temp": preheat_bed,
                    "preheat_nozzle_temp": preheat_nozzle,
                    "push_height_mode": push_height_mode,
                    "push_height_mm": push_height_mm,
                    "push_height_offset_mm": push_height_offset_mm,
                    "bending_mode": "nhdfarm" if bending_mode_var.get() == "on" else "none",
                    "push_mode": push_mode_var.get(),
                    "template": template or "",
                }
                result = str(
                    merge_3mf_chain_files(
                        [item["path"] for item in file_list_data],
                        out_path,
                        [item["settings"] for item in file_list_data],
                        default_params,
                        skip_retraction_between_jobs=skip_retraction_between_loops_var.get(),
                        reheat_between_jobs=reheat_between_loops_var.get(),
                        preheat_bed_temp=preheat_bed,
                        preheat_nozzle_temp=preheat_nozzle,
                    )
                )
            elif len(file_list_data) > 1:
                with tempfile.NamedTemporaryFile(suffix=".3mf", delete=False) as tmp:
                    merge_3mf_files(
                        [item["path"] for item in file_list_data],
                        tmp.name,
                        settings_per_file=[item["settings"] for item in file_list_data],
                    )
                    input_for_process = Path(tmp.name)
                try:
                    result = process_3mf(
                        input_path=input_for_process,
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
                        bed_level_interval=max(0, int(bed_level_interval_var.get().strip() or "0")),
                        remove_purge_line=remove_purge_var.get(),
                        fans_during_cooldown=fans_during_cooldown_var.get(),
                        skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
                        reheat_between_loops=reheat_between_loops_var.get(),
                        preheat_bed_temp=preheat_bed,
                        preheat_nozzle_temp=preheat_nozzle,
                        cooldown_hold_seconds=cooldown_hold,
                    )
                finally:
                    if input_for_process.exists():
                        input_for_process.unlink(missing_ok=True)
            else:
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
                    bed_level_interval=max(0, int(bed_level_interval_var.get().strip() or "0")),
                    remove_purge_line=remove_purge_var.get(),
                    fans_during_cooldown=fans_during_cooldown_var.get(),
                    skip_retraction_between_loops=skip_retraction_between_loops_var.get(),
                    reheat_between_loops=reheat_between_loops_var.get(),
                    preheat_bed_temp=preheat_bed,
                    preheat_nozzle_temp=preheat_nozzle,
                    cooldown_hold_seconds=cooldown_hold,
                )
            messagebox.showinfo("Success", f"Exported to:\n{result}")
            if open_export_folder_var.get():
                _open_folder_in_explorer(Path(result).parent)
            save_last_settings(settings_to_dict(
                cooldown_mode=cooldown_mode,
                cooldown_time=cooldown_time_var.get().strip(),
                cooldown_temp=cooldown_temp_var.get().strip(),
                cooldown_hold_seconds=cooldown_hold_seconds_var.get().strip(),
                push_height_mode=push_height_mode,
                push_height_mm=push_height_mm_var.get().strip(),
                push_height_offset_mm=push_height_offset_var.get().strip(),
                bending_mode="nhdfarm" if bending_mode_var.get() == "on" else "none",
                push_mode=push_mode_var.get(),
                loop_count=str(loop_count),
                bed_level_interval=bed_level_interval_var.get().strip(),
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
• Fans 100% during cooldown: Runs all fans at 100% during cooldown, bending,
  and the first push; fans turn off after the first push to maximize cooldown.
• Extra cooldown hold (sec): When using temp-based cooldown, extra seconds to
  wait after the bed reaches target temp before starting the sweep. Default 60.

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

MULTIPLE 3MF FILES
------------------
• **One continuous print** (checkbox under the file list, default ON): Exports
  one job — print file 1, auto-clear, then file 2, etc., in a single plate gcode.
  Use the same AMS slot / filament when possible; “Skip retraction between loops”
  keeps filament loaded between jobs (like multi-loop).
• **Unchecked**: Separate plates in one project (each plate is its own print in Bambu).

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

PUSH HEIGHT
-----------
• Auto: Set offset (mm) subtracted from max part height for sweep Z. Larger
  offset = lower sweep = closer to bed. If the sweep misses the part, try 25–30 mm.
• Manual: Fixed Z height in mm (1–250). Use for known part heights.

PUSH MODE
---------
• center_only: Two pushes forward at center X (125 mm) only. No rake passes.
• center_and_sweep: Center pushes + right-to-left rake passes (full Auto-Clear style).
• Part center: Push at each part center/back only (no rake). Requires trimesh.
• Part center + sweep: Push at each part center plus rake passes. Requires trimesh.
• Part center / + sweep: Footprint-only columns in safe X (32–206 mm); travel away from chute first.

BENDING
-------------------
Movement to flex the plate and help break adhesion before sweeps:
• On: NHDFARM Z235↔Z200 repeated 6 times.
• Off: No bending.

PROFILES
--------
• Load Profile: Apply saved settings (filament type or custom).
• Save Profile: Edit the profile name in the box and click Save. Same name
  overwrites; new name creates a new profile. Built-in names save custom overrides.
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
• Save Profile: Edit the profile name in the box and click Save. Same name overwrites; new name creates a new profile.
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
    ttk.Button(btn_frame, text="Help", command=show_help).pack(side=tk.LEFT, padx=(0, 10))

    def do_restart():
        save_last_settings(settings_to_dict(
            cooldown_mode=cooldown_mode_var.get(),
            cooldown_time=cooldown_time_var.get().strip(),
            cooldown_temp=cooldown_temp_var.get().strip(),
            cooldown_hold_seconds=cooldown_hold_seconds_var.get().strip(),
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
        vov = version_override_var.get().strip()
        save_app_config({
            "default_export_path": default_export_path_var.get().strip(),
            "open_export_folder_after_export": open_export_folder_var.get(),
            "default_import_path": default_import_path_var.get().strip(),
            "version_override": vov if vov and vov != "(use detected)" else "",
        })
        root.quit()
        root.update()
        os.execv(sys.executable, [sys.executable, "-m", "p1s_autoclear"])

    def open_export_folder():
        exp_dir = default_export_path_var.get().strip()
        if exp_dir and Path(exp_dir).is_dir():
            _open_folder_in_explorer(Path(exp_dir))
            return
        if file_list_data:
            first_path = Path(file_list_data[0]["path"])
            if first_path.parent.is_dir():
                _open_folder_in_explorer(first_path.parent)
                return
        messagebox.showinfo(
            "Open Export Folder",
            "Set the export folder in Settings (Export section), or add a 3MF file first.",
        )

    ttk.Button(btn_frame, text="Restart", command=do_restart).pack(side=tk.LEFT, padx=(0, 4))
    ttk.Button(btn_frame, text="Open Export Folder", command=open_export_folder).pack(side=tk.LEFT, padx=(0, 4))

    def on_closing():
        save_last_settings(settings_to_dict(
            cooldown_mode=cooldown_mode_var.get(),
            cooldown_time=cooldown_time_var.get().strip(),
            cooldown_temp=cooldown_temp_var.get().strip(),
            cooldown_hold_seconds=cooldown_hold_seconds_var.get().strip(),
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
        vov = version_override_var.get().strip()
        save_app_config({
            "default_export_path": default_export_path_var.get().strip(),
            "open_export_folder_after_export": open_export_folder_var.get(),
            "default_import_path": default_import_path_var.get().strip(),
            "version_override": vov if vov and vov != "(use detected)" else "",
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
