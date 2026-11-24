# studio-helper changes

The version is in `VERSION`; `build.sh` builds it into the program, and Studio reads it to say when a computer runs an older helper.

## 0.08 (2026-11-20)
- The app is now called Eidoloom Studio: the window, tray, folder picker and sign-in entry use the new name. Nothing else changed.

## 0.07 (2026-11-17)
- Starting a newer helper while an older one runs replaces it: the old one quits and the new one takes over the folders (before, it only opened the old one's window, so the folders went on with the old version, which Studio showed). Sign-in autostart then points at the new file.
- Disconnect in the helper (its window, its page, or `studio-helper remove N`) tells Studio too: Studio's My computers drops the folder at once and keeps its copy as an ordinary folder. Before, Studio kept listing it as offline until it was disconnected there as well. When Studio cannot be reached, the helper says so.

## 0.06 (2026-11-11)
- Web servers on this computer show in Studio: the Live tab, `open_preview` and the agent's `view_page` reach them through the helper, which makes each request to 127.0.0.1 here and sends the answer back (up to 16 MB each). Only servers started from Studio's commands and terminals count; nothing else running on the computer is reachable. A server started before this version needs starting again.
- A command that starts a server in the background no longer hangs until Studio's time limit (it waits at most 3 s for the output after the command ends).
- Windows exit codes such as 0xC000013A show as negative numbers, as Windows tools write them.

## 0.05 (2026-11-11)
- The window has the helper's icon on the taskbar, not PowerShell's: it takes an app identity of its own (`StrataStudio.Helper`) before it opens.
- Studio's Connect dialog has a New code button (from Studio, not the helper).

## 0.04 (2026-11-11)
- The window opens again on Windows. 0.03 started PowerShell hidden, and Windows applied that to the window itself, so it stayed invisible and held the one-window lock; the tray's Open and Connect then did nothing.
- At start the helper closes windows an earlier run left behind, and a window closes itself when its helper has quit.
- The window shows its errors in a message box and writes them to `window.log` beside the settings.
- The version is in the tray menu, the window's title and its new About button; Studio's My computers list shows each computer's version.
- Started by hand it opens its window; at sign-in it stays silent in the tray.

## 0.03
- On Windows, its own Windows Forms window (through PowerShell) instead of a page in the browser. Started quietly, but a double-click with folders already saved then showed nothing.

## 0.02
- No console window: a tray icon, a form page in the browser, a folder picker, and starting at sign-in after the first pairing.

## 0.01
- First version: pairing, sync and commands, with questions answered in a console window.
