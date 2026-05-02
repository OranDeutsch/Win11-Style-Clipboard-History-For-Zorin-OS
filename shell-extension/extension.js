import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const WIDTH = 580;
const HEIGHT = 720;
const POSITIONS = [
    ['center', 'Center'],
    ['pointer', 'Mouse'],
    ['focus-window', 'Window'],
];

export default class ClipboardHistoryExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._picker = new ClipboardHistoryPicker(this._settings);

        Main.wm.addKeybinding(
            'show-history',
            this._settings,
            Meta.KeyBindingFlags.IGNORE_AUTOREPEAT,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW,
            () => this._picker.toggle()
        );
    }

    disable() {
        Main.wm.removeKeybinding('show-history');
        this._picker?.destroy();
        this._picker = null;
        this._settings = null;
    }
}

class ClipboardHistoryPicker {
    constructor(settings) {
        this._settings = settings;
        this._entries = [];
        this._selected = 0;
        this._targetWindow = null;
        this._targetWmClass = '';

        this._buildUi();
        this._settingsChangedId = this._settings.connect('changed::popup-position', () => {
            this._refreshPositionButtons();
            if (this._overlay.visible)
                this._positionPanel();
        });
    }

    destroy() {
        if (this._settingsChangedId) {
            this._settings.disconnect(this._settingsChangedId);
            this._settingsChangedId = 0;
        }
        this.hide();
        this._overlay.destroy();
    }

    toggle() {
        if (this._overlay.visible)
            this.hide();
        else
            this.show();
    }

    show() {
        this._targetWindow = global.display.focus_window;
        this._targetWmClass = (this._targetWindow?.get_wm_class() ?? '').toLowerCase();

        this._search.set_text('');
        this._loadEntries();
        this._overlay.show();
        this._positionPanel();
        global.stage.set_key_focus(this._search.clutter_text);
    }

    hide() {
        this._overlay.hide();
    }

    _buildUi() {
        this._overlay = new St.Widget({
            style_class: 'clipboard-history-shade',
            reactive: true,
            visible: false,
            x: 0,
            y: 0,
        });
        this._overlay.set_size(global.stage.width, global.stage.height);
        this._overlay.connect('button-press-event', (_actor, event) => {
            if (event.get_source() === this._overlay) {
                this.hide();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        this._panel = new St.BoxLayout({
            style_class: 'popup-menu-content clipboard-history-panel',
            vertical: true,
            reactive: true,
            can_focus: true,
        });
        this._panel.set_size(WIDTH, HEIGHT);
        this._overlay.add_child(this._panel);

        this._search = new St.Entry({
            style_class: 'clipboard-history-search',
            hint_text: 'Search clipboard history',
            can_focus: true,
            x_expand: true,
        });
        this._panel.add_child(this._search);
        this._search.clutter_text.connect('text-changed', () => this._renderRows());
        this._search.clutter_text.connect('activate', () => this._pasteSelected());
        this._search.clutter_text.connect('key-press-event', (_actor, event) => this._onKeyPress(event));

        this._toolbar = new St.BoxLayout({
            style_class: 'clipboard-history-toolbar',
            vertical: false,
        });
        this._panel.add_child(this._toolbar);

        this._toolbar.add_child(new St.Label({
            style_class: 'clipboard-history-toolbar-label',
            text: 'Open at',
            y_align: Clutter.ActorAlign.CENTER,
        }));

        this._positionButtons = new Map();
        for (const [value, label] of POSITIONS) {
            const button = new St.Button({
                style_class: 'clipboard-history-segment',
                label,
                can_focus: true,
                x_expand: true,
            });
            button.connect('clicked', () => {
                this._settings.set_string('popup-position', value);
            });
            this._toolbar.add_child(button);
            this._positionButtons.set(value, button);
        }

        const clear = new St.Button({
            style_class: 'clipboard-history-icon-button',
            child: new St.Icon({icon_name: 'edit-clear-all-symbolic'}),
            can_focus: true,
        });
        clear.connect('clicked', () => {
            this._run(['--clear']);
            this._loadEntries();
        });
        this._toolbar.add_child(clear);

        this._scroll = new St.ScrollView({
            style_class: 'clipboard-history-list',
            overlay_scrollbars: true,
            x_expand: true,
            y_expand: true,
        });
        this._rowsBox = new St.BoxLayout({
            style_class: 'clipboard-history-rows',
            vertical: true,
            x_expand: true,
        });
        this._scroll.set_child(this._rowsBox);
        this._panel.add_child(this._scroll);

        this._overlay.connect('key-press-event', (_actor, event) => this._onKeyPress(event));
        Main.layoutManager.addChrome(this._overlay, {
            affectsInputRegion: true,
            trackFullscreen: true,
        });
        this._refreshPositionButtons();
    }

    _onKeyPress(event) {
        const symbol = event.get_key_symbol();
        if (symbol === Clutter.KEY_Escape) {
            this.hide();
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_Down) {
            this._selectRelative(1);
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_Up) {
            this._selectRelative(-1);
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_Home) {
            this._selectIndex(0);
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_End) {
            this._selectIndex(this._visibleEntries().length - 1);
            return Clutter.EVENT_STOP;
        }
        if (symbol === Clutter.KEY_Return || symbol === Clutter.KEY_KP_Enter) {
            this._pasteSelected();
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
    }

    _loadEntries() {
        const text = this._run(['--list']);
        try {
            this._entries = JSON.parse(text || '[]');
        } catch (error) {
            logError(error, 'Failed to parse clipboard history');
            this._entries = [];
        }
        this._selected = 0;
        this._renderRows();
    }

    _renderRows() {
        this._rowsBox.destroy_all_children();
        const entries = this._visibleEntries();

        if (entries.length === 0) {
            this._rowsBox.add_child(new St.Label({
                style_class: 'clipboard-history-empty',
                text: 'No clipboard history',
            }));
            return;
        }

        this._selected = Math.max(0, Math.min(this._selected, entries.length - 1));
        entries.forEach((entry, index) => this._rowsBox.add_child(this._makeRow(entry, index)));
    }

    _makeRow(entry, index) {
        const selected = index === this._selected;
        const row = new St.Button({
            style_class: selected ? 'clipboard-history-row clipboard-history-row-selected' : 'clipboard-history-row',
            can_focus: true,
            x_expand: true,
        });

        const layout = new St.BoxLayout({vertical: false, x_expand: true});
        row.set_child(layout);

        const textBox = new St.BoxLayout({vertical: true, x_expand: true});
        const preview = this._preview(entry);
        textBox.add_child(new St.Label({
            style_class: 'clipboard-history-row-text',
            text: preview,
            x_expand: true,
        }));
        textBox.add_child(new St.Label({
            style_class: 'clipboard-history-row-time',
            text: this._timeLabel(entry.created_at),
        }));
        layout.add_child(textBox);

        const actions = new St.BoxLayout({
            style_class: 'clipboard-history-row-actions',
            vertical: false,
        });

        const pin = new St.Button({
            style_class: 'clipboard-history-icon-button',
            child: new St.Icon({icon_name: entry.pinned ? 'view-pin-symbolic' : 'view-pin-symbolic'}),
            can_focus: true,
        });
        pin.connect('clicked', () => {
            this._run(['--pin', String(entry.id)]);
            this._loadEntries();
        });
        actions.add_child(pin);

        const del = new St.Button({
            style_class: 'clipboard-history-icon-button',
            child: new St.Icon({icon_name: 'edit-delete-symbolic'}),
            can_focus: true,
        });
        del.connect('clicked', () => {
            this._run(['--delete', String(entry.id)]);
            this._loadEntries();
        });
        actions.add_child(del);

        layout.add_child(actions);

        row.connect('clicked', () => {
            this._selected = index;
            this._pasteSelected();
        });
        return row;
    }

    _visibleEntries() {
        const query = this._search.get_text().trim().toLowerCase();
        if (!query)
            return this._entries;

        return this._entries.filter(entry => (entry.content ?? '').toLowerCase().includes(query));
    }

    _preview(entry) {
        if (entry.kind === 'image')
            return '[Image]';
        return (entry.content ?? '[empty]').replace(/[\n\t]+/g, ' ').slice(0, 140);
    }

    _timeLabel(seconds) {
        const date = new Date(seconds * 1000);
        return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    }

    _selectRelative(delta) {
        this._selectIndex(this._selected + delta);
    }

    _selectIndex(index) {
        const entries = this._visibleEntries();
        if (entries.length === 0)
            return;
        this._selected = Math.max(0, Math.min(index, entries.length - 1));
        this._renderRows();
    }

    _pasteSelected() {
        const entry = this._visibleEntries()[this._selected];
        if (!entry)
            return;

        const terminal = this._isTerminal(this._targetWmClass);
        this.hide();
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 80, () => {
            const args = ['--paste', String(entry.id)];
            if (terminal)
                args.push('--terminal');
            this._run(args);
            return GLib.SOURCE_REMOVE;
        });
    }

    _isTerminal(wmClass) {
        return wmClass.includes('terminal') ||
            ['kgx', 'console', 'tilix', 'alacritty', 'kitty', 'xterm', 'konsole'].includes(wmClass);
    }

    _refreshPositionButtons() {
        const active = this._settings.get_string('popup-position');
        for (const [value, button] of this._positionButtons) {
            if (value === active)
                button.add_style_class_name('clipboard-history-segment-active');
            else
                button.remove_style_class_name('clipboard-history-segment-active');
        }
    }

    _positionPanel() {
        this._overlay.set_position(0, 0);
        this._overlay.set_size(global.stage.width, global.stage.height);

        const monitor = Main.layoutManager.primaryMonitor;
        let x = monitor.x + Math.floor((monitor.width - WIDTH) / 2);
        let y = monitor.y + Math.floor((monitor.height - HEIGHT) / 2);

        const mode = this._settings.get_string('popup-position');
        if (mode === 'pointer') {
            const [pointerX, pointerY] = global.get_pointer();
            x = pointerX + 12;
            y = pointerY + 12;
        } else if (mode === 'focus-window' && this._targetWindow) {
            const rect = this._targetWindow.get_frame_rect();
            x = rect.x + Math.floor((rect.width - WIDTH) / 2);
            y = rect.y + Math.min(64, Math.floor(rect.height / 5));
        }

        x = Math.max(monitor.x + 12, Math.min(x, monitor.x + monitor.width - WIDTH - 12));
        y = Math.max(monitor.y + 12, Math.min(y, monitor.y + monitor.height - HEIGHT - 12));
        this._panel.set_position(x, y);
    }

    _run(args) {
        const bin = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'clipboard-history']);
        try {
            const [ok, stdout, stderr, status] = GLib.spawn_sync(
                null,
                [bin, ...args],
                null,
                GLib.SpawnFlags.SEARCH_PATH,
                null
            );

            if (!ok || status !== 0) {
                const message = new TextDecoder().decode(stderr).trim();
                if (message)
                    log(`clipboard-history: ${message}`);
                return '';
            }
            return new TextDecoder().decode(stdout).trim();
        } catch (error) {
            logError(error, 'Failed to run clipboard-history helper');
            return '';
        }
    }
}
