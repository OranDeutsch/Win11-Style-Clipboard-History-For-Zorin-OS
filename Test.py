#!/usr/bin/env python3
import pyatspi

def get_focused_accessible():
    desktop = pyatspi.Registry.getDesktop(0)

    def walk(acc):
        try:
            if acc.getState().contains(pyatspi.STATE_FOCUSED):
                return acc
        except Exception:
            return None

        try:
            for i in range(acc.childCount):
                found = walk(acc.getChildAtIndex(i))
                if found:
                    return found
        except Exception:
            pass

        return None

    return walk(desktop)

def get_caret_rect():
    acc = get_focused_accessible()
    if not acc:
        return None

    try:
        text = acc.queryText()
    except Exception:
        return None

    offset = text.caretOffset

    try:
        # Returns x, y, width, height in screen coordinates
        x, y, w, h = text.getCharacterExtents(
            offset,
            pyatspi.DESKTOP_COORDS
        )
        return x, y, w, h, acc.name, acc.getRoleName()
    except Exception:
        return None

if __name__ == "__main__":
    result = get_caret_rect()

    if result:
        x, y, w, h, name, role = result
        print(f"Caret position: x={x}, y={y}, width={w}, height={h}")
        print(f"Focused object: {name!r}, role={role}")
    else:
        print("Could not get caret position.")