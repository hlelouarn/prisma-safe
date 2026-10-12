r"""
Sauvegarde Event - Photos Les Garennes
Copie les dossiers HD (depot), REDIM et DATA d'un event vers le dossier de tri
du disque dur, sous la forme :
    <DOSSIER DE TRI>\<EVENT>_HD
    <DOSSIER DE TRI>\<EVENT>_REDIM
    <DOSSIER DE TRI>\<EVENT>_DATA
Le contenu est copié directement dans ces dossiers (aucun niveau ajouté).

Les chemins sont réglés une fois par PC (bouton Réglages) et enregistrés dans
%APPDATA%\SauvegardeEvent\config.json.
"""

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Version publiée : le tag Git doit porter le même numéro (v1.0.0), le build le vérifie.
VERSION = "1.0.0"

APP_NAME = "Sauvegarde Event"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "SauvegardeEvent"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "sauvegarde.log"

DEFAULT_CONFIG = {
    "src_hd": r"C:\Kadra\depot",
    "src_redim": r"C:\Kadra\redim",
    "src_data": r"C:\Kadra\phase 1\python\data",
    "dest": r"R:\0.2 DOSSIER DE TRI",
    "miroir": True,
    "auto": True,
    "intervalle_min": 10,
}

# Robocopy : codes de retour < 8 = succès (avec ou sans fichiers copiés)
ROBOCOPY_OK_MAX = 7

# Garde-fou miroir : on bloque la suppression si elle toucherait plus de
# SEUIL_SUPPRESSION des photos déjà sauvegardées (à partir de MIN_FICHIERS photos).
SEUIL_SUPPRESSION = 0.80
MIN_FICHIERS = 10
EXT_PHOTOS = {".jpg", ".jpeg", ".nef", ".nrw", ".cr2", ".cr3", ".arw", ".raf",
              ".dng", ".tif", ".tiff", ".png", ".heic"}


# ---------------------------------------------------------------- config ----

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def config_is_set():
    return CONFIG_FILE.exists()


# ------------------------------------------------------------ logique -------

def list_events(cfg):
    """Events = sous-dossiers de depot et de redim, les plus récents en premier."""
    found = {}
    for key in ("src_hd", "src_redim"):
        root = Path(cfg[key])
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir():
                    mtime = d.stat().st_mtime
                    found[d.name] = max(found.get(d.name, 0), mtime)
    return sorted(found, key=lambda n: found[n], reverse=True)


def current_event_hint(cfg):
    """Lit le fichier current_event du dossier data, s'il existe."""
    data = Path(cfg["src_data"])
    for name in ("current_event", "current_event.txt"):
        f = data / name
        if f.is_file():
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore").strip()
                if txt:
                    return txt.splitlines()[0].strip()
            except Exception:
                pass
    return None


def build_jobs(cfg, event):
    """Liste (libellé, source, destination). Pas de niveau ajouté."""
    dest_root = Path(cfg["dest"])
    return [
        ("HD", Path(cfg["src_hd"]) / event, dest_root / f"{event}_HD"),
        ("REDIM", Path(cfg["src_redim"]) / event, dest_root / f"{event}_REDIM"),
        ("DATA", Path(cfg["src_data"]), dest_root / f"{event}_DATA"),
    ]


def list_files(root):
    """Chemins relatifs (en minuscules) de tous les fichiers sous root."""
    out = set()
    if not Path(root).is_dir():
        return out
    for dirpath, _, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        for f in files:
            out.add(os.path.normcase(os.path.join(rel, f)).lower())
    return out


def check_suppression(src, dst):
    """Retourne (bloquer, nb_supprimées, nb_sauvegardées) pour les photos.
    S'il n'y a aucune photo côté destination, on raisonne sur tous les fichiers."""
    dst_files = list_files(dst)
    if not dst_files:
        return False, 0, 0
    src_files = list_files(src)
    photos = {f for f in dst_files if os.path.splitext(f)[1] in EXT_PHOTOS}
    base = photos or dst_files
    supprimees = len(base - src_files)
    total = len(base)
    bloquer = total >= MIN_FICHIERS and supprimees / total > SEUIL_SUPPRESSION
    return bloquer, supprimees, total


def run_robocopy(src, dst, miroir):
    args = [
        "robocopy", str(src), str(dst),
        "/E",            # sous-dossiers, y compris vides
        "/COPY:DAT",     # données, attributs, dates
        "/R:2", "/W:5",  # 2 essais, 5 s d'attente
        "/MT:8",         # 8 copies en parallèle
        "/NP", "/NDL", "/NJH",
        "/XF", "Thumbs.db", "desktop.ini", "*.tmp",
    ]
    if miroir:
        args.append("/PURGE")  # supprime côté destination ce qui n'existe plus à la source
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="cp850", errors="replace", creationflags=flags)
    copied = sum(1 for line in p.stdout.splitlines()
                 if line.strip().lower().startswith(("nouveau fichier", "new file",
                                                     "plus récent", "newer",
                                                     "modifié", "changed")))
    return p.returncode, copied, p.stdout


# ------------------------------------------------------------------ GUI ----

def window_icon():
    """Icône des fenêtres : celle de l'exe une fois compilé, logo.ico à côté du script sinon."""
    if getattr(sys, "frozen", False):
        return sys.executable
    ico = Path(__file__).with_name("logo.ico")
    return str(ico) if ico.is_file() else None


class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("src_hd", "Dossier HD (depot)"),
        ("src_redim", "Dossier REDIM"),
        ("src_data", "Dossier DATA"),
        ("dest", "Dossier de tri (disque dur)"),
    ]

    def __init__(self, master, cfg, on_save):
        super().__init__(master)
        self.title("Réglages des chemins")
        self.resizable(False, False)
        self.cfg, self.on_save = dict(cfg), on_save
        self.vars = {}
        for i, (key, label) in enumerate(self.FIELDS):
            ttk.Label(self, text=label).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            v = tk.StringVar(value=self.cfg[key])
            self.vars[key] = v
            ttk.Entry(self, textvariable=v, width=55).grid(row=i, column=1, padx=4)
            ttk.Button(self, text="…", width=3,
                       command=lambda k=key: self.browse(k)).grid(row=i, column=2, padx=8)
        ttk.Button(self, text="Enregistrer", command=self.save).grid(
            row=len(self.FIELDS), column=1, sticky="e", padx=4, pady=10)
        self.transient(master)
        self.grab_set()

    def browse(self, key):
        d = filedialog.askdirectory(initialdir=self.vars[key].get() or "C:\\", parent=self)
        if d:
            self.vars[key].set(os.path.normpath(d))

    def save(self):
        for k, v in self.vars.items():
            self.cfg[k] = v.get().strip()
        save_config(self.cfg)
        self.on_save(self.cfg)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        icon = window_icon()
        if icon:
            try:
                self.iconbitmap(default=icon)  # vaut aussi pour la fenêtre des réglages
            except tk.TclError:
                pass
        self.minsize(620, 420)
        self.cfg = load_config()
        self.busy = False
        self.next_run = time.time() + 5 if self.cfg["auto"] else None
        self.alertes_vues = set()  # évite de répéter la même alerte toutes les 10 min

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Event :").pack(side="left")
        self.event_var = tk.StringVar()
        self.event_cb = ttk.Combobox(top, textvariable=self.event_var, width=40, state="readonly")
        self.event_cb.pack(side="left", padx=6)
        ttk.Button(top, text="↻", width=3, command=self.refresh_events).pack(side="left")
        ttk.Button(top, text="Réglages", command=self.open_settings).pack(side="right")

        opts = ttk.Frame(self, padding=(10, 0))
        opts.pack(fill="x")
        self.auto_var = tk.BooleanVar(value=self.cfg["auto"])
        self.interval_var = tk.IntVar(value=self.cfg["intervalle_min"])
        self.miroir_var = tk.BooleanVar(value=self.cfg["miroir"])
        ttk.Checkbutton(opts, text="Automatique toutes les", variable=self.auto_var,
                        command=self.on_auto_toggle).pack(side="left")
        ttk.Spinbox(opts, from_=1, to=120, width=4, textvariable=self.interval_var).pack(side="left", padx=4)
        ttk.Label(opts, text="min").pack(side="left")
        ttk.Checkbutton(opts, text="Miroir (supprime aussi sur le disque)",
                        variable=self.miroir_var, command=self.persist_options).pack(side="left", padx=20)

        btns = ttk.Frame(self, padding=10)
        btns.pack(fill="x")
        self.run_btn = ttk.Button(btns, text="Sauvegarder maintenant", command=self.start_backup)
        self.run_btn.pack(side="left")
        self.status = ttk.Label(btns, text="")
        self.status.pack(side="left", padx=12)

        self.log = tk.Text(self, height=14, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.refresh_events()
        if not config_is_set():
            self.after(200, self.open_settings)
        self.after(1000, self.tick)

    # -- helpers
    def write_log(self, msg):
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d} {line}\n")
        except Exception:
            pass

    def persist_options(self):
        self.cfg["miroir"] = self.miroir_var.get()
        self.cfg["auto"] = self.auto_var.get()
        try:
            self.cfg["intervalle_min"] = max(1, int(self.interval_var.get()))
        except (ValueError, tk.TclError):
            pass
        save_config(self.cfg)

    def refresh_events(self):
        events = list_events(self.cfg)
        self.event_cb["values"] = events
        hint = current_event_hint(self.cfg)
        if hint in events:
            self.event_var.set(hint)
        elif events and self.event_var.get() not in events:
            self.event_var.set(events[0])

    def open_settings(self):
        SettingsDialog(self, self.cfg, self.on_settings_saved)

    def on_settings_saved(self, cfg):
        self.cfg.update(cfg)
        self.write_log("Réglages enregistrés.")
        self.refresh_events()

    def on_auto_toggle(self):
        self.persist_options()
        if self.auto_var.get():
            self.next_run = time.time()
            self.write_log(f"Mode automatique activé ({self.cfg['intervalle_min']} min).")
        else:
            self.next_run = None
            self.write_log("Mode automatique désactivé.")

    def tick(self):
        if self.auto_var.get() and self.next_run and time.time() >= self.next_run and not self.busy:
            self.start_backup()
        if self.auto_var.get() and self.next_run and not self.busy:
            rest = int(self.next_run - time.time())
            if rest > 0:
                self.status.configure(text=f"Prochaine copie dans {rest // 60:02d}:{rest % 60:02d}")
        self.after(1000, self.tick)

    # -- sauvegarde
    def start_backup(self):
        event = self.event_var.get()
        if not event:
            messagebox.showwarning(APP_NAME, "Aucun event sélectionné.")
            return
        if self.busy:
            return
        self.persist_options()
        self.busy = True
        self.run_btn.configure(state="disabled")
        self.status.configure(text="Copie en cours…")
        threading.Thread(target=self.backup_worker,
                         args=(event, self.miroir_var.get()), daemon=True).start()

    def backup_worker(self, event, miroir):
        dest_root = Path(self.cfg["dest"])
        errors = 0
        if not dest_root.is_dir():
            self.after(0, self.write_log, f"⚠ Disque de destination introuvable : {dest_root}")
            errors = 3
        else:
            for label, src, dst in build_jobs(self.cfg, event):
                if not src.is_dir():
                    self.after(0, self.write_log, f"{label:5}  source absente, ignorée ({src})")
                    continue
                miroir_job = miroir
                if miroir:
                    bloquer, nb_sup, nb_tot = check_suppression(src, dst)
                    if bloquer:
                        miroir_job = False
                        errors += 1
                        msg = (f"{label} : le miroir supprimerait {nb_sup} des {nb_tot} photos "
                               f"déjà sauvegardées ({nb_sup * 100 // nb_tot} %). "
                               f"Suppression BLOQUÉE : seules les nouveautés sont copiées.")
                        self.after(0, self.write_log, "⚠ " + msg)
                        cle = (event, label)
                        if cle not in self.alertes_vues:
                            self.alertes_vues.add(cle)
                            self.after(0, lambda m=msg, d=dst: messagebox.showwarning(
                                APP_NAME, f"{m}\n\nVérifie la source avant toute chose.\n"
                                          f"Si la suppression est voulue, fais le ménage à la main dans :\n{d}"))
                try:
                    code, copied, _ = run_robocopy(src, dst, miroir_job)
                except FileNotFoundError:
                    self.after(0, self.write_log, "⚠ robocopy introuvable (Windows requis).")
                    errors += 1
                    break
                if code <= ROBOCOPY_OK_MAX:
                    self.after(0, self.write_log, f"{label:5}  OK, {copied} fichier(s) copié(s) → {dst.name}")
                else:
                    errors += 1
                    self.after(0, self.write_log, f"⚠ {label:5}  ERREUR robocopy (code {code}) → {dst}")
        self.after(0, self.backup_done, errors)

    def backup_done(self, errors):
        self.busy = False
        self.run_btn.configure(state="normal")
        now = datetime.now().strftime("%H:%M")
        if errors:
            self.status.configure(text=f"⚠ Erreur à {now}, voir le journal")
            self.bell()
        else:
            self.status.configure(text=f"Dernière copie OK à {now}")
        if self.auto_var.get():
            self.next_run = time.time() + 60 * self.cfg["intervalle_min"]


if __name__ == "__main__":
    App().mainloop()
