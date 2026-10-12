r"""
Prisma Safe - Photos Les Garennes
Copie les dossiers HD (depot), REDIM et DATA d'un event vers le dossier de tri
du disque dur, sous la forme :
    <DOSSIER DE TRI>\<EVENT>_HD
    <DOSSIER DE TRI>\<EVENT>_REDIM
    <DOSSIER DE TRI>\<EVENT>_DATA
Le contenu est copié directement dans ces dossiers (aucun niveau ajouté).

Les chemins sont réglés une fois par PC (bouton Réglages) et enregistrés dans
%APPDATA%\PrismaSafe\config.json.

Fermer la fenêtre ne l'arrête pas : elle se range près de l'horloge et les
copies continuent. « Quitter » (dans la fenêtre, ou au clic droit sur l'icône)
l'arrête vraiment.

Bibliothèque standard seule : robocopy et curl sont ceux de Windows, l'icône
près de l'horloge passe par Win32 (ctypes).
"""

import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from types import SimpleNamespace

# Version publiée : le tag Git doit porter le même numéro (« v » devant), le build le vérifie.
VERSION = "1.2.0"

APP_NAME = "Prisma Safe"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PrismaSafe"
OLD_CONFIG_DIR = CONFIG_DIR.with_name("SauvegardeEvent")  # le nom de l'outil en version 1.0.0
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


def adopt_old_config():
    """Un PC réglé du temps où l'outil s'appelait Sauvegarde Event garde ses chemins."""
    old = OLD_CONFIG_DIR / "config.json"
    try:
        if not CONFIG_FILE.exists() and old.is_file():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old, CONFIG_FILE)
    except OSError:
        pass  # tant pis : la fenêtre des réglages s'ouvrira, comme sur un PC neuf


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
    # Popen plutôt que run, pour que Stop puisse l'arrêter ; la commande, elle, n'a pas changé.
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="cp850", errors="replace", creationflags=flags)
    _robocopy.append(p)
    try:
        if _stop.is_set():  # Stop est tombé pile pendant le démarrage
            p.kill()
        stdout = p.communicate()[0]
    finally:
        _robocopy.remove(p)
    copied = sum(1 for line in stdout.splitlines()
                 if line.strip().lower().startswith(("nouveau fichier", "new file",
                                                     "plus récent", "newer",
                                                     "modifié", "changed")))
    return p.returncode, copied, stdout


_robocopy = []             # le robocopy en cours
_stop = threading.Event()  # levé par Stop, baissé au départ de chaque copie


def stop_robocopy():
    """Stop : arrête net le robocopy en cours, s'il y en a un. Le fichier qu'il copiait reste
    incomplet sur le disque ; la copie suivante le reprend (taille et date ne correspondent pas).
    Un robocopy arrêté rend un code qui ressemble à un succès : c'est `_stop` qui fait foi."""
    _stop.set()
    for p in list(_robocopy):
        try:
            p.kill()
        except OSError:
            pass


def event_to_follow(hint, followed, events):
    """L'event sur lequel Kadra vient de passer, s'il faut y passer aussi ; None sinon.

    `hint` est ce que dit son fichier current_event, `followed` le dernier indice déjà pris
    en compte : un event choisi à la main reste donc en place tant que Kadra n'en change pas.
    Un event qui n'a pas encore de dossier n'est pas suivi (rien à copier) : on y reviendra.
    """
    return hint if hint and hint != followed and hint in events else None


# ---------------------------------------------------- démarrage de Windows ----
# La clé Run de l'utilisateur : pas de droits d'administrateur, rien à nettoyer ailleurs.

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "PrismaSafe"
ARG_AT_STARTUP = "--au-demarrage"  # lancé par Windows : la fenêtre reste près de l'horloge


def startup_enabled(key=RUN_KEY):
    """Vrai si Windows lance l'outil à l'ouverture de la session."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_startup(on, key=RUN_KEY, exe=None):
    """Inscrit l'outil au démarrage de Windows, ou l'en retire."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ,
                              f'"{exe or sys.executable}" {ARG_AT_STARTUP}')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


def refresh_startup(key=RUN_KEY):
    """L'exe a pu être déplacé ou renommé : l'inscription suit son emplacement."""
    try:
        if getattr(sys, "frozen", False) and startup_enabled(key):
            set_startup(True, key)
    except OSError:
        pass


# ----------------------------------------------------------- mise à jour ----
# Les Releases publiques du dépôt GitHub : rien de secret, pas de jeton. Le nouvel exe est
# téléchargé à côté de l'actuel, vérifié, mis à sa place, puis relancé.

DEPOT = "hlelouarn/prisma-safe"
URL_LATEST = f"https://api.github.com/repos/{DEPOT}/releases/latest"
URL_DOWNLOADS = f"https://github.com/{DEPOT}/releases/download/"
UPDATE_EVERY_H = 6  # l'application reste ouverte des jours : on redemande de temps en temps
ARG_AFTER_UPDATE = "--apres-maj"


def parse_version(text):
    """'v1.2.3' ou '1.2.3' → (1, 2, 3) ; None si ce n'est pas un numéro de version."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(text).strip())
    return tuple(int(n) for n in m.groups()) if m else None


def find_update(release, current=VERSION):
    """Lit la fiche GitHub de la dernière Release. Rend {version, url, size, sha256} si elle
    est plus récente que `current` et porte un exe du dépôt ; None sinon."""
    latest = parse_version(release.get("tag_name", ""))
    if not latest or latest <= (parse_version(current) or (0, 0, 0)):
        return None
    for asset in release.get("assets") or []:
        url = str(asset.get("browser_download_url", ""))
        if str(asset.get("name", "")).lower().endswith(".exe") and url.startswith(URL_DOWNLOADS):
            digest = str(asset.get("digest") or "")
            return {"version": ".".join(map(str, latest)), "url": url, "size": asset.get("size"),
                    "sha256": digest[7:].lower() if digest.startswith("sha256:") else None}
    return None


def fetch(url, dest, timeout):
    """Télécharge url dans dest. urllib d'abord ; s'il échoue — sur un poste, le certificat
    racine de GitHub manquait au magasin Windows — curl.exe de Windows prend le relais :
    lui sait aller chercher la racine. La vérification du certificat n'est jamais coupée."""
    agent = f"PrismaSafe/{VERSION}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": agent})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        return
    except Exception as first:
        try:
            p = subprocess.run(
                ["curl.exe", "--fail", "--location", "--silent", "--show-error",
                 "--max-time", str(timeout * 4), "--user-agent", agent, "--output", str(dest), url],
                capture_output=True, text=True, errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            raise first from None
        if p.returncode != 0:
            raise RuntimeError(f"{first} ; curl : {p.stderr.strip() or p.returncode}") from None


def check_update():
    """Interroge GitHub. Rend la mise à jour disponible (cf. find_update) ou None."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_DIR / "derniere_release.json"
    try:
        fetch(URL_LATEST, tmp, 15)
        return find_update(json.loads(tmp.read_text(encoding="utf-8")))
    finally:
        tmp.unlink(missing_ok=True)


def verify_download(path, size=None, sha256=None):
    """Refuse un téléchargement tronqué ou altéré : la taille et l'empreinte annoncées par
    GitHub, et que ce soit bien un exe."""
    real = Path(path).stat().st_size
    if size and real != size:
        raise ValueError(f"téléchargement incomplet ({real} octets sur {size})")
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        head = f.read(2)
        digest.update(head)
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    if head != b"MZ":
        raise ValueError("le fichier reçu n'est pas un exe")
    if sha256 and digest.hexdigest() != sha256:
        raise ValueError("l'empreinte du fichier reçu n'est pas celle annoncée par GitHub")


def download_update(update):
    """Télécharge le nouvel exe à côté de l'actuel et le vérifie. Rend son chemin."""
    if not getattr(sys, "frozen", False):  # lancé comme script : l'« exe » serait Python lui-même
        raise RuntimeError("la mise à jour ne s'installe que sur l'exe")
    exe = Path(sys.executable)
    new = exe.with_name(exe.name + ".new")
    try:
        fetch(update["url"], new, 60)
        verify_download(new, update.get("size"), update.get("sha256"))
    except Exception:
        try:
            new.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return new


def apply_update(new):
    """Met le nouvel exe à la place de l'actuel. Windows refuse d'écraser un exe en marche
    mais accepte de le renommer : l'ancien devient .old, supprimé au lancement suivant."""
    exe = Path(sys.executable)
    old = exe.with_name(exe.name + ".old")
    old.unlink(missing_ok=True)
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)  # on remet l'ancien : mieux vaut l'ancienne version que rien
        raise


def relaunch():
    """Lance le nouvel exe comme une application neuve. Sans PYINSTALLER_RESET_ENVIRONMENT,
    il se croirait l'enfant de celui-ci (même chemin) et tournerait sur ses fichiers
    temporaires : l'ancienne version, bientôt effacée."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI_", "_MEIPASS"))
           and k.upper() not in ("TCL_LIBRARY", "TK_LIBRARY")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
             | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    subprocess.Popen([sys.executable, ARG_AFTER_UPDATE], env=env, close_fds=True,
                     creationflags=flags)


def cleanup_old():
    """Efface l'exe de la version précédente et un téléchargement resté en plan."""
    if getattr(sys, "frozen", False):
        for suffix in (".old", ".new"):
            try:
                Path(sys.executable + suffix).unlink(missing_ok=True)
            except OSError:
                pass  # l'ancienne version finit de se fermer : ce sera pour le lancement suivant


# -------------------------------------------------------------- Windows ----

# Une seule application par dossier de réglages : ce nom sert au verrou, et à retrouver la
# fenêtre cachée de l'icône depuis un second lancement.
INSTANCE_ID = "PrismaSafe-" + hashlib.sha256(
    str(CONFIG_DIR).lower().encode("utf-8")).hexdigest()[:12]

WM_NULL, WM_DESTROY, WM_CLOSE, WM_COMMAND = 0x0000, 0x0002, 0x0010, 0x0111
WM_CONTEXTMENU, WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x007B, 0x0202, 0x0203, 0x0205
NIN_BALLOONUSERCLICK = 0x0405
WM_TRAY, WM_SHOW = 0x8001, 0x8002  # clic sur l'icône ; « montre-toi », d'un second lancement
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 16
NIIF_INFO, NIIF_WARNING = 1, 2
MF_STRING, MF_SEPARATOR, TPM_RIGHTBUTTON = 0x0000, 0x0800, 0x0002
MENU = {1: "open", 2: "backup", 3: "quit", 4: "stop"}
ERROR_ALREADY_EXISTS = 183

_mutex = []  # le verrou d'instance, gardé jusqu'à la fin du processus


def win32():
    """Les fonctions Win32 utilisées, avec leurs signatures : sans elles un handle 64 bits
    est tronqué. Chargées au premier besoin."""
    if hasattr(win32, "api"):
        return win32.api
    w, c_int = wintypes, ctypes.c_int
    lresult = ctypes.c_ssize_t
    wndproc = ctypes.WINFUNCTYPE(lresult, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", w.UINT), ("lpfnWndProc", wndproc), ("cbClsExtra", c_int),
                    ("cbWndExtra", c_int), ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                    ("hCursor", w.HANDLE), ("hbrBackground", w.HANDLE),
                    ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
                    ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", w.WCHAR * 128),
                    ("dwState", w.DWORD), ("dwStateMask", w.DWORD), ("szInfo", w.WCHAR * 256),
                    ("uVersion", w.UINT), ("szInfoTitle", w.WCHAR * 64),
                    ("dwInfoFlags", w.DWORD), ("guidItem", ctypes.c_byte * 16),
                    ("hBalloonIcon", w.HICON)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

    def sig(func, restype, *argtypes):
        func.restype, func.argtypes = restype, argtypes

    sig(user32.DefWindowProcW, lresult, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    sig(user32.RegisterClassW, w.ATOM, ctypes.POINTER(WNDCLASSW))
    sig(user32.CreateWindowExW, w.HWND, w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
        c_int, c_int, c_int, c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
    sig(user32.DestroyWindow, w.BOOL, w.HWND)
    sig(user32.GetMessageW, c_int, ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
    sig(user32.TranslateMessage, w.BOOL, ctypes.POINTER(w.MSG))
    sig(user32.DispatchMessageW, lresult, ctypes.POINTER(w.MSG))
    sig(user32.PostMessageW, w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
    sig(user32.PostQuitMessage, None, c_int)
    sig(user32.RegisterWindowMessageW, w.UINT, w.LPCWSTR)
    sig(user32.FindWindowW, w.HWND, w.LPCWSTR, w.LPCWSTR)
    sig(user32.SetForegroundWindow, w.BOOL, w.HWND)
    sig(user32.GetCursorPos, w.BOOL, ctypes.POINTER(w.POINT))
    sig(user32.CreatePopupMenu, w.HMENU)
    sig(user32.AppendMenuW, w.BOOL, w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR)
    sig(user32.SetMenuDefaultItem, w.BOOL, w.HMENU, w.UINT, w.UINT)
    sig(user32.TrackPopupMenu, w.BOOL, w.HMENU, w.UINT, c_int, c_int, c_int, w.HWND, w.LPVOID)
    sig(user32.DestroyMenu, w.BOOL, w.HMENU)
    sig(user32.SetWindowPos, w.BOOL, w.HWND, w.HWND, c_int, c_int, c_int, c_int, w.UINT)
    sig(user32.MessageBoxW, c_int, w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT)
    sig(user32.LoadIconW, w.HICON, w.HINSTANCE, w.LPVOID)
    sig(shell32.Shell_NotifyIconW, w.BOOL, w.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
    sig(shell32.ExtractIconExW, w.UINT, w.LPCWSTR, c_int, ctypes.POINTER(w.HICON),
        ctypes.POINTER(w.HICON), w.UINT)
    sig(kernel32.GetModuleHandleW, w.HMODULE, w.LPCWSTR)
    sig(kernel32.CreateMutexW, w.HANDLE, w.LPVOID, w.BOOL, w.LPCWSTR)
    sig(kernel32.CloseHandle, w.BOOL, w.HANDLE)
    sig(dwmapi.DwmSetWindowAttribute, ctypes.c_long, w.HWND, w.DWORD, w.LPCVOID, w.DWORD)
    win32.api = SimpleNamespace(user32=user32, shell32=shell32, kernel32=kernel32,
                                dwmapi=dwmapi, WNDPROC=wndproc, WNDCLASSW=WNDCLASSW,
                                NOTIFYICONDATAW=NOTIFYICONDATAW)
    return win32.api


def another_instance_running():
    """Vrai si l'application tourne déjà avec ces réglages. Sinon prend le verrou."""
    kernel32 = win32().kernel32
    handle = kernel32.CreateMutexW(None, False, INSTANCE_ID)
    if handle and ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return True
    _mutex.append(handle)
    return False


def wake_first_instance():
    """Demande à l'application déjà ouverte de montrer sa fenêtre. Faux si on ne la trouve pas."""
    user32 = win32().user32
    hwnd = user32.FindWindowW(INSTANCE_ID, None)
    return bool(hwnd and user32.PostMessageW(hwnd, WM_SHOW, 0, 0))


def enable_dpi_awareness():
    """Texte net sur un écran à 125 ou 150 % (sinon Windows agrandit l'image, floue)."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def dark_title_bar(window):
    """Barre de titre à la couleur de l'application (Windows 10 et 11). À appeler une fois
    la fenêtre à l'écran ; sans effet si Windows ne sait pas faire."""
    try:
        api = win32()
        hwnd = int(window.wm_frame(), 16)
        on = ctypes.c_int(1)
        api.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)     # mode sombre
        r, g, b = (int(NIGHT[i:i + 2], 16) for i in (1, 3, 5))
        color = wintypes.DWORD(r | g << 8 | b << 16)
        api.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), 4)  # teinte exacte (11)
        api.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)             # redessine le cadre
    except Exception:
        pass


class Tray(threading.Thread):
    """L'icône près de l'horloge, sans dépendance : Win32 par ctypes.

    Elle vit dans son propre fil, avec sa fenêtre invisible et sa boucle de messages, et ne
    touche JAMAIS à Tk : elle dépose ses demandes ("open", "backup", "quit") dans `events`,
    que la fenêtre relève. Un appel Tk fait d'ici bloquerait l'application.
    """

    def __init__(self, events, icon_path):
        super().__init__(name="tray", daemon=True)
        self.events, self.icon_path = events, icon_path
        self.ready = threading.Event()
        self.ok = False
        self.hwnd = None
        self._nid = None
        self._taskbar_created = None
        self._lock = threading.Lock()

    def run(self):
        try:
            self._setup()
            self.ok = True
        except Exception:
            pass
        self.ready.set()
        if not self.ok:
            return
        user32, msg = win32().user32, wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _setup(self):
        api = win32()
        self._proc_ref = api.WNDPROC(self._proc)  # à garder en vie tant que la fenêtre existe
        self._taskbar_created = api.user32.RegisterWindowMessageW("TaskbarCreated")
        hinst = api.kernel32.GetModuleHandleW(None)
        cls = api.WNDCLASSW()
        cls.lpfnWndProc, cls.hInstance, cls.lpszClassName = self._proc_ref, hinst, INSTANCE_ID
        if not api.user32.RegisterClassW(ctypes.byref(cls)):
            raise OSError("RegisterClassW")
        self.hwnd = api.user32.CreateWindowExW(0, INSTANCE_ID, APP_NAME, 0, 0, 0, 0, 0,
                                               None, None, hinst, None)
        if not self.hwnd:
            raise OSError("CreateWindowExW")
        icon = wintypes.HICON()
        if self.icon_path:  # un .ico comme un .exe : Windows en tire la petite icône
            api.shell32.ExtractIconExW(self.icon_path, 0, None, ctypes.byref(icon), 1)
        nid = api.NOTIFYICONDATAW()
        nid.cbSize, nid.hWnd, nid.uID = ctypes.sizeof(nid), self.hwnd, 1
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = icon.value or api.user32.LoadIconW(None, ctypes.c_void_p(32512))
        nid.szTip = APP_NAME
        self._nid = nid
        if not self._notify(NIM_ADD):
            raise OSError("Shell_NotifyIconW")

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam in (WM_LBUTTONUP, WM_LBUTTONDBLCLK, NIN_BALLOONUSERCLICK):
                    self.events.put("open")
                elif lparam in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._menu()
                return 0
            if msg == WM_SHOW:
                self.events.put("open")
                return 0
            if msg == WM_COMMAND:
                if (wparam & 0xFFFF) in MENU:
                    self.events.put(MENU[wparam & 0xFFFF])
                return 0
            if msg == self._taskbar_created:  # l'Explorateur a redémarré : l'icône est à remettre
                self._notify(NIM_ADD)
                return 0
            if msg == WM_CLOSE:
                win32().user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._notify(NIM_DELETE)
                win32().user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return win32().user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _menu(self):
        user32 = win32().user32
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, 1, f"Ouvrir {APP_NAME}")
        user32.AppendMenuW(menu, MF_STRING, 2, "Sauvegarder maintenant")
        user32.AppendMenuW(menu, MF_STRING, 4, "Stop : arrêter les copies")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, 3, "Quitter")
        user32.SetMenuDefaultItem(menu, 1, False)
        pos = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pos))
        user32.SetForegroundWindow(self.hwnd)  # sans quoi le menu reste ouvert quand on clique ailleurs
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, pos.x, pos.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

    def _notify(self, action, tip=None, balloon=None):
        with self._lock:
            nid = self._nid
            if nid is None:
                return False
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            if tip is not None:
                nid.szTip = tip[:127]
            if balloon:
                nid.uFlags |= NIF_INFO
                nid.szInfoTitle, nid.szInfo, nid.dwInfoFlags = balloon[0][:63], balloon[1][:255], balloon[2]
            return bool(win32().shell32.Shell_NotifyIconW(action, ctypes.byref(nid)))

    # -- appelé par la fenêtre
    def set_tip(self, text):
        self._notify(NIM_MODIFY, tip=text)

    def balloon(self, title, text, warning=False):
        self._notify(NIM_MODIFY, balloon=(title, text, NIIF_WARNING if warning else NIIF_INFO))

    def stop(self):
        if self.ok and self.hwnd:
            self.ok = False
            win32().user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            self.join(2)


# ------------------------------------------------------------------ GUI ----

# Les couleurs de l'icône. L'ambre est la seule couleur vive : l'action et l'alerte.
NIGHT = "#0E1A2B"   # fond
PANEL = "#17283F"   # champs et boutons discrets
RULE = "#2B4160"    # filets, survol
EDGE = "#5F7189"    # contour d'une tuile au repos
PAPER = "#F4F0E6"   # blanc cassé : le texte, et la tuile en cours de copie
MUTED = "#8FA0B5"   # texte secondaire
AMBER = "#FFC300"
INK = "#091320"     # fond du journal
LOG_LINES = 1500    # l'application reste ouverte des jours : le journal à l'écran est borné


def window_icon():
    """Icône des fenêtres : celle de l'exe une fois compilé, logo.ico à côté du script sinon."""
    if getattr(sys, "frozen", False):
        return sys.executable
    ico = Path(__file__).with_name("logo.ico")
    return str(ico) if ico.is_file() else None


def pick_font(*names):
    """La première police installée parmi `names` (Bahnschrift et Cascadia manquent aux vieux Windows)."""
    installed = set(tkfont.families())
    return next((n for n in names if n in installed), "Segoe UI")


class FlatButton(tk.Button):
    """Bouton plat. "primary" (ambre) est réservé à l'action ; "dark" se pose sur un fond ambre."""
    KINDS = {"primary": (AMBER, NIGHT, "#FFD75E"), "quiet": (PANEL, PAPER, RULE),
             "dark": (NIGHT, PAPER, PANEL)}

    def __init__(self, master, text, command, kind="quiet", **kw):
        self.rest, fg, self.hover = self.KINDS[kind]
        super().__init__(master, text=text, command=command, bg=self.rest, fg=fg,
                         activebackground=self.hover, activeforeground=fg,
                         disabledforeground=MUTED, relief="flat", bd=0, cursor="hand2",
                         highlightthickness=1, highlightbackground=master["bg"],
                         highlightcolor=PAPER, **kw)
        self.bind("<Enter>", lambda _: self.enabled() and self.configure(bg=self.hover))
        self.bind("<Leave>", lambda _: self.enabled() and self.configure(bg=self.rest))

    def enabled(self):
        return str(self["state"]) != "disabled"

    def enable(self, on, text=None):
        self.configure(state="normal" if on else "disabled", bg=self.rest if on else PANEL,
                       cursor="hand2" if on else "arrow")
        if text:
            self.configure(text=text)


class Check(tk.Frame):
    """Case à cocher dessinée : celle de Tk en thème sombre affiche une croix pour « oui »."""

    def __init__(self, master, text, variable, command, app):
        super().__init__(master, bg=NIGHT, takefocus=1, cursor="hand2", highlightthickness=1,
                         highlightbackground=NIGHT, highlightcolor=PAPER)
        self.variable, self.command, self.size = variable, command, app.px(16)
        self.box = tk.Canvas(self, width=self.size, height=self.size, bg=NIGHT,
                             highlightthickness=0)
        self.box.pack(side="left", padx=(app.px(2), app.px(8)), pady=app.px(2))
        label = tk.Label(self, text=text, bg=NIGHT, fg=PAPER, font=app.body(10))
        label.pack(side="left", padx=(0, app.px(2)))
        for widget in (self, self.box, label):
            widget.bind("<Button-1>", self.toggle)
        self.bind("<space>", self.toggle)
        variable.trace_add("write", lambda *_: self.draw())
        self.draw()

    def toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        self.command()

    def draw(self):
        s, box = self.size, self.box
        box.delete("all")
        if self.variable.get():
            box.create_rectangle(0, 0, s, s, fill=AMBER, outline=AMBER)
            box.create_line(s * .22, s * .52, s * .43, s * .72, s * .79, s * .29, fill=NIGHT,
                            width=max(2, round(s / 8)), capstyle="round", joinstyle="round")
        else:
            box.create_rectangle(1, 1, s - 2, s - 2, outline=EDGE)


class Tile(tk.Frame):
    """Une des trois copies. En contour au repos, pleine pendant sa copie, ambre quand elle
    demande qu'on s'en occupe : ce sont les trois cartes de l'icône."""
    LOOKS = {  # fond, texte, texte secondaire, contour
        "rest": (NIGHT, PAPER, MUTED, EDGE),
        "busy": (PAPER, NIGHT, "#4E5B6E", PAPER),
        "alert": (AMBER, NIGHT, "#4D3B00", AMBER),
    }

    def __init__(self, master, title, app):
        super().__init__(master, bd=0, highlightthickness=app.px(2))
        pad = app.px(14)
        self.lbl_title = tk.Label(self, text=title, font=(app.ff_display, 17), anchor="w")
        self.lbl_state = tk.Label(self, font=app.bold(10), anchor="w", width=1)
        self.lbl_detail = tk.Label(self, font=app.body(9), anchor="w", width=1)
        self.lbl_dest = tk.Label(self, font=(app.ff_mono, 8), anchor="w", width=1)
        self.lbl_title.pack(fill="x", padx=pad, pady=(app.px(10), 0))
        self.lbl_state.pack(fill="x", padx=pad, pady=(app.px(4), 0))
        self.lbl_detail.pack(fill="x", padx=pad)
        self.lbl_dest.pack(fill="x", padx=pad, pady=(app.px(10), app.px(12)))
        self.btn = FlatButton(self, "", None, kind="dark", font=app.bold(9), padx=app.px(10),
                              pady=app.px(3))
        self.btn_pack = {"anchor": "w", "padx": pad, "pady": (0, app.px(12))}
        self.show("rest", "En attente")

    def show(self, look, state, detail="", action=None):
        """`action` = (libellé, fonction) : un bouton dans la tuile, pour ce qu'elle réclame."""
        bg, fg, soft, edge = self.LOOKS[look]
        self.configure(bg=bg, highlightbackground=edge, highlightcolor=edge)
        self.lbl_title.configure(bg=bg, fg=fg)
        self.lbl_state.configure(bg=bg, fg=fg, text=state)
        self.lbl_detail.configure(bg=bg, fg=soft, text=detail)
        self.lbl_dest.configure(bg=bg, fg=soft)
        if action:
            self.btn.configure(text=action[0], command=action[1], highlightbackground=bg)
            self.btn.pack(**self.btn_pack)
        else:
            self.btn.pack_forget()

    def set_dest(self, name):
        self.lbl_dest.configure(text=f"→ {name}" if name else "")


class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("src_hd", "Dossier HD (depot)"),
        ("src_redim", "Dossier REDIM"),
        ("src_data", "Dossier DATA"),
        ("dest", "Dossier de tri (disque dur)"),
    ]

    def __init__(self, master, cfg, on_save):
        super().__init__(master, bg=NIGHT)
        self.withdraw()  # cachée le temps de se construire et de se placer
        self.title("Réglages des chemins")
        self.resizable(False, False)
        self.cfg, self.on_save = dict(cfg), on_save
        self.vars = {}
        px, side = master.px, master.px(22)
        tk.Label(self, text="Réglés une fois pour ce PC.", bg=NIGHT, fg=MUTED,
                 font=master.body(9)).pack(anchor="w", padx=side, pady=(px(18), 0))
        for key, label in self.FIELDS:
            tk.Label(self, text=label, bg=NIGHT, fg=PAPER, font=master.bold(10)).pack(
                anchor="w", padx=side, pady=(px(14), px(4)))
            row = tk.Frame(self, bg=NIGHT)
            row.pack(fill="x", padx=side)
            v = tk.StringVar(value=self.cfg[key])
            self.vars[key] = v
            field = tk.Frame(row, bg=PANEL, highlightthickness=1, highlightbackground=RULE)
            field.pack(side="left", fill="x", expand=True)
            entry = tk.Entry(field, textvariable=v, width=58, bg=PANEL, fg=PAPER, bd=0,
                             insertbackground=PAPER, relief="flat", highlightthickness=0,
                             font=master.body(10))
            entry.pack(fill="x", padx=px(8), ipady=px(5))  # le cadre donne sa marge au texte
            entry.bind("<FocusIn>", lambda _, f=field: f.configure(highlightbackground=PAPER))
            entry.bind("<FocusOut>", lambda _, f=field: f.configure(highlightbackground=RULE))
            FlatButton(row, "…", lambda k=key: self.browse(k), font=master.bold(10),
                       padx=px(10), pady=px(2)).pack(side="left", padx=(px(8), 0))
        bottom = tk.Frame(self, bg=NIGHT)
        bottom.pack(fill="x", padx=side, pady=px(20))
        self.startup_var = None
        if getattr(sys, "frozen", False):  # un script n'a pas d'exe à inscrire
            self.startup_var = tk.BooleanVar(value=startup_enabled())
            Check(bottom, "Lancer au démarrage de Windows", self.startup_var, lambda: None,
                  master).pack(side="left")
        FlatButton(bottom, "Enregistrer", self.save, kind="primary", font=master.bold(10),
                   padx=px(20), pady=px(7)).pack(side="right")
        self.transient(master)
        self.update_idletasks()
        # Au milieu de la fenêtre principale (plus grande que celle-ci : elle n'en sort pas).
        # La position se donne AVANT de toucher à la transparence : réglée après, Windows
        # l'ignore et ouvre la fenêtre dans le coin de l'écran.
        x = master.winfo_x() + max(0, (master.winfo_width() - self.winfo_reqwidth()) // 2)
        y = master.winfo_y() + max(0, (master.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.attributes("-alpha", 0.0)  # invisible le temps de teinter la barre de titre
        self.deiconify()
        self.update()
        dark_title_bar(self)
        self.attributes("-alpha", 1.0)
        self.grab_set()

    def browse(self, key):
        d = filedialog.askdirectory(initialdir=self.vars[key].get() or "C:\\", parent=self)
        if d:
            self.vars[key].set(os.path.normpath(d))

    def save(self):
        for k, v in self.vars.items():
            self.cfg[k] = v.get().strip()
        save_config(self.cfg)
        if self.startup_var is not None and self.startup_var.get() != startup_enabled():
            try:
                set_startup(self.startup_var.get())
            except OSError as exc:
                messagebox.showwarning(APP_NAME, "Le lancement au démarrage de Windows n'a pas "
                                                 f"pu être réglé :\n{exc}", parent=self)
        self.on_save(self.cfg)
        self.destroy()


class App(tk.Tk):
    def __init__(self, after_update=False, at_startup=False):
        super().__init__()
        self.attributes("-alpha", 0.0)  # invisible le temps de se dessiner : pas d'éclair blanc
        self.title(f"{APP_NAME} v{VERSION}")
        icon = window_icon()
        if icon:
            try:
                self.iconbitmap(default=icon)  # vaut aussi pour la fenêtre des réglages
            except tk.TclError:
                pass
        self.k = self.winfo_fpixels("1i") / 96  # échelle de l'écran (1 à 100 %, 1,5 à 150 %)
        self.minsize(self.px(640), self.px(560))
        self.geometry(f"{self.px(760)}x{self.px(660)}")
        self.cfg = load_config()
        self.busy = False
        # lancé par Windows, on laisse une minute aux disques et au réseau pour arriver
        self.next_run = time.time() + (60 if at_startup else 5) if self.cfg["auto"] else None
        self.alertes_vues = set()  # évite de répéter la même alerte toutes les 10 min
        self.followed = None       # le dernier event de Kadra pris en compte (cf. event_to_follow)
        self.pending_update = None
        self.installing = False
        self.checking = False
        self.update_error_seen = False
        self.told_hidden = False

        self.tray_events = queue.Queue()
        self.tray = None
        if sys.platform == "win32":
            tray = Tray(self.tray_events, icon)
            tray.start()
            tray.ready.wait(3)
            if tray.ok:
                self.tray = tray

        self.build()
        self.refresh_events()
        # lancé par Windows : pas de fenêtre, seulement l'icône (sauf sur un PC pas encore réglé)
        discreet = bool(at_startup and self.tray and config_is_set())
        if discreet:
            self.withdraw()
        self.update()
        dark_title_bar(self)
        self.attributes("-alpha", 1.0)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.write_log(f"Mise à jour installée : version {VERSION}." if after_update
                       else f"{APP_NAME} {VERSION} démarré.")
        if discreet:
            self.told_hidden = True
            self.tray.balloon(f"{APP_NAME} est lancé",
                              f"Copie automatique toutes les {self.cfg['intervalle_min']} min."
                              if self.cfg["auto"] else
                              "Copie automatique coupée : ouvre la fenêtre pour la remettre.")
        if not config_is_set():
            self.after(200, self.open_settings)
        self.after(1000, self.tick)
        self.after(200, self.poll_tray)
        if getattr(sys, "frozen", False):
            self.after(3000, self.look_for_update)
            self.after(30000, cleanup_old)
        else:
            self.version_lbl.configure(text=f"v{VERSION} · script", cursor="arrow")

    # -- construction
    def px(self, n):
        return max(1, round(n * self.k))

    def body(self, size):
        return ("Segoe UI", size)

    def bold(self, size):
        return ("Segoe UI Semibold", size)

    def build(self):
        px, side = self.px, self.px(22)
        self.configure(bg=NIGHT)
        self.ff_display = pick_font("Bahnschrift SemiBold Condensed", "Bahnschrift SemiBold",
                                    "Segoe UI Semibold")
        self.ff_mono = pick_font("Cascadia Mono", "Consolas")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=PAPER,
                        arrowcolor=PAPER, bordercolor=RULE, lightcolor=PANEL, darkcolor=PANEL,
                        selectbackground=PANEL, selectforeground=PAPER, padding=px(6),
                        arrowsize=px(14))
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)],
                  foreground=[("readonly", PAPER)], selectbackground=[("readonly", PANEL)],
                  selectforeground=[("readonly", PAPER)], bordercolor=[("focus", PAPER)],
                  background=[("active", RULE), ("readonly", PANEL)])
        style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=INK,
                        bordercolor=INK, arrowcolor=MUTED, lightcolor=PANEL, darkcolor=PANEL,
                        gripcount=0)
        style.map("Vertical.TScrollbar", background=[("active", RULE)])
        for option, value in (("background", PANEL), ("foreground", PAPER),
                              ("selectBackground", AMBER), ("selectForeground", NIGHT),
                              ("font", self.body(10))):
            self.option_add(f"*TCombobox*Listbox.{option}", value)

        # bandeau de mise à jour, posé tout en haut quand il y en a une
        self.banner = tk.Frame(self, bg=AMBER)
        self.banner_text = tk.Label(self.banner, bg=AMBER, fg=NIGHT, font=self.bold(10))
        self.banner_text.pack(side="left", padx=(side, px(12)), pady=px(9))
        self.banner_btn = FlatButton(self.banner, "Mettre à jour", self.install_update,
                                     kind="dark", font=self.bold(9), padx=px(14), pady=px(4))
        self.banner_btn.pack(side="right", padx=side, pady=px(7))

        self.header = tk.Frame(self, bg=NIGHT)
        self.header.pack(fill="x", padx=side, pady=(px(18), 0))
        tk.Label(self.header, text="PRISMA SAFE", bg=NIGHT, fg=PAPER,
                 font=(self.ff_display, 22)).pack(side="left")
        FlatButton(self.header, "Réglages", self.open_settings, font=self.body(10),
                   padx=px(14), pady=px(5)).pack(side="right")
        self.dest_label = tk.Label(self, bg=NIGHT, fg=MUTED, font=self.body(9), anchor="w")
        self.dest_label.pack(fill="x", padx=side)

        top = tk.Frame(self, bg=NIGHT)
        top.pack(fill="x", padx=side, pady=(px(18), 0))
        tk.Label(top, text="Event", bg=NIGHT, fg=MUTED, font=self.body(10)).pack(side="left")
        self.event_var = tk.StringVar()
        self.event_cb = ttk.Combobox(top, textvariable=self.event_var, state="readonly",
                                     font=self.body(10))
        self.event_cb.pack(side="left", fill="x", expand=True, padx=(px(12), px(8)))
        self.event_cb.bind("<<ComboboxSelected>>", self.on_event_chosen)
        FlatButton(top, "↻", self.refresh_events, font=self.bold(11), padx=px(10),
                   pady=px(1)).pack(side="left")

        tiles = tk.Frame(self, bg=NIGHT)
        tiles.pack(fill="x", padx=side, pady=(px(14), 0))
        self.tiles = {}
        for i, title in enumerate(("HD", "REDIM", "DATA")):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            self.tiles[title] = Tile(tiles, title, self)
            self.tiles[title].grid(row=0, column=i, sticky="nsew", padx=(px(10) if i else 0, 0))

        btns = tk.Frame(self, bg=NIGHT)
        btns.pack(fill="x", padx=side, pady=(px(18), 0))
        self.run_btn = FlatButton(btns, "Sauvegarder maintenant", self.start_backup,
                                  kind="primary", font=self.bold(11), padx=px(22), pady=px(8))
        self.run_btn.pack(side="left")
        FlatButton(btns, "Stop", self.stop_copies, font=self.bold(11), padx=px(18),
                   pady=px(8)).pack(side="left", padx=(px(8), 0))
        self.status = tk.Label(btns, text="", bg=NIGHT, fg=PAPER, font=self.body(10))
        self.status.pack(side="left", padx=px(16))

        opts = tk.Frame(self, bg=NIGHT)
        opts.pack(fill="x", padx=side, pady=(px(12), 0))
        self.auto_var = tk.BooleanVar(value=self.cfg["auto"])
        self.interval_var = tk.IntVar(value=self.cfg["intervalle_min"])
        self.miroir_var = tk.BooleanVar(value=self.cfg["miroir"])
        Check(opts, "Automatique toutes les", self.auto_var, self.on_auto_toggle,
              self).pack(side="left")
        tk.Spinbox(opts, from_=1, to=120, width=4, textvariable=self.interval_var, bg=PANEL,
                   fg=PAPER, buttonbackground=PANEL, insertbackground=PAPER, relief="flat", bd=0,
                   justify="center", font=self.body(10), highlightthickness=1,
                   highlightbackground=RULE, highlightcolor=PAPER).pack(
            side="left", padx=(px(4), px(8)), ipady=px(2))
        tk.Label(opts, text="min", bg=NIGHT, fg=PAPER, font=self.body(10)).pack(side="left")
        Check(opts, "Miroir (supprime aussi sur le disque)", self.miroir_var,
              self.persist_options, self).pack(side="left", padx=(px(26), 0))

        foot = tk.Frame(self, bg=NIGHT)
        foot.pack(side="bottom", fill="x", padx=side, pady=(px(10), px(14)))
        note = ("Fermer la fenêtre ne l'arrête pas : elle reste près de l'horloge."
                if self.tray else "Fermer la fenêtre la réduit dans la barre des tâches.")
        FlatButton(foot, "Quitter", self.quit_app, font=self.body(9), padx=px(12),
                   pady=px(3)).pack(side="right")
        # où en est la mise à jour ; un clic revérifie tout de suite
        self.version_lbl = tk.Label(foot, text=f"v{VERSION}", bg=NIGHT, fg=MUTED,
                                    font=self.body(9), cursor="hand2")
        self.version_lbl.pack(side="right", padx=px(14))
        self.version_lbl.bind("<Button-1>", self.check_update_now)
        tk.Label(foot, text=note, bg=NIGHT, fg=MUTED, font=self.body(9), anchor="w",
                 width=1).pack(side="left", fill="x", expand=True)

        box = tk.Frame(self, bg=INK)
        box.pack(fill="both", expand=True, padx=side, pady=(px(16), 0))
        self.log = tk.Text(box, height=8, state="disabled", font=(self.ff_mono, 9), bg=INK,
                           fg=PAPER, bd=0, relief="flat", highlightthickness=0, wrap="word",
                           padx=px(12), pady=px(10), spacing1=px(2), cursor="arrow")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log.yview)

        def on_scroll(first, last):  # l'ascenseur n'apparaît que s'il y a de quoi défiler
            scroll.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                scroll.pack_forget()
            elif not scroll.winfo_ismapped():
                scroll.pack(side="right", fill="y", before=self.log)

        self.log.configure(yscrollcommand=on_scroll)
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("time", foreground=MUTED)
        self.log.tag_configure("alert", foreground=AMBER)

    # -- helpers
    def write_log(self, msg):
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        self.log.configure(state="normal")
        self.log.insert("end", line[:10], "time", msg + "\n", "alert" if "⚠" in msg else "")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > LOG_LINES:
            self.log.delete("1.0", f"{lines - LOG_LINES}.0")
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
            self.followed = hint
        elif events and self.event_var.get() not in events:
            self.event_var.set(events[0])
        self.show_destinations()

    def adopt_event(self, event, events):
        """Kadra est passé sur un autre event : les copies le suivent, et on le dit."""
        self.event_cb["values"] = events
        self.event_var.set(event)
        self.on_event_chosen()
        self.write_log(f"Kadra est passé sur {event} : les copies le suivent.")
        if self.tray and self.hidden():
            self.tray.balloon("Nouvel event", f"Les copies suivent Kadra : {event}.")

    def on_event_chosen(self, _event=None):
        self.event_cb.selection_clear()
        for tile in self.tiles.values():  # ce qu'elles disaient valait pour l'event d'avant
            tile.show("rest", "En attente")
        self.show_destinations()

    def show_destinations(self):
        """Où va chaque copie : le dossier de tri sous le titre, le nom du dossier dans la tuile."""
        self.dest_label.configure(text=f"Les photos de l'event, copiées vers {self.cfg['dest']}")
        event = self.event_var.get()
        for label, _, dst in build_jobs(self.cfg, event):
            self.tiles[label].set_dest(dst.name if event else "")

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
            self.start_backup(manual=False)
        if self.auto_var.get() and self.next_run and not self.busy:
            rest = int(self.next_run - time.time())
            if rest > 0:
                self.status.configure(text=f"Prochaine copie dans {rest // 60:02d}:{rest % 60:02d}")
        self.after(1000, self.tick)

    # -- fenêtre rangée près de l'horloge
    def hidden(self):
        return self.wm_state() == "withdrawn"

    def show_window(self):
        self.deiconify()
        self.update_idletasks()
        dark_title_bar(self)  # lancée par Windows, la fenêtre se montre ici pour la première fois
        self.lift()
        self.attributes("-topmost", True)  # passe devant, sans y rester
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def on_close(self):
        """La croix range la fenêtre ; les copies continuent."""
        if not self.tray:
            self.iconify()
            return
        self.withdraw()
        if not self.told_hidden:
            self.told_hidden = True
            self.tray.balloon(f"{APP_NAME} reste ouvert",
                              "Les copies continuent. Clique sur cette icône pour rouvrir la "
                              "fenêtre ; clic droit pour quitter.")

    def poll_tray(self):
        try:
            while True:
                ask = self.tray_events.get_nowait()
                if ask == "open":
                    self.show_window()
                elif ask == "backup":
                    self.start_backup()
                elif ask == "stop":
                    self.stop_copies()
                elif ask == "quit" and self.quit_app():
                    return
        except queue.Empty:
            pass
        self.after(200, self.poll_tray)

    def quit_app(self):
        """Arrête vraiment l'application. Rend False si on y renonce (copie en cours)."""
        if self.busy:
            self.show_window()
            if not messagebox.askyesno(APP_NAME, "Une copie est en cours. Quitter quand même ?"):
                return False
        self.close_down()
        return True

    def close_down(self):
        if self.tray:
            self.tray.stop()
        self.destroy()

    def tell_tray(self, text):
        if self.tray:
            self.tray.set_tip(f"{APP_NAME}\n{self.event_var.get()}\n{text}".replace("\n\n", "\n"))

    # -- sauvegarde
    def start_backup(self, manual=True):
        event = self.event_var.get()
        if not event and not manual:
            self.refresh_events()  # un event est peut-être apparu depuis le lancement
            event = self.event_var.get()
        if not event:
            if manual:
                if self.hidden():
                    self.show_window()
                messagebox.showwarning(APP_NAME, "Aucun event sélectionné.")
            else:  # en automatique : pas de message à répétition, on réessaie au tour suivant
                self.status.configure(text="Aucun event à sauvegarder : vérifie les réglages")
                self.next_run = time.time() + 60 * self.cfg["intervalle_min"]
            return
        if self.busy:
            return
        self.persist_options()
        self.busy = True
        _stop.clear()
        self.run_btn.enable(False)
        self.status.configure(text="Copie en cours…", fg=PAPER)
        self.tell_tray("Copie en cours…")
        threading.Thread(target=self.backup_worker,
                         args=(event, self.miroir_var.get(), not manual), daemon=True).start()

    def backup_worker(self, event, miroir, follow=False):
        if follow:  # en automatique : si Kadra a changé d'event, c'est le sien qu'on copie
            try:
                events = list_events(self.cfg)
                new = event_to_follow(current_event_hint(self.cfg), self.followed, events)
            except OSError:
                new = None
            if new:
                self.followed = new
                if new != event:
                    event = new
                    self.after(0, self.adopt_event, new, events)
        dest_root = Path(self.cfg["dest"])
        errors = 0
        if not dest_root.is_dir():
            self.after(0, self.write_log, f"⚠ Disque de destination introuvable : {dest_root}")
            for tile in self.tiles.values():
                self.after(0, tile.show, "alert", "Disque introuvable", str(dest_root))
            errors = 3
        else:
            for label, src, dst in build_jobs(self.cfg, event):
                tile = self.tiles[label]
                if _stop.is_set():
                    break
                if not src.is_dir():
                    self.after(0, self.write_log, f"{label:5}  source absente, ignorée ({src})")
                    self.after(0, tile.show, "rest", "Source absente", "dossier introuvable sur ce PC")
                    continue
                self.after(0, tile.show, "busy", "Copie en cours…")
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
                            self.after(0, self.warn_blocked, msg, label)
                try:
                    code, copied, _ = run_robocopy(src, dst, miroir_job)
                except FileNotFoundError:
                    self.after(0, self.write_log, "⚠ robocopy introuvable (Windows requis).")
                    self.after(0, tile.show, "alert", "robocopy introuvable", "Windows requis")
                    errors += 1
                    break
                when = datetime.now().strftime("%H:%M")
                if _stop.is_set():
                    self.after(0, self.write_log, f"{label:5}  copie arrêtée en cours de route (Stop)")
                    self.after(0, tile.show, "alert", "Arrêtée", f"copie interrompue · {when}")
                    break
                if code <= ROBOCOPY_OK_MAX:
                    self.after(0, self.write_log, f"{label:5}  OK, {copied} fichier(s) copié(s) → {dst.name}")
                    news = ("rien de nouveau" if not copied else
                            "1 fichier copié" if copied == 1 else f"{copied} fichiers copiés")
                    if miroir and not miroir_job:
                        self.after(0, tile.show, "alert", "Suppression bloquée",
                                   f"{news}, rien d'effacé · {when}",
                                   ("Forcer la suppression…", lambda l=label: self.ask_force(l)))
                    else:
                        self.after(0, tile.show, "rest", "À jour", f"{news} · {when}")
                else:
                    errors += 1
                    self.after(0, self.write_log, f"⚠ {label:5}  ERREUR robocopy (code {code}) → {dst}")
                    self.after(0, tile.show, "alert", "Erreur de copie",
                               f"robocopy, code {code} · {when}")
        self.after(0, self.backup_done, errors)

    def warn_blocked(self, msg, label):
        if self.hidden():
            self.show_window()  # une alerte ne reste pas cachée près de l'horloge
        messagebox.showwarning(
            APP_NAME, f"{msg}\n\nVérifie la source avant toute chose.\n"
                      f"Si la suppression est voulue : « Forcer la suppression… », "
                      f"sur la tuile {label}.")

    # -- la suppression que le garde-fou a bloquée, validée à la main
    def ask_force(self, label):
        """Bouton d'une tuile bloquée. On recompte d'abord : la question porte sur ce qui
        serait supprimé maintenant, pas sur ce qu'on avait vu dix minutes plus tôt."""
        if self.busy:
            messagebox.showinfo(APP_NAME, "Une copie est en cours. Réessaie quand elle est finie.")
            return
        event = self.event_var.get()
        job = next((j for j in build_jobs(self.cfg, event) if j[0] == label), None)
        if not event or not job:
            return
        self.busy = True  # rien ne démarre pendant le comptage ni pendant la question
        _stop.clear()
        self.run_btn.enable(False)
        self.status.configure(text="Comptage…", fg=PAPER)
        threading.Thread(target=self.count_for_force, args=(event, *job), daemon=True).start()

    def count_for_force(self, event, label, src, dst):
        try:
            found = src.is_dir()
            nb_sup, nb_tot = check_suppression(src, dst)[1:] if found else (0, 0)
        except OSError:
            found, nb_sup, nb_tot = False, 0, 0
        self.after(0, self.confirm_force, event, label, src, dst, found, nb_sup, nb_tot)

    def confirm_force(self, event, label, src, dst, found, nb_sup, nb_tot):
        what, all_of_them = (("fichiers sauvegardés", "TOUS") if label == "DATA"
                             else ("photos sauvegardées", "TOUTES"))
        if not found:
            messagebox.showwarning(APP_NAME, f"{label} : la source est introuvable.\n{src}\n\n"
                                             "Rien n'est supprimé.")
        elif not nb_sup:
            messagebox.showinfo(APP_NAME, f"{label} : plus rien à supprimer, le disque "
                                          "correspond à la source.")
        elif messagebox.askyesno(
                APP_NAME,
                f"{label} : {nb_sup} des {nb_tot} {what} ne sont plus dans la source"
                f"{' : ' + all_of_them if nb_sup == nb_tot else ''}.\n\n"
                f"Source : {src}\nDisque : {dst}\n\n"
                "Supprimer du disque tout ce qui n'est plus dans la source ?\n"
                "C'est définitif : rien ne passe par la corbeille.",
                icon="warning", default="no"):
            self.write_log(f"⚠ {label} : suppression forcée à la main "
                           f"({nb_sup} des {nb_tot} {what}).")
            self.alertes_vues.discard((event, label))  # un nouveau blocage redonnera l'alerte
            self.status.configure(text="Suppression en cours…")
            threading.Thread(target=self.force_worker, args=(label, src, dst),
                             daemon=True).start()
            return
        self.busy = False  # on a renoncé : tout reste comme c'était
        self.run_btn.enable(True)
        self.status.configure(text="")

    def force_worker(self, label, src, dst):
        """Le miroir de ce seul dossier, garde-fou levé : c'est ce qu'on vient de valider."""
        tile, errors = self.tiles[label], 0
        self.after(0, tile.show, "busy", "Suppression en cours…")
        try:
            code = run_robocopy(src, dst, True)[0]
        except FileNotFoundError:
            code = None
        when = datetime.now().strftime("%H:%M")
        if _stop.is_set():
            self.after(0, self.write_log, f"{label:5}  suppression arrêtée en cours de route (Stop)")
            self.after(0, tile.show, "alert", "Arrêtée", f"suppression interrompue · {when}")
        elif code is not None and code <= ROBOCOPY_OK_MAX:
            self.after(0, self.write_log, f"{label:5}  OK, le disque correspond à la source → {dst.name}")
            self.after(0, tile.show, "rest", "À jour", f"suppression forcée · {when}")
        else:
            errors = 1
            self.after(0, self.write_log, f"⚠ {label:5}  ERREUR robocopy (code {code}) → {dst}")
            self.after(0, tile.show, "alert", "Erreur de copie", f"robocopy, code {code} · {when}")
        self.after(0, self.backup_done, errors)

    def stop_copies(self):
        """Stop : la copie en cours s'arrête net, et plus rien ne part tout seul tant qu'on
        n'a pas recoché « Automatique ». « Sauvegarder maintenant » reste possible."""
        was_busy = self.busy
        stop_robocopy()
        self.auto_var.set(False)
        self.persist_options()
        self.next_run = None
        self.write_log("⚠ Stop : " + ("copie en cours arrêtée, " if was_busy else "")
                       + "copie automatique coupée.")
        if not was_busy:
            self.show_stopped()

    def show_stopped(self):
        self.status.configure(text="Copies arrêtées. Coche « Automatique » pour reprendre.",
                              fg=AMBER)
        self.tell_tray("Copies arrêtées")

    def backup_done(self, errors):
        self.busy = False
        self.run_btn.enable(True)
        if _stop.is_set():  # arrêtée par Stop : ni « OK », ni erreur, ni prochain tour
            self.show_stopped()
            return
        now = datetime.now().strftime("%H:%M")
        self.status.configure(fg=AMBER if errors else PAPER)
        if errors:
            self.status.configure(text=f"⚠ Erreur à {now}, voir le journal")
            self.bell()
            if self.tray and self.hidden():
                self.tray.balloon("Sauvegarde : à vérifier",
                                  f"Erreur à {now}. Clique pour ouvrir le journal.", warning=True)
        else:
            self.status.configure(text=f"Dernière copie OK à {now}")
        self.tell_tray(self.status.cget("text"))
        if self.auto_var.get():
            self.next_run = time.time() + 60 * self.cfg["intervalle_min"]

    # -- mise à jour
    def look_for_update(self):
        self.check_update_now()
        self.after(UPDATE_EVERY_H * 3600 * 1000, self.look_for_update)

    def check_update_now(self, _event=None):
        """Au lancement, toutes les 6 heures, et au clic sur la version en bas de fenêtre."""
        if self.checking or self.installing or not getattr(sys, "frozen", False):
            return
        self.checking = True
        self.version_lbl.configure(text=f"v{VERSION} · vérification…")
        threading.Thread(target=self.update_worker, daemon=True).start()

    def update_worker(self):
        try:
            found, error = check_update(), None
        except Exception as exc:
            found, error = None, exc
        self.after(0, self.update_checked, found, error)

    def update_checked(self, found, error):
        """Ce que la dernière vérification a donné, écrit en bas de fenêtre."""
        self.checking = False
        if error is not None:
            self.version_lbl.configure(text=f"v{VERSION} · mise à jour non vérifiée")
            if not self.update_error_seen:  # sans Internet sur un concours : une ligne, pas une par tour
                self.update_error_seen = True
                self.write_log(f"Recherche de mise à jour impossible pour l'instant ({error}).")
        elif found:
            self.version_lbl.configure(text=f"v{VERSION} · {found['version']} disponible")
            self.offer_update(found)
        else:
            self.version_lbl.configure(text=f"v{VERSION} · à jour")

    def offer_update(self, found):
        if self.installing or (self.pending_update or {}).get("version") == found["version"]:
            return
        self.pending_update = found
        self.banner_text.configure(
            text=f"La version {found['version']} est disponible (ici : {VERSION}).")
        self.banner_btn.enable(True, "Mettre à jour")
        self.banner.pack(fill="x", before=self.header)
        self.write_log(f"Mise à jour disponible : version {found['version']}.")
        if self.tray and self.hidden():
            self.tray.balloon("Mise à jour disponible",
                              f"Version {found['version']}. Clique pour ouvrir {APP_NAME}.")

    def install_update(self):
        if self.installing or not self.pending_update:
            return
        self.installing = True
        self.banner_btn.enable(False, "Téléchargement…")
        threading.Thread(target=self.download_worker, args=(self.pending_update,),
                         daemon=True).start()

    def download_worker(self, found):
        try:
            new = download_update(found)
        except Exception as exc:
            self.after(0, self.update_failed, exc)
            return
        self.after(0, self.finish_update, new)

    def finish_update(self, new):
        if self.busy:  # jamais pendant une copie
            self.banner_btn.enable(False, "Après la copie…")
            self.after(1000, self.finish_update, new)
            return
        try:
            apply_update(new)
        except OSError as exc:
            self.update_failed(exc)
            return
        self.write_log(f"Version {self.pending_update['version']} installée, redémarrage…")
        try:
            relaunch()
        except OSError as exc:
            self.banner.pack_forget()
            self.write_log(f"⚠ Nouvelle version en place, mais elle n'a pas pu se lancer ({exc}) : "
                           "quitte et relance l'application.")
            return
        self.close_down()

    def update_failed(self, exc):
        self.installing = False
        self.banner_btn.enable(True, "Réessayer")
        self.write_log(f"⚠ Mise à jour impossible : {exc}")


def main():
    after_update = ARG_AFTER_UPDATE in sys.argv[1:]
    at_startup = ARG_AT_STARTUP in sys.argv[1:]
    if sys.platform == "win32":
        enable_dpi_awareness()
        # après une mise à jour, l'ancienne version finit de se fermer : on lui laisse le temps
        deadline = time.time() + (20 if after_update else 0)
        while another_instance_running():
            if time.time() >= deadline:
                if not wake_first_instance():
                    win32().user32.MessageBoxW(None, f"{APP_NAME} est déjà ouvert.", APP_NAME, 0x40)
                return
            time.sleep(0.25)
        refresh_startup()
    cleanup_old()
    adopt_old_config()
    App(after_update, at_startup).mainloop()


if __name__ == "__main__":
    main()
