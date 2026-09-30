#!/usr/bin/env python3
"""
ff8_battle_moment.py

Teleport-to-any-battle helper for FF8 (2000 US v1.2 with FFNx, and Remastered).
Companion to the FF8 field & moment tool. Pick a battle id from the list, arm
it, then trigger a battle in-game and you drop straight into that battle scene.

How it works
------------
FF8 keeps the current battle-scene id at logical address 0x1CFF6E0 on the
2000 US v1.2 build. This tool freezes that value to the id you choose, so
whatever battle you trigger loads the scene you selected. Remastered resolves
the same logical address through FFVIII_EFIGS.dll's page table.
There is no FFNx "battle debug" menu (unlike Field Debug), so freezing the id
is the equivalent.

Trigger a battle with FFNx's built-in force-battle: press Ctrl+B in-game, then
take a step on a field, or run around on the worldmap. Because the id is
frozen, that battle loads as your selected id. Change the id, trigger again,
repeat.

Labels: ships with ff8_battle_labels.json (built from the Battle Ambience
sheet), so each id reads like "0000  G-Soldier (Dollet)". Keep that file next
to this script. Optionally, Load scene.out to also append each encounter's
enemy model slots (c0m files) in brackets.

Live memory (arm/freeze) needs pymem and is Windows-only. On other OSes the
list still opens so you can browse and copy ids.

    pip install pymem
    python ff8_battle_moment.py
"""

import os
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

# ---------------------------------------------------------------- addresses
# These are classic FF8 logical addresses. OG PC uses them directly;
# Remastered translates them through FFVIII_EFIGS.dll's game-memory page table.
GLOBAL_BATTLE_ENCOUNTER_ID = 0x1CFF6E0   # WORD: current battle scene id
BATTLE_RESULT_STATE        = 0x1CFF6E7   # BYTE: 2 escaped, 4 won, other = misc

# Some builds may read the scene id from a second WORD (opcode_battle + 0x66)
# instead. If freezing 0x1CFF6E0 alone does not swap the loaded battle, set
# this to that address and the tool will freeze both. 0 = off.
BATTLE_ENCOUNTER_ID_ALT    = 0x0

IMAGE_BASE_EXPECTED = 0x400000
REMASTERED_PAGE_TABLE_RVA = 0x188EDD0  # dword_1188EDD0 in FFVIII_EFIGS.dll
PROC_NAMES          = ["FF8.exe", "FF8_EN.exe", "FFVIII.exe"]

# ---------------------------------------------------------------- constants
MAX_BATTLES  = 1024          # 0..1023 possible battle ids
SCENE_RECORD = 128           # scene.out record size in bytes
ENEMY_OFF    = 56            # offset of enemy_com_value[8] inside a record
ENEMY_SLOTS  = 8
FREEZE_HZ    = 250           # how often the freeze thread rewrites the id

# id -> label file, kept next to this script. Built from the Battle Ambience
# sheet: "Description (Location)", e.g. 0 -> "G-Soldier (Dollet)".
LABELS_FILE  = "ff8_battle_labels.json"


def com_label(com):
    """Map a scene.out com value to a readable model name."""
    # com_id 16 == c0m000, so c0m# = com - 16. com 0 = empty slot.
    if com >= 16:
        return f"c0m{com - 16:03d}"
    return f"com{com}"


def load_labels():
    """Load id -> label text from LABELS_FILE next to this script (if present)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), LABELS_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return {int(k): (v or "") for k, v in raw.items()}
    except Exception:
        return {}


def build_battle_items(labels, scene_path=None):
    """Return a list of (id, label) for every battle id.

    Primary label comes from the labels dict (the Battle Ambience sheet).
    Ids with no label show 'Encounter N'. If a scene.out is given, the enemy
    model slots (c0m###) are appended in brackets.
    """
    enemies = {}
    count = MAX_BATTLES

    if scene_path and os.path.isfile(scene_path):
        with open(scene_path, "rb") as fh:
            data = fh.read()
        for i in range(len(data) // SCENE_RECORD):
            rec = data[i * SCENE_RECORD:(i + 1) * SCENE_RECORD]
            coms = rec[ENEMY_OFF:ENEMY_OFF + ENEMY_SLOTS]
            enemies[i] = [c for c in coms if c]  # nonzero slot = enemy present
        count = max(count, len(data) // SCENE_RECORD)

    if labels:
        count = max(count, max(labels) + 1)

    items = []
    for i in range(count):
        text = labels.get(i) or f"Encounter {i}"
        slots = enemies.get(i)
        if slots:
            text = text + "  [" + ", ".join(com_label(c) for c in slots) + "]"
        items.append((i, f"{i:04d}   {text}"))
    return items


# ---------------------------------------------------------------- memory
class Mem:
    def __init__(self, log):
        self.pm = None
        self.log = log
        self.base = None
        self.name = None
        self.is_remastered = False
        self.page_table_address = None

    def attached(self):
        return self.pm is not None

    def attach(self):
        try:
            import pymem
        except ImportError:
            self.log("pymem is not installed. Run:  pip install pymem")
            return False

        last = None
        for name in PROC_NAMES:
            try:
                self.pm = pymem.Pymem(name)
                self.name = name
                break
            except Exception as e:  # process not found / access
                last = e
                self.pm = None

        if not self.pm:
            self.log(f"FF8 not found ({', '.join(PROC_NAMES)}). Is it running? ({last})")
            return False

        try:
            self.base = self.pm.base_address
        except Exception:
            self.base = None

        if self.name.lower() == "ffviii.exe":
            try:
                from pymem.process import module_from_name
                module = module_from_name(self.pm.process_handle, "FFVIII_EFIGS.dll")
                if module is None:
                    raise RuntimeError("FFVIII_EFIGS.dll is not loaded")
                self.page_table_address = module.lpBaseOfDll + REMASTERED_PAGE_TABLE_RVA
                self.is_remastered = True
            except Exception as e:
                self.pm = None
                self.name = None
                self.log(f"Could not locate FFVIII_EFIGS.dll page table: {e}")
                return False

        try:
            cur = self.pm.read_ushort(self.resolve_address(GLOBAL_BATTLE_ENCOUNTER_ID))
            self.log(f"Attached: {self.name} (PID {self.pm.process_id}). Current battle id: {cur}")
        except Exception as e:
            self.log(f"Attached: {self.name} (PID {self.pm.process_id}), but can't read encounter id: {e}")

        if self.is_remastered:
            self.log("Remastered detected; game addresses will be resolved through FFVIII_EFIGS.dll.")
        elif self.base not in (None, IMAGE_BASE_EXPECTED):
            self.log(f"Note: image base is 0x{self.base:X}, expected 0x{IMAGE_BASE_EXPECTED:X}. "
                     "Addresses assume the 2000 US v1.2 build; this may be a different version.")
        return True

    def detach(self):
        self.pm = None
        self.base = None
        self.name = None
        self.is_remastered = False
        self.page_table_address = None

    def resolve_address(self, address):
        if not self.is_remastered:
            return address

        page_index = address >> 12
        try:
            page_base = self.pm.read_uint(self.page_table_address + page_index * 4)
        except Exception as e:
            raise RuntimeError(f"Could not read Remastered page table: {e}") from e
        if page_base == 0:
            raise RuntimeError(f"Remastered address page 0x{page_index:X} is not mapped.")
        return page_base + (address & 0xFFF)

    def read_id(self):
        if not self.pm:
            return None
        try:
            return self.pm.read_ushort(self.resolve_address(GLOBAL_BATTLE_ENCOUNTER_ID))
        except Exception:
            return None

    def read_result(self):
        if not self.pm:
            return None
        try:
            return self.pm.read_uchar(self.resolve_address(BATTLE_RESULT_STATE))
        except Exception:
            return None

    def write_id(self, val):
        if not self.pm:
            return False
        try:
            self.pm.write_ushort(self.resolve_address(GLOBAL_BATTLE_ENCOUNTER_ID), val & 0xFFFF)
            if BATTLE_ENCOUNTER_ID_ALT:
                self.pm.write_ushort(self.resolve_address(BATTLE_ENCOUNTER_ID_ALT), val & 0xFFFF)
            return True
        except Exception:
            return False


RESULT_TEXT = {0: "", 1: "misc", 2: "escaped", 3: "misc", 4: "won", 5: "?"}


# ---------------------------------------------------------------- UI
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FF8 Battle Teleport")
        self.geometry("560x640")
        self.minsize(480, 520)

        self.mem = Mem(self._log)
        self.labels = load_labels()
        self.all_items = build_battle_items(self.labels)
        self.scene_path = None

        self._armed = False
        self._target = None

        self._build_ui()
        self._refresh_list()

        if self.labels:
            self._log(f"Loaded {len(self.labels)} battle labels from {LABELS_FILE}.")
        else:
            self._log(f"No {LABELS_FILE} next to the script; showing generic labels.")

        # freeze worker
        self._stop = False
        self._t = threading.Thread(target=self._freeze_loop, daemon=True)
        self._t.start()

        # live poll of the in-game id
        self.after(300, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- layout
    def _build_ui(self):
        try:
            ttk.Style(self).theme_use("clam")
        except tk.TclError:
            pass

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.attach_btn = ttk.Button(top, text="Attach to FF8", command=self._attach)
        self.attach_btn.pack(side="left")
        self.status_var = tk.StringVar(value="Not attached.")
        ttk.Label(top, textvariable=self.status_var).pack(side="left", padx=10)

        live = ttk.LabelFrame(self, text="In game", padding=8)
        live.pack(fill="x", padx=8)
        self.live_var = tk.StringVar(value="battle id: -")
        ttk.Label(live, textvariable=self.live_var, font=("TkDefaultFont", 11, "bold")).pack(side="left")
        self.armed_var = tk.StringVar(value="idle")
        ttk.Label(live, textvariable=self.armed_var).pack(side="right")

        pick = ttk.Frame(self, padding=(8, 4))
        pick.pack(fill="x")
        ttk.Label(pick, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._refresh_list())
        ttk.Entry(pick, textvariable=self.filter_var, width=18).pack(side="left", padx=(4, 12))
        ttk.Label(pick, text="ID:").pack(side="left")
        self.id_var = tk.IntVar(value=0)
        ttk.Spinbox(pick, from_=0, to=MAX_BATTLES - 1, width=7, textvariable=self.id_var,
                    command=self._select_from_spin).pack(side="left", padx=4)

        mid = ttk.Frame(self, padding=(8, 0))
        mid.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(mid, activestyle="dotbox")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        self.listbox.bind("<Double-Button-1>", lambda e: self._arm())

        act = ttk.Frame(self, padding=8)
        act.pack(fill="x")
        ttk.Button(act, text="Prev", width=6, command=lambda: self._step(-1)).pack(side="left")
        ttk.Button(act, text="Next", width=6, command=lambda: self._step(1)).pack(side="left", padx=(4, 12))
        self.arm_btn = ttk.Button(act, text="Arm (freeze id)", command=self._arm)
        self.arm_btn.pack(side="left")
        ttk.Button(act, text="Disarm", command=self._disarm).pack(side="left", padx=4)
        ttk.Button(act, text="Copy id", width=8, command=self._copy).pack(side="right")

        tools = ttk.Frame(self, padding=(8, 0))
        tools.pack(fill="x")
        ttk.Button(tools, text="Load scene.out for labels...", command=self._load_scene).pack(side="left")
        ttk.Button(tools, text="Help", width=6, command=self._help).pack(side="right")

        logf = ttk.LabelFrame(self, text="Log", padding=6)
        logf.pack(fill="both", padx=8, pady=8)
        self.logbox = tk.Text(logf, height=7, wrap="word")
        self.logbox.pack(fill="both", expand=True)
        self.logbox.configure(state="disabled")

    # ---- helpers
    def _log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _refresh_list(self):
        f = self.filter_var.get().strip().lower()
        self.listbox.delete(0, "end")
        self._view = []
        for bid, label in self.all_items:
            if not f or f in label.lower() or f == str(bid):
                self.listbox.insert("end", label)
                self._view.append(bid)

    def _selected_id(self):
        sel = self.listbox.curselection()
        if sel:
            return self._view[sel[0]]
        return int(self.id_var.get())

    def _on_list_select(self, _e):
        bid = self._selected_id()
        self.id_var.set(bid)

    def _select_from_spin(self):
        bid = int(self.id_var.get())
        if bid in self._view:
            idx = self._view.index(bid)
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(idx)
            self.listbox.see(idx)

    def _step(self, delta):
        bid = max(0, min(MAX_BATTLES - 1, self._selected_id() + delta))
        self.id_var.set(bid)
        self.filter_var.set("")
        self._refresh_list()
        self._select_from_spin()
        if self._armed:
            self._target = bid
            self.mem.write_id(bid)
            self.armed_var.set(f"ARMED  {bid:04d}")

    def _attach(self):
        if self.mem.attach():
            self.status_var.set(f"Attached: {self.mem.name}")
        else:
            self.status_var.set("Not attached (see log).")

    def _arm(self):
        if not self.mem.attached():
            self._log("Attach to FF8 first.")
            return
        bid = self._selected_id()
        self._target = bid
        self._armed = True
        self.mem.write_id(bid)
        self.armed_var.set(f"ARMED  {bid:04d}")
        self._log(f"Armed battle {bid}. In FF8: press Ctrl+B, then take a step to trigger it.")

    def _disarm(self):
        self._armed = False
        self.armed_var.set("idle")
        self._log("Disarmed. Encounters are back to normal.")

    def _copy(self):
        bid = self._selected_id()
        self.clipboard_clear()
        self.clipboard_append(str(bid))
        self._log(f"Copied id {bid} to clipboard.")

    def _load_scene(self):
        path = filedialog.askopenfilename(
            title="Select battle/scene.out",
            filetypes=[("scene.out", "scene.out"), ("All files", "*.*")])
        if not path:
            return
        self.scene_path = path
        self.all_items = build_battle_items(self.labels, path)
        self._refresh_list()
        self._log(f"Loaded {os.path.basename(path)}: enemy model slots appended to each label.")

    def _help(self):
        self._log(
            "1) Start FF8 with FFNx, or launch Remastered, and load any save.\n"
            "2) Attach to FF8.\n"
            "3) Pick a battle id, click Arm (freeze id).\n"
            "4) In FF8: press Ctrl+B (FFNx force-battle), then take a step on a field, "
            "or move on the worldmap. You load the armed battle.\n"
            "5) Change id and trigger again to cycle. Disarm to stop.\n"
            "If the loaded battle does not match the armed id on your build, tell me and "
            "we set BATTLE_ENCOUNTER_ID_ALT (the tool then freezes both addresses).")

    # ---- workers
    def _freeze_loop(self):
        period = 1.0 / FREEZE_HZ
        while not self._stop:
            if self._armed and self._target is not None:
                self.mem.write_id(self._target)
            time.sleep(period)

    def _poll(self):
        if self.mem.attached():
            cur = self.mem.read_id()
            res = self.mem.read_result()
            if cur is None:
                self.live_var.set("battle id: - (process gone?)")
            else:
                rtxt = RESULT_TEXT.get(res, "")
                self.live_var.set(f"battle id: {cur}" + (f"   ({rtxt})" if rtxt else ""))
        self.after(300, self._poll)

    def _on_close(self):
        self._stop = True
        self._armed = False
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
