#!/usr/bin/gjs

imports.gi.versions.Gtk = '4.0';
imports.gi.versions.Gdk = '4.0';

const {Gdk, Gio, GLib, Gtk} = imports.gi;

const POPUP_WIDTH = 580;
const POPUP_HEIGHT = 720;
const DATA_DIR = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'share', 'clipboard-history']);
const SETTINGS_PATH = GLib.build_filenamev([DATA_DIR, 'settings.ini']);
const HELPER = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'clipboard-history']);

const POSITION_LABELS = {
    center: 'Center',
    pointer: 'Mouse',
    window: 'Window',
};

function run(argv) {
    try {
        const [, stdout, stderr, status] = GLib.spawn_sync(
            null,
            argv,
            null,
            GLib.SpawnFlags.SEARCH_PATH,
            null
        );
        if (status !== 0) {
            const message = new TextDecoder().decode(stderr).trim();
            if (message)
                printerr(message);
            return '';
        }
        return new TextDecoder().decode(stdout).trim();
    } catch (error) {
        printerr(error.message);
        return '';
    }
}

function commandOutput(command, args) {
    return run([command, ...args]);
}

function loadPosition() {
    const keyFile = new GLib.KeyFile();
    try {
        keyFile.load_from_file(SETTINGS_PATH, GLib.KeyFileFlags.NONE);
        const value = keyFile.get_string('ui', 'position');
        if (Object.hasOwn(POSITION_LABELS, value))
            return value;
    } catch (_) {
    }
    return 'center';
}

function savePosition(value) {
    GLib.mkdir_with_parents(DATA_DIR, 0o755);
    const keyFile = new GLib.KeyFile();
    keyFile.set_string('ui', 'position', value);
    const [data] = keyFile.to_data();
    GLib.file_set_contents(SETTINGS_PATH, data);
}

function activeTarget() {
    const windowId = commandOutput('xdotool', ['getactivewindow']);
    const wmClass = windowId
        ? commandOutput('xdotool', ['getwindowclassname', windowId]).toLowerCase()
        : '';
    return {windowId, wmClass};
}

function displaySize() {
    const text = commandOutput('xdotool', ['getdisplaygeometry']);
    const parts = text.split(/\s+/).map(part => Number.parseInt(part, 10));
    if (parts.length >= 2 && parts.every(Number.isFinite))
        return [parts[0], parts[1]];
    return [1600, 900];
}

function pointerPosition() {
    const text = commandOutput('xdotool', ['getmouselocation', '--shell']);
    const x = /X=(\d+)/.exec(text)?.[1];
    const y = /Y=(\d+)/.exec(text)?.[1];
    return [Number.parseInt(x ?? '0', 10), Number.parseInt(y ?? '0', 10)];
}

function activeWindowRect(windowId) {
    if (!windowId)
        return null;
    const text = commandOutput('xdotool', ['getwindowgeometry', '--shell', windowId]);
    const x = /X=(-?\d+)/.exec(text)?.[1];
    const y = /Y=(-?\d+)/.exec(text)?.[1];
    const width = /WIDTH=(\d+)/.exec(text)?.[1];
    const height = /HEIGHT=(\d+)/.exec(text)?.[1];
    if ([x, y, width, height].some(value => value === undefined))
        return null;
    return {
        x: Number.parseInt(x, 10),
        y: Number.parseInt(y, 10),
        width: Number.parseInt(width, 10),
        height: Number.parseInt(height, 10),
    };
}

function isTerminal(wmClass) {
    return wmClass.includes('terminal') ||
        ['kgx', 'console', 'tilix', 'alacritty', 'kitty', 'xterm', 'konsole'].includes(wmClass);
}

class ClipboardHistoryPicker {
    constructor(application) {
        this.application = application;
        this.entries = [];
        this.visibleEntries = [];
        this.selected = 0;
        this.target = activeTarget();
        this.position = loadPosition();

        this.window = new Gtk.ApplicationWindow({
            application,
            title: 'Clipboard History',
            default_width: POPUP_WIDTH,
            default_height: POPUP_HEIGHT,
            resizable: false,
        });

        this.buildUi();
        this.loadEntries();
        this.positionWindow();
    }

    buildUi() {
        const root = new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL,
            spacing: 8,
            margin_top: 10,
            margin_bottom: 10,
            margin_start: 10,
            margin_end: 10,
        });

        this.search = new Gtk.SearchEntry({hexpand: true});
        root.append(this.search);
        this.search.connect('search-changed', () => {
            this.selected = 0;
            this.renderRows();
        });
        this.search.connect('activate', () => this.pasteSelected());

        const toolbar = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            spacing: 8,
        });
        toolbar.append(new Gtk.Label({label: 'Open at'}));

        this.positionCombo = new Gtk.ComboBoxText();
        for (const [value, label] of Object.entries(POSITION_LABELS))
            this.positionCombo.append(value, label);
        this.positionCombo.set_active_id(this.position);
        this.positionCombo.connect('changed', () => {
            this.position = this.positionCombo.get_active_id() ?? 'center';
            savePosition(this.position);
            this.positionWindow();
        });
        toolbar.append(this.positionCombo);

        const clear = new Gtk.Button({icon_name: 'edit-clear-all-symbolic'});
        clear.set_tooltip_text('Clear unpinned history');
        clear.connect('clicked', () => {
            run([HELPER, '--clear']);
            this.loadEntries();
        });
        toolbar.append(clear);
        root.append(toolbar);

        const scrolled = new Gtk.ScrolledWindow({
            hscrollbar_policy: Gtk.PolicyType.NEVER,
            vscrollbar_policy: Gtk.PolicyType.AUTOMATIC,
            vexpand: true,
        });
        this.list = new Gtk.ListBox({
            selection_mode: Gtk.SelectionMode.BROWSE,
            activate_on_single_click: true,
        });
        this.list.connect('row-activated', (_list, row) => {
            this.selected = row.get_index();
            this.pasteSelected();
        });
        scrolled.set_child(this.list);
        root.append(scrolled);
        this.window.set_child(root);

        const key = new Gtk.EventControllerKey();
        key.connect('key-pressed', (_controller, keyval) => {
            if (keyval === Gdk.KEY_Escape) {
                this.application.quit();
                return true;
            }
            if (keyval === Gdk.KEY_Down) {
                this.selectRelative(1);
                return true;
            }
            if (keyval === Gdk.KEY_Up) {
                this.selectRelative(-1);
                return true;
            }
            if (keyval === Gdk.KEY_Home) {
                this.selectIndex(0);
                return true;
            }
            if (keyval === Gdk.KEY_End) {
                this.selectIndex(this.visibleEntries.length - 1);
                return true;
            }
            if (keyval === Gdk.KEY_Return || keyval === Gdk.KEY_KP_Enter) {
                this.pasteSelected();
                return true;
            }
            return false;
        });
        this.window.add_controller(key);
    }

    loadEntries() {
        const text = run([HELPER, '--list']);
        try {
            this.entries = JSON.parse(text || '[]');
        } catch (error) {
            printerr(`Failed to parse clipboard history: ${error.message}`);
            this.entries = [];
        }
        this.renderRows();
    }

    renderRows() {
        while (this.list.get_first_child())
            this.list.remove(this.list.get_first_child());

        const query = this.search.get_text().trim().toLowerCase();
        this.visibleEntries = query
            ? this.entries.filter(entry => (entry.content ?? '').toLowerCase().includes(query))
            : [...this.entries];

        if (this.visibleEntries.length === 0) {
            const row = new Gtk.ListBoxRow();
            row.set_child(new Gtk.Label({
                label: 'No clipboard history',
                margin_top: 30,
                margin_bottom: 30,
            }));
            this.list.append(row);
            return;
        }

        this.selected = Math.max(0, Math.min(this.selected, this.visibleEntries.length - 1));
        for (const entry of this.visibleEntries)
            this.list.append(this.makeRow(entry));
        this.selectIndex(this.selected);
    }

    makeRow(entry) {
        const row = new Gtk.ListBoxRow();
        const box = new Gtk.Box({
            orientation: Gtk.Orientation.HORIZONTAL,
            spacing: 6,
            margin_top: 6,
            margin_bottom: 6,
            margin_start: 6,
            margin_end: 6,
        });

        const content = new Gtk.Box({
            orientation: Gtk.Orientation.VERTICAL,
            spacing: 2,
            hexpand: true,
        });

        const preview = entry.kind === 'image'
            ? '[Image]'
            : (entry.content ?? '[empty]').replace(/[\n\t]+/g, ' ').slice(0, 150);
        const label = new Gtk.Label({
            label: preview,
            xalign: 0,
            ellipsize: 3,
            max_width_chars: 48,
        });
        content.append(label);

        const time = new Date(entry.created_at * 1000).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
        });
        const timestamp = new Gtk.Label({label: time, xalign: 0});
        timestamp.add_css_class('dim-label');
        content.append(timestamp);
        box.append(content);

        const pin = new Gtk.Button({icon_name: 'view-pin-symbolic'});
        pin.set_tooltip_text(entry.pinned ? 'Unpin' : 'Pin');
        pin.connect('clicked', () => {
            run([HELPER, '--pin', String(entry.id)]);
            this.loadEntries();
        });
        box.append(pin);

        const del = new Gtk.Button({icon_name: 'edit-delete-symbolic'});
        del.set_tooltip_text('Delete');
        del.connect('clicked', () => {
            run([HELPER, '--delete', String(entry.id)]);
            this.loadEntries();
        });
        box.append(del);

        row.set_child(box);
        return row;
    }

    selectRelative(delta) {
        this.selectIndex(this.selected + delta);
    }

    selectIndex(index) {
        if (this.visibleEntries.length === 0)
            return;
        this.selected = Math.max(0, Math.min(index, this.visibleEntries.length - 1));
        const row = this.list.get_row_at_index(this.selected);
        if (row)
            this.list.select_row(row);
    }

    pasteSelected() {
        const entry = this.visibleEntries[this.selected];
        if (!entry)
            return;

        const target = this.target;
        const terminal = isTerminal(target.wmClass);
        this.window.hide();
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 120, () => {
            if (target.windowId)
                commandOutput('xdotool', ['windowactivate', target.windowId]);
            const args = [HELPER, '--paste', String(entry.id)];
            if (terminal)
                args.push('--terminal');
            run(args);
            this.application.quit();
            return GLib.SOURCE_REMOVE;
        });
    }

    positionWindow() {
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 30, () => {
            const [screenW, screenH] = displaySize();
            let x = Math.floor((screenW - POPUP_WIDTH) / 2);
            let y = Math.floor((screenH - POPUP_HEIGHT) / 2);

            if (this.position === 'pointer') {
                const [pointerX, pointerY] = pointerPosition();
                x = pointerX + 12;
                y = pointerY + 12;
            } else if (this.position === 'window') {
                const rect = activeWindowRect(this.target.windowId);
                if (rect) {
                    x = rect.x + Math.floor((rect.width - POPUP_WIDTH) / 2);
                    y = rect.y + Math.min(64, Math.floor(rect.height / 5));
                }
            }

            x = Math.max(12, Math.min(x, screenW - POPUP_WIDTH - 12));
            y = Math.max(12, Math.min(y, screenH - POPUP_HEIGHT - 12));
            const id = commandOutput('xdotool', ['search', '--name', 'Clipboard History'])
                .split('\n')
                .filter(Boolean)
                .pop();
            if (id)
                commandOutput('xdotool', ['windowmove', id, String(x), String(y)]);
            return GLib.SOURCE_REMOVE;
        });
    }

    present() {
        this.window.present();
        this.search.grab_focus();
    }
}

const app = new Gtk.Application({
    application_id: 'tech.missionzero.clipboard-history-picker',
    flags: Gio.ApplicationFlags.FLAGS_NONE,
});

app.connect('activate', application => {
    const picker = new ClipboardHistoryPicker(application);
    picker.present();
});

app.run(ARGV);
