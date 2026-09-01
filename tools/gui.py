#!/usr/bin/env python3
"""Drag-and-drop GC/Bongos patcher: drop a disc image on the window, done.

Extracts the disc, checks sys/main.dol against the exact USA retail hook
instructions, adds an executable DOL section containing all six controller
hooks, branches to them directly, and rebuilds via wit (Wiimms ISO Tool).

The rebuilt image replaces the original *in place*, keeping its filename and
folder -- USB loaders key off the `/wbfs/<Title> [ID6]/` layout, so a renamed
file next to it can leave the loader unable to find the title. The untouched
original is kept alongside as `<name>.bak`.

The injector uses the auto-poll implementation confirmed by the hardware
investigation.  It accepts a clean RDKE01 retail DOL and deliberately refuses
other revisions or an already-patched image.
"""
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject_dol

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAVE_DND = True
except ImportError:                                    # fall back to click-to-browse
    HAVE_DND = False

EXPECTED_DISC_ID = 'RDKE01'


def find_wit():
    """A wit bundled with this app (PyInstaller build) wins over PATH."""
    name = 'wit.exe' if os.name == 'nt' else 'wit'
    if getattr(sys, 'frozen', False):
        for base in (getattr(sys, '_MEIPASS', None), os.path.dirname(sys.executable)):
            if base:
                bundled = os.path.join(base, name)
                if os.path.isfile(bundled):
                    return bundled
    return shutil.which('wit')


def find_file(root, name):
    for r, _, files in os.walk(root):
        if name in files:
            return os.path.join(r, name)
    return None


def read_disc_id(fst):
    boot = find_file(fst, 'boot.bin')
    if not boot:
        return None
    with open(boot, 'rb') as f:
        header = f.read(6)
    return header.decode('ascii', 'replace')


def run_patch(image_path, variant, log, done):
    try:
        wit = find_wit()
        if wit is None:
            raise RuntimeError('wit (Wiimms ISO Tool) not found: not bundled with this '
                                'build and not on PATH')

        fmt = '--iso' if image_path.lower().endswith('.iso') else '--wbfs'

        with tempfile.TemporaryDirectory(prefix='dkbb_patch_') as tmp:
            fst = os.path.join(tmp, 'fst')
            log('extracting %s...' % os.path.basename(image_path))
            r = subprocess.run([wit, 'extract', image_path, '--dest', fst,
                                 '--psel', 'data', '--overwrite', '-q'],
                                capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError('extract failed:\n' + (r.stderr or r.stdout))

            disc_id = read_disc_id(fst)
            if not disc_id:
                raise RuntimeError('could not read sys/boot.bin from the extracted disc')
            if disc_id != EXPECTED_DISC_ID:
                raise RuntimeError(
                    'disc id %s is not the supported target (%s, USA)' % (disc_id, EXPECTED_DISC_ID))
            log('disc: %s' % disc_id)

            dol_path = find_file(fst, 'main.dol')
            if not dol_path or os.path.basename(os.path.dirname(dol_path)) != 'sys':
                raise RuntimeError('could not find sys/main.dol in the extracted disc')

            if variant != 'autopoll':
                raise RuntimeError(
                    'the clean-disc injector currently supports the auto-poll variant only')
            patched_dol = dol_path + '.patched'
            section, hooks, size = inject_dol.inject(dol_path, patched_dol)
            os.replace(patched_dol, dol_path)
            log('  injected %d hooks into DOL text section %d (%d bytes)' %
                (len(hooks), section, size))

            staged = os.path.join(tmp, 'patched.img')
            log('rebuilding...')
            r = subprocess.run([wit, 'copy', fst, '--dest', staged, fmt, '--overwrite', '-q'],
                                capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError('rebuild failed:\n' + (r.stderr or r.stdout))

            # Only touch the user's file once the rebuild has actually succeeded.
            backup = image_path + '.bak'
            if os.path.exists(backup):
                log('  backup already exists, keeping it: %s' % os.path.basename(backup))
            else:
                shutil.copyfile(image_path, backup)
                log('  backed up original -> %s' % os.path.basename(backup))
            shutil.move(staged, image_path)
            log('done: patched in place, %s' % os.path.basename(image_path))
            done(True, image_path)
    except Exception as e:
        log('ERROR: %s' % e)
        done(False, str(e))


BASE = TkinterDnD.Tk if HAVE_DND else tk.Tk


class App(BASE):
    def __init__(self):
        super().__init__()
        self.title('DKBB GC/Bongos Patcher')
        self.geometry('560x460')
        self.msgq = queue.Queue()
        self.busy = False

        self.variant = tk.StringVar(value='autopoll')
        tk.Label(self, text='Clean-disc injector · auto-poll SI').pack(
            fill='x', padx=10, pady=(10, 0))

        hint = ('Drop a .wbfs or .iso here\n\n(or click to choose one)'
                if HAVE_DND else 'Click to choose a .wbfs or .iso')
        self.drop = tk.Label(self, text=hint, relief='ridge', bd=2,
                             padx=10, pady=30, cursor='hand2')
        self.drop.pack(fill='x', padx=10, pady=10)
        self.drop.bind('<Button-1>', lambda e: self.pick())

        if HAVE_DND:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind('<<Drop>>', self.on_drop)

        tk.Label(self, text='The original is kept alongside as <name>.bak',
                 fg='#666').pack()

        self.log = tk.Text(self, height=14, state='disabled', wrap='word')
        self.log.pack(fill='both', expand=True, padx=10, pady=10)

        self.after(100, self.poll_queue)

    def on_drop(self, event):
        paths = self.tk.splitlist(event.data)      # handles {braced paths with spaces}
        if paths:
            self.start(paths[0])

    def pick(self):
        if self.busy:
            return
        p = filedialog.askopenfilename(
            title='Select disc image',
            filetypes=[('Wii disc image', '*.wbfs *.iso'), ('All files', '*')])
        if p:
            self.start(p)

    def append_log(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def poll_queue(self):
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == 'log':
                    self.append_log(payload)
                elif kind == 'done':
                    ok, msg = payload
                    self.busy = False
                    self.drop.configure(state='normal')
                    if ok:
                        messagebox.showinfo('Done', 'Patched in place:\n%s' % msg)
                    else:
                        messagebox.showerror('Patch failed', msg)
        except queue.Empty:
            pass
        self.after(100, self.poll_queue)

    def start(self, image_path):
        if self.busy:
            return
        if not os.path.isfile(image_path):
            messagebox.showerror('Not a file', '%s is not a file.' % image_path)
            return

        self.busy = True
        self.drop.configure(state='disabled')
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

        threading.Thread(
            target=run_patch,
            args=(image_path, self.variant.get(),
                  lambda t: self.msgq.put(('log', t)),
                  lambda ok, m: self.msgq.put(('done', (ok, m)))),
            daemon=True,
        ).start()


if __name__ == '__main__':
    App().mainloop()
